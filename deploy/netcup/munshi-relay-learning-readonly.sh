#!/usr/bin/env bash
# Bounded, cursor-driven authorized read-only learning.
# No employer submits, candidate values, or executable adapter promotion.
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == munshi ]] || { echo 'LEARNING_BLOCKED=WRONG_OS_OWNER' >&2; exit 12; }
root=/opt/munshi/runtime
state="$root/relay-learning-cursor-v1.json"
exec 9>"$root/relay-learning-readonly.lock"
flock -n 9 || { echo 'LEARNING_ALREADY_RUNNING=YES'; exit 0; }
container=munshi-netcup-shadow-hunter-1
health="$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || true)"
[[ "$health" == healthy ]] || { echo 'LEARNING_BLOCKED=HUNTER_NOT_HEALTHY' >&2; exit 12; }
start="$(python3 - "$state" <<'PY'
import json,sys
from pathlib import Path
path=Path(sys.argv[1])
try:
 obj=json.loads(path.read_text()) if path.exists() else {"next_page":1}
 n=int(obj["next_page"])
 if not 1<=n<=200: raise ValueError("Out of range")
except (ValueError, TypeError, KeyError, OSError):
 raise SystemExit("LEARNING_BLOCKED=INVALID_HISTORY_CURSOR")
print(n)
PY
)"
pages=$((201 - start))
(( pages > 20 )) && pages=20
echo "LEARNING_MODE=AUTHORIZED_READ_ONLY; START_PAGE=$start; MAX_PAGES=$pages; MAX_PER_PAGE=15"
if (( start > 1 )); then
  # Always refresh the newest applications before continuing older history.
  timeout --signal=TERM --kill-after=20s 120s \
    docker exec -e MUNSHI_TSENTA_LEARNING_AUTO_READONLY_ENABLED=true \
    "$container" python -m app.tsenta_learning_monitor_v1 --once --pages 1 --start-page 1
fi
# Capture only the sanitized machine summary for atomic local cursor update.
result="$(timeout --signal=TERM --kill-after=20s 1200s \
  docker exec -e MUNSHI_TSENTA_LEARNING_AUTO_READONLY_ENABLED=true \
  "$container" python -m app.tsenta_learning_monitor_v1 --once \
  --pages "$pages" --start-page "$start")"
printf '%s\n' "$result"
python3 - "$result" "$state" <<'PY'
import json,os,sys
from pathlib import Path
payload=json.loads(sys.argv[1])
if payload.get("status")!="COMPLETED_READ_ONLY":
 raise SystemExit("LEARNING_BLOCKED=INCOMPLETE_SYNC")
if payload.get("provider_submissions_started") != 0 or payload.get("native_auto_upgrades_applied") != 0:
 raise SystemExit("LEARNING_BLOCKED=SUBMISSION_OR_PROMOTION_ASSERTION")
if payload.get("history_page_cap_reached") is True:
 raise SystemExit("LEARNING_BLOCKED=MAX_HISTORY_PAGE_REACHED")
n=payload.get("history_next_page")
if not isinstance(n,int) or not 1<=n<=200:
 raise SystemExit("LEARNING_BLOCKED=INVALID_NEXT_HISTORY_PAGE")
path=Path(sys.argv[2])
tmp=path.with_suffix(".tmp")
tmp.write_text(json.dumps({"next_page":n},sort_keys=True)+"\n")
os.chmod(tmp,0o600)
os.replace(tmp,path)
print("LEARNING_CURSOR_COMMITTED="+str(n))
PY
echo 'LEARNING_CYCLE_PROCESSED=YES; NO_EMPLOYER_SUBMISSIONS_FROM_CYCLE=YES'
