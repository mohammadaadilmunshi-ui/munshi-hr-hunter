#!/bin/bash

# AADIL_APPS_SCRIPT_SECRET_BRIDGE_V1_3
if [ -f "$HOME/.aadil_hr_hunter_secrets" ]; then
  set -a
  . "$HOME/.aadil_hr_hunter_secrets"
  set +a
fi
# END AADIL_APPS_SCRIPT_SECRET_BRIDGE_V1_3
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT="${AADIL_HR_HUNTER_PROJECT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
ENV_FILE="$PROJECT/.runtime/n8n_runtime.env"
LOG="$PROJECT/logs/n8n_start.log"
PID_FILE="$PROJECT/logs/n8n.pid"
URL="${N8N_BASE_URL:-http://${N8N_HOST:-127.0.0.1}:${N8N_PORT:-5678}}"

mkdir -p "$PROJECT/logs"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: Runtime environment file is missing: $ENV_FILE"
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

export N8N_BLOCK_ENV_ACCESS_IN_NODE=false

if nc -z "${N8N_HOST:-127.0.0.1}" "${N8N_PORT:-5678}" 2>/dev/null; then
  echo "n8n is already running at $URL"
  exit 0
fi

nohup /usr/local/bin/n8n start >"$LOG" 2>&1 &
PID=$!
echo "$PID" >"$PID_FILE"

for _ in $(seq 1 90); do
  if nc -z "${N8N_HOST:-127.0.0.1}" "${N8N_PORT:-5678}" 2>/dev/null; then
    echo "n8n is online at $URL"
    echo "PID: $PID"
    echo "Environment access: enabled"
    exit 0
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "ERROR: n8n exited before opening port 5678."
    tail -n 100 "$LOG"
    exit 1
  fi
  sleep 1
done

echo "ERROR: n8n did not open port 5678."
tail -n 100 "$LOG"
exit 1
