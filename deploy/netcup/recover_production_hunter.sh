#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Incident entrypoint is accepted only when production is on the known auth
# candidate (or the one-time bootstrap SHA), but recovery deliberately returns
# Hunter to the exact last-known-good pre-auth code/image pair. The live DB is
# backed up and preserved; it is never restored/replaced by this helper.
EXPECTED_CURRENT_SHA="380896964d12199936ee7c676e39352a1a68cec8"
TEMP_SHA="e55ca0a82d8ede6a5053c0a5705e5bb0e1979a90"
RECOVERY_SHA="4c0f39fa503dabb55ef3212a23d2301ad04ec18a"
RECOVERY_BRANCH="fix/production-sqlite-wal-backup-v1"
ROLLBACK_IMAGE="munshi-netcup-shadow-hunter:rollback-deploy-20260905T015437Z"

ROOT="${MUNSHI_ROOT:-/opt/munshi}"
REPO="$ROOT/repo"
ENV_FILE="$ROOT/secrets/netcup-shadow.env"
H="munshi-netcup-shadow-hunter-1"
N="munshi-netcup-shadow-n8n-1"
O="munshi-netcup-shadow-ollama-1"
EDGE="munshi-staging-edge-caddy"

expected=""
while (($#)); do
  case "$1" in
    --expected-sha) expected="${2:-}"; shift 2 ;;
    -h|--help) echo "Usage: $0 --expected-sha $EXPECTED_CURRENT_SHA"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$expected" == "$EXPECTED_CURRENT_SHA" ]] || { echo "unexpected incident source SHA" >&2; exit 3; }

for tool in docker git python3 curl sha256sum timeout; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 10; }
done

docker info >/dev/null 2>&1 || { echo "DOCKER_ACCESS=FAIL" >&2; exit 11; }
[[ -d "$REPO/.git" ]] || { echo "production repo missing" >&2; exit 12; }
[[ -f "$ENV_FILE" ]] || { echo "production env missing" >&2; exit 13; }
[[ -z "$(git -C "$REPO" status --porcelain)" ]] || { echo "production repo dirty" >&2; exit 14; }

head="$(git -C "$REPO" rev-parse HEAD)"
echo "PRODUCTION_HEAD_BEFORE=$head"
case "$head" in
  "$EXPECTED_CURRENT_SHA"|"$TEMP_SHA") ;;
  *) echo "unexpected production head: $head" >&2; exit 15 ;;
esac

git -C "$REPO" cat-file -e "$RECOVERY_SHA^{commit}"
docker image inspect "$ROLLBACK_IMAGE" >/dev/null 2>&1 || { echo "missing exact last-known-good rollback image" >&2; exit 16; }
echo "RECOVERY_TARGET_SHA=$RECOVERY_SHA"
echo "RECOVERY_ROLLBACK_IMAGE=$ROLLBACK_IMAGE"

for c in "$H" "$N" "$O"; do
  docker inspect "$c" >/dev/null 2>&1 || { echo "missing runtime object: $c" >&2; exit 17; }
done

n_state="$(docker inspect -f '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}|{{.State.OOMKilled}}' "$N")"
o_state="$(docker inspect -f '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}|{{.State.OOMKilled}}' "$O")"
echo "N8N_PRECHECK=$n_state"
echo "OLLAMA_PRECHECK=$o_state"
[[ "$n_state" == true\|healthy\|false ]] || { echo "n8n is not healthy; Hunter-only recovery refuses" >&2; exit 18; }
[[ "$o_state" == true\|healthy\|false ]] || { echo "Ollama is not healthy; Hunter-only recovery refuses" >&2; exit 19; }

n_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$N")"
o_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$O")"
edge_before=""
if docker inspect "$EDGE" >/dev/null 2>&1; then
  edge_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}|{{.RestartCount}}' "$EDGE")"
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
incident_dir="$ROOT/backups/runtime-recovery-$stamp"
mkdir -p "$incident_dir"
chmod 700 "$incident_dir"
raw_log="$incident_dir/hunter-pre-recovery.log"
docker logs --timestamps --since 60m "$H" >"$raw_log" 2>&1 || true
chmod 600 "$raw_log"
echo "PRE_RECOVERY_LOG_CAPTURED=YES"

python3 - "$raw_log" <<'PY'
from pathlib import Path
import re, sys
text = Path(sys.argv[1]).read_text(errors="replace")
checks = {
    "DIAG_TELEGRAM_CONFLICT_409": bool(re.search(r"(?:409|Conflict).*(?:getUpdates|terminated by other getUpdates|telegram)|telegram.*(?:409|Conflict)", text, re.I | re.S)),
    "DIAG_REQUIRED_TELEGRAM_EXIT": "Required Hunter lane exited: telegram" in text,
    "DIAG_REQUIRED_FASTAPI_EXIT": "Required Hunter lane exited: fastapi" in text,
    "DIAG_REQUIRED_STREAMLIT_EXIT": "Required Hunter lane exited: streamlit" in text,
    "DIAG_FASTAPI_NOT_READY": "FastAPI did not become ready before writer lanes" in text,
    "DIAG_DEVICE_AUTH_KEY_ERROR": bool(re.search(r"device-auth signing key|dashboard device-auth signing key", text, re.I)),
    "DIAG_PERMISSION_ERROR": bool(re.search(r"PermissionError|Permission denied", text)),
}
for key, value in checks.items():
    print(f"{key}={'YES' if value else 'NO'}")
PY

hunter_image_name="$(docker inspect -f '{{.Config.Image}}' "$H")"
prod_volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/hunter/data"}}{{.Name}}{{end}}{{end}}' "$H")"
[[ -n "$hunter_image_name" && -n "$prod_volume" ]] || { echo "Hunter image/volume resolution failed" >&2; exit 20; }

echo "=== QUIESCE HUNTER ONLY ==="
docker stop -t 20 "$H" >/dev/null 2>&1 || true
echo "PRODUCTION_HUNTER_QUIESCED=PASS"

backup="$incident_dir/hunter.db"
free="$(df -PB1 "$incident_dir" | awk 'NR==2 {print $4}')"
db_bytes="$(docker run --rm --network none --user 0:0 --mount "type=volume,src=$prod_volume,dst=/app/hunter/data,readonly" --entrypoint python "$ROLLBACK_IMAGE" -c 'import os; print(os.path.getsize("/app/hunter/data/hunter.db"))')"
[[ "$free" =~ ^[0-9]+$ && "$db_bytes" =~ ^[0-9]+$ ]] || { echo "backup capacity resolution failed" >&2; exit 21; }
required=$((db_bytes + 1073741824))
(( free >= required )) || { echo "backup free space too low" >&2; exit 22; }

timeout 1800s docker run --rm -i \
  --network none \
  --user 0:0 \
  --mount "type=volume,src=$prod_volume,dst=/app/hunter/data" \
  --mount "type=bind,src=$incident_dir,dst=/backup" \
  --entrypoint python \
  "$ROLLBACK_IMAGE" - /app/hunter/data/hunter.db /backup/hunter.db <<'PY'
import os, sqlite3, sys
src, dst = sys.argv[1:3]
source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
source.execute("PRAGMA query_only=ON")
source.execute("PRAGMA busy_timeout=60000")
dest = sqlite3.connect(dst)
try:
    source.backup(dest)
    result = dest.execute("PRAGMA quick_check").fetchone()[0]
finally:
    dest.close(); source.close()
if result != "ok":
    raise SystemExit(f"backup quick_check failed: {result}")
os.chmod(dst, 0o600)
print("PRODUCTION_DB_BACKUP_QUICK_CHECK=PASS")
PY
[[ -f "$backup" ]] || { echo "backup missing" >&2; exit 23; }
echo "PRODUCTION_DB_BACKUP=$backup"
echo "PRODUCTION_DB_BACKUP_SHA256=$(sha256sum "$backup" | awk '{print $1}')"

compose=(
  docker compose
  --project-name munshi-netcup-shadow
  --env-file "$ENV_FILE"
  -f "$REPO/compose.yaml"
  -f "$REPO/compose.netcup-shadow.yaml"
  -f "$ROOT/runtime/stage10-imported.override.yaml"
  -f "$ROOT/runtime/stage12-production.override.yaml"
  -f "$ROOT/runtime/stage12-n8n-runtime-repair.override.yaml"
)

for f in \
  "$ROOT/runtime/stage10-imported.override.yaml" \
  "$ROOT/runtime/stage12-production.override.yaml" \
  "$ROOT/runtime/stage12-n8n-runtime-repair.override.yaml"
do
  [[ -f "$f" ]] || { echo "missing runtime override: $f" >&2; exit 24; }
done

echo "=== RESTORE EXACT LAST-KNOWN-GOOD HUNTER ==="
git -C "$REPO" checkout -q -B "$RECOVERY_BRANCH" "$RECOVERY_SHA"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$RECOVERY_SHA" ]]
[[ -z "$(git -C "$REPO" status --porcelain)" ]]
docker tag "$ROLLBACK_IMAGE" "$hunter_image_name"
"${compose[@]}" config -q
"${compose[@]}" up -d --no-deps --force-recreate hunter

healthy=0
for _ in $(seq 1 72); do
  state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$H" 2>/dev/null || true)"
  if [[ "$state" == "healthy" ]]; then healthy=1; break; fi
  sleep 5
done
if [[ "$healthy" != "1" ]]; then
  post_log="$incident_dir/hunter-post-recovery-failed.log"
  docker logs --timestamps --since 15m "$H" >"$post_log" 2>&1 || true
  chmod 600 "$post_log"
  echo "PRODUCTION_HUNTER_RECOVERY_HEALTH=FAIL" >&2
  python3 - "$post_log" <<'PY'
from pathlib import Path
import re, sys
text = Path(sys.argv[1]).read_text(errors="replace")
checks = {
    "POST_DIAG_TELEGRAM_CONFLICT_409": bool(re.search(r"(?:409|Conflict).*(?:getUpdates|terminated by other getUpdates|telegram)|telegram.*(?:409|Conflict)", text, re.I | re.S)),
    "POST_DIAG_REQUIRED_TELEGRAM_EXIT": "Required Hunter lane exited: telegram" in text,
    "POST_DIAG_REQUIRED_FASTAPI_EXIT": "Required Hunter lane exited: fastapi" in text,
    "POST_DIAG_REQUIRED_STREAMLIT_EXIT": "Required Hunter lane exited: streamlit" in text,
    "POST_DIAG_FASTAPI_NOT_READY": "FastAPI did not become ready before writer lanes" in text,
    "POST_DIAG_DEVICE_AUTH_KEY_ERROR": bool(re.search(r"device-auth signing key|dashboard device-auth signing key", text, re.I)),
    "POST_DIAG_PERMISSION_ERROR": bool(re.search(r"PermissionError|Permission denied", text)),
}
for key, value in checks.items():
    print(f"{key}={'YES' if value else 'NO'}")
PY
  exit 25
fi
echo "PRODUCTION_HUNTER_RECOVERY_HEALTH=PASS"

[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$N")" == "$n_before" ]] || { echo "n8n identity changed" >&2; exit 26; }
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$O")" == "$o_before" ]] || { echo "Ollama identity changed" >&2; exit 27; }
if [[ -n "$edge_before" ]]; then
  [[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}|{{.RestartCount}}' "$EDGE")" == "$edge_before" ]] || { echo "Caddy identity changed" >&2; exit 28; }
fi
echo "N8N_OLLAMA_CADDY_IDENTITIES_UNCHANGED=PASS"

curl -fsS --max-time 10 http://127.0.0.1:8000/health >/dev/null
curl -fsS --max-time 10 http://127.0.0.1:8501/_stcore/health >/dev/null
curl -fsS --max-time 10 http://127.0.0.1:5678/healthz >/dev/null

docker exec -i "$H" python - <<'PY'
from app.database import get_connection
c = get_connection()
try:
    assert c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
finally:
    c.close()
print("PRODUCTION_DB_LIVE_QUICK_CHECK=PASS")
PY

[[ "$(git -C "$REPO" rev-parse HEAD)" == "$RECOVERY_SHA" ]]
echo "PRODUCTION_HEAD_AFTER=$RECOVERY_SHA"
echo "AUTH_UPGRADE_ROLLED_BACK_FOR_STABILITY=YES"
echo "N8N_RECREATED=NO"
echo "OLLAMA_RECREATED=NO"
echo "CADDY_RECREATED=NO"
echo "DATABASE_RESTORED_OR_REPLACED=NO"
echo "RESULT=PRODUCTION_HUNTER_RECOVERY_PASS"
