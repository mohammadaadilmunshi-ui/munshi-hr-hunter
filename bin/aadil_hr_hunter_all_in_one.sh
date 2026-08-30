#!/usr/bin/env bash
#
# AADIL HR HUNTER — ALL-IN-ONE CONTROLLER
# Version: 1.1.0
#
# macOS / Bash 3.2 compatible.
#
# Commands:
#   install               Install controller, repair legacy runtime, start everything
#   start                 Start/adopt all core services and enable both real timers
#   stop [--force]        Stop timers and core services
#   restart [--force]     Safe full restart
#   status                Show services, timers, PIDs, databases, and URLs
#   health                Strict health check; exits nonzero if unhealthy
#   doctor                Read-only dependency, import, database, and configuration checks
#   network               Check DNS/connectivity used by source adapters
#   logs [service]        Follow logs (n8n, fastapi, streamlit, telegram, randomized, hourly)
#   open                  Open n8n and Streamlit in the browser
#   install-autostart     Install supervised login LaunchAgents for core services
#   remove-autostart      Remove the core-service login LaunchAgents
#   start-timers          Load the randomized and unified-hourly LaunchAgents
#   stop-timers           Unload the randomized and unified-hourly LaunchAgents
#   kick-timers           Safely request one immediate tick from each timer
#   help                  Show help
#
# Important architecture:
# - Core services: Ollama, n8n, FastAPI, Streamlit, Telegram listener
# - Search timer: com.aadil.hr-hunter.randomized-sources (every 5 minutes;
#   its own database schedule decides which source is actually due)
# - Queue/dispatch timer: com.aadil.hr-hunter.unified-hourly (hourly)
# - The obsolete aadil_worker_scheduler.py loop is always disabled because
#   app.hunter_worker without dashboard arguments is only an import/self-test.
#

set -u
set -o pipefail
umask 077

VERSION="1.1.0"

PROJECT="${AADIL_HR_HUNTER_PROJECT:-$HOME/Aadil-HR-Hunter}"
APP_DIR="$PROJECT/app"
ENV_FILE="$PROJECT/.env"
PYTHON="$PROJECT/.venv/bin/python"
LEGACY_VENV="$PROJECT/.venv_runtime"

RUNTIME_DIR="${AADIL_HR_HUNTER_RUNTIME:-$HOME/.aadil_hr_hunter_runtime}"
PID_DIR="$RUNTIME_DIR/pids"
LOG_DIR="$RUNTIME_DIR/logs"
LOCK_DIR="$RUNTIME_DIR/controller.lock"

PROJECT_LOG_DIR="$PROJECT/logs"
BACKUP_DIR="$PROJECT/patch_backups/all_in_one_startup_controller"

N8N_DB="$HOME/.n8n/database.sqlite"
HUNTER_DB="$PROJECT/data/hunter.db"

BIN_DIR="$PROJECT/bin"
INSTALLED_CONTROLLER="$BIN_DIR/aadil_hr_hunter_all_in_one.sh"
HOME_WRAPPER="$HOME/start_aadil_hr_hunter_everything.sh"
PRIMARY_HOME_COMMAND="$HOME/aadil-hr-hunter"
FALLBACK_HOME_COMMAND="$HOME/aadil-hr-hunterctl"

if [ -d "$PRIMARY_HOME_COMMAND" ] && [ ! -L "$PRIMARY_HOME_COMMAND" ]; then
  HOME_COMMAND="$FALLBACK_HOME_COMMAND"
else
  HOME_COMMAND="$PRIMARY_HOME_COMMAND"
fi
OLD_FINAL_LAUNCHER="$HOME/start_aadil_hr_hunter_all_final.sh"

TIMER_RANDOM_LABEL="com.aadil.hr-hunter.randomized-sources"
TIMER_HOURLY_LABEL="com.aadil.hr-hunter.unified-hourly"
TIMER_RANDOM_PLIST="$HOME/Library/LaunchAgents/${TIMER_RANDOM_LABEL}.plist"
TIMER_HOURLY_PLIST="$HOME/Library/LaunchAgents/${TIMER_HOURLY_LABEL}.plist"

CORE_N8N_LABEL="com.aadil.hr-hunter.n8n"
CORE_FASTAPI_LABEL="com.aadil.hr-hunter.fastapi"
CORE_STREAMLIT_LABEL="com.aadil.hr-hunter.streamlit"
CORE_TELEGRAM_LABEL="com.aadil.hr-hunter.telegram"

CORE_N8N_PLIST="$HOME/Library/LaunchAgents/${CORE_N8N_LABEL}.plist"
CORE_FASTAPI_PLIST="$HOME/Library/LaunchAgents/${CORE_FASTAPI_LABEL}.plist"
CORE_STREAMLIT_PLIST="$HOME/Library/LaunchAgents/${CORE_STREAMLIT_LABEL}.plist"
CORE_TELEGRAM_PLIST="$HOME/Library/LaunchAgents/${CORE_TELEGRAM_LABEL}.plist"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin"

FORCE=0

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*"
}

ok() {
  printf '✅ %s\n' "$*"
}

warn() {
  printf '⚠️  %s\n' "$*" >&2
}

fail() {
  printf '❌ %s\n' "$*" >&2
}

die() {
  fail "$*"
  exit 1
}

section() {
  printf '\n============================================================\n'
  printf '%s\n' "$1"
  printf '============================================================\n'
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

ensure_dirs() {
  mkdir -p \
    "$RUNTIME_DIR" \
    "$PID_DIR" \
    "$LOG_DIR" \
    "$PROJECT_LOG_DIR" \
    "$BIN_DIR" \
    "$HOME/Library/LaunchAgents" \
    "$BACKUP_DIR"
}

acquire_controller_lock() {
  ensure_dirs

  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    trap 'rm -rf "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT INT TERM
    return 0
  fi

  old_pid=""
  if [ -f "$LOCK_DIR/pid" ]; then
    old_pid="$(tr -cd '0-9' < "$LOCK_DIR/pid" 2>/dev/null || true)"
  fi

  if [ -n "$old_pid" ] && kill -0 "$old_pid" >/dev/null 2>&1; then
    die "Another controller command is running as PID $old_pid."
  fi

  warn "Removing stale controller lock."
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR" || die "Could not acquire controller lock."
  printf '%s\n' "$$" > "$LOCK_DIR/pid"
  trap 'rm -rf "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT INT TERM
}

release_controller_lock() {
  rm -rf "$LOCK_DIR" >/dev/null 2>&1 || true
  trap - EXIT INT TERM
}

preflight() {
  [ -d "$PROJECT" ] || die "Project directory not found: $PROJECT"
  [ -d "$APP_DIR" ] || die "Project app directory not found: $APP_DIR"
  [ -x "$PYTHON" ] || die "Project Python not found: $PYTHON"
  [ -f "$APP_DIR/api.py" ] || die "Missing app/api.py"
  [ -f "$APP_DIR/dashboard.py" ] || die "Missing app/dashboard.py"
  [ -f "$APP_DIR/telegram_listener.py" ] || die "Missing app/telegram_listener.py"
  [ -f "$APP_DIR/randomized_source_runner.py" ] || die "Missing app/randomized_source_runner.py"
  [ -f "$APP_DIR/unified_hourly_coordinator.py" ] || die "Missing app/unified_hourly_coordinator.py"

  command_exists curl || die "curl is required."
  command_exists lsof || die "lsof is required."
  command_exists launchctl || die "launchctl is required."
  command_exists sqlite3 || die "sqlite3 is required."

  "$PYTHON" - <<'PY' >/dev/null 2>&1 || die "Project Python dependencies are incomplete."
import dotenv
import fastapi
import jobspy
import pandas
import pydantic
import requests
import streamlit
import telegram
import uvicorn
PY
}

load_env_safely() {
  [ -f "$ENV_FILE" ] || {
    warn "No .env file found at $ENV_FILE. Using defaults where possible."
    return 0
  }

  env_exports="$(
    "$PYTHON" - "$ENV_FILE" <<'PY'
import re
import shlex
import sys
from dotenv import dotenv_values

path = sys.argv[1]
valid = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

for key, value in dotenv_values(path).items():
    if not key or not valid.fullmatch(key) or value is None:
        continue
    print("export %s=%s" % (key, shlex.quote(str(value))))
PY
  )" || die "Could not parse $ENV_FILE"

  eval "$env_exports"

  N8N_PORT="${N8N_PORT:-5678}"
  FASTAPI_PORT="${FASTAPI_PORT:-8000}"
  STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"

  export N8N_PORT FASTAPI_PORT STREAMLIT_PORT
  export N8N_HOST="${N8N_HOST:-127.0.0.1}"
  export N8N_PROTOCOL="${N8N_PROTOCOL:-http}"
  export N8N_SECURE_COOKIE="${N8N_SECURE_COOKIE:-false}"
  export N8N_BLOCK_ENV_ACCESS_IN_NODE="${N8N_BLOCK_ENV_ACCESS_IN_NODE:-false}"
  export N8N_RUNNERS_HEARTBEAT_INTERVAL="${N8N_RUNNERS_HEARTBEAT_INTERVAL:-120}"
  export N8N_RUNNERS_TASK_TIMEOUT="${N8N_RUNNERS_TASK_TIMEOUT:-300}"
  export N8N_RUNNERS_MAX_OLD_SPACE_SIZE="${N8N_RUNNERS_MAX_OLD_SPACE_SIZE:-4096}"
  export PYTHONUNBUFFERED=1
  export PYTHONPATH="$PROJECT${PYTHONPATH:+:$PYTHONPATH}"
}

http_code() {
  local url="$1"

  curl \
    --silent \
    --show-error \
    --output /dev/null \
    --max-time 3 \
    --write-out '%{http_code}' \
    "$url" 2>/dev/null || printf '000'
}

http_ok() {
  local url="$1"
  local expected="${2:-200}"
  [ "$(http_code "$url")" = "$expected" ]
}

wait_http() {
  local label="$1"
  local url="$2"
  local timeout_seconds="${3:-45}"
  local expected="${4:-200}"
  local elapsed=0
  local code="000"

  while [ "$elapsed" -lt "$timeout_seconds" ]; do
    code="$(http_code "$url")"

    if [ "$code" = "$expected" ]; then
      ok "$label is healthy: $url"
      return 0
    fi

    sleep 1
    elapsed=$((elapsed + 1))
  done

  fail "$label did not become healthy. Last HTTP code: $code"
  return 1
}

port_pid() {
  local port="$1"
  lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
}

pid_command() {
  local pid="$1"
  ps -p "$pid" -o command= 2>/dev/null || true
}

pid_cwd() {
  local pid="$1"
  lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

pid_is_project_process() {
  local pid="$1"
  local expected_pattern="$2"
  local command_text
  local cwd

  [ -n "$pid" ] || return 1
  kill -0 "$pid" >/dev/null 2>&1 || return 1

  command_text="$(pid_command "$pid")"
  printf '%s\n' "$command_text" | grep -Eiq "$expected_pattern" || return 1

  cwd="$(pid_cwd "$pid")"
  [ "$cwd" = "$PROJECT" ] || return 1

  return 0
}

find_project_pid() {
  local pattern="$1"
  local pid
  local pids

  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"

  for pid in $pids; do
    if pid_is_project_process "$pid" "$pattern"; then
      printf '%s\n' "$pid"
      return 0
    fi
  done

  return 1
}

write_pid() {
  local name="$1"
  local pid="$2"

  ensure_dirs

  if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
    printf '%s\n' "$pid" > "$PID_DIR/$name.pid"
  else
    rm -f "$PID_DIR/$name.pid"
  fi
}

remove_stale_pid_files() {
  local file
  local pid

  ensure_dirs

  for file in "$PID_DIR"/*.pid; do
    [ -e "$file" ] || continue

    pid="$(tr -cd '0-9' < "$file" 2>/dev/null || true)"

    if [ -z "$pid" ] || ! kill -0 "$pid" >/dev/null 2>&1; then
      rm -f "$file"
    fi
  done
}

launch_loaded() {
  local label="$1"
  launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1
}

launch_bootstrap() {
  local label="$1"
  local plist="$2"

  if launch_loaded "$label"; then
    return 0
  fi

  launchctl bootstrap "gui/$(id -u)" "$plist" >/dev/null 2>&1 ||
    launchctl load "$plist" >/dev/null 2>&1 ||
    return 1

  launch_loaded "$label"
}

launch_bootout() {
  local label="$1"
  local plist="$2"

  if ! launch_loaded "$label"; then
    return 0
  fi

  launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 ||
    launchctl unload "$plist" >/dev/null 2>&1 ||
    return 1
}

launch_kickstart() {
  local label="$1"
  launchctl kickstart "gui/$(id -u)/$label" >/dev/null 2>&1
}

plist_escape() {
  printf '%s' "$1" |
    sed \
      -e 's/&/\&amp;/g' \
      -e 's/</\&lt;/g' \
      -e 's/>/\&gt;/g' \
      -e 's/"/\&quot;/g' \
      -e "s/'/\&apos;/g"
}

backup_file() {
  local source="$1"
  local stamp
  local target

  [ -e "$source" ] || return 0

  stamp="$(date '+%Y%m%d_%H%M%S')"
  mkdir -p "$BACKUP_DIR"
  target="$BACKUP_DIR/$(basename "$source").$stamp.bak"

  cp -a "$source" "$target" || die "Could not back up $source"
  log "Backup created: $target"
}

write_random_timer_plist() {
  local project_xml
  local python_xml
  local stdout_xml
  local stderr_xml

  project_xml="$(plist_escape "$PROJECT")"
  python_xml="$(plist_escape "$PYTHON")"
  stdout_xml="$(plist_escape "$PROJECT_LOG_DIR/randomized_sources_scheduler.log")"
  stderr_xml="$(plist_escape "$PROJECT_LOG_DIR/randomized_sources_scheduler_error.log")"

  cat > "$TIMER_RANDOM_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$TIMER_RANDOM_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$python_xml</string>
    <string>-u</string>
    <string>-m</string>
    <string>app.randomized_source_runner</string>
    <string>--scheduled</string>
    <string>--quiet-start</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$project_xml</string>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>RunAtLoad</key>
  <false/>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
    <key>PYTHONPATH</key>
    <string>$project_xml</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$stdout_xml</string>
  <key>StandardErrorPath</key>
  <string>$stderr_xml</string>
</dict>
</plist>
EOF

  chmod 0644 "$TIMER_RANDOM_PLIST"
}

write_hourly_timer_plist() {
  local project_xml
  local python_xml
  local stdout_xml
  local stderr_xml

  project_xml="$(plist_escape "$PROJECT")"
  python_xml="$(plist_escape "$PYTHON")"
  stdout_xml="$(plist_escape "$PROJECT_LOG_DIR/unified_hourly_launchd.out.log")"
  stderr_xml="$(plist_escape "$PROJECT_LOG_DIR/unified_hourly_launchd.err.log")"

  cat > "$TIMER_HOURLY_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$TIMER_HOURLY_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$python_xml</string>
    <string>-m</string>
    <string>app.unified_hourly_coordinator</string>
    <string>--skip-workers</string>
    <string>--mode</string>
    <string>production</string>
    <string>--worker-timeout</string>
    <string>420</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$project_xml</string>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>RunAtLoad</key>
  <false/>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
    <key>PYTHONPATH</key>
    <string>$project_xml</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$stdout_xml</string>
  <key>StandardErrorPath</key>
  <string>$stderr_xml</string>
</dict>
</plist>
EOF

  chmod 0644 "$TIMER_HOURLY_PLIST"
}

timer_plist_valid() {
  local plist="$1"
  local module="$2"
  local interval="$3"

  [ -f "$plist" ] || return 1
  plutil -lint "$plist" >/dev/null 2>&1 || return 1
  grep -Fq "$PYTHON" "$plist" || return 1
  grep -Fq "$module" "$plist" || return 1
  grep -Fq "<integer>$interval</integer>" "$plist" || return 1
  return 0
}

ensure_timer_plists() {
  ensure_dirs

  if ! timer_plist_valid "$TIMER_RANDOM_PLIST" "app.randomized_source_runner" "300"; then
    [ -e "$TIMER_RANDOM_PLIST" ] && backup_file "$TIMER_RANDOM_PLIST"
    write_random_timer_plist
    ok "Installed randomized-source timer plist."
  fi

  if ! timer_plist_valid "$TIMER_HOURLY_PLIST" "app.unified_hourly_coordinator" "3600"; then
    [ -e "$TIMER_HOURLY_PLIST" ] && backup_file "$TIMER_HOURLY_PLIST"
    write_hourly_timer_plist
    ok "Installed unified-hourly timer plist."
  fi
}

start_timers() {
  ensure_timer_plists

  if launch_bootstrap "$TIMER_RANDOM_LABEL" "$TIMER_RANDOM_PLIST"; then
    ok "Randomized-source timer is loaded."
  else
    fail "Could not load randomized-source timer."
    return 1
  fi

  if launch_bootstrap "$TIMER_HOURLY_LABEL" "$TIMER_HOURLY_PLIST"; then
    ok "Unified-hourly timer is loaded."
  else
    fail "Could not load unified-hourly timer."
    return 1
  fi

  return 0
}

stop_timers() {
  local rc=0

  if launch_bootout "$TIMER_RANDOM_LABEL" "$TIMER_RANDOM_PLIST"; then
    ok "Randomized-source timer is unloaded."
  else
    fail "Could not unload randomized-source timer."
    rc=1
  fi

  if launch_bootout "$TIMER_HOURLY_LABEL" "$TIMER_HOURLY_PLIST"; then
    ok "Unified-hourly timer is unloaded."
  else
    fail "Could not unload unified-hourly timer."
    rc=1
  fi

  return "$rc"
}

kick_timers() {
  start_timers || return 1

  if find_project_pid "app\.randomized_source_runner" >/dev/null 2>&1; then
    warn "Randomized source runner is already active; not forcing a second copy."
  else
    launch_kickstart "$TIMER_RANDOM_LABEL" &&
      ok "Requested one randomized-source tick." ||
      warn "Could not kick randomized-source timer."
  fi

  if find_project_pid "app\.unified_hourly_coordinator" >/dev/null 2>&1; then
    warn "Unified hourly coordinator is already active; not forcing a second copy."
  else
    launch_kickstart "$TIMER_HOURLY_LABEL" &&
      ok "Requested one unified-hourly tick." ||
      warn "Could not kick unified-hourly timer."
  fi
}

active_n8n_executions() {
  [ -f "$N8N_DB" ] || {
    printf '0\n'
    return 0
  }

  sqlite3 -readonly "$N8N_DB" "
    SELECT COUNT(*)
    FROM execution_entity
    WHERE lower(COALESCE(status,'')) IN ('new','running','waiting')
      AND stoppedAt IS NULL;
  " 2>/dev/null || printf '0\n'
}

assert_n8n_safe_to_stop() {
  local active
  active="$(active_n8n_executions)"
  active="${active:-0}"

  case "$active" in
    ''|*[!0-9]*)
      active=0
      ;;
  esac

  if [ "$active" -gt 0 ] && [ "$FORCE" -ne 1 ]; then
    die "Refusing to stop n8n: $active active execution(s). Re-run with --force only when intentional."
  fi
}

disable_legacy_worker_scheduler() {
  local pids
  local pid
  local command_text

  pids="$(pgrep -f 'aadil_worker_scheduler\.py' 2>/dev/null || true)"

  if [ -z "$pids" ]; then
    rm -f "$PID_DIR/hunter_worker_scheduler.pid"
    return 0
  fi

  warn "Disabling obsolete 30-minute hunter_worker scheduler."

  for pid in $pids; do
    command_text="$(pid_command "$pid")"

    if printf '%s\n' "$command_text" |
      grep -Fq "$RUNTIME_DIR/aadil_worker_scheduler.py"; then
      kill -TERM "$pid" >/dev/null 2>&1 || true
    fi
  done

  sleep 2

  for pid in $pids; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      command_text="$(pid_command "$pid")"

      if printf '%s\n' "$command_text" |
        grep -Fq "$RUNTIME_DIR/aadil_worker_scheduler.py"; then
        kill -KILL "$pid" >/dev/null 2>&1 || true
      fi
    fi
  done

  rm -f "$PID_DIR/hunter_worker_scheduler.pid"
  ok "Obsolete scheduler disabled. Real source timers remain managed by launchd."
}

repair_legacy_runtime() {
  local real_project_python
  local real_legacy_python
  local stamp
  local backup

  [ -x "$PYTHON" ] || die "Cannot repair legacy runtime without $PYTHON"

  real_project_python="$("$PYTHON" -c 'import os,sys; print(os.path.realpath(sys.executable))')"

  if [ -x "$LEGACY_VENV/bin/python" ]; then
    real_legacy_python="$("$LEGACY_VENV/bin/python" -c 'import os,sys; print(os.path.realpath(sys.executable))' 2>/dev/null || true)"
  else
    real_legacy_python=""
  fi

  if [ "$real_legacy_python" = "$real_project_python" ]; then
    ok "Legacy runtime compatibility path already resolves to project Python."
    return 0
  fi

  if [ -L "$LEGACY_VENV" ]; then
    rm -f "$LEGACY_VENV"
  elif [ -e "$LEGACY_VENV" ]; then
    stamp="$(date '+%Y%m%d_%H%M%S')"
    backup="${LEGACY_VENV}.legacy_${stamp}"
    mv "$LEGACY_VENV" "$backup" ||
      die "Could not archive legacy runtime."
    log "Archived broken legacy runtime to: $backup"
  fi

  ln -s "$PROJECT/.venv" "$LEGACY_VENV" ||
    die "Could not create compatibility symlink: $LEGACY_VENV"

  ok ".venv_runtime now safely resolves to the Python 3.12 project environment."
}

service_is_healthy() {
  local service="$1"

  case "$service" in
    ollama)
      http_ok "http://127.0.0.1:11434/api/tags"
      ;;
    n8n)
      http_ok "http://127.0.0.1:${N8N_PORT}/healthz"
      ;;
    fastapi)
      http_ok "http://127.0.0.1:${FASTAPI_PORT}/health"
      ;;
    streamlit)
      http_ok "http://127.0.0.1:${STREAMLIT_PORT}/_stcore/health"
      ;;
    telegram)
      find_project_pid "app\.telegram_listener" >/dev/null 2>&1
      ;;
    *)
      return 1
      ;;
  esac
}

service_pid() {
  local service="$1"
  local pid=""

  case "$service" in
    ollama)
      pid="$(port_pid 11434)"
      ;;
    n8n)
      pid="$(port_pid "$N8N_PORT")"
      ;;
    fastapi)
      pid="$(port_pid "$FASTAPI_PORT")"
      ;;
    streamlit)
      pid="$(port_pid "$STREAMLIT_PORT")"
      ;;
    telegram)
      pid="$(find_project_pid "app\.telegram_listener" 2>/dev/null || true)"
      ;;
  esac

  printf '%s\n' "$pid"
}

start_ollama() {
  local pid

  if service_is_healthy ollama; then
    pid="$(service_pid ollama)"
    write_pid ollama "$pid"
    ok "Ollama already healthy on port 11434."
    return 0
  fi

  if [ -d "/Applications/Ollama.app" ]; then
    log "Opening Ollama.app."
    open -gja "/Applications/Ollama.app" >/dev/null 2>&1 || true
  elif command_exists ollama; then
    log "Starting ollama serve."
    (
      cd "$PROJECT" || exit 1
      nohup "$(command -v ollama)" serve \
        >> "$PROJECT_LOG_DIR/ollama_controller.log" 2>&1 < /dev/null &
      printf '%s\n' "$!" > "$PID_DIR/ollama.pid"
    )
  else
    die "Ollama is not installed."
  fi

  wait_http "Ollama" "http://127.0.0.1:11434/api/tags" 45 ||
    return 1

  pid="$(service_pid ollama)"
  write_pid ollama "$pid"

  if command_exists ollama; then
    if ollama list 2>/dev/null |
      awk 'NR > 1 {print $1}' |
      grep -Fxq 'gemma3:4b'; then
      ok "Ollama model gemma3:4b is installed."
    else
      warn "Ollama is running, but gemma3:4b is not installed."
    fi
  fi
}

start_n8n_manual() {
  local n8n_bin

  n8n_bin="$(command -v n8n 2>/dev/null || true)"
  [ -n "$n8n_bin" ] || die "n8n executable not found."

  log "Starting n8n on port $N8N_PORT."

  (
    cd "$PROJECT" || exit 1
    nohup "$n8n_bin" start \
      >> "$PROJECT_LOG_DIR/n8n_controller.log" 2>&1 < /dev/null &
    printf '%s\n' "$!" > "$PID_DIR/n8n.pid"
  )
}

start_fastapi_manual() {
  log "Starting FastAPI bridge on port $FASTAPI_PORT."

  (
    cd "$PROJECT" || exit 1
    nohup "$PYTHON" -m uvicorn app.api:app \
      --host 127.0.0.1 \
      --port "$FASTAPI_PORT" \
      >> "$PROJECT_LOG_DIR/fastapi_controller.log" 2>&1 < /dev/null &
    printf '%s\n' "$!" > "$PID_DIR/fastapi.pid"
  )
}

start_streamlit_manual() {
  log "Starting Streamlit dashboard on port $STREAMLIT_PORT."

  (
    cd "$PROJECT" || exit 1
    nohup "$PYTHON" -m streamlit run "$APP_DIR/dashboard.py" \
      --server.address 127.0.0.1 \
      --server.port "$STREAMLIT_PORT" \
      --server.headless true \
      >> "$PROJECT_LOG_DIR/streamlit_controller.log" 2>&1 < /dev/null &
    printf '%s\n' "$!" > "$PID_DIR/streamlit.pid"
  )
}

start_telegram_manual() {
  log "Starting Telegram listener."

  (
    cd "$PROJECT" || exit 1
    nohup "$PYTHON" -u -m app.telegram_listener \
      >> "$PROJECT_LOG_DIR/telegram_controller.log" 2>&1 < /dev/null &
    printf '%s\n' "$!" > "$PID_DIR/telegram.pid"
  )
}

core_label_for_service() {
  case "$1" in
    n8n) printf '%s\n' "$CORE_N8N_LABEL" ;;
    fastapi) printf '%s\n' "$CORE_FASTAPI_LABEL" ;;
    streamlit) printf '%s\n' "$CORE_STREAMLIT_LABEL" ;;
    telegram) printf '%s\n' "$CORE_TELEGRAM_LABEL" ;;
    *) return 1 ;;
  esac
}

core_plist_for_service() {
  case "$1" in
    n8n) printf '%s\n' "$CORE_N8N_PLIST" ;;
    fastapi) printf '%s\n' "$CORE_FASTAPI_PLIST" ;;
    streamlit) printf '%s\n' "$CORE_STREAMLIT_PLIST" ;;
    telegram) printf '%s\n' "$CORE_TELEGRAM_PLIST" ;;
    *) return 1 ;;
  esac
}

start_core_service() {
  local service="$1"
  local label
  local plist
  local pid
  local port
  local command_text

  if service_is_healthy "$service"; then
    pid="$(service_pid "$service")"
    write_pid "$service" "$pid"
    ok "$service already healthy${pid:+ (PID $pid)}."
    return 0
  fi

  case "$service" in
    n8n) port="$N8N_PORT" ;;
    fastapi) port="$FASTAPI_PORT" ;;
    streamlit) port="$STREAMLIT_PORT" ;;
    *) port="" ;;
  esac

  if [ -n "$port" ]; then
    pid="$(port_pid "$port")"

    if [ -n "$pid" ]; then
      command_text="$(pid_command "$pid")"
      die "Port $port is occupied by an unhealthy process: PID $pid — $command_text"
    fi
  fi

  label="$(core_label_for_service "$service")"
  plist="$(core_plist_for_service "$service")"

  if [ -f "$plist" ]; then
    log "Starting $service through LaunchAgent $label."

    launch_bootstrap "$label" "$plist" ||
      die "Could not load $label"

    launch_kickstart "$label" >/dev/null 2>&1 || true
  else
    case "$service" in
      n8n) start_n8n_manual ;;
      fastapi) start_fastapi_manual ;;
      streamlit) start_streamlit_manual ;;
      telegram) start_telegram_manual ;;
      *) die "Unknown service: $service" ;;
    esac
  fi

  case "$service" in
    n8n)
      wait_http "n8n" "http://127.0.0.1:${N8N_PORT}/healthz" 75 ||
        return 1
      ;;
    fastapi)
      wait_http "FastAPI" "http://127.0.0.1:${FASTAPI_PORT}/health" 45 ||
        return 1
      ;;
    streamlit)
      wait_http "Streamlit" "http://127.0.0.1:${STREAMLIT_PORT}/_stcore/health" 60 ||
        return 1
      ;;
    telegram)
      elapsed=0
      while [ "$elapsed" -lt 30 ]; do
        if service_is_healthy telegram; then
          ok "Telegram listener is running."
          break
        fi
        sleep 1
        elapsed=$((elapsed + 1))
      done

      service_is_healthy telegram || {
        fail "Telegram listener did not stay running."
        return 1
      }
      ;;
  esac

  pid="$(service_pid "$service")"
  write_pid "$service" "$pid"
}

start_all() {
  local rc=0

  acquire_controller_lock
  preflight
  load_env_safely
  ensure_dirs
  remove_stale_pid_files
  disable_legacy_worker_scheduler

  section "STARTING AADIL HR HUNTER"

  start_ollama || rc=1
  start_core_service n8n || rc=1
  start_core_service fastapi || rc=1
  start_core_service streamlit || rc=1
  start_core_service telegram || rc=1
  start_timers || rc=1

  reconcile_pids

  section "FINAL STATUS"
  status_report

  if [ "$rc" -ne 0 ]; then
    fail "One or more components failed to start."
    return "$rc"
  fi

  if strict_health_check >/dev/null 2>&1; then
    ok "FULL STACK READY"
    printf '\n'
    printf 'n8n:       http://127.0.0.1:%s\n' "$N8N_PORT"
    printf 'Dashboard: http://127.0.0.1:%s\n' "$STREAMLIT_PORT"
    printf 'FastAPI:   http://127.0.0.1:%s/health\n' "$FASTAPI_PORT"
  else
    fail "Startup finished, but strict health verification failed."
    return 1
  fi
}

stop_pid_if_owned() {
  local pid="$1"
  local expected_pattern="$2"
  local label="$3"
  local command_text
  local elapsed=0

  [ -n "$pid" ] || return 0
  kill -0 "$pid" >/dev/null 2>&1 || return 0

  command_text="$(pid_command "$pid")"
  printf '%s\n' "$command_text" | grep -Eiq "$expected_pattern" || {
    warn "Refusing to stop unrecognized $label PID $pid: $command_text"
    return 1
  }

  log "Stopping $label PID $pid."
  kill -TERM "$pid" >/dev/null 2>&1 || true

  while [ "$elapsed" -lt 15 ]; do
    kill -0 "$pid" >/dev/null 2>&1 || return 0
    sleep 1
    elapsed=$((elapsed + 1))
  done

  warn "$label did not stop after SIGTERM; sending SIGKILL."
  kill -KILL "$pid" >/dev/null 2>&1 || true
}

stop_core_service() {
  local service="$1"
  local label
  local plist
  local pid
  local pattern

  label="$(core_label_for_service "$service")"
  plist="$(core_plist_for_service "$service")"

  if launch_loaded "$label"; then
    launch_bootout "$label" "$plist" ||
      warn "Could not unload $label"
  fi

  pid="$(service_pid "$service")"

  case "$service" in
    n8n) pattern='(^|/|[[:space:]])n8n([[:space:]]|$).*start|/usr/local/bin/n8n start' ;;
    fastapi) pattern='uvicorn app\.api:app' ;;
    streamlit) pattern='streamlit run .*/app/dashboard\.py' ;;
    telegram) pattern='app\.telegram_listener' ;;
    *) return 1 ;;
  esac

  stop_pid_if_owned "$pid" "$pattern" "$service" || true
  rm -f "$PID_DIR/$service.pid"
}

stop_ollama() {
  local pid

  pid="$(service_pid ollama)"

  if [ -d "/Applications/Ollama.app" ]; then
    osascript -e 'tell application "Ollama" to quit' >/dev/null 2>&1 || true
    sleep 2
  fi

  if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
    stop_pid_if_owned "$pid" 'ollama serve|/Applications/Ollama\.app' "Ollama" || true
  fi

  rm -f "$PID_DIR/ollama.pid"
}

stop_all() {
  acquire_controller_lock
  preflight
  load_env_safely
  assert_n8n_safe_to_stop

  section "STOPPING AADIL HR HUNTER"

  stop_timers || true
  disable_legacy_worker_scheduler

  stop_core_service telegram
  stop_core_service streamlit
  stop_core_service fastapi
  stop_core_service n8n
  stop_ollama

  remove_stale_pid_files

  ok "All managed components have been stopped."
}

restart_all() {
  acquire_controller_lock
  preflight
  load_env_safely
  assert_n8n_safe_to_stop
  release_controller_lock

  if [ "$FORCE" -eq 1 ]; then
    "$0" stop --force || return 1
  else
    "$0" stop || return 1
  fi

  sleep 2
  "$0" start
}

reconcile_pids() {
  local service
  local pid

  for service in ollama n8n fastapi streamlit telegram; do
    pid="$(service_pid "$service")"

    if service_is_healthy "$service"; then
      write_pid "$service" "$pid"
    else
      rm -f "$PID_DIR/$service.pid"
    fi
  done

  remove_stale_pid_files
}

print_service_status() {
  local label="$1"
  local service="$2"
  local url="${3:-}"
  local pid
  local command_text

  pid="$(service_pid "$service")"

  if service_is_healthy "$service"; then
    if [ -n "$pid" ]; then
      command_text="$(pid_command "$pid")"
      printf '✅ %-22s RUNNING  PID %-7s %s\n' "$label" "$pid" "$url"
      printf '   %s\n' "$command_text"
    else
      printf '✅ %-22s RUNNING            %s\n' "$label" "$url"
    fi
  else
    printf '❌ %-22s NOT HEALTHY        %s\n' "$label" "$url"
  fi
}

print_timer_status() {
  local label="$1"
  local friendly="$2"
  local line

  if launch_loaded "$label"; then
    line="$(
      launchctl print "gui/$(id -u)/$label" 2>/dev/null |
        grep -E '^[[:space:]]*(state|pid|runs|last exit code|run interval) =' |
        sed 's/^[[:space:]]*//' |
        tr '\n' '; ' |
        sed 's/; $//'
    )"
    printf '✅ %-22s LOADED   %s\n' "$friendly" "$line"
  else
    printf '❌ %-22s NOT LOADED\n' "$friendly"
  fi
}

database_status() {
  local db="$1"

  if [ ! -f "$db" ]; then
    printf 'missing'
    return 1
  fi

  DB_PATH="$db" "$PYTHON" - <<'PY'
import os
import sqlite3
import time
from pathlib import Path

path = Path(os.environ["DB_PATH"])
uri = f"file:{path}?mode=ro"

for attempt in range(4):
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            value = str(row[0]) if row else "unknown"
            print(value)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                print("busy (active writer)")
            else:
                print(f"error: {exc}")
                raise SystemExit(1)
        finally:
            con.close()
        raise SystemExit(0)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            time.sleep(1)
            continue
        print(f"error: {exc}")
        raise SystemExit(1)
    except sqlite3.DatabaseError as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

print("busy (active writer)")
PY
}

database_is_healthy() {
  local value
  value="$(database_status "$1" 2>/dev/null || true)"

  case "$value" in
    ok|"busy (active writer)")
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

pending_queue_count() {
  local db="$1"

  [ -f "$db" ] || {
    printf 'missing'
    return 1
  }

  DB_PATH="$db" "$PYTHON" - <<'PY'
import os
import sqlite3
import time
from pathlib import Path

path = Path(os.environ["DB_PATH"])
uri = f"file:{path}?mode=ro"

for attempt in range(4):
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
        con.execute("PRAGMA busy_timeout=5000")
        columns = {
            row[1]
            for row in con.execute('PRAGMA table_info("n8n_dispatch_queue")')
        }
        if not columns:
            print("table missing")
            raise SystemExit(1)
        status_column = "queue_status" if "queue_status" in columns else (
            "status" if "status" in columns else None
        )
        if not status_column:
            print("status column missing")
            raise SystemExit(1)
        sql = (
            f'SELECT COUNT(*) FROM "n8n_dispatch_queue" '
            f'WHERE lower(COALESCE("{status_column}",\'\')) '
            "IN ('pending','queued','ready','retry','reserved','accepted')"
        )
        count = con.execute(sql).fetchone()[0]
        con.close()
        print(int(count))
        raise SystemExit(0)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            time.sleep(1)
            continue
        print(f"unavailable: {exc}")
        raise SystemExit(1)

print("busy")
PY
}

status_report() {
  preflight
  load_env_safely
  reconcile_pids

  section "AADIL HR HUNTER STATUS"

  print_service_status "Ollama" ollama "http://127.0.0.1:11434"
  print_service_status "n8n" n8n "http://127.0.0.1:${N8N_PORT}"
  print_service_status "FastAPI" fastapi "http://127.0.0.1:${FASTAPI_PORT}/health"
  print_service_status "Streamlit" streamlit "http://127.0.0.1:${STREAMLIT_PORT}"
  print_service_status "Telegram listener" telegram ""

  printf '\n'
  print_timer_status "$TIMER_RANDOM_LABEL" "Randomized sources"
  print_timer_status "$TIMER_HOURLY_LABEL" "Unified hourly"

  printf '\n'

  legacy_count="$(pgrep -f 'aadil_worker_scheduler\.py' 2>/dev/null | wc -l | tr -d ' ')"
  if [ "${legacy_count:-0}" -eq 0 ]; then
    printf '✅ %-22s DISABLED\n' "Legacy worker loop"
  else
    printf '❌ %-22s %s PROCESS(ES)\n' "Legacy worker loop" "$legacy_count"
  fi

  printf '\n'
  printf 'n8n active executions: %s\n' "$(active_n8n_executions)"
  printf 'n8n DB quick_check:     %s\n' "$(database_status "$N8N_DB" || true)"
  printf 'Hunter DB quick_check:  %s\n' "$(database_status "$HUNTER_DB" || true)"

  if [ -f "$HUNTER_DB" ]; then
    printf 'Pending n8n queue:       %s\n' "$(pending_queue_count "$HUNTER_DB" 2>/dev/null || true)"
  fi

  printf '\n'
  printf 'Controller: %s\n' "$INSTALLED_CONTROLLER"
  printf 'Home command: %s\n' "$HOME_COMMAND"
  printf 'Project:    %s\n' "$PROJECT"
  printf 'Python:     %s\n' "$PYTHON"
  printf 'Runtime:    %s\n' "$RUNTIME_DIR"
}

strict_health_check() {
  local rc=0

  preflight
  load_env_safely

  service_is_healthy ollama || rc=1
  service_is_healthy n8n || rc=1
  service_is_healthy fastapi || rc=1
  service_is_healthy streamlit || rc=1
  service_is_healthy telegram || rc=1
  launch_loaded "$TIMER_RANDOM_LABEL" || rc=1
  launch_loaded "$TIMER_HOURLY_LABEL" || rc=1

  if pgrep -f 'aadil_worker_scheduler\.py' >/dev/null 2>&1; then
    rc=1
  fi

  database_is_healthy "$N8N_DB" || rc=1
  database_is_healthy "$HUNTER_DB" || rc=1

  return "$rc"
}

health_report() {
  status_report

  if strict_health_check; then
    ok "STRICT HEALTH CHECK PASSED"
    return 0
  fi

  fail "STRICT HEALTH CHECK FAILED"
  return 1
}

doctor() {
  local rc=0
  local file

  section "AADIL HR HUNTER DOCTOR"

  preflight
  load_env_safely

  printf 'Project: %s\n' "$PROJECT"
  printf 'Python:  %s\n' "$("$PYTHON" --version 2>&1)"
  printf 'n8n:     %s\n' "$(n8n --version 2>/dev/null || printf 'not found')"
  printf 'Ollama:  %s\n' "$(ollama --version 2>/dev/null || printf 'not found')"

  printf '\nPython imports:\n'

  "$PYTHON" - <<'PY' || rc=1
import importlib.util

modules = [
    "dotenv",
    "telegram",
    "requests",
    "fastapi",
    "uvicorn",
    "streamlit",
    "pydantic",
    "jobspy",
    "pandas",
]

for name in modules:
    found = importlib.util.find_spec(name) is not None
    print(("OK      " if found else "MISSING ") + name)
    if not found:
        raise SystemExit(1)
PY

  printf '\nStatic compile:\n'

  for file in \
    "$APP_DIR/api.py" \
    "$APP_DIR/dashboard.py" \
    "$APP_DIR/telegram_listener.py" \
    "$APP_DIR/hunter_worker.py" \
    "$APP_DIR/randomized_source_runner.py" \
    "$APP_DIR/unified_hourly_coordinator.py"
  do
    if "$PYTHON" -m py_compile "$file" 2>/dev/null; then
      printf 'OK      %s\n' "$file"
    else
      printf 'FAILED  %s\n' "$file"
      rc=1
    fi
  done

  printf '\nConfiguration:\n'
  printf 'N8N_PORT=%s\n' "$N8N_PORT"
  printf 'FASTAPI_PORT=%s\n' "$FASTAPI_PORT"
  printf 'STREAMLIT_PORT=%s\n' "$STREAMLIT_PORT"
  printf 'N8N_BLOCK_ENV_ACCESS_IN_NODE=%s\n' "$N8N_BLOCK_ENV_ACCESS_IN_NODE"

  ensure_timer_plists

  printf '\nLaunchAgent plists:\n'
  plutil -lint "$TIMER_RANDOM_PLIST" || rc=1
  plutil -lint "$TIMER_HOURLY_PLIST" || rc=1

  printf '\nDatabases:\n'
  printf 'n8n:    %s\n' "$(database_status "$N8N_DB" || true)"
  printf 'Hunter: %s\n' "$(database_status "$HUNTER_DB" || true)"

  database_is_healthy "$N8N_DB" || rc=1
  database_is_healthy "$HUNTER_DB" || rc=1

  printf '\nActive n8n executions: %s\n' "$(active_n8n_executions)"

  if pgrep -f 'aadil_worker_scheduler\.py' >/dev/null 2>&1; then
    fail "Obsolete hunter_worker scheduler is still running."
    rc=1
  else
    ok "Obsolete hunter_worker scheduler is not running."
  fi

  printf '\nDisk:\n'
  df -h "$PROJECT" "$HOME/.n8n" 2>/dev/null || true

  return "$rc"
}

network_report() {
  local rc=0
  local host

  section "SOURCE NETWORK AND DNS CHECK"

  for host in \
    api.smartrecruiters.com \
    api.ashbyhq.com \
    www.comeet.co \
    weworkremotely.com \
    data.cityofnewyork.us \
    remoteok.com \
    api.telegram.org \
    www.dice.com
  do
    if HOST_TO_CHECK="$host" "$PYTHON" - <<'PY' >/dev/null 2>&1
import os
import socket
socket.getaddrinfo(os.environ["HOST_TO_CHECK"], 443)
PY
    then
      printf '✅ %-32s DNS resolved\n' "$host"
    else
      printf '❌ %-32s DNS FAILED\n' "$host"
      rc=1
    fi
  done

  printf '\n'
  code="$(http_code "https://api.telegram.org")"
  if [ "$code" != "000" ]; then
    ok "Outbound HTTPS is reachable (Telegram HTTP $code)."
  else
    fail "Outbound HTTPS is not reachable."
    rc=1
  fi

  if [ "$rc" -eq 0 ]; then
    ok "DNS/network check passed. Adapter failures can clear on their next successful scheduled run."
  else
    fail "DNS/network check failed. The adapter failures are currently infrastructure/network failures, not nine independent code defects."
  fi

  return "$rc"
}

log_path_for_service() {
  case "$1" in
    n8n)
      newest_log_matching 'n8n*log'
      ;;
    fastapi)
      newest_log_matching 'fastapi*log'
      ;;
    streamlit)
      newest_log_matching 'streamlit*log'
      ;;
    telegram)
      newest_log_matching 'telegram*log'
      ;;
    randomized)
      printf '%s\n' "$PROJECT_LOG_DIR/randomized_sources_scheduler.log"
      ;;
    randomized-error)
      printf '%s\n' "$PROJECT_LOG_DIR/randomized_sources_scheduler_error.log"
      ;;
    hourly)
      printf '%s\n' "$PROJECT_LOG_DIR/unified_hourly_launchd.out.log"
      ;;
    hourly-error)
      printf '%s\n' "$PROJECT_LOG_DIR/unified_hourly_launchd.err.log"
      ;;
    ollama)
      newest_log_matching 'ollama*log'
      ;;
    *)
      return 1
      ;;
  esac
}

newest_log_matching() {
  local pattern="$1"
  local result

  result="$(
    find "$PROJECT_LOG_DIR" "$LOG_DIR" \
      -maxdepth 1 \
      -type f \
      -name "$pattern" \
      -print0 2>/dev/null |
      xargs -0 ls -t 2>/dev/null |
      head -n 1
  )"

  printf '%s\n' "$result"
}

logs_command() {
  local service="${1:-}"
  local path

  if [ -z "$service" ]; then
    printf 'Available log names:\n'
    printf '  n8n fastapi streamlit telegram ollama\n'
    printf '  randomized randomized-error hourly hourly-error\n\n'
    find "$PROJECT_LOG_DIR" "$LOG_DIR" \
      -maxdepth 1 \
      -type f \
      -name '*.log' \
      -print 2>/dev/null |
      sort
    return 0
  fi

  path="$(log_path_for_service "$service" 2>/dev/null || true)"

  [ -n "$path" ] || die "Unknown log service: $service"
  [ -f "$path" ] || die "Log file not found for $service"

  log "Following $path"
  tail -n 200 -F "$path"
}

open_urls() {
  preflight
  load_env_safely

  open "http://127.0.0.1:${N8N_PORT}" >/dev/null 2>&1 || true
  open "http://127.0.0.1:${STREAMLIT_PORT}" >/dev/null 2>&1 || true

  ok "Opened n8n and the Streamlit dashboard."
}

service_run() {
  local service="$1"

  preflight
  load_env_safely
  cd "$PROJECT" || exit 1

  case "$service" in
    n8n)
      exec "$PYTHON" "$PROJECT/scripts/start_n8n_aadil_env.py"
      ;;
    fastapi)
      exec "$PYTHON" -m uvicorn app.api:app \
        --host 127.0.0.1 \
        --port "$FASTAPI_PORT"
      ;;
    streamlit)
      exec "$PYTHON" -m streamlit run "$APP_DIR/dashboard.py" \
        --server.address 127.0.0.1 \
        --server.port "$STREAMLIT_PORT" \
        --server.headless true
      ;;
    telegram)
      exec "$PYTHON" -u -m app.telegram_listener
      ;;
    *)
      die "Unknown supervised service: $service"
      ;;
  esac
}

write_core_plist() {
  local label="$1"
  local service="$2"
  local plist="$3"
  local controller_xml
  local project_xml
  local stdout_xml
  local stderr_xml

  controller_xml="$(plist_escape "$INSTALLED_CONTROLLER")"
  project_xml="$(plist_escape "$PROJECT")"
  stdout_xml="$(plist_escape "$PROJECT_LOG_DIR/${service}_launchd.out.log")"
  stderr_xml="$(plist_escape "$PROJECT_LOG_DIR/${service}_launchd.err.log")"

  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$controller_xml</string>
    <string>service-run</string>
    <string>$service</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$project_xml</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$(plist_escape "$HOME")</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$stdout_xml</string>
  <key>StandardErrorPath</key>
  <string>$stderr_xml</string>
</dict>
</plist>
EOF

  chmod 0644 "$plist"
  plutil -lint "$plist" >/dev/null ||
    die "Generated invalid plist: $plist"
}

install_autostart() {
  local active

  acquire_controller_lock
  preflight
  load_env_safely
  ensure_dirs

  [ -x "$INSTALLED_CONTROLLER" ] ||
    die "Run '$0 install' before install-autostart."

  active="$(active_n8n_executions)"
  active="${active:-0}"

  if [ "$active" -gt 0 ] && [ "$FORCE" -ne 1 ]; then
    die "Cannot migrate n8n to LaunchAgent while $active execution(s) are active."
  fi

  section "INSTALLING CORE LOGIN AUTOSTART"

  stop_core_service telegram
  stop_core_service streamlit
  stop_core_service fastapi
  stop_core_service n8n

  for plist in \
    "$CORE_N8N_PLIST" \
    "$CORE_FASTAPI_PLIST" \
    "$CORE_STREAMLIT_PLIST" \
    "$CORE_TELEGRAM_PLIST"
  do
    [ -e "$plist" ] && backup_file "$plist"
  done

  write_core_plist "$CORE_N8N_LABEL" n8n "$CORE_N8N_PLIST"
  write_core_plist "$CORE_FASTAPI_LABEL" fastapi "$CORE_FASTAPI_PLIST"
  write_core_plist "$CORE_STREAMLIT_LABEL" streamlit "$CORE_STREAMLIT_PLIST"
  write_core_plist "$CORE_TELEGRAM_LABEL" telegram "$CORE_TELEGRAM_PLIST"

  launch_bootstrap "$CORE_N8N_LABEL" "$CORE_N8N_PLIST" ||
    die "Could not load $CORE_N8N_LABEL"
  launch_bootstrap "$CORE_FASTAPI_LABEL" "$CORE_FASTAPI_PLIST" ||
    die "Could not load $CORE_FASTAPI_LABEL"
  launch_bootstrap "$CORE_STREAMLIT_LABEL" "$CORE_STREAMLIT_PLIST" ||
    die "Could not load $CORE_STREAMLIT_LABEL"
  launch_bootstrap "$CORE_TELEGRAM_LABEL" "$CORE_TELEGRAM_PLIST" ||
    die "Could not load $CORE_TELEGRAM_LABEL"

  wait_http "n8n" "http://127.0.0.1:${N8N_PORT}/healthz" 75 ||
    return 1
  wait_http "FastAPI" "http://127.0.0.1:${FASTAPI_PORT}/health" 45 ||
    return 1
  wait_http "Streamlit" "http://127.0.0.1:${STREAMLIT_PORT}/_stcore/health" 60 ||
    return 1

  elapsed=0
  while [ "$elapsed" -lt 30 ]; do
    service_is_healthy telegram && break
    sleep 1
    elapsed=$((elapsed + 1))
  done

  service_is_healthy telegram ||
    die "Telegram LaunchAgent did not stay running."

  start_timers
  reconcile_pids
  ok "Core login autostart installed and running."
}

remove_autostart() {
  acquire_controller_lock
  preflight
  load_env_safely
  assert_n8n_safe_to_stop

  section "REMOVING CORE LOGIN AUTOSTART"

  launch_bootout "$CORE_TELEGRAM_LABEL" "$CORE_TELEGRAM_PLIST" || true
  launch_bootout "$CORE_STREAMLIT_LABEL" "$CORE_STREAMLIT_PLIST" || true
  launch_bootout "$CORE_FASTAPI_LABEL" "$CORE_FASTAPI_PLIST" || true
  launch_bootout "$CORE_N8N_LABEL" "$CORE_N8N_PLIST" || true

  rm -f \
    "$CORE_N8N_PLIST" \
    "$CORE_FASTAPI_PLIST" \
    "$CORE_STREAMLIT_PLIST" \
    "$CORE_TELEGRAM_PLIST"

  ok "Core login autostart removed."
  log "Run '$INSTALLED_CONTROLLER start' to start the core services manually."
}

install_compatibility_wrapper() {
  local stamp
  local backup

  cat > "$HOME_WRAPPER" <<EOF
#!/usr/bin/env bash
exec "$INSTALLED_CONTROLLER" start "\$@"
EOF
  chmod 0755 "$HOME_WRAPPER"

  if [ -d "$PRIMARY_HOME_COMMAND" ] && [ ! -L "$PRIMARY_HOME_COMMAND" ]; then
    HOME_COMMAND="$FALLBACK_HOME_COMMAND"
    warn "$PRIMARY_HOME_COMMAND is an existing directory; using $HOME_COMMAND as the command."
  else
    HOME_COMMAND="$PRIMARY_HOME_COMMAND"
  fi

  if [ -e "$HOME_COMMAND" ] || [ -L "$HOME_COMMAND" ]; then
    if [ -d "$HOME_COMMAND" ] && [ ! -L "$HOME_COMMAND" ]; then
      die "Refusing to replace directory: $HOME_COMMAND"
    fi
    [ ! -L "$HOME_COMMAND" ] && backup_file "$HOME_COMMAND"
    rm -f "$HOME_COMMAND"
  fi

  ln -s "$INSTALLED_CONTROLLER" "$HOME_COMMAND" ||
    die "Could not create home command: $HOME_COMMAND"

  if [ -e "$OLD_FINAL_LAUNCHER" ]; then
    stamp="$(date '+%Y%m%d_%H%M%S')"
    backup="${OLD_FINAL_LAUNCHER}.legacy_${stamp}"
    mv "$OLD_FINAL_LAUNCHER" "$backup" ||
      die "Could not archive old final launcher."
    log "Archived old final launcher to: $backup"
  fi

  cat > "$OLD_FINAL_LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "$INSTALLED_CONTROLLER" start "\$@"
EOF
  chmod 0755 "$OLD_FINAL_LAUNCHER"
}

install_self() {
  local source_path
  local source_real
  local installed_real

  acquire_controller_lock
  preflight
  load_env_safely
  ensure_dirs

  source_path="$0"
  source_real="$("$PYTHON" - "$source_path" <<'PY'
import os
import sys
print(os.path.realpath(sys.argv[1]))
PY
  )"

  installed_real="$("$PYTHON" - "$INSTALLED_CONTROLLER" <<'PY'
import os
import sys
print(os.path.realpath(sys.argv[1]))
PY
  )"

  section "INSTALLING ALL-IN-ONE CONTROLLER"

  if [ "$source_real" != "$installed_real" ]; then
    [ -e "$INSTALLED_CONTROLLER" ] && backup_file "$INSTALLED_CONTROLLER"
    cp "$source_path" "$INSTALLED_CONTROLLER" ||
      die "Could not install controller."
    chmod 0755 "$INSTALLED_CONTROLLER"
  else
    chmod 0755 "$INSTALLED_CONTROLLER"
  fi

  repair_legacy_runtime
  ensure_timer_plists
  install_compatibility_wrapper

  ok "Installed controller: $INSTALLED_CONTROLLER"
  ok "Home command: $HOME_COMMAND"
  ok "One-click wrapper: $HOME_WRAPPER"
  ok "Legacy final launcher now safely forwards to this controller."

  release_controller_lock
  exec "$INSTALLED_CONTROLLER" start
}

usage() {
  cat <<EOF
Aadil HR Hunter All-in-One Controller v$VERSION

Usage:
  $(basename "$0") install
  $(basename "$0") start
  $(basename "$0") stop [--force]
  $(basename "$0") restart [--force]
  $(basename "$0") status
  $(basename "$0") health
  $(basename "$0") doctor
  $(basename "$0") network
  $(basename "$0") logs [service]
  $(basename "$0") open
  $(basename "$0") install-autostart [--force]
  $(basename "$0") remove-autostart [--force]
  $(basename "$0") start-timers
  $(basename "$0") stop-timers
  $(basename "$0") kick-timers

Recommended first run:
  bash "$0" install

Daily use:
  "$HOME_COMMAND" start
  "$HOME_COMMAND" status
  "$HOME_COMMAND" restart
  "$HOME_COMMAND" logs telegram

URLs:
  n8n:       http://127.0.0.1:5678
  Streamlit: http://127.0.0.1:8501
  FastAPI:   http://127.0.0.1:8000/health
EOF
}

parse_force() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --force)
        FORCE=1
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
    shift
  done
}

main() {
  command="${1:-help}"

  if [ "$#" -gt 0 ]; then
    shift
  fi

  case "$command" in
    install)
      parse_force "$@"
      install_self
      ;;
    start)
      parse_force "$@"
      start_all
      ;;
    stop)
      parse_force "$@"
      stop_all
      ;;
    restart)
      parse_force "$@"
      restart_all
      ;;
    status)
      status_report
      ;;
    health)
      health_report
      ;;
    doctor)
      doctor
      ;;
    network)
      preflight
      load_env_safely
      network_report
      ;;
    logs)
      logs_command "${1:-}"
      ;;
    open)
      open_urls
      ;;
    install-autostart)
      parse_force "$@"
      install_autostart
      ;;
    remove-autostart)
      parse_force "$@"
      remove_autostart
      ;;
    start-timers)
      acquire_controller_lock
      preflight
      load_env_safely
      start_timers
      ;;
    stop-timers)
      acquire_controller_lock
      preflight
      load_env_safely
      stop_timers
      ;;
    kick-timers)
      acquire_controller_lock
      preflight
      load_env_safely
      kick_timers
      ;;
    service-run)
      [ "$#" -ge 1 ] || die "service-run requires a service name."
      service_run "$1"
      ;;
    help|-h|--help)
      usage
      ;;
    *)
      usage
      die "Unknown command: $command"
      ;;
  esac
}

main "$@"
