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

restore_production_db() {
  echo "=== RESTORE PRODUCTION DB BACKUP ===" >&2
  "${compose[@]}" stop hunter >/dev/null 2>&1 || docker stop "$H" >/dev/null 2>&1 || true
  hunter_stopped=1

  timeout 1200s docker run --rm -i \
    --network none \
    --user 0:0 \
    --mount "type=volume,src=$hunter_data_volume,dst=/app/hunter/data" \
    --mount "type=bind,src=$backup_dir,dst=/backup,readonly" \
    --entrypoint python \
    "$old_hunter_image_id" \
    - "$db_path" "/backup/$backup_name" "$db_uid" "$db_gid" "$db_mode" <<'PYRESTORE'
import os
import shutil
import sqlite3
import sys
from pathlib import Path

live = Path(sys.argv[1])
backup = Path(sys.argv[2])
uid = int(sys.argv[3])
gid = int(sys.argv[4])
mode = int(sys.argv[5], 8)
tmp = Path(str(live) + ".rollback.tmp")

if not backup.is_file():
    raise SystemExit(f"production rollback backup missing: {backup}")

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

shutil.copyfile(backup, tmp)
os.chown(tmp, uid, gid)
os.chmod(tmp, mode)
os.replace(tmp, live)
os.chown(live, uid, gid)
os.chmod(live, mode)

db = sqlite3.connect(f"file:{live}?mode=ro", uri=True, timeout=60)
try:
    db.execute("PRAGMA query_only=ON")
    quick = str(db.execute("PRAGMA quick_check").fetchone()[0])
    fk = db.execute("PRAGMA foreign_key_check").fetchall()
finally:
    db.close()

if quick != "ok":
    raise SystemExit(f"restored production DB quick_check failed: {quick}")
if fk:
    raise SystemExit(f"restored production DB FK violations: {len(fk)}")
print("ROLLBACK_DB_RESTORE=PASS")
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

echo "=== PRODUCTION HUNTER DB PREDEPLOY BACKUP ==="
backup_dir="$ROOT/backups"
mkdir -p "$backup_dir"
chmod 750 "$backup_dir"

db_path="$(docker exec -i "$H" python - <<'PY'
from app.database import DB_PATH
print(DB_PATH)
PY
)"
[[ -n "$db_path" ]] || { echo "production DB path unresolved" >&2; exit 24; }

db_meta="$(docker exec "$H" stat -c '%u:%g:%a' "$db_path")"
IFS=: read -r db_uid db_gid db_mode <<< "$db_meta"
[[ "$db_uid" =~ ^[0-9]+$ && "$db_gid" =~ ^[0-9]+$ && "$db_mode" =~ ^[0-7]+$ ]] || {
  echo "production DB ownership/mode unresolved" >&2
  exit 24
}
echo "PRODUCTION_DB_UID_GID_MODE=$db_uid:$db_gid:$db_mode"

db_bytes="$(docker exec "$H" stat -c '%s' "$db_path")"
[[ "$db_bytes" =~ ^[0-9]+$ ]] || { echo "production DB size unresolved" >&2; exit 25; }
backup_free_bytes="$(df -PB1 "$backup_dir" | awk 'NR==2 {print $4}')"
[[ "$backup_free_bytes" =~ ^[0-9]+$ ]] || { echo "production backup free space unresolved" >&2; exit 26; }
backup_required_bytes=$((db_bytes + 1073741824))
echo "PRODUCTION_DB_BYTES=$db_bytes"
echo "PRODUCTION_BACKUP_FREE_BYTES=$backup_free_bytes"
echo "PRODUCTION_BACKUP_REQUIRED_BYTES=$backup_required_bytes"
(( backup_free_bytes >= backup_required_bytes )) || {
  echo "insufficient production backup free space" >&2
  exit 27
}

db_backup="$backup_dir/hunter-predeploy-$stamp.db"
backup_name="$(basename "$db_backup")"
host_uid="$(id -u)"
host_gid="$(id -g)"
hunter_data_volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/hunter/data"}}{{.Name}}{{end}}{{end}}' "$H")"
[[ -n "$hunter_data_volume" ]] || {
  echo "production Hunter data volume unresolved" >&2
  exit 28
}
echo "PRODUCTION_HUNTER_DATA_VOLUME=$hunter_data_volume"

timeout 1200s docker run --rm -i \
  --network none \
  --user 0:0 \
  --mount "type=volume,src=$hunter_data_volume,dst=/app/hunter/data" \
  --mount "type=bind,src=$backup_dir,dst=/backup" \
  --entrypoint python \
  "$old_hunter_image_id" \
  - "$db_path" "/backup/$backup_name" "$host_uid" "$host_gid" <<'PY'
import os
import sqlite3
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
uid = int(sys.argv[3])
gid = int(sys.argv[4])

if not src.is_file():
    raise SystemExit(f"production source DB is missing: {src}")
if dst.exists():
    raise SystemExit(f"production backup destination already exists: {dst}")

source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
source.execute("PRAGMA query_only=ON")
source.execute("PRAGMA busy_timeout=30000")
dest = sqlite3.connect(dst)
try:
    source.backup(dest)
    result = dest.execute("PRAGMA quick_check").fetchone()[0]
finally:
    dest.close()
    source.close()

if result != "ok":
    raise SystemExit(f"production backup quick_check failed: {result}")

os.chown(dst, uid, gid)
os.chmod(dst, 0o600)
print(f"PRODUCTION_DB_BACKUP_CONTAINER_QUICK_CHECK=PASS path={dst}")
PY

[[ -f "$db_backup" ]] || { echo "production DB backup missing on host" >&2; exit 28; }
[[ "$(stat -c '%u:%g' "$db_backup")" == "$host_uid:$host_gid" ]] || {
  echo "production DB backup ownership mismatch" >&2
  exit 29
}

python3 - "$db_backup" <<'PY'
import sqlite3
import sys

p = sys.argv[1]
db = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
try:
    db.execute("PRAGMA query_only=ON")
    schema_version = int(db.execute("PRAGMA schema_version").fetchone()[0])
    table_count = int(
        db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    )
finally:
    db.close()
print(f"PRODUCTION_DB_BACKUP_HOST_OPEN=PASS schema_version={schema_version} tables={table_count}")
PY
echo "PRODUCTION_DB_BACKUP=$db_backup"

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

echo "=== STOP HUNTER FOR COMPLETE-LOOP MIGRATION ==="
migration_attempted=1
"${compose[@]}" stop hunter
hunter_stopped=1

echo "=== APPLY COMPLETE-LOOP MIGRATIONS 041-051 TO PRODUCTION DB ==="
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
echo "N8N_RECREATED=NO"
echo "OLLAMA_RECREATED=NO"
echo "RESULT=PRODUCTION_DEPLOYMENT_PASS"
trap - ERR
