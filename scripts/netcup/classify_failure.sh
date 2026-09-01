#!/usr/bin/env bash
set -euo pipefail

evidence=${1:-}
[[ -n "$evidence" && -f "$evidence" ]] || { printf 'Usage: %s EVIDENCE_LOG\n' "$0" >&2; exit 2; }
text=$(tr '[:upper:]' '[:lower:]' < "$evidence")
category=SOURCE_DEFECT
if grep -Eq 'oom|out of memory|memory exhaustion|no space left|disk exhaustion' <<<"$text"; then
  category=RESOURCE_DEFECT
elif grep -Eq 'hardware|nvme|cpu count|ram below|unsupported os|kernel' <<<"$text"; then
  category=HOST_DEFECT
elif grep -Eq 'dns|connection refused|network unreachable|timeout.*connect|route' <<<"$text"; then
  category=NETWORK_DEFECT
elif grep -Eq 'compose|container|image|healthcheck|restart policy|mount' <<<"$text"; then
  category=CONTAINER_DEFECT
elif grep -Eq 'environment|missing.*secret|configuration|config|flag' <<<"$text"; then
  category=CONFIG_DEFECT
elif grep -Eq 'upstream|rate limit|429|registry unavailable|github unavailable' <<<"$text"; then
  category=UPSTREAM_DEFECT
elif grep -Eq 'transient|temporary|try again|connection reset' <<<"$text"; then
  category=TRANSIENT_DEFECT
fi
printf 'FAILURE_CLASS=%s\n' "$category"
