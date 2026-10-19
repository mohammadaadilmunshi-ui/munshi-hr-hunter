#!/usr/bin/env bash
# MUNSHI authorized read-only Relay learning.
# A successful page advances the persistent cursor atomically. A failed page
# preserves it for later retries; no external application or submit is called.
set -Eeuo pipefail
umask 077
case "${1:---window}" in
  --one-page) window=1 ;;
  --five-pages) window=5 ;;
  --window) window=20 ;;
  *) echo 'LEARNING_BLOCKED=UNSUPPORTED_WINDOW' >&2; exit 2 ;;
esac
[[ "$(id -un)" == munshi ]] || { echo 'LEARNING_BLOCKED=WRONG_OS_OWNER' >&2; exit 12; }
root=/opt/munshi/runtime
state="$root/relay-learning-cursor-v1.json"
exec 9>"$root/relay-learning-readonly.lock"
flock -n 9 || { echo 'LEARNING_ALREADY_RUNNING=YES'; exit 0; }
container=munshi-netcup-shadow-hunter-1
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null)" == healthy ]] || {
  echo 'LEARNING_BLOCKED=HUNTER_UNHEALTHY' >&2
  exit 12
}
start="$(python3 - "$state" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1])
try:
    n=int(json.loads(p.read_text())["next_page"]) if p.exists() else 1
    if not 1<=n<=200: raise ValueError()
except (OSError,TypeError,ValueError,KeyError):
    raise SystemExit("LEARNING_BLOCKED=INVALID_HISTORY_CURSOR")
print(n)
PY
)"
printf 'LEARNING_MODE=AUTHORIZED_READ_ONLY; START_PAGE=%s; WINDOW_MAX=%s; PER_PAGE=15\n' "$start" "$window"

run_page() {
  local page="$1" output
  if ! output="$(timeout --signal=TERM --kill-after=15s 180s \
    docker exec -e MUNSHI_TSENTA_LEARNING_AUTO_READONLY_ENABLED=true \
    "$container" python -m app.tsenta_learning_monitor_v1 \
    --once --pages 1 --start-page "$page")"; then
    printf 'LEARNING_PAGE_FAILED=%s; NO_CURSOR_ADVANCE=YES\n' "$page" >&2
    return 1
  fi
  printf '%s\n' "$output"
  LEARNING_RESPONSE="$output"
}
validate_response() {
  local page="$1" update="$2"
  python3 - "$page" "$update" "$state" "$LEARNING_RESPONSE" <<'PY'
import json,os,sys
from pathlib import Path
page=int(sys.argv[1]); update=sys.argv[2]=="save"; path=Path(sys.argv[3])
report=json.loads(sys.argv[4])
if (report.get("status")!="COMPLETED_READ_ONLY" or
    report.get("provider_submissions_started")!=0 or
    report.get("native_auto_upgrades_applied")!=0 or
    report.get("history_start_page")!=page):
    raise SystemExit("LEARNING_BLOCKED=UNVERIFIED_PAGE")
if report.get("history_page_cap_reached") is True:
    raise SystemExit("LEARNING_BLOCKED=MAX_HISTORY_PAGE")
next_page=report.get("history_next_page")
if not isinstance(next_page,int) or not 1<=next_page<=200:
    raise SystemExit("LEARNING_BLOCKED=INVALID_NEXT_CURSOR")
if next_page not in (1,page+1):
    raise SystemExit("LEARNING_BLOCKED=NONCONTIGUOUS_CURSOR")
if not update:
    print("RECENT_PAGE_REFRESHED=YES")
    raise SystemExit(0)
tmp=path.with_suffix(".tmp")
tmp.write_text(json.dumps({"next_page":next_page},sort_keys=True)+"\n")
os.chmod(tmp,0o600)
os.replace(tmp,path)
print("LEARNING_CURSOR_COMMITTED="+str(next_page))
PY
}

# On every scheduled run, refresh the latest authorized applications first.
# Do not move the historical cursor during this independent refresh.
if (( start > 1 )); then
  run_page 1
  validate_response 1 refresh
fi

end=$((start+window-1))
(( end > 200 )) && end=200
for (( page=start; page<=end; page++ )); do
  run_page "$page"
  validate_response "$page" save
  next="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["next_page"])' "$state")"
  if (( next == 1 )); then
    printf 'HISTORICAL_PROVIDER_LIST_END=REACHED; NEXT_SCAN=PAGE_1\n'
    break
  fi
done
echo 'LEARNING_WINDOW_SUCCESS=YES; EMPLOYER_SUBMISSIONS_STARTED=0'
