#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT="${AADIL_HR_HUNTER_PROJECT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOGS="$PROJECT/logs"
LABEL="com.aadil.hr-hunter.randomized-sources"

code() {
  curl -sS --max-time 5 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || true
}

echo "============================================================"
echo "AADIL HR HUNTER — READ-ONLY STATUS"
echo "============================================================"

N8N_BASE_URL="${N8N_BASE_URL:-http://${N8N_HOST:-127.0.0.1}:${N8N_PORT:-5678}}"
FASTAPI_BASE_URL="${FASTAPI_BASE_URL:-http://${FASTAPI_HOST:-127.0.0.1}:${FASTAPI_PORT:-8000}}"
STREAMLIT_BASE_URL="${STREAMLIT_BASE_URL:-http://${STREAMLIT_HOST:-127.0.0.1}:${STREAMLIT_PORT:-8501}}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://${OLLAMA_HOST:-127.0.0.1}:${OLLAMA_PORT:-11434}}"
N8N_CODE="$(code "$N8N_BASE_URL/healthz")"
[ "$N8N_CODE" = "000" ] && N8N_CODE="$(code "$N8N_BASE_URL/health")"

printf '%-22s HTTP %s\n' "n8n" "$N8N_CODE"
printf '%-22s HTTP %s\n' "FastAPI" "$(code "$FASTAPI_BASE_URL/health")"
printf '%-22s HTTP %s\n' "Streamlit" "$(code "$STREAMLIT_BASE_URL")"
if [ "${OLLAMA_ENABLED:-false}" = "true" ]; then
  printf '%-22s HTTP %s\n' "Ollama" "$(code "$OLLAMA_BASE_URL/api/tags")"
else
  printf '%-22s %s\n' "Ollama" "DISABLED"
fi
printf '%-22s %s\n' "Telegram listener" "$(
  if pgrep -f '[a]pp\.telegram_listener' >/dev/null 2>&1; then echo RUNNING; else echo STOPPED; fi
)"
printf '%-22s %s\n' "Random scheduler" "$(
  if [ "$(uname -s)" = "Darwin" ] && launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then echo LOADED; else echo EXTERNAL_OR_NOT_LOADED; fi
)"

echo
echo "Processes"
ps -axo pid=,etime=,command= |
  grep -Ei \
  '[n]8n start|[u]vicorn .*app\.api:app|[a]pp\.telegram_listener|[s]treamlit .*app/dashboard\.py|[o]llama serve' ||
  true

echo
echo "Listeners"
for port in 5678 8000 8501 11434; do
  echo "--- port $port"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || echo "No listener"
done

echo
echo "Recent logs"
for file in \
  "$LOGS/n8n.log" \
  "$LOGS/fastapi.log" \
  "$LOGS/telegram_listener.log" \
  "$LOGS/streamlit.log" \
  "$LOGS/randomized_sources_scheduler.log" \
  "$LOGS/randomized_sources_scheduler_error.log"
do
  if [ -f "$file" ]; then
    echo
    echo "----- $file"
    tail -n 12 "$file"
  fi
done

echo
echo "FINAL RESULT: READ-ONLY STATUS COMPLETE"
