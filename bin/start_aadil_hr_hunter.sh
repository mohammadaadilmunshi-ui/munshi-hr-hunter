#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT="${AADIL_HR_HUNTER_PROJECT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PYTHON="${AADIL_HR_HUNTER_PYTHON:-$PROJECT/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"; fi
LOGS="$PROJECT/logs"
DATA="$PROJECT/data"
ENV_FILE="$PROJECT/.env"
SCHEDULER_LABEL="com.aadil.hr-hunter.randomized-sources"
SCHEDULER_PLIST="$HOME/Library/LaunchAgents/${SCHEDULER_LABEL}.plist"
RESTART=0
LOAD_SCHEDULER=1
FASTAPI_HOST="127.0.0.1"
FASTAPI_PORT="8000"
STREAMLIT_HOST="127.0.0.1"
STREAMLIT_PORT="8501"
N8N_HOST="127.0.0.1"
N8N_PORT="5678"
N8N_BASE_URL="http://127.0.0.1:5678"
OLLAMA_BASE_URL="http://127.0.0.1:11434"
OLLAMA_ENABLED=0

for arg in "$@"; do
  case "$arg" in
    --restart) RESTART=1 ;;
    --no-scheduler) LOAD_SCHEDULER=0 ;;
    -h|--help)
      echo "Usage: $0 [--restart] [--no-scheduler]"
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "ERROR: $*"; exit 1; }
http_ok() { curl -fsS --max-time 5 "$1" >/dev/null 2>&1; }
port_pid() { lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -1 || true; }
port_open() { [ -n "$(port_pid "$1")" ]; }

wait_http() {
  local name="$1" url="$2" limit="$3" i
  for ((i=1; i<=limit; i++)); do
    if http_ok "$url"; then
      log "$name is healthy."
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_process() {
  local pattern="$1" limit="$2" i
  for ((i=1; i<=limit; i++)); do
    pgrep -f "$pattern" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

find_n8n() {
  local candidate
  for candidate in \
    "$(command -v n8n 2>/dev/null || true)" \
    "/usr/local/bin/n8n" \
    "/opt/homebrew/bin/n8n"
  do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

active_n8n_count() {
  "$PYTHON" - <<'PY'
from pathlib import Path
import sqlite3

db = Path.home() / ".n8n" / "database.sqlite"
workflow_id = "L1u2xZkgFpi7KEuv"

if not db.exists():
    print(0)
    raise SystemExit

con = sqlite3.connect(db)
try:
    cols = [r[1] for r in con.execute("PRAGMA table_info(execution_entity)")]
    workflow_col = "workflowId" if "workflowId" in cols else (
        "workflow_id" if "workflow_id" in cols else None
    )
    if not workflow_col:
        print(0)
        raise SystemExit

    wanted = [
        x for x in (
            "status" if "status" in cols else None,
            "finished" if "finished" in cols else None,
            "stoppedAt" if "stoppedAt" in cols else None,
            "waitTill" if "waitTill" in cols else None,
        ) if x
    ]
    rows = con.execute(
        f"SELECT {', '.join(wanted)} FROM execution_entity "
        f"WHERE {workflow_col}=?",
        (workflow_id,),
    ).fetchall()

    terminal = {
        "success", "error", "canceled", "cancelled",
        "crashed", "completed", "failed",
    }
    active_status = {"running", "waiting", "new", "unknown"}
    count = 0

    for row in rows:
        record = dict(zip(wanted, row))
        status = str(record.get("status", "") or "").strip().lower()
        stopped = record.get("stoppedAt")
        wait_till = record.get("waitTill")
        finished = record.get("finished")

        if stopped is not None or status in terminal:
            continue
        if status in active_status:
            count += 1
        elif not status and finished in {0, False, None}:
            count += 1
        elif wait_till is not None:
            count += 1

    print(count)
finally:
    con.close()
PY
}

stop_pattern() {
  local pattern="$1" label="$2" pids
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  [ -n "$pids" ] || return 0
  log "Stopping $label: $pids"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 3
}

load_env() {
  export PYTHONPATH="$PROJECT${PYTHONPATH:+:$PYTHONPATH}"
  if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    log "Loaded .env without printing secrets."
  fi
}

configure_runtime() {
  FASTAPI_HOST="${FASTAPI_HOST:-127.0.0.1}"
  FASTAPI_PORT="${FASTAPI_PORT:-8000}"
  STREAMLIT_HOST="${STREAMLIT_HOST:-127.0.0.1}"
  STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"
  N8N_HOST="${N8N_HOST:-127.0.0.1}"
  N8N_PORT="${N8N_PORT:-5678}"
  N8N_BASE_URL="${N8N_BASE_URL:-http://${N8N_HOST}:${N8N_PORT}}"
  OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://${OLLAMA_HOST:-127.0.0.1}:${OLLAMA_PORT:-11434}}"
  OLLAMA_ENABLED="${OLLAMA_ENABLED:-${OLLAMA_REQUIRED:-0}}"
  case "$(printf '%s' "${OLLAMA_REQUIRED:-0}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on|required|enabled) OLLAMA_ENABLED=1 ;;
  esac
  LOGS="${AADIL_HR_HUNTER_LOGS:-$PROJECT/logs}"
  SCHEDULER_PLIST="${LAUNCH_AGENTS_DIRECTORY:-$HOME/Library/LaunchAgents}/${SCHEDULER_LABEL}.plist"
}

is_macos() { [ "$(uname -s)" = "Darwin" ]; }

restart_if_requested() {
  [ "$RESTART" -eq 1 ] || return 0

  local active
  active="$(active_n8n_count)"
  [ "$active" = "0" ] ||
    die "Refusing restart: $active genuinely active n8n execution(s)."

  stop_pattern '[a]pp\.telegram_listener' "Telegram listener"
  stop_pattern '[u]vicorn[[:space:]].*app\.api:app' "FastAPI"
  stop_pattern '[s]treamlit[[:space:]].*app/dashboard\.py' "Streamlit"

  local pid command
  pid="$(port_pid 5678)"
  if [ -n "$pid" ]; then
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    printf '%s' "$command" | grep -qi n8n ||
      die "Port 5678 belongs to a non-n8n process: $command"
    log "Stopping n8n PID $pid"
    kill "$pid" 2>/dev/null || true
    sleep 5
  fi

  if [ -f "$DATA/telegram_listener.lock" ]; then
    local lock_pid
    lock_pid="$(cat "$DATA/telegram_listener.lock" 2>/dev/null || true)"
    if [ -z "$lock_pid" ] || ! kill -0 "$lock_pid" 2>/dev/null; then
      rm -f "$DATA/telegram_listener.lock"
      log "Removed stale Telegram lock."
    fi
  fi
}

start_ollama() {
  case "$(printf '%s' "$OLLAMA_ENABLED" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on|required|enabled) ;;
    *) log "Ollama is disabled (set OLLAMA_ENABLED=true to enable)."; return 0 ;;
  esac

  if http_ok "$OLLAMA_BASE_URL/api/tags"; then
    log "Ollama already online."
    return
  fi

  if is_macos && [ -d "/Applications/Ollama.app" ]; then
    log "Starting Ollama."
    open -gja Ollama >/dev/null 2>&1 || true
  elif command -v ollama >/dev/null 2>&1; then
    log "Starting ollama serve."
    nohup ollama serve > "$LOGS/ollama.log" 2>&1 &
  else
    die "Ollama is not installed."
  fi

  wait_http "Ollama" "$OLLAMA_BASE_URL/api/tags" 45 ||
    die "Ollama failed. Check $LOGS/ollama.log"
}

start_n8n() {
  if http_ok "$N8N_BASE_URL/healthz" ||
     http_ok "$N8N_BASE_URL/health"; then
    log "n8n already online."
    return
  fi

  if port_open 5678; then
    local pid command
    pid="$(port_pid 5678)"
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    die "Port 5678 is occupied but n8n health failed. PID $pid: $command"
  fi

  local n8n_bin
  n8n_bin="$(find_n8n)" || die "n8n executable not found."
  log "Starting n8n."
  nohup "$n8n_bin" start > "$LOGS/n8n.log" 2>&1 &

  local i
  for ((i=1; i<=90; i++)); do
    if http_ok "$N8N_BASE_URL/healthz" ||
       http_ok "$N8N_BASE_URL/health"; then
      log "n8n is healthy."
      return
    fi
    sleep 1
  done

  die "n8n failed. Check $LOGS/n8n.log"
}

start_fastapi() {
  if http_ok "http://${FASTAPI_HOST}:${FASTAPI_PORT}/health"; then
    log "FastAPI already online."
    return
  fi

  if port_open "$FASTAPI_PORT"; then
    local pid command
    pid="$(port_pid 8000)"
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    die "Port 8000 is occupied but FastAPI health failed. PID $pid: $command"
  fi

  log "Starting FastAPI."
  nohup "$PYTHON" -m uvicorn app.api:app \
    --host "$FASTAPI_HOST" \
    --port "$FASTAPI_PORT" \
    > "$LOGS/fastapi.log" 2>&1 &

  wait_http "FastAPI" "http://${FASTAPI_HOST}:${FASTAPI_PORT}/health" 60 ||
    die "FastAPI failed. Check $LOGS/fastapi.log"
}

start_telegram() {
  if pgrep -f '[a]pp\.telegram_listener' >/dev/null 2>&1; then
    log "Telegram listener already online."
    return
  fi

  if [ -f "$DATA/telegram_listener.lock" ]; then
    local lock_pid
    lock_pid="$(cat "$DATA/telegram_listener.lock" 2>/dev/null || true)"
    if [ -z "$lock_pid" ] || ! kill -0 "$lock_pid" 2>/dev/null; then
      rm -f "$DATA/telegram_listener.lock"
      log "Removed stale Telegram lock."
    fi
  fi

  log "Starting Telegram listener."
  nohup "$PYTHON" -m app.telegram_listener \
    > "$LOGS/telegram_listener.log" 2>&1 &

  wait_process '[a]pp\.telegram_listener' 45 ||
    die "Telegram listener failed. Check $LOGS/telegram_listener.log"

  log "Telegram listener is running."
}

start_streamlit() {
  if http_ok "http://${STREAMLIT_HOST}:${STREAMLIT_PORT}"; then
    log "Streamlit already online."
    return
  fi

  if port_open "$STREAMLIT_PORT"; then
    local pid command
    pid="$(port_pid 8501)"
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    die "Port 8501 is occupied but Streamlit health failed. PID $pid: $command"
  fi

  log "Starting Streamlit."
  nohup "$PYTHON" -m streamlit run "$PROJECT/app/dashboard.py" \
    --server.address "$STREAMLIT_HOST" \
    --server.port "$STREAMLIT_PORT" \
    --server.headless true \
    > "$LOGS/streamlit.log" 2>&1 &

  wait_http "Streamlit" "http://${STREAMLIT_HOST}:${STREAMLIT_PORT}" 60 ||
    die "Streamlit failed. Check $LOGS/streamlit.log"
}

load_scheduler() {
  [ "$LOAD_SCHEDULER" -eq 1 ] || {
    log "Scheduler left unchanged."
    return
  }

  is_macos || { log "External scheduler owns lifecycle on non-macOS; LaunchAgents skipped."; return 0; }

  if [ ! -f "$SCHEDULER_PLIST" ]; then
    log "Scheduler plist not found: $SCHEDULER_PLIST"
    return
  fi

  if launchctl print "gui/$UID/$SCHEDULER_LABEL" >/dev/null 2>&1; then
    log "Randomized source scheduler already loaded."
  else
    log "Loading randomized source scheduler."
    launchctl bootstrap "gui/$UID" "$SCHEDULER_PLIST" >/dev/null 2>&1 ||
      launchctl load "$SCHEDULER_PLIST" >/dev/null 2>&1 ||
      die "Could not load $SCHEDULER_LABEL"
  fi

  log "Scheduler loaded; no immediate kickstart was performed."
}

status_word() {
  "$@" >/dev/null 2>&1 && echo ONLINE || echo OFFLINE
}

print_summary() {
  echo
  echo "============================================================"
  echo "AADIL HR HUNTER — SERVICES"
  echo "============================================================"
  printf '%-22s %s\n' "n8n" "$(
    if http_ok "$N8N_BASE_URL/healthz" ||
       http_ok "$N8N_BASE_URL/health"; then echo ONLINE; else echo OFFLINE; fi
  )"
  printf '%-22s %s\n' "FastAPI" "$(status_word http_ok "http://${FASTAPI_HOST}:${FASTAPI_PORT}/health")"
  printf '%-22s %s\n' "Telegram listener" "$(
    if pgrep -f '[a]pp\.telegram_listener' >/dev/null 2>&1; then echo ONLINE; else echo OFFLINE; fi
  )"
  printf '%-22s %s\n' "Streamlit" "$(status_word http_ok "http://${STREAMLIT_HOST}:${STREAMLIT_PORT}")"
  printf '%-22s %s\n' "Ollama" "$(status_word http_ok "$OLLAMA_BASE_URL/api/tags")"
  printf '%-22s %s\n' "Random scheduler" "$(
    if is_macos && launchctl print "gui/$UID/$SCHEDULER_LABEL" >/dev/null 2>&1; then echo LOADED; else echo EXTERNAL_OR_NOT_LOADED; fi
  )"
  echo
  echo "n8n:       $N8N_BASE_URL"
  echo "FastAPI:   http://${FASTAPI_HOST}:${FASTAPI_PORT}"
  echo "Streamlit: http://${STREAMLIT_HOST}:${STREAMLIT_PORT}"
  echo "Ollama:    $OLLAMA_BASE_URL"
  echo
  echo "FINAL RESULT: STARTUP SEQUENCE COMPLETED"
}

main() {
  [ -d "$PROJECT" ] || die "Project missing: $PROJECT"
  [ -x "$PYTHON" ] || die "Project Python missing: $PYTHON"
  [ -f "$PROJECT/app/api.py" ] || die "Missing app/api.py"
  [ -f "$PROJECT/app/telegram_listener.py" ] || die "Missing app/telegram_listener.py"
  [ -f "$PROJECT/app/dashboard.py" ] || die "Missing app/dashboard.py"
  [ -f "$PROJECT/data/hunter.db" ] || die "Missing data/hunter.db"

  load_env
  configure_runtime
  mkdir -p "$LOGS" "$DATA"
  cd "$PROJECT"
  restart_if_requested
  start_ollama
  start_n8n
  start_fastapi
  start_telegram
  start_streamlit
  load_scheduler
  print_summary
}

main "$@"
