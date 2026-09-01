#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

hours=1
interval=60
while (($#)); do
  case "$1" in
    --hours) hours=${2:-}; shift 2 ;;
    --interval-seconds) interval=${2:-}; shift 2 ;;
    -h|--help) printf 'Usage: %s [--hours 1|6|24|48|72] [--interval-seconds N]\n' "$0"; exit 0 ;;
    *) netcup_die "unknown argument: $1" ;;
  esac
done
[[ "$hours" =~ ^(1|6|24|48|72)$ ]] || netcup_die "hours must be 1, 6, 24, 48, or 72"
[[ "$interval" =~ ^[0-9]+$ ]] && ((interval >= 15 && interval <= 900)) || netcup_die "interval must be 15..900 seconds"

root=${MUNSHI_CLOUD_ROOT:-/opt/munshi}
repo=${MUNSHI_REPO_ROOT:-$root/repo}
env_file=${MUNSHI_SHADOW_ENV_FILE:-$root/secrets/netcup-shadow.env}
project=${COMPOSE_PROJECT_NAME:-$NETCUP_COMPOSE_PROJECT}
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || netcup_die "endurance watch is remote Linux x86_64 only"
[[ -f "$env_file" && -d "$repo/.git" ]] || netcup_die "shadow deployment is not present"

started_epoch=$(date +%s)
end_epoch=$((started_epoch + hours * 3600))
watch_dir="$root/reports/netcup/endurance_$(date -u +%Y%m%dT%H%M%SZ)_${hours}h"
mkdir -p "$watch_dir"
snapshots="$watch_dir/snapshots.tsv"
events="$watch_dir/no_go_events.log"
metadata="$watch_dir/metadata.txt"
printf 'timestamp\tload1\tmem_used_mib\tmem_total_mib\tswap_used_mib\tdisk_free_mib\tdocker_usage\tzombies\tchromium_processes\thunter_health\tn8n_health\tollama_health\n' > "$snapshots"
: > "$events"
cd "$repo"
compose=(docker compose --project-name "$project" --env-file "$env_file" -f compose.yaml -f compose.netcup-shadow.yaml)

{
  printf 'started=%s\n' "$(date -u +%FT%TZ)"
  printf 'hours=%s\ninterval_seconds=%s\n' "$hours" "$interval"
  printf 'repo_head=%s\nbranch=%s\n' "$(git rev-parse HEAD)" "$(git branch --show-current)"
  printf 'host=%s\narchitecture=%s\n' "$(hostname -f 2>/dev/null || hostname)" "$(uname -m)"
  . /etc/os-release; printf 'os=%s\n' "$PRETTY_NAME"
  printf 'docker=%s\ncompose=%s\n' "$(docker --version)" "$(docker compose version)"
  printf 'n8n_version=%s\n' "$("${compose[@]}" exec -T n8n n8n --version | tr -d '\r')"
  printf 'canonical_workflow_sha256=%s\nproduction_mac_mutations=0\n' "$(sha256sum n8n/workflows/canonical_hr_hunter_workflow.json | awk '{print $1}')"
  printf 'git_status=%s\n' "$(git status --short --branch | tr '\n' ';')"
  printf 'container_status=%s\n' "$("${compose[@]}" ps --format json | tr '\n' ';')"
} > "$metadata"

declare -A baseline_restarts
declare -A unhealthy_streak
for service in hunter n8n ollama; do
  cid=$("${compose[@]}" ps -q "$service")
  [[ -n "$cid" ]] || netcup_die "missing service: $service"
  baseline_restarts[$service]=$(docker inspect -f '{{.RestartCount}}' "$cid")
  unhealthy_streak[$service]=0
done
baseline_log_bytes=0
high_memory_streak=0
for cid in $("${compose[@]}" ps -q); do
  log_path=$(docker inspect -f '{{.LogPath}}' "$cid")
  [[ -f "$log_path" ]] && baseline_log_bytes=$((baseline_log_bytes + $(stat -c %s "$log_path")))
done

record_event() {
  local kind=$1 detail=$2
  printf '%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$kind" "$detail" | tee -a "$events" >&2
}

while (( $(date +%s) < end_epoch )); do
  now=$(date -u +%FT%TZ)
  load1=$(awk '{print $1}' /proc/loadavg)
  read -r mem_total mem_used swap_total swap_used < <(free -m | awk '/^Mem:/{mt=$2;mu=$3}/^Swap:/{print mt,mu,$2,$3}')
  disk_free=$(df -Pm "$root" | awk 'NR==2{print $4}')
  docker_usage=$(docker system df --format '{{.Type}}={{.Size}}' 2>/dev/null | paste -sd ';' - | tr '\t' ' ')
  zombies=$(ps -eo stat= | awk '$1 ~ /^Z/{n++}END{print n+0}')
  chromium=$(ps -eo comm= | awk '/chrom(e|ium)/{n++}END{print n+0}')
  health_values=()

  for service in hunter n8n ollama; do
    cid=$("${compose[@]}" ps -q "$service")
    if [[ -z "$cid" ]]; then
      health=missing
      record_event CONTAINER_MISSING "$service"
    else
      running=$(docker inspect -f '{{.State.Running}}' "$cid")
      health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid")
      restarts=$(docker inspect -f '{{.RestartCount}}' "$cid")
      oom=$(docker inspect -f '{{.State.OOMKilled}}' "$cid")
      [[ "$oom" == false ]] || record_event OOM_KILL "$service"
      [[ "$restarts" == "${baseline_restarts[$service]}" ]] || record_event UNEXPLAINED_RESTART "$service baseline=${baseline_restarts[$service]} current=$restarts"
      if [[ "$running" != true || "$health" != healthy ]]; then
        unhealthy_streak[$service]=$((unhealthy_streak[$service] + 1))
        if ((unhealthy_streak[$service] >= 3)); then record_event PERSISTENT_HEALTH_FAILURE "$service health=$health running=$running"; fi
      else
        unhealthy_streak[$service]=0
      fi
    fi
    health_values+=("$health")
  done

  if ((disk_free < 10240)); then record_event DISK_EXHAUSTION_RISK "free_mib=$disk_free"; fi
  if ((mem_total > 0 && mem_used * 100 / mem_total >= 90)); then
    high_memory_streak=$((high_memory_streak + 1))
    if ((high_memory_streak >= 3)); then record_event RESOURCE_EXHAUSTION_RISK "memory_used_mib=$mem_used total_mib=$mem_total"; fi
  else
    high_memory_streak=0
  fi
  if ((zombies > 0)); then record_event ZOMBIE_PROCESS "count=$zombies"; fi
  if journalctl -k --since "@$started_epoch" --no-pager 2>/dev/null | grep -Eqi 'out of memory|oom-kill|segfault.*(n8n|node|docker)|I/O error|filesystem error'; then
    record_event KERNEL_OR_OOM_SIGNAL "relevant kernel signal detected"
  fi
  if "${compose[@]}" logs --since "${interval}s" n8n 2>/dev/null | grep -Eqi 'segmentation fault|segfault|fatal error|panic'; then
    record_event N8N_CRASH_SIGNAL "n8n fatal/segfault signal"
  fi

  if ! "${compose[@]}" exec -T hunter python -c "import sqlite3; db=sqlite3.connect('/app/hunter/data/hunter.db'); assert db.execute('pragma integrity_check').fetchone()[0]=='ok'" >/dev/null 2>&1; then
    record_event DATABASE_INTEGRITY_FAILURE hunter
  fi
  n8n_copy="$watch_dir/database.sqlite"
  n8n_cid=$("${compose[@]}" ps -q n8n)
  docker cp "$n8n_cid:/home/node/.n8n/database.sqlite-wal" "$watch_dir/database.sqlite-wal" >/dev/null 2>&1 || true
  docker cp "$n8n_cid:/home/node/.n8n/database.sqlite-shm" "$watch_dir/database.sqlite-shm" >/dev/null 2>&1 || true
  if ! docker cp "$n8n_cid:/home/node/.n8n/database.sqlite" "$n8n_copy" >/dev/null 2>&1 || [[ "$(sqlite3 "$n8n_copy" 'pragma integrity_check;' 2>/dev/null)" != ok ]]; then
    record_event DATABASE_INTEGRITY_FAILURE n8n
  fi
  rm -f "$n8n_copy" "$watch_dir/database.sqlite-wal" "$watch_dir/database.sqlite-shm"

  current_log_bytes=0
  for cid in $("${compose[@]}" ps -q); do
    log_path=$(docker inspect -f '{{.LogPath}}' "$cid")
    [[ -f "$log_path" ]] && current_log_bytes=$((current_log_bytes + $(stat -c %s "$log_path")))
  done
  growth=$((current_log_bytes - baseline_log_bytes))
  elapsed=$(( $(date +%s) - started_epoch + 1 ))
  if ((current_log_bytes > 1073741824 || growth * 3600 / elapsed > 536870912)); then
    record_event UNCONTROLLED_LOG_GROWTH "bytes=$current_log_bytes hourly_rate=$((growth * 3600 / elapsed))"
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$now" "$load1" "$mem_used" "$mem_total" "$swap_used" "$disk_free" "$docker_usage" "$zombies" "$chromium" \
    "${health_values[0]}" "${health_values[1]}" "${health_values[2]}" >> "$snapshots"
  sleep_for=$interval
  remaining=$((end_epoch - $(date +%s)))
  ((remaining < sleep_for)) && sleep_for=$remaining
  ((sleep_for > 0)) && sleep "$sleep_for"
done

printf 'completed=%s\n' "$(date -u +%FT%TZ)" >> "$metadata"
if [[ -s "$events" ]]; then
  printf 'RESULT: NO_GO_CLOUD_ENDURANCE\n'
  exit 1
fi
printf 'ENDURANCE_DIRECTORY=%s\n' "$watch_dir"
printf 'RESULT: GO_NETCUP_ENDURANCE_%sH\n' "$hours"
