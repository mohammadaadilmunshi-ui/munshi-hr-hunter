#!/usr/bin/env bash
# Exact-SHA, migration-free Hunter image swap. This does NOT back up or migrate DB.
# This script MUST be invoked by the approved GitHub production workflow only.
set -Eeuo pipefail
umask 077

ROOT=/opt/munshi
REPO="$ROOT/repo"
LIVE_BASE=3b4e400c3ef9c8f32bcb8b4c037b036ef2a37e0b
RELEASE_BRANCH=feat/tsenta-connector-completion-20261015
H=munshi-netcup-shadow-hunter-1
A=munshi-apply-production-apply-1
P=munshi-apply-production-prepare-worker-1
S=munshi-apply-production-submit-worker-1
N=munshi-netcup-shadow-n8n-1
O=munshi-netcup-shadow-ollama-1
target=""
branch=""
bundle=""
expected_base=""
changed_repo=0
new_container=0
succeeded=0

block() { printf 'CODE_ONLY_BLOCKED=%s\n' "$*" >&2; exit 3; }
while (($#)); do
  case "$1" in
    --commit) target="${2:-}";shift 2 ;;
    --branch) branch="${2:-}";shift 2 ;;
    --expected-base) expected_base="${2:-}";shift 2 ;;
    --bundle) bundle="${2:-}";shift 2 ;;
    *) block 'Unsupported arguments' ;;
  esac
done
[[ "$target" =~ ^[0-9a-f]{40}$ ]] || block 'Exact target SHA required'
[[ "$expected_base" == "$LIVE_BASE" ]] || block 'Expected production baseline changed'
[[ "$branch" == "$RELEASE_BRANCH" ]] || block 'Only the approved production source branch is allowed'
[[ "$(id -un)" == munshi ]] || block 'Must run as authorized munshi owner'
[[ "$bundle" == "$ROOT/runtime/code-only/munshi-codeonly-$target.bundle" && -s "$bundle" ]] || block 'Exact target bundle missing'
[[ -r "$bundle" ]] || block 'Cannot read release bundle'
[[ -f "$REPO/compose.yaml" ]] || block 'Missing production compose'
[[ -f "$ROOT/runtime/stage12-production.override.yaml" ]] || block 'Missing production runtime override'
[[ -z "$(git -C "$REPO" status --porcelain)" ]] || block 'Production repository is dirty'

old_head="$(git -C "$REPO" rev-parse HEAD)"
[[ "$old_head" == "$LIVE_BASE" ]] || block 'Live Hunter SHA differs from expected old release'
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$H")" == healthy ]] || block 'Original Hunter unhealthy'
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$A")" == healthy ]] || block 'Apply unhealthy'
for c in "$P" "$S"; do
  [[ "$(docker inspect -f '{{.State.Running}}' "$c")" == true ]] || block 'Apply worker missing'
done
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$N")" == healthy ]] || block 'n8n unhealthy'
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$O")" == healthy ]] || block 'Ollama unhealthy'
old_branch="$(git -C "$REPO" branch --show-current)"
old_image="$(docker inspect -f '{{.Image}}' "$H")"
image_tag="$(docker inspect -f '{{.Config.Image}}' "$H")"
[[ -n "$old_image" && -n "$image_tag" ]] || block 'Cannot identify old Hunter image'
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
rollback_image="${image_tag%:*}:codeonly-rollback-$stamp"
old_n_id="$(docker inspect -f '{{.Id}}' "$N")"
old_o_id="$(docker inspect -f '{{.Id}}' "$O")"
old_a_id="$(docker inspect -f '{{.Id}}' "$A")"
old_p_id="$(docker inspect -f '{{.Id}}' "$P")"
old_s_id="$(docker inspect -f '{{.Id}}' "$S")"
old_n_started="$(docker inspect -f '{{.State.StartedAt}}' "$N")"
old_a_started="$(docker inspect -f '{{.State.StartedAt}}' "$A")"
old_s_started="$(docker inspect -f '{{.State.StartedAt}}' "$S")"

compose=(
  docker compose --project-name munshi-netcup-shadow
  --env-file "$ROOT/secrets/netcup-shadow.env"
  -f "$REPO/compose.yaml"
  -f "$REPO/compose.netcup-shadow.yaml"
  -f "$ROOT/runtime/stage10-imported.override.yaml"
  -f "$ROOT/runtime/stage12-production.override.yaml"
  -f "$ROOT/runtime/stage12-n8n-runtime-repair.override.yaml"
)

db_schema() {
  docker exec -i "$H" python - <<'PY'
from app.database import DB_PATH
import sqlite3
with sqlite3.connect('file:'+str(DB_PATH)+'?mode=ro',uri=True,timeout=20) as con:
    con.execute('PRAGMA query_only=ON')
    print(con.execute('PRAGMA schema_version').fetchone()[0])
PY
}
old_schema="$(db_schema)"
[[ "$old_schema" =~ ^[0-9]+$ ]] || block 'Cannot read Hunter schema state'
old_mount="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/hunter/data"}}{{.Name}}{{end}}{{end}}' "$H")"
[[ -n "$old_mount" ]] || block 'Hunter data volume unresolved'
active_submits="$(docker exec -i "$A" python - <<'PY'
from munshi_apply_native.settings import Settings
import sqlite3
p=Settings.from_environment().database_path
with sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True) as c:
    c.execute('PRAGMA query_only=ON')
    print(c.execute("SELECT COUNT(*) FROM complete_application_sessions WHERE state IN ('SUBMITTING','APPLYING')").fetchone()[0])
PY
)"
[[ "$active_submits" == 0 ]] || block 'Live application submission is active'
[[ -z "$(ps -eo args | grep '[d]eploy-production-release --commit' || true)" ]] || block 'An existing full release is active'
# Prevent the restart from colliding with n8n dispatches that could write Hunter
# while its service is being replaced. An idle queue is an exact release gate.
n8n_active="$(docker exec -i "$H" python - <<'PYQUEUE'
from app.database import get_connection
c=get_connection()
try:
 c.execute('PRAGMA query_only=ON')
 rows=c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='n8n_dispatch_queue'").fetchall()
 if not rows:print('unavailable')
 else:
  columns={r[1] for r in c.execute('PRAGMA table_info(n8n_dispatch_queue)')}
  field=next((value for value in ('queue_status','status','state') if value in columns),None)
  if field is None:print('unavailable')
  else:
   values=('queued','pending','dispatching','reserved','accepted','running','processing','in_progress')
   q=','.join('?' for _ in values)
   print(c.execute(f'SELECT COUNT(*) FROM n8n_dispatch_queue WHERE "{field}" IN ({q})',values).fetchone()[0])
finally:c.close()
PYQUEUE
)"
[[ "$n8n_active" == 0 ]] || block 'n8n queue busy or unavailable'
echo 'LIVE_DISPATCH_QUEUE=EMPTY'

rollback() {
  exit_code="$?"
  if (( exit_code != 0 && succeeded == 0 )); then
    printf 'CODE_ONLY_FAILED=1; ORIGINAL_IMAGE_ROLLBACK=STARTED\n' >&2
    if (( changed_repo )); then
      cd "$REPO"
      if [[ -n "$old_branch" ]]; then
        git checkout -q -B "$old_branch" "$old_head" || true
      else
        git checkout -q --detach "$old_head" || true
      fi
      docker tag "$old_image" "$image_tag" || true
      if (( new_container )); then
        "${compose[@]}" up -d --no-deps --force-recreate hunter || true
      fi
    fi
    printf 'CODE_ONLY_FAILURE=inspect_live_SHA_health_and_DB_before_any_retry\n' >&2
  fi
  exit "$exit_code"
}
trap rollback EXIT

cd "$REPO"
git bundle verify "$bundle" >/dev/null
deploy_ref="refs/remotes/github-codeonly/$branch"
git fetch --no-tags "$bundle" "+refs/remotes/origin/$branch:$deploy_ref" >/dev/null
git cat-file -e "$target^{commit}"
git merge-base --is-ancestor "$target" "$deploy_ref" || block 'Target absent from reviewed bundle'
git merge-base --is-ancestor "$old_head" "$target" || block 'Target is not a descendant of deployed baseline'

# Narrow one-time allowance for the already-reviewed V4 and native-route fixes.
# Anything else, including database modules, SQL scripts or Docker manifests,
# needs the original DB-backed guarded full release.
allowed_files=(
  app/native_cover_letter_writer_v1.py
  app/product_experience_vnext.py
  app/application_provider_ui_v1.py
  app/product_application_flow_v2.py
  app/autoapply_settings_ui.py
  tests/test_native_cover_letter_writer_v1.py
  tests/test_product_experience_vnext.py
  tests/test_application_provider_ui_v1.py
  tests/test_autoapply_settings_ui.py
  tests/test_product_application_flow_v2.py
  tests/test_submit_authority_delivery_v1.py
  tests/test_native_approval_provider_lock_v1.py
  deploy/netcup/deploy_code_only_release.sh
  .github/workflows/netcup-code-only-deploy.yml
  tests/test_code_only_release_contract_v1.py
)
allowed_file() {
  for f in "${allowed_files[@]}"; do
    [[ "$f" == "$1" ]] && return 0
  done
  return 1
}
changed=0
while IFS=$'\t' read -r status path; do
  [[ -z "$status" ]] && continue
  [[ "$status" == M || "$status" == A ]] || block 'Code-only release forbids file deletion or renaming'
  allowed_file "$path" || block "Unreviewed source change: $path"
  [[ "$(git ls-tree "$target" -- "$path" | awk '{print $1}')" == 100644 ||
     "$(git ls-tree "$target" -- "$path" | awk '{print $1}')" == 100755 ]] ||
    block "Non-regular release file: $path"
  changed=$((changed+1))
done < <(git diff --name-status "$old_head" "$target")
(( changed >= 1 )) || block 'No source changes'
echo "CODE_ONLY_ALLOWED_SOURCE_FILES=$changed"
echo 'CODE_ONLY_SCHEMA_MIGRATIONS=NONE'
echo 'CODE_ONLY_NEW_DATABASE_BACKUP=NONE (image rollback only)'

docker tag "$old_image" "$rollback_image"
echo "ORIGINAL_IMAGE_ROLLBACK_TAG=$rollback_image"
git checkout -q -B "$branch" "$target"
changed_repo=1
python3 -m compileall -q app
"${compose[@]}" config -q
"${compose[@]}" build hunter
new_image="$(docker image inspect -f '{{.Id}}' "$image_tag")"
[[ "$new_image" != "$old_image" ]] || block 'New image identical to old running image'
timeout 900 docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  --tmpfs /app/hunter/data:rw,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=0700 \
  --mount "type=bind,src=$REPO/tests,dst=/app/hunter/tests,readonly" \
  --entrypoint python -e CLOUD_SHADOW_MODE=false \
  -e HUNTER_API_SECRET=isolated-codeonly-ci-tests-not-production-12345 \
  "$new_image" -m pytest -q \
    /app/hunter/tests/test_native_cover_letter_writer_v1.py \
    /app/hunter/tests/test_native_approval_provider_lock_v1.py \
    /app/hunter/tests/test_product_application_flow_v2.py \
    /app/hunter/tests/test_application_provider_ui_v1.py
echo 'EXACT_IMAGE_TESTS=PASS'

new_container=1
"${compose[@]}" up -d --no-deps --force-recreate hunter
healthy=0
for _ in $(seq 1 50); do
  if [[ "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$H" 2>/dev/null || true)" == healthy ]]; then
    healthy=1
    break
  fi
  sleep 4
done
(( healthy )) || block 'Replacement Hunter failed health check'
[[ "$(git rev-parse HEAD)" == "$target" ]] || block 'New source SHA differs'
[[ "$(docker inspect -f '{{.Image}}' "$H")" == "$new_image" ]] || block 'Running Hunter image differs'
[[ "$(db_schema)" == "$old_schema" ]] || block 'Hunter SQLite schema_version unexpectedly changed; manual data inspection required'
[[ "$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/hunter/data"}}{{.Name}}{{end}}{{end}}' "$H")" == "$old_mount" ]] || block 'Hunter data volume changed'
for c in "$N" "$O" "$A" "$P" "$S"; do
  case "$c" in
    "$N") initial="$old_n_id";;
    "$O") initial="$old_o_id";;
    "$A") initial="$old_a_id";;
    "$P") initial="$old_p_id";;
    "$S") initial="$old_s_id";;
  esac
  [[ "$(docker inspect -f '{{.Id}}' "$c")" == "$initial" ]] || block 'Non-Hunter service was replaced'
done
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$N")" == "$old_n_started" ]] || block 'n8n restarted'
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$A")" == "$old_a_started" ]] || block 'Apply restarted'
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$S")" == "$old_s_started" ]] || block 'Submit worker restarted'
[[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/health)" == 200 ]] || block 'Hunter API health check failed'
[[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8501/_stcore/health)" == 200 ]] || block 'Dashboard health check failed'
succeeded=1
echo "CODE_ONLY_DEPLOY_VERIFIED=$target"
echo "ROLLBACK_IMAGE=$rollback_image"
echo 'DATABASE_SCHEMA_UNCHANGED=PASS; NO_NEW_MULTI_GB_BACKUP=YES'
