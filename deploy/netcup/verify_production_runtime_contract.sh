#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${MUNSHI_ROOT:-/opt/munshi}"
REPO="$ROOT/repo"
ENV_FILE="$ROOT/secrets/netcup-shadow.env"
PROJECT="${MUNSHI_COMPOSE_PROJECT:-munshi-netcup-shadow}"

H="${MUNSHI_HUNTER_CONTAINER:-munshi-netcup-shadow-hunter-1}"
N="${MUNSHI_N8N_CONTAINER:-munshi-netcup-shadow-n8n-1}"
O="${MUNSHI_OLLAMA_CONTAINER:-munshi-netcup-shadow-ollama-1}"

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

for f in \
  "$ENV_FILE" \
  "$REPO/compose.yaml" \
  "$REPO/compose.netcup-shadow.yaml" \
  "$ROOT/runtime/stage10-imported.override.yaml" \
  "$ROOT/runtime/stage12-production.override.yaml" \
  "$ROOT/runtime/stage12-n8n-runtime-repair.override.yaml"
do
  [[ -f "$f" ]] || { echo "missing required production contract file: $f" >&2; exit 10; }
done

"${compose[@]}" config -q
echo "FIVE_LAYER_COMPOSE=PASS"

for c in "$H" "$N" "$O"; do
  running="$(docker inspect -f '{{.State.Running}}' "$c")"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c")"
  restart="$(docker inspect -f '{{.RestartCount}}' "$c")"
  oom="$(docker inspect -f '{{.State.OOMKilled}}' "$c")"
  echo "$c running=$running health=$health restart=$restart oom=$oom"
  [[ "$running" == "true" && "$health" == "healthy" && "$oom" == "false" ]] || exit 11
done

docker exec "$H" sh -lc '
set -eu
for kv in \
  "HUNTER_ENABLE_TELEGRAM=true" \
  "HUNTER_ENABLE_DISCOVERY_SCHEDULER=true" \
  "HUNTER_ENABLE_COORDINATOR=true" \
  "HUNTER_DISCOVERY_INTERVAL_SECONDS=300" \
  "HUNTER_COORDINATOR_INTERVAL_SECONDS=3600" \
  "PRODUCTION_STATE_IMPORTED=true" \
  "PRODUCTION_CALLBACKS_ENABLED=true" \
  "N8N_USER_FOLDER=/app/n8n-readonly" \
  "N8N_DATABASE_PATH=/app/n8n-readonly/database.sqlite"
do
  k=${kv%%=*}; want=${kv#*=}
  eval "got=\${$k:-}"
  echo "$k=$got"
  [ "$got" = "$want" ]
done
'
echo "PRODUCTION_ENV_CONTRACT=PASS"

docker inspect "$H" | python3 -c '
import json,sys
obj=json.load(sys.stdin)[0]
m=[x for x in obj.get("Mounts",[]) if x.get("Destination")=="/app/n8n-readonly"]
assert len(m)==1, m
assert m[0].get("Name")=="munshi-netcup-shadow_n8n_data", m[0]
assert m[0].get("RW") is False, m[0]
print("N8N_RO_MOUNT=PASS")
'

docker exec -i "$H" python - <<'PY'
import os, sqlite3
hp="/app/hunter/data/hunter.db"
h=sqlite3.connect(f"file:{hp}?mode=ro",uri=True,timeout=30)
h.execute("PRAGMA query_only=ON")
h.execute("PRAGMA busy_timeout=30000")
assert h.execute("PRAGMA quick_check").fetchone()[0]=="ok"
h.close()
print("HUNTER_DB_QUICK_CHECK=PASS")

np=os.environ.get("N8N_DATABASE_PATH","")
assert np=="/app/n8n-readonly/database.sqlite", np
n=sqlite3.connect(f"file:{np}?mode=ro",uri=True,timeout=30)
n.execute("PRAGMA query_only=ON")
n.execute("PRAGMA busy_timeout=30000")
assert n.execute("PRAGMA quick_check").fetchone()[0]=="ok"
n.close()
print("N8N_DB_RO_QUICK_CHECK=PASS")
PY

for url in \
  "http://127.0.0.1:8000/health" \
  "http://127.0.0.1:8501/_stcore/health" \
  "http://127.0.0.1:5678/healthz"
do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$url")"
  echo "$url -> $code"
  [[ "$code" == "200" ]] || exit 12
done

echo "RESULT=PRODUCTION_RUNTIME_CONTRACT_PASS"
