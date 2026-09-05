#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${MUNSHI_STAGING_PROJECT:-munshi-netcup-staging}"
STAGING_ROOT="${MUNSHI_STAGING_ROOT:-/home/munshi/munshi-staging-v1}"
STAGING_REPO="$STAGING_ROOT/repo"
STAGING_ENV="$STAGING_ROOT/staging.env"
STAGING_OVERRIDE="$STAGING_ROOT/staging.override.yaml"
VERIFY_PROD="/opt/munshi/bin/verify-production-runtime-contract"
VERIFY_STAGING="/opt/munshi/bin/verify-staging-runtime-contract"
ROLLBACK_TAG="munshi-netcup-staging-hunter:staging-rollback-20260905T021130Z"

H="$PROJECT-hunter-1"
N="$PROJECT-n8n-1"
O="$PROJECT-ollama-1"
EDGE="munshi-staging-edge-caddy"

from_sha=""
to_sha=""
to_branch=""
while (($#)); do
  case "$1" in
    --from-sha) from_sha="${2:-}"; shift 2 ;;
    --to-sha) to_sha="${2:-}"; shift 2 ;;
    --to-branch) to_branch="${2:-}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$from_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid --from-sha" >&2; exit 3; }
[[ "$to_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid --to-sha" >&2; exit 4; }
[[ "$to_branch" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "invalid --to-branch" >&2; exit 5; }
git check-ref-format --branch "$to_branch" >/dev/null

# This helper exists only to recover the one known temporary bootstrap release.
[[ "$from_sha" == "2fd6814c6685bb21adb1d0d7fd60249680da1c1c" ]] || { echo "unexpected recovery source SHA" >&2; exit 6; }
[[ "$to_sha" == "1785c022a6a9d3205fcfc36b2aed120494044158" ]] || { echo "unexpected recovery target SHA" >&2; exit 7; }
[[ "$to_branch" == "fix/dashboard-device-auth-staging-v1" ]] || { echo "unexpected recovery target branch" >&2; exit 8; }

[[ -d "$STAGING_REPO/.git" ]] || { echo "staging repo missing" >&2; exit 9; }
[[ -f "$STAGING_ENV" && -f "$STAGING_OVERRIDE" ]] || { echo "staging runtime files missing" >&2; exit 10; }
[[ -x "$VERIFY_PROD" && -x "$VERIFY_STAGING" ]] || { echo "runtime verifier missing" >&2; exit 11; }
[[ -z "$(git -C "$STAGING_REPO" status --porcelain)" ]] || { echo "staging repo dirty" >&2; exit 12; }
[[ "$(git -C "$STAGING_REPO" rev-parse HEAD)" == "$from_sha" ]] || { echo "staging source SHA changed; refusing recovery" >&2; exit 13; }
git -C "$STAGING_REPO" cat-file -e "$to_sha^{commit}"

echo "=== RECOVERY PRECHECK ==="
"$VERIFY_PROD" >/dev/null
echo "PRODUCTION_RUNTIME_CONTRACT=PASS"

docker image inspect "$ROLLBACK_TAG" >/dev/null 2>&1 || { echo "known staging rollback image missing" >&2; exit 14; }
for c in "$H" "$N" "$O" "$EDGE"; do
  docker inspect "$c" >/dev/null 2>&1 || { echo "required runtime object missing: $c" >&2; exit 15; }
done

n8n_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$N")"
ollama_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$O")"
edge_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}|{{.RestartCount}}' "$EDGE")"
hunter_image_name="$(docker inspect -f '{{.Config.Image}}' "$H")"
hunter_volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/hunter/data"}}{{.Name}}{{end}}{{end}}' "$H")"
[[ -n "$hunter_image_name" && -n "$hunter_volume" ]] || { echo "staging Hunter image/volume unresolved" >&2; exit 16; }

echo "STAGING_N8N_BASELINE_CAPTURED=PASS"
echo "STAGING_OLLAMA_BASELINE_CAPTURED=PASS"
echo "STAGING_EDGE_BASELINE_CAPTURED=PASS"

backup_dir="$STAGING_ROOT/backups"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$backup_dir/hunter-pre-recovery-$stamp.db"
backup_name="$(basename "$backup")"
host_uid="$(id -u)"
host_gid="$(id -g)"

echo "=== WAL-SAFE STAGING DB RECOVERY BACKUP ==="
timeout 600s docker run --rm -i \
  --network none \
  --user 0:0 \
  --mount "type=volume,src=$hunter_volume,dst=/app/hunter/data" \
  --mount "type=bind,src=$backup_dir,dst=/backup" \
  --entrypoint python \
  "$ROLLBACK_TAG" \
  - "/app/hunter/data/hunter.db" "/backup/$backup_name" "$host_uid" "$host_gid" <<'PY'
import os, sqlite3, sys
src, dst, uid, gid = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
source.execute("PRAGMA query_only=ON")
source.execute("PRAGMA busy_timeout=30000")
dest = sqlite3.connect(dst)
try:
    source.backup(dest)
    assert dest.execute("PRAGMA quick_check").fetchone()[0] == "ok"
finally:
    dest.close(); source.close()
os.chown(dst, uid, gid)
os.chmod(dst, 0o600)
print("STAGING_RECOVERY_DB_BACKUP_QUICK_CHECK=PASS")
PY
[[ -f "$backup" ]] || { echo "staging recovery DB backup missing" >&2; exit 17; }
echo "STAGING_RECOVERY_DB_BACKUP=$backup"

compose=(
  docker compose
  --project-name "$PROJECT"
  --env-file "$STAGING_ENV"
  -f "$STAGING_REPO/compose.yaml"
  -f "$STAGING_REPO/compose.netcup-shadow.yaml"
  -f "$STAGING_OVERRIDE"
)

recreated=0
rollback() {
  rc=$?
  trap - ERR
  echo "=== STAGING RECOVERY FALLBACK rc=$rc ===" >&2
  git -C "$STAGING_REPO" checkout -q -B "$to_branch" "$to_sha" || true
  docker tag "$ROLLBACK_TAG" "$hunter_image_name" || true
  "${compose[@]}" up -d --no-deps --force-recreate hunter || true
  for _ in $(seq 1 48); do
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$H" 2>/dev/null || true)"
    [[ "$health" == "healthy" ]] && break
    sleep 5
  done
  "$VERIFY_PROD" || true
  "$VERIFY_STAGING" || true
  echo "RESULT=STAGING_AUTH_BOOTSTRAP_RECOVERY_FALLBACK" >&2
  exit "$rc"
}
trap rollback ERR

cd "$STAGING_REPO"
echo "=== RESTORE EXACT STAGING SOURCE ==="
git checkout -q -B "$to_branch" "$to_sha"
[[ "$(git rev-parse HEAD)" == "$to_sha" ]]
[[ "$(git branch --show-current)" == "$to_branch" ]]
[[ -z "$(git status --porcelain)" ]]
echo "STAGING_SOURCE_RESTORED=PASS"

# Restore the exact pre-bootstrap image captured by the staging deployment wrapper.
docker tag "$ROLLBACK_TAG" "$hunter_image_name"
echo "STAGING_ROLLBACK_IMAGE_RESTORED=PASS"

"${compose[@]}" config -q
"${compose[@]}" up -d --no-deps --force-recreate hunter
recreated=1

healthy=0
for _ in $(seq 1 48); do
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$H" 2>/dev/null || true)"
  if [[ "$health" == "healthy" ]]; then healthy=1; break; fi
  sleep 5
done
[[ "$healthy" == "1" ]] || { echo "staging Hunter recovery did not become healthy" >&2; exit 20; }
echo "STAGING_HUNTER_RECOVERED_HEALTHY=PASS"

[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$N")" == "$n8n_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$O")" == "$ollama_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}|{{.RestartCount}}' "$EDGE")" == "$edge_before" ]]
echo "STAGING_N8N_RECREATED=NO"
echo "STAGING_OLLAMA_RECREATED=NO"
echo "STAGING_EDGE_RECREATED=NO"

"$VERIFY_STAGING" >/dev/null
"$VERIFY_PROD" >/dev/null
echo "POST_RECOVERY_RUNTIME_CONTRACTS=PASS"
echo "RECOVERED_FROM_SHA=$from_sha"
echo "RECOVERED_TO_SHA=$to_sha"
echo "RECOVERED_TO_BRANCH=$to_branch"
echo "RESULT=STAGING_AUTH_BOOTSTRAP_RECOVERY_PASS"
trap - ERR
