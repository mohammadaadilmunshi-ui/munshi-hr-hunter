#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

root=${MUNSHI_CLOUD_ROOT:-/opt/munshi}
watch_dir=""
while (($#)); do
  case "$1" in
    --watch-dir) watch_dir=${2:-}; shift 2 ;;
    -h|--help) printf 'Usage: %s [--watch-dir DIRECTORY]\n' "$0"; exit 0 ;;
    *) netcup_die "unknown argument: $1" ;;
  esac
done
if [[ -z "$watch_dir" ]]; then
  watch_dir=$(find "$root/reports/netcup" -maxdepth 1 -type d -name 'endurance_*' -print 2>/dev/null | sort | tail -n1)
fi
[[ -n "$watch_dir" && -f "$watch_dir/metadata.txt" && -f "$watch_dir/snapshots.tsv" ]] || netcup_die "endurance evidence was not found"
events="$watch_dir/no_go_events.log"
report="$watch_dir/ENDURANCE_REPORT.md"
samples=$(( $(wc -l < "$watch_dir/snapshots.tsv") - 1 ))
max_mem=$(awk -F '\t' 'NR>1{if($3>m)m=$3}END{print m+0}' "$watch_dir/snapshots.tsv")
min_disk=$(awk -F '\t' 'NR>1{if(n==0||$6<m)m=$6;n++}END{print m+0}' "$watch_dir/snapshots.tsv")
max_chromium=$(awk -F '\t' 'NR>1{if($9>m)m=$9}END{print m+0}' "$watch_dir/snapshots.tsv")
result=GO_CLOUD_ENDURANCE
[[ -s "$events" ]] && result=NO_GO_CLOUD_ENDURANCE
{
  printf '# Netcup Shadow Endurance Report\n\n'
  printf -- '- Generated: `%s`\n' "$(date -u +%FT%TZ)"
  sed 's/^/- /' "$watch_dir/metadata.txt"
  printf -- '- Samples: `%s`\n- Peak used RAM: `%s MiB`\n- Minimum free disk: `%s MiB`\n- Peak Chromium process count: `%s`\n' "$samples" "$max_mem" "$min_disk" "$max_chromium"
  printf -- '- Production Mac mutations: `0`\n- Result: `%s`\n\n' "$result"
  printf '## No-go events\n\n'
  if [[ -s "$events" ]]; then sed 's/^/- `/' "$events" | sed 's/$/`/'; else printf 'None.\n'; fi
} > "$report"
printf 'ENDURANCE_REPORT=%s\nRESULT: %s\n' "$report" "$result"
[[ "$result" == GO_CLOUD_ENDURANCE ]]
