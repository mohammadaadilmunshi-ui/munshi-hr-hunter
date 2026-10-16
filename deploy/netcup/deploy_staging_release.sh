#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${MUNSHI_STAGING_PROJECT:-munshi-netcup-staging}"
STAGING_ROOT="${MUNSHI_STAGING_ROOT:-/home/munshi/munshi-staging-v1}"
STAGING_REPO="$STAGING_ROOT/repo"
STAGING_ENV="$STAGING_ROOT/staging.env"
STAGING_OVERRIDE="$STAGING_ROOT/staging.override.yaml"
STAGING_OPERATIONAL_OVERRIDE="$STAGING_ROOT/operational.override.yaml"
STAGING_SAFETY_OVERRIDE="$STAGING_ROOT/staging.safety.override.yaml"
VERIFY="/opt/munshi/bin/verify-staging-runtime-contract"
VERIFY_PROD="/opt/munshi/bin/verify-production-runtime-contract"
STAGING_MAINTENANCE_HELPER="${MUNSHI_STAGING_MAINTENANCE_HELPER:-/opt/munshi/bin/ensure-staging-maintenance-mode}"

H="$PROJECT-hunter-1"
N="$PROJECT-n8n-1"
O="$PROJECT-ollama-1"
EDGE="munshi-staging-edge-caddy"

commit=""
branch=""
bundle_file=""
deploy_ref=""

cleanup() {
  if [[ -n "${bundle_file:-}" ]]; then
    rm -f "$bundle_file" 2>/dev/null || true
  fi
  if [[ -n "${deploy_ref:-}" && -d "$STAGING_REPO/.git" ]]; then
    git -C "$STAGING_REPO" update-ref -d "$deploy_ref" >/dev/null 2>&1 || true
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
[[ -x "$VERIFY" ]] || { echo "stable staging verifier missing: $VERIFY" >&2; exit 5; }
[[ -x "$VERIFY_PROD" ]] || { echo "stable production verifier missing: $VERIFY_PROD" >&2; exit 6; }

bundle_file="$(mktemp /tmp/munshi-github-staging-deploy.XXXXXX.bundle)"
timeout 120s cat > "$bundle_file" || { echo "staging deployment bundle transfer timed out" >&2; exit 7; }
[[ -s "$bundle_file" ]] || { echo "staging deployment bundle is empty" >&2; exit 8; }

safety_tmp="$(mktemp "$STAGING_ROOT/.staging-safety.XXXXXX.yaml")"
cat > "$safety_tmp" <<'YAML'
services:
  hunter:
    environment:
      HUNTER_ENABLE_TELEGRAM: "false"
      HUNTER_ENABLE_DISCOVERY_SCHEDULER: "false"
      HUNTER_ENABLE_COORDINATOR: "false"
      TELEGRAM_ENABLED: "false"
      DISCOVERY_ENABLED: "false"
      SCHEDULER_ENABLED: "false"
      COORDINATOR_ENABLED: "false"
      PRODUCTION_CALLBACKS_ENABLED: "false"
      PRODUCTION_STATE_IMPORTED: "false"
      CLOUD_SHADOW_MODE: "true"
YAML
chmod 600 "$safety_tmp"
mv -f "$safety_tmp" "$STAGING_SAFETY_OVERRIDE"
echo "STAGING_SAFETY_OVERRIDE=ENFORCED"

compose=(
  docker compose
  --project-name "$PROJECT"
  --env-file "$STAGING_ENV"
  -f "$STAGING_REPO/compose.yaml"
  -f "$STAGING_REPO/compose.netcup-shadow.yaml"
  -f "$STAGING_OVERRIDE"
)
if [[ -f "$STAGING_OPERATIONAL_OVERRIDE" ]]; then
  compose+=(-f "$STAGING_OPERATIONAL_OVERRIDE")
  echo "STAGING_OPERATIONAL_OVERRIDE=ENABLED"
else
  echo "STAGING_OPERATIONAL_OVERRIDE=ABSENT"
fi
compose+=(-f "$STAGING_SAFETY_OVERRIDE")
echo "STAGING_SAFETY_OVERRIDE_LAST=PASS"

cd "$STAGING_REPO"

echo "=== PREDEPLOY PRODUCTION NON-REGRESSION CONTRACT ==="
"$VERIFY_PROD"

if [[ -x "$STAGING_MAINTENANCE_HELPER" ]]; then
  echo "=== ENSURE STAGING MAINTENANCE MODE ==="
  "$STAGING_MAINTENANCE_HELPER"
else
  echo "STAGING_MAINTENANCE_HELPER=ABSENT"
fi

echo "=== PREDEPLOY STAGING CONTRACT ==="
"$VERIFY"

[[ -z "$(git status --porcelain)" ]] || { echo "dirty staging repository; refusing deployment" >&2; exit 10; }

old_head="$(git rev-parse HEAD)"
old_branch="$(git branch --show-current)"
old_hunter_image_id="$(docker inspect -f '{{.Image}}' "$H")"
hunter_image_name="$(docker inspect -f '{{.Config.Image}}' "$H")"

n8n_id_before="$(docker inspect -f '{{.Id}}' "$N")"
n8n_started_before="$(docker inspect -f '{{.State.StartedAt}}' "$N")"
ollama_id_before="$(docker inspect -f '{{.Id}}' "$O")"
ollama_started_before="$(docker inspect -f '{{.State.StartedAt}}' "$O")"
edge_id_before="$(docker inspect -f '{{.Id}}' "$EDGE")"
edge_started_before="$(docker inspect -f '{{.State.StartedAt}}' "$EDGE")"

prod_snapshot_before="$(
  for c in \
    munshi-netcup-shadow-hunter-1 \
    munshi-netcup-shadow-n8n-1 \
    munshi-netcup-shadow-ollama-1
  do
    printf '%s|' "$c"
    docker inspect -f '{{.Id}}|{{.State.StartedAt}}|{{.RestartCount}}' "$c"
  done
)"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
rollback_tag="${hunter_image_name%:*}:staging-rollback-$stamp"
recreated=0
migration_attempted=0
hunter_stopped=0
db_restore_completed=0

backup_dir="$STAGING_ROOT/backups"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
db_backup="$backup_dir/hunter-predeploy-$stamp.db"
backup_name="$(basename "$db_backup")"

db_path="$(docker exec "$H" python -c 'from app.database import DB_PATH; print(DB_PATH)')"
[[ -n "$db_path" ]] || { echo "staging DB path unresolved" >&2; exit 11; }

hunter_data_volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/hunter/data"}}{{.Name}}{{end}}{{end}}' "$H")"
[[ -n "$hunter_data_volume" ]] || { echo "staging Hunter data volume unresolved" >&2; exit 12; }

db_meta="$(docker exec "$H" stat -c '%u:%g:%a' "$db_path")"
IFS=: read -r db_uid db_gid db_mode <<< "$db_meta"
[[ "$db_uid" =~ ^[0-9]+$ && "$db_gid" =~ ^[0-9]+$ && "$db_mode" =~ ^[0-7]+$ ]] || {
  echo "staging DB ownership/mode unresolved" >&2
  exit 13
}
echo "STAGING_DB_PATH=$db_path"
echo "STAGING_HUNTER_DATA_VOLUME=$hunter_data_volume"
echo "STAGING_DB_UID_GID_MODE=$db_uid:$db_gid:$db_mode"

docker exec -i "$H" python - "$stamp" <<'PY'
import sqlite3
import sys
from pathlib import Path

from app.database import DB_PATH

stamp = sys.argv[1]
src = Path(DB_PATH)
dst = Path("/tmp") / f"hunter-predeploy-{stamp}.db"

if not src.is_file():
    raise SystemExit(f"staging source DB is missing: {src}")

source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
dest = sqlite3.connect(dst)
try:
    source.backup(dest)
    result = dest.execute("PRAGMA quick_check").fetchone()[0]
finally:
    dest.close()
    source.close()

if result != "ok":
    raise SystemExit(f"backup quick_check failed: {result}")
print(dst)
PY

docker cp "$H:/tmp/hunter-predeploy-$stamp.db" "$db_backup"
docker exec "$H" rm -f "/tmp/hunter-predeploy-$stamp.db"
chmod 600 "$db_backup"

python3 - "$db_backup" <<'PY'
import sqlite3
import sys
p = sys.argv[1]
db = sqlite3.connect(p)
try:
    result = db.execute("PRAGMA quick_check").fetchone()[0]
finally:
    db.close()
if result != "ok":
    raise SystemExit(f"host staging DB backup quick_check failed: {result}")
print("STAGING_DB_BACKUP_QUICK_CHECK=PASS")
PY
echo "STAGING_DB_BACKUP=$db_backup"

restore_staging_db() {
  echo "=== RESTORE STAGING DB BACKUP ===" >&2
  "${compose[@]}" stop hunter >/dev/null 2>&1 || docker stop "$H" >/dev/null 2>&1 || true
  hunter_stopped=1

  timeout 600s docker run --rm -i \
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
    raise SystemExit(f"staging rollback backup missing: {backup}")

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
    raise SystemExit(f"restored staging DB quick_check failed: {quick}")
if fk:
    raise SystemExit(f"restored staging DB FK violations: {len(fk)}")
print("ROLLBACK_DB_RESTORE=PASS")
PYRESTORE
  db_restore_completed=1
}

rollback() {
  trapped_rc=$?
  rc="${1:-$trapped_rc}"
  trap - ERR
  echo "=== AUTOMATIC STAGING DEPLOYMENT ROLLBACK rc=$rc ===" >&2

  if (( migration_attempted )); then
    if ! restore_staging_db; then
      echo "CRITICAL: staging DB rollback restore failed; Hunter remains stopped." >&2
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
  "$VERIFY_PROD" || true
  echo "RESULT=STAGING_DEPLOYMENT_ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback ERR

echo "=== VERIFY GITHUB-PUBLISHED TARGET BUNDLE ==="
git bundle verify "$bundle_file"
bundle_ref="refs/remotes/origin/$branch"
deploy_ref="refs/remotes/github-staging-deploy/$branch"
git fetch --no-tags "$bundle_file" "+$bundle_ref:$deploy_ref"
git cat-file -e "$commit^{commit}"
git merge-base --is-ancestor "$commit" "$deploy_ref" || {
  echo "requested SHA is not contained in bundled source branch" >&2
  rollback 20
}
echo "GITHUB_STAGING_BUNDLE_IMPORT=PASS"

echo "=== ROLLBACK IMAGE ==="
docker tag "$old_hunter_image_id" "$rollback_tag"
echo "rollback_image=$rollback_tag"
echo "old_head=$old_head"

echo "=== CHECKOUT EXACT SHA ON STAGING SOURCE BRANCH ==="
git checkout -q -B "$branch" "$commit"
[[ "$(git rev-parse HEAD)" == "$commit" ]]
[[ "$(git branch --show-current)" == "$branch" ]]

echo "=== STATIC STAGING DEPLOYMENT VALIDATION ==="
bash -n deploy/netcup/deploy_staging_release.sh
bash -n deploy/netcup/verify_staging_runtime_contract.sh
python3 -m compileall -q app scripts integrations

echo "=== RENDER + VERIFY ISOLATED STAGING COMPOSE ==="
"${compose[@]}" config -q
rendered="$(mktemp /tmp/munshi-staging-rendered.XXXXXX.yaml)"
"${compose[@]}" config > "$rendered"

for required in \
  'HUNTER_ENABLE_TELEGRAM: "false"' \
  'HUNTER_ENABLE_DISCOVERY_SCHEDULER: "false"' \
  'HUNTER_ENABLE_COORDINATOR: "false"' \
  'PRODUCTION_CALLBACKS_ENABLED: "false"' \
  'PRODUCTION_STATE_IMPORTED: "false"' \
  'CLOUD_SHADOW_MODE: "true"'
do
  grep -Fq "$required" "$rendered" || {
    echo "missing staging safety token: $required" >&2
    rm -f "$rendered"
    rollback 21
  }
done
if grep -Eq '/opt/munshi/(repo|runtime|secrets|backups)' "$rendered"; then
  echo "rendered staging config unexpectedly references production filesystem" >&2
  rm -f "$rendered"
  rollback 22
fi
if grep -Fq 'munshi-netcup-shadow_' "$rendered"; then
  echo "rendered staging config unexpectedly references production project resources" >&2
  rm -f "$rendered"
  rollback 23
fi
rm -f "$rendered"
echo "STAGING_CONFIG_SAFETY=PASS"

echo "=== BUILD STAGING HUNTER ONLY ==="
"${compose[@]}" build hunter
new_hunter_image_id="$(docker image inspect -f '{{.Id}}' "$hunter_image_name")"
[[ -n "$new_hunter_image_id" ]] || rollback 29
echo "new_hunter_image_id=$new_hunter_image_id"

echo "=== STOP STAGING HUNTER FOR COMPLETE-LOOP MIGRATION ==="
migration_attempted=1
"${compose[@]}" stop hunter
hunter_stopped=1

echo "=== APPLY GUARDED MIGRATIONS 041-059 TO STAGING DB ==="
timeout 600s docker run --rm -i   --network none   --read-only   --tmpfs /tmp:rw,nosuid,nodev,size=256m   --mount "type=volume,src=$hunter_data_volume,dst=/app/hunter/data"   --entrypoint python   -e CLOUD_SHADOW_MODE=true   -e PRODUCTION_STATE_IMPORTED=false   -e PRODUCTION_CALLBACKS_ENABLED=false   "$new_hunter_image_id"   /app/hunter/scripts/complete_loop_migrate.py   --db-path "$db_path"   --environment staging   --full-quick-check
echo "STAGING_COMPLETE_LOOP_MIGRATION=PASS"

echo "=== RECREATE STAGING HUNTER ONLY ==="
"${compose[@]}" up -d --no-deps --force-recreate hunter
recreated=1
hunter_stopped=0

healthy=0
for _ in $(seq 1 48); do
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$H" 2>/dev/null || true)"
  if [[ "$health" == "healthy" ]]; then
    healthy=1
    break
  fi
  sleep 5
done
if [[ "$healthy" != "1" ]]; then
  echo "staging Hunter did not return healthy" >&2
  rollback 30
fi

echo "=== VERIFY STAGING NON-HUNTER CONTAINERS UNCHANGED ==="
[[ "$(docker inspect -f '{{.Id}}' "$N")" == "$n8n_id_before" ]]
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$N")" == "$n8n_started_before" ]]
[[ "$(docker inspect -f '{{.Id}}' "$O")" == "$ollama_id_before" ]]
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$O")" == "$ollama_started_before" ]]
[[ "$(docker inspect -f '{{.Id}}' "$EDGE")" == "$edge_id_before" ]]
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$EDGE")" == "$edge_started_before" ]]
echo "STAGING_N8N_RECREATED=NO"
echo "STAGING_OLLAMA_RECREATED=NO"
echo "STAGING_EDGE_RECREATED=NO"

echo "=== VERIFY PRODUCTION CONTAINERS UNCHANGED ==="
prod_snapshot_after="$(
  for c in \
    munshi-netcup-shadow-hunter-1 \
    munshi-netcup-shadow-n8n-1 \
    munshi-netcup-shadow-ollama-1
  do
    printf '%s|' "$c"
    docker inspect -f '{{.Id}}|{{.State.StartedAt}}|{{.RestartCount}}' "$c"
  done
)"
if [[ "$prod_snapshot_before" != "$prod_snapshot_after" ]]; then
  echo "production container snapshot changed during staging deployment" >&2
  rollback 31
fi
echo "PRODUCTION_CONTAINERS_UNCHANGED=PASS"

echo "=== POSTDEPLOY STAGING CONTRACT ==="
"$VERIFY"

echo "=== VERIFY STAGING COMPLETE-LOOP SCHEMA ==="
docker exec "$H" python /app/hunter/scripts/complete_loop_migrate.py   --db-path "$db_path"   --environment staging   --verify-only   --full-quick-check
echo "STAGING_COMPLETE_LOOP_SCHEMA_READY=YES"

echo "=== POSTDEPLOY PRODUCTION NON-REGRESSION CONTRACT ==="
"$VERIFY_PROD"

echo "=== STAGING DEPLOYMENT RESULT ==="
echo "DEPLOYED_SHA=$commit"
echo "DEPLOYED_BRANCH=$branch"
echo "PREVIOUS_STAGING_SHA=$old_head"
echo "ROLLBACK_IMAGE=$rollback_tag"
echo "STAGING_DB_BACKUP=$db_backup"
echo "STAGING_N8N_RECREATED=NO"
echo "STAGING_OLLAMA_RECREATED=NO"
echo "STAGING_EDGE_RECREATED=NO"
echo "PRODUCTION_CONTAINERS_CHANGED=NO"
echo "PRODUCTION_DEPLOYMENT_PERFORMED=NO"
echo "RESULT=STAGING_DEPLOYMENT_PASS"
trap - ERR
