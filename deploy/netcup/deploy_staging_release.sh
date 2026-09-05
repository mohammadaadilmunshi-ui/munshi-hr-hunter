#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${MUNSHI_STAGING_PROJECT:-munshi-netcup-staging}"
STAGING_ROOT="${MUNSHI_STAGING_ROOT:-/home/munshi/munshi-staging-v1}"
STAGING_REPO="$STAGING_ROOT/repo"
STAGING_ENV="$STAGING_ROOT/staging.env"
STAGING_OVERRIDE="$STAGING_ROOT/staging.override.yaml"
VERIFY="/opt/munshi/bin/verify-staging-runtime-contract"
VERIFY_PROD="/opt/munshi/bin/verify-production-runtime-contract"

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

compose=(
  docker compose
  --project-name "$PROJECT"
  --env-file "$STAGING_ENV"
  -f "$STAGING_REPO/compose.yaml"
  -f "$STAGING_REPO/compose.netcup-shadow.yaml"
  -f "$STAGING_OVERRIDE"
)

cd "$STAGING_REPO"

echo "=== PREDEPLOY PRODUCTION NON-REGRESSION CONTRACT ==="
"$VERIFY_PROD"

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

backup_dir="$STAGING_ROOT/backups"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
db_backup="$backup_dir/hunter-predeploy-$stamp.db"

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

rollback() {
  rc="${1:-$?}"
  trap - ERR
  echo "=== AUTOMATIC STAGING DEPLOYMENT ROLLBACK rc=$rc ===" >&2

  if [[ -n "$old_branch" ]]; then
    git checkout -q -B "$old_branch" "$old_head" || true
  else
    git checkout -q --detach "$old_head" || true
  fi

  docker tag "$old_hunter_image_id" "$hunter_image_name" || true
  echo "ROLLBACK_IMAGE_TAG_RESTORED=YES" >&2

  if (( recreated )); then
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

echo "=== RECREATE STAGING HUNTER ONLY ==="
"${compose[@]}" up -d --no-deps --force-recreate hunter
recreated=1

healthy=0
for _ in $(seq 1 48); do
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$H" 2>/dev/null || true)"
  if [[ "$health" == "healthy" ]]; then
    healthy=1
    break
  fi
  sleep 5
done
[[ "$healthy" == "1" ]] || {
  echo "staging Hunter did not return healthy" >&2
  rollback 30
}

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
[[ "$prod_snapshot_before" == "$prod_snapshot_after" ]] || {
  echo "production container snapshot changed during staging deployment" >&2
  rollback 31
}
echo "PRODUCTION_CONTAINERS_UNCHANGED=PASS"

echo "=== POSTDEPLOY STAGING CONTRACT ==="
"$VERIFY"

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
