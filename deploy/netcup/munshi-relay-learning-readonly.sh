#!/usr/bin/env bash
# Read-only provider MCP learning, sanitized MUNSHI observations only.
# This does NOT train/alter an executable ATS adapter or submit an application.
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == munshi ]] || { echo 'LEARNING_BLOCKED=WRONG_OS_OWNER' >&2; exit 12; }
exec 9>/opt/munshi/runtime/relay-learning-readonly.lock
flock -n 9 || { echo 'LEARNING_ALREADY_RUNNING=YES'; exit 0; }
container=munshi-netcup-shadow-hunter-1
health="$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || true)"
[[ "$health" == healthy ]] || { echo 'LEARNING_BLOCKED=HUNTER_NOT_HEALTHY' >&2; exit 12; }
printf 'LEARNING_MODE=AUTHORIZED_READ_ONLY; PAGES_MAX=20; LIMIT_PER_PAGE=15\n'
# The explicit capability flag is scoped only to this one child process. All
# other production services and submission policies remain unchanged.
# Timeout, lock and bounded 20-page pagination prevent run overlap or runaway.
timeout --signal=TERM --kill-after=20s 1200s \
  docker exec \
  -e MUNSHI_TSENTA_LEARNING_AUTO_READONLY_ENABLED=true \
  "$container" python -m app.tsenta_learning_monitor_v1 --once --pages 20
echo 'LEARNING_CYCLE_PROCESSED=YES; NO_EMPLOYER_SUBMISSIONS_FROM_CYCLE=YES'
