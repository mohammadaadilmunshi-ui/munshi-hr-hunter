#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${AADIL_HR_HUNTER_PROJECT:-$HOME/Aadil-HR-Hunter}"
DATA="$PROJECT/data"
STOP_SCHEDULER=0
STOP_OLLAMA=0
LABEL="com.aadil.hr-hunter.randomized-sources"

for arg in "$@"; do
  case "$arg" in
    --scheduler) STOP_SCHEDULER=1 ;;
    --ollama) STOP_OLLAMA=1 ;;
    --all) STOP_SCHEDULER=1; STOP_OLLAMA=1 ;;
    -h|--help)
      echo "Usage: $0 [--scheduler] [--ollama] [--all]"
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

stop_pattern() {
  local pattern="$1" label="$2" pids
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    echo "$label: already stopped"
  else
    echo "$label: stopping $pids"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
  fi
}

stop_pattern '[a]pp\.telegram_listener' "Telegram listener"
stop_pattern '[u]vicorn[[:space:]].*app\.api:app' "FastAPI"
stop_pattern '[s]treamlit[[:space:]].*app/dashboard\.py' "Streamlit"

N8N_PID="$(lsof -tiTCP:5678 -sTCP:LISTEN 2>/dev/null | head -1 || true)"
if [ -n "$N8N_PID" ]; then
  CMD="$(ps -p "$N8N_PID" -o command= 2>/dev/null || true)"
  if printf '%s' "$CMD" | grep -qi n8n; then
    echo "n8n: stopping PID $N8N_PID"
    kill "$N8N_PID" 2>/dev/null || true
  else
    echo "n8n: port belongs to another process; unchanged"
  fi
else
  echo "n8n: already stopped"
fi

sleep 3

if [ -f "$DATA/telegram_listener.lock" ]; then
  LOCK_PID="$(cat "$DATA/telegram_listener.lock" 2>/dev/null || true)"
  if [ -z "$LOCK_PID" ] || ! kill -0 "$LOCK_PID" 2>/dev/null; then
    rm -f "$DATA/telegram_listener.lock"
    echo "Removed stale Telegram lock."
  fi
fi

if [ "$STOP_SCHEDULER" -eq 1 ]; then
  launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 ||
    launchctl unload "$HOME/Library/LaunchAgents/${LABEL}.plist" >/dev/null 2>&1 ||
    true
  echo "Random scheduler: unloaded"
fi

if [ "$STOP_OLLAMA" -eq 1 ]; then
  stop_pattern '[o]llama serve' "Ollama"
fi

echo
echo "FINAL RESULT: STOP SEQUENCE COMPLETED"
