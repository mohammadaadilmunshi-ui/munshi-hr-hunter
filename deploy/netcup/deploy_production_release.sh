#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${MUNSHI_ROOT:-/opt/munshi}"
REPO="$ROOT/repo"
ENV_FILE="$ROOT/secrets/netcup-shadow.env"
PROJECT="${MUNSHI_COMPOSE_PROJECT:-munshi-netcup-shadow}"
VERIFY="/opt/munshi/bin/verify-production-runtime-contract"

H="munshi-netcup-shadow-hunter-1"
N="munshi-netcup-shadow-n8n-1"
O="munshi-netcup-shadow-ollama-1"

commit=""
branch=""
bundle_file=""
deploy_ref=""
reuse_backup=""

cleanup() {
  if [[ -n "${bundle_file:-}" ]]; then
    rm -f "$bundle_file" 2>/dev/null || true
  fi
  if [[ -n "${deploy_ref:-}" && -d "$REPO/.git" ]]; then
    git -C "$REPO" update-ref -d "$deploy_ref" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --commit) commit="${2:-}"; shift 2 ;;
    --branch) branch="${2:-}"; shift 2 ;;
    --reuse-backup) reuse_backup="${2:-}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo "--commit must be a full lowercase Git SHA" >&2; exit 3; }
[[ "$branch" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "--branch invalid" >&2; exit 4; }
git check-ref-format --branch "$branch" >/dev/null || { echo "--branch is not a valid Git branch name" >&2; exit 4; }
[[ -x "$VERIFY" ]] || { echo "stable verifier missing: $VERIFY" >&2; exit 5; }

bundle_file="$(mktemp /tmp/munshi-github-deploy.XXXXXX.bundle)"
timeout 120s cat > "$bundle_file" || { echo "deployment bundle transfer timed out" >&2; exit 6; }
[[ -s "$bundle_file" ]] || { echo "deployment bundle is empty" >&2; exit 7; }

compose=(
  docker compose
  --project-name "$PROJECT"
  --env-file "$ENV_FILE"
  -f "$REPO/compose.yaml"
  -f "$REPO/compose.netcup-shadow.yaml"
  -f "$ROOT/runtime/stage10-imported.override.yaml"
  -f "$ROOT/runtime/stage12-production.override.yaml"
  -f "$ROOT/runtime/stage12-n8n-runtime-repair.override.yaml"
)

cd "$REPO"

echo "=== PREDEPLOY CONTRACT ==="
"$VERIFY"

[[ -z "$(git status --porcelain)" ]] || {
  echo "dirty production repository; refusing deployment" >&2
  exit 10
}

old_head="$(git rev-parse HEAD)"
old_branch="$(git branch --show-current)"
old_hunter_image_id="$(docker inspect -f '{{.Image}}' "$H")"
hunter_image_name="$(docker inspect -f '{{.Config.Image}}' "$H")"
n8n_id_before="$(docker inspect -f '{{.Id}}' "$N")"
n8n_started_before="$(docker inspect -f '{{.State.StartedAt}}' "$N")"
ollama_id_before="$(docker inspect -f '{{.Id}}' "$O")"
ollama_started_before="$(docker inspect -f '{{.State.StartedAt}}' "$O")"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
rollback_tag="${hunter_image_name%:*}:rollback-deploy-$stamp"
recreated=0
migration_attempted=0
hunter_stopped=0
db_restore_completed=0
db_backup=""
backup_name=""
db_backup_format=""
db_backup_sha256=""

restore_production_db() {
  echo "=== RESTORE PRODUCTION DB BACKUP ===" >&2
  "${compose[@]}" stop hunter >/dev/null 2>&1 || docker stop "$H" >/dev/null 2>&1 || true
  hunter_stopped=1

  [[ -n "$db_backup" && -n "$backup_name" && -n "$db_backup_format" && -n "$db_backup_sha256" ]] || {
    echo "production rollback snapshot metadata is incomplete" >&2
    return 1
  }

  timeout 1800s docker run --rm -i \
    --network none \
    --user 0:0 \
    --mount "type=volume,src=$hunter_data_volume,dst=/app/hunter/data" \
    --mount "type=bind,src=$backup_dir,dst=/backup,readonly" \
    --entrypoint python \
    "$old_hunter_image_id" \
    - "$db_path" "/backup/$backup_name" "$db_uid" "$db_gid" "$db_mode" \
      "$db_backup_format" "$db_backup_sha256" <<'PYRESTORE'
import gzip
import hashlib
import os
import sqlite3
import sys
from pathlib import Path

live = Path(sys.argv[1])
backup = Path(sys.argv[2])
uid = int(sys.argv[3])
gid = int(sys.argv[4])
mode = int(sys.argv[5], 8)
backup_format = sys.argv[6]
expected_sha256 = sys.argv[7]
tmp = Path(str(live) + ".rollback.tmp")

if not backup.is_file():
    raise SystemExit(f"production rollback backup missing: {backup}")
if backup_format not in {"RAW_IDENTICAL", "GZIP_V1"}:
    raise SystemExit(f"unsupported production rollback backup format: {backup_format}")
if len(expected_sha256) != 64:
    raise SystemExit("production rollback raw SHA-256 is invalid")

for candidate in (
    tmp,
    Path(str(live) + "-wal"),
    Path(str(live) + "-shm"),
    Path(str(live) + "-journal"),
):
    try:
        candidate.unlink()
    except FileNotFoundError:
        pass

source = open(backup, "rb") if backup_format == "RAW_IDENTICAL" else gzip.open(backup, "rb")
digest = hashlib.sha256()
try:
    with source, open(tmp, "xb") as out:
        while True:
            chunk = source.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
except Exception:
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    raise

actual_sha256 = digest.hexdigest()
if actual_sha256 != expected_sha256:
    tmp.unlink(missing_ok=True)
    raise SystemExit(
        f"production rollback raw SHA-256 mismatch: {actual_sha256} != {expected_sha256}"
    )

os.chown(tmp, uid, gid)
os.chmod(tmp, mode)
os.replace(tmp, live)
os.chown(live, uid, gid)
os.chmod(live, mode)

db = sqlite3.connect(f"file:{live}?mode=ro", uri=True, timeout=60)
try:
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=60000")
    quick = str(db.execute("PRAGMA quick_check").fetchone()[0])
    fk = db.execute("PRAGMA foreign_key_check").fetchall()
finally:
    db.close()

if quick != "ok":
    raise SystemExit(f"restored production DB quick_check failed: {quick}")
if fk:
    raise SystemExit(f"restored production DB FK violations: {len(fk)}")
print("ROLLBACK_DB_RESTORE=PASS")
print(f"ROLLBACK_DB_RAW_SHA256={actual_sha256}")
PYRESTORE
  db_restore_completed=1
}
rollback() {
  trapped_rc=$?
  rc="${1:-$trapped_rc}"
  trap - ERR
  echo "=== AUTOMATIC DEPLOYMENT ROLLBACK rc=$rc ===" >&2

  if (( migration_attempted )); then
    if ! restore_production_db; then
      echo "CRITICAL: production DB rollback restore failed; Hunter remains stopped." >&2
      exit 99
    fi
  fi

  if [[ -n "$old_branch" ]]; then
    git checkout -q -B "$old_branch" "$old_head" || true
  else
    git checkout -q --detach "$old_head" || true
  fi
  docker tag "$old_hunter_image_id" "$hunter_image_name" || true
  echo "ROLLBACK_IMAGE_TAG_RESTORED=YES" >&2
  if (( recreated || hunter_stopped || migration_attempted )); then
    "${compose[@]}" up -d --no-deps --force-recreate hunter || true
    for _ in $(seq 1 48); do
      health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$H" 2>/dev/null || true)"
      [[ "$health" == "healthy" ]] && break
      sleep 5
    done
  fi
  "$VERIFY" || true
  echo "RESULT=DEPLOYMENT_ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback ERR

echo "=== SAFETY / IDLE GATE ==="
active_workers="$(docker exec "$H" sh -lc 'ps -eo args= | grep -E "[a]pp\.stored_job_n8n_worker|[a]pp\.manual_input_worker" | wc -l | tr -d " "')"
[[ "$active_workers" == "0" ]] || { echo "manual/stored worker active" >&2; exit 20; }

explicit_locks="$(docker logs --timestamps --since 10m "$H" 2>&1 | grep -Ei 'database is locked|database table is locked|database schema is locked|OperationalError:.*locked' || true)"
[[ -z "$explicit_locks" ]] || { echo "$explicit_locks" >&2; exit 21; }

active_n8n="$(docker exec -i "$H" python - <<'PY'
import os,sqlite3
p=os.environ["N8N_DATABASE_PATH"]
db=sqlite3.connect(f"file:{p}?mode=ro",uri=True,timeout=30)
db.execute("PRAGMA query_only=ON")
db.execute("PRAGMA busy_timeout=30000")
try:
    n=db.execute("SELECT COUNT(*) FROM execution_entity WHERE status IN ('new','running','waiting') OR stoppedAt IS NULL").fetchone()[0]
except Exception:
    n=db.execute("SELECT COUNT(*) FROM execution_entity WHERE status IN ('new','running','waiting')").fetchone()[0]
print(n)
db.close()
PY
)"
[[ "$active_n8n" == "0" ]] || { echo "active n8n executions=$active_n8n" >&2; exit 22; }

open_queue="$(docker exec -i "$H" python - <<'PY'
import sqlite3
p="/app/hunter/data/hunter.db"
db=sqlite3.connect(f"file:{p}?mode=ro",uri=True,timeout=30)
db.execute("PRAGMA query_only=ON")
cols={r[1] for r in db.execute("PRAGMA table_info(n8n_dispatch_queue)")}
state=next((c for c in ("queue_status","status","state") if c in cols),None)
if not state:
    raise SystemExit("queue state column unresolved")
states=("queued","pending","dispatching","reserved","accepted","running","processing","in_progress")
ph=",".join("?" for _ in states)
n=db.execute(f'SELECT COUNT(*) FROM n8n_dispatch_queue WHERE "{state}" IN ({ph})', states).fetchone()[0]
print(n)
db.close()
PY
)"
[[ "$open_queue" == "0" ]] || { echo "open n8n queue=$open_queue" >&2; exit 23; }

prepare_storage_safe_backup() {
  echo "=== PRODUCTION HUNTER DB STORAGE-SAFE ROLLBACK SNAPSHOT ==="
  backup_dir="$ROOT/backups"
  mkdir -p "$backup_dir"
  chmod 750 "$backup_dir"

  db_path="$(docker exec -i "$H" python - <<'PY'
from app.database import DB_PATH
print(DB_PATH)
PY
)"
  [[ -n "$db_path" ]] || { echo "production DB path unresolved" >&2; return 24; }

  db_meta="$(docker exec "$H" stat -c '%u:%g:%a' "$db_path")"
  IFS=: read -r db_uid db_gid db_mode <<< "$db_meta"
  [[ "$db_uid" =~ ^[0-9]+$ && "$db_gid" =~ ^[0-9]+$ && "$db_mode" =~ ^[0-7]+$ ]] || {
    echo "production DB ownership/mode unresolved" >&2
    return 24
  }
  echo "PRODUCTION_DB_UID_GID_MODE=$db_uid:$db_gid:$db_mode"

  db_bytes="$(docker exec "$H" stat -c '%s' "$db_path")"
  [[ "$db_bytes" =~ ^[0-9]+$ ]] || { echo "production DB size unresolved" >&2; return 25; }
  echo "PRODUCTION_DB_BYTES=$db_bytes"

  hunter_data_volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/hunter/data"}}{{.Name}}{{end}}{{end}}' "$H")"
  [[ -n "$hunter_data_volume" ]] || {
    echo "production Hunter data volume unresolved" >&2
    return 28
  }
  echo "PRODUCTION_HUNTER_DATA_VOLUME=$hunter_data_volume"

  # Freeze all Hunter DB writers before deriving a rollback snapshot.
  "${compose[@]}" stop hunter
  hunter_stopped=1

  # With Hunter stopped, checkpoint WAL into the main DB and prove the live
  # database is structurally sound before hashing/compressing it.
  timeout 1200s docker run --rm -i \
    --network none \
    --user 0:0 \
    --mount "type=volume,src=$hunter_data_volume,dst=/app/hunter/data" \
    --entrypoint python \
    "$old_hunter_image_id" \
    - "$db_path" <<'PYCHECK'
import sqlite3
import sys

path = sys.argv[1]
db = sqlite3.connect(path, timeout=60)
try:
    db.execute("PRAGMA busy_timeout=60000")
    checkpoint = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    quick = str(db.execute("PRAGMA quick_check").fetchone()[0])
    fk = db.execute("PRAGMA foreign_key_check").fetchall()
finally:
    db.close()
if quick != "ok":
    raise SystemExit(f"live production DB quick_check failed: {quick}")
if fk:
    raise SystemExit(f"live production DB FK violations: {len(fk)}")
print("PRODUCTION_DB_WAL_CHECKPOINT=PASS")
print("PRODUCTION_DB_PREBACKUP_QUICK_CHECK=PASS")
PYCHECK

  db_backup_sha256="$(timeout 1200s docker run --rm -i \
    --network none \
    --user 0:0 \
    --mount "type=volume,src=$hunter_data_volume,dst=/app/hunter/data,readonly" \
    --entrypoint python \
    "$old_hunter_image_id" \
    - "$db_path" <<'PYHASH'
import hashlib
import sys
from pathlib import Path
p = Path(sys.argv[1])
h = hashlib.sha256()
with p.open("rb") as fh:
    while True:
        chunk = fh.read(8 * 1024 * 1024)
        if not chunk:
            break
        h.update(chunk)
print(h.hexdigest())
PYHASH
)"
  [[ "$db_backup_sha256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "live production DB SHA-256 unresolved" >&2
    return 29
  }
  echo "PRODUCTION_DB_RAW_SHA256=$db_backup_sha256"

  # First preference: reuse the previously verified raw snapshot, but ONLY when
  # it is byte-for-byte identical to the now-frozen/checkpointed live DB.
  if [[ -n "$reuse_backup" ]]; then
    reuse_real="$(realpath -e "$reuse_backup" 2>/dev/null || true)"
    backup_root_real="$(realpath -e "$backup_dir")"
    if [[ -n "$reuse_real" && "$reuse_real" == "$backup_root_real/"* && -f "$reuse_real" ]]; then
      reuse_sha="$(sha256sum "$reuse_real" | awk '{print $1}')"
      if [[ "$reuse_sha" == "$db_backup_sha256" ]]; then
        python3 - "$reuse_real" <<'PYREUSE'
import sqlite3
import sys
p = sys.argv[1]
db = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=60)
try:
    db.execute("PRAGMA query_only=ON")
    quick = str(db.execute("PRAGMA quick_check").fetchone()[0])
    fk = db.execute("PRAGMA foreign_key_check").fetchall()
finally:
    db.close()
if quick != "ok":
    raise SystemExit(f"reused production backup quick_check failed: {quick}")
if fk:
    raise SystemExit(f"reused production backup FK violations: {len(fk)}")
print("PRODUCTION_DB_REUSED_BACKUP_INTEGRITY=PASS")
PYREUSE
        db_backup="$reuse_real"
        backup_name="$(basename "$db_backup")"
        db_backup_format="RAW_IDENTICAL"
        echo "PRODUCTION_DB_BACKUP_REUSED=YES"
        echo "PRODUCTION_DB_BACKUP_FORMAT=RAW_IDENTICAL"
        echo "PRODUCTION_DB_BACKUP=$db_backup"
        return 0
      fi
      echo "PRODUCTION_DB_EXISTING_BACKUP_IDENTICAL=NO"
    else
      echo "PRODUCTION_DB_EXISTING_BACKUP_REUSABLE=NO"
    fi
  fi

  # If the old raw backup is stale, create a CURRENT compressed snapshot only.
  # Never create another uncompressed 8+ GB host backup.
  max_compressed_bytes="${MUNSHI_MAX_COMPRESSED_HUNTER_BACKUP_BYTES:-7516192768}"
  [[ "$max_compressed_bytes" =~ ^[0-9]+$ && "$max_compressed_bytes" -gt 0 ]] || {
    echo "invalid MUNSHI_MAX_COMPRESSED_HUNTER_BACKUP_BYTES" >&2
    return 30
  }
  backup_free_bytes="$(df -PB1 "$backup_dir" | awk 'NR==2 {print $4}')"
  [[ "$backup_free_bytes" =~ ^[0-9]+$ ]] || {
    echo "production backup free space unresolved" >&2
    return 31
  }
  echo "PRODUCTION_BACKUP_FREE_BYTES=$backup_free_bytes"
  echo "PRODUCTION_COMPRESSED_BACKUP_MAX_BYTES=$max_compressed_bytes"
  (( backup_free_bytes >= max_compressed_bytes + 536870912 )) || {
    echo "insufficient free space for bounded compressed rollback snapshot" >&2
    return 32
  }

  db_backup="$backup_dir/hunter-predeploy-$stamp.db.gz"
  backup_name="$(basename "$db_backup")"
  db_backup_format="GZIP_V1"
  host_uid="$(id -u)"
  host_gid="$(id -g)"

  timeout 2400s docker run --rm -i \
    --network none \
    --user 0:0 \
    --mount "type=volume,src=$hunter_data_volume,dst=/app/hunter/data,readonly" \
    --mount "type=bind,src=$backup_dir,dst=/backup" \
    --entrypoint python \
    "$old_hunter_image_id" \
    - "$db_path" "/backup/$backup_name" "$db_backup_sha256" \
      "$max_compressed_bytes" "$host_uid" "$host_gid" <<'PYCOMPRESS'
import gzip
import hashlib
import os
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
expected_sha = sys.argv[3]
max_bytes = int(sys.argv[4])
uid = int(sys.argv[5])
gid = int(sys.argv[6])
partial = Path(str(dst) + ".partial")

if dst.exists() or partial.exists():
    raise SystemExit(f"compressed rollback destination already exists: {dst}")

digest = hashlib.sha256()
try:
    with src.open("rb") as source, partial.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1, mtime=0) as compressed:
            while True:
                chunk = source.read(8 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                compressed.write(chunk)
                if raw.tell() > max_bytes:
                    raise SystemExit(
                        f"compressed rollback snapshot exceeded storage bound: {raw.tell()} > {max_bytes}"
                    )
        raw.flush()
        os.fsync(raw.fileno())
    if digest.hexdigest() != expected_sha:
        raise SystemExit("compressed rollback source SHA-256 changed during snapshot")
    if partial.stat().st_size > max_bytes:
        raise SystemExit("compressed rollback snapshot exceeded storage bound")
    os.replace(partial, dst)
    os.chown(dst, uid, gid)
    os.chmod(dst, 0o600)
except BaseException:
    for candidate in (partial, dst):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
    raise

print(f"PRODUCTION_DB_COMPRESSED_BACKUP_CREATED=PASS bytes={dst.stat().st_size}")
PYCOMPRESS

  [[ -f "$db_backup" ]] || {
    echo "compressed production rollback snapshot missing" >&2
    return 33
  }

  archive_bytes="$(stat -c '%s' "$db_backup")"
  [[ "$archive_bytes" =~ ^[0-9]+$ && "$archive_bytes" -le "$max_compressed_bytes" ]] || {
    echo "compressed production rollback snapshot size invalid" >&2
    return 34
  }

  python3 - "$db_backup" "$db_backup_sha256" <<'PYVERIFY'
import gzip
import hashlib
import sys

path, expected = sys.argv[1:]
digest = hashlib.sha256()
with gzip.open(path, "rb") as fh:
    while True:
        chunk = fh.read(8 * 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
actual = digest.hexdigest()
if actual != expected:
    raise SystemExit(f"compressed rollback verification mismatch: {actual} != {expected}")
print("PRODUCTION_DB_COMPRESSED_BACKUP_VERIFY=PASS")
PYVERIFY

  echo "PRODUCTION_DB_BACKUP_REUSED=NO"
  echo "PRODUCTION_DB_BACKUP_FORMAT=GZIP_V1"
  echo "PRODUCTION_DB_BACKUP_COMPRESSED_BYTES=$archive_bytes"
  echo "PRODUCTION_DB_BACKUP=$db_backup"
}

echo "=== VERIFY GITHUB-PUBLISHED TARGET BUNDLE ==="
git bundle verify "$bundle_file"
bundle_ref="refs/remotes/origin/$branch"
deploy_ref="refs/remotes/github-deploy/$branch"
git fetch --no-tags "$bundle_file" "+$bundle_ref:$deploy_ref"
git cat-file -e "$commit^{commit}"
git merge-base --is-ancestor "$commit" "$deploy_ref" || {
  echo "requested SHA is not contained in bundled source branch" >&2
  exit 30
}
echo "GITHUB_BUNDLE_IMPORT=PASS"

echo "=== ROLLBACK IMAGE ==="
docker tag "$old_hunter_image_id" "$rollback_tag"
echo "rollback_image=$rollback_tag"
echo "old_head=$old_head"

echo "=== CHECKOUT EXACT SHA ON SOURCE BRANCH ==="
git checkout -q -B "$branch" "$commit"
[[ "$(git rev-parse HEAD)" == "$commit" ]]
[[ "$(git branch --show-current)" == "$branch" ]]

echo "=== STATIC DEPLOYMENT VALIDATION ==="
bash -n deploy/netcup/deploy_production_release.sh
bash -n deploy/netcup/verify_production_runtime_contract.sh
python3 -m compileall -q app scripts integrations

echo "=== BUILD HUNTER ONLY ==="
"${compose[@]}" config -q
"${compose[@]}" build hunter
new_hunter_image_id="$(docker image inspect -f '{{.Id}}' "$hunter_image_name")"
[[ -n "$new_hunter_image_id" ]] || rollback
echo "new_hunter_image_id=$new_hunter_image_id"

echo "=== RUN EXACT-IMAGE HUNTER AUTOAPPLY CONTRACT SUITE (NETWORK DISABLED) ==="
timeout 1200s docker run --rm \
  --network none \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  --tmpfs /app/hunter/data:rw,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=0700 \
  --mount "type=bind,src=$REPO/tests,dst=/app/hunter/tests,readonly" \
  --entrypoint python \
  -e CLOUD_SHADOW_MODE=false \
  "$new_hunter_image_id" \
  -m pytest -q \
    /app/hunter/tests/test_universal_workday_fields_v1.py \
    /app/hunter/tests/test_application_execution_mandate_v1.py \
    /app/hunter/tests/test_application_review_v2.py \
    /app/hunter/tests/test_application_submit_authorization_v1.py \
    /app/hunter/tests/test_submit_authority_delivery_v1.py \
    /app/hunter/tests/test_application_production_submission_receipt_v1.py \
    /app/hunter/tests/test_prepare_worker_adapters_v1c.py
echo "HUNTER_PRODUCTION_EXACT_IMAGE_AUTOAPPLY_TESTS=PASS"

echo "=== PREPARE CURRENT STORAGE-SAFE ROLLBACK SNAPSHOT ==="
prepare_storage_safe_backup
migration_attempted=1

echo "=== APPLY COMPLETE-LOOP MIGRATIONS 041-055 TO PRODUCTION DB ==="
timeout 1800s docker run --rm -i   --network none   --read-only   --tmpfs /tmp:rw,nosuid,nodev,size=256m   --mount "type=volume,src=$hunter_data_volume,dst=/app/hunter/data"   --entrypoint python   -e CLOUD_SHADOW_MODE=false   "$new_hunter_image_id"   /app/hunter/scripts/complete_loop_migrate.py   --db-path "$db_path"   --environment production   --authorization AUTHORIZED_GUARDED_COMPLETE_LOOP_MIGRATION   --full-quick-check
echo "PRODUCTION_COMPLETE_LOOP_MIGRATION=PASS"

echo "=== RECREATE HUNTER ONLY ==="
"${compose[@]}" up -d --no-deps --force-recreate hunter
recreated=1
hunter_stopped=0

healthy=0
for _ in $(seq 1 48); do
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$H" 2>/dev/null || true)"
  if [[ "$health" == "healthy" ]]; then healthy=1; break; fi
  sleep 5
done
[[ "$healthy" == "1" ]] || { echo "Hunter did not return healthy" >&2; rollback 40; }

echo "=== VERIFY NON-HUNTER CONTAINERS UNCHANGED ==="
[[ "$(docker inspect -f '{{.Id}}' "$N")" == "$n8n_id_before" ]]
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$N")" == "$n8n_started_before" ]]
[[ "$(docker inspect -f '{{.Id}}' "$O")" == "$ollama_id_before" ]]
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$O")" == "$ollama_started_before" ]]
echo "N8N_RECREATED=NO"
echo "OLLAMA_RECREATED=NO"

echo "=== POSTDEPLOY CONTRACT ==="
"$VERIFY"

echo "=== VERIFY PRODUCTION COMPLETE-LOOP SCHEMA ==="
docker exec "$H" python /app/hunter/scripts/complete_loop_migrate.py   --db-path "$db_path"   --environment production   --verify-only
echo "PRODUCTION_COMPLETE_LOOP_SCHEMA_READY=YES"

echo "=== DEPLOYMENT RESULT ==="
echo "DEPLOYED_SHA=$commit"
echo "DEPLOYED_BRANCH=$branch"
echo "PREVIOUS_SHA=$old_head"
echo "ROLLBACK_IMAGE=$rollback_tag"
echo "PRODUCTION_DB_BACKUP=$db_backup"
echo "PRODUCTION_DB_BACKUP_FORMAT=$db_backup_format"
echo "PRODUCTION_DB_RAW_SHA256=$db_backup_sha256"
echo "N8N_RECREATED=NO"
echo "OLLAMA_RECREATED=NO"
echo "RESULT=PRODUCTION_DEPLOYMENT_PASS"
trap - ERR