#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${MUNSHI_STAGING_PROJECT:-munshi-netcup-staging}"
H="${PROJECT}-hunter-1"
VERIFY="${MUNSHI_STAGING_VERIFY:-/opt/munshi/bin/verify-staging-runtime-contract}"

[[ "$PROJECT" == *staging* ]] || {
  echo "refusing maintenance repair outside a staging project: $PROJECT" >&2
  exit 2
}
docker inspect "$H" >/dev/null 2>&1 || {
  echo "staging Hunter container missing: $H" >&2
  exit 3
}

read_env() {
  docker inspect "$H" --format '{{range .Config.Env}}{{println .}}{{end}}'
}

for required in   'HUNTER_ENABLE_TELEGRAM=false'   'HUNTER_ENABLE_DISCOVERY_SCHEDULER=false'   'HUNTER_ENABLE_COORDINATOR=false'   'TELEGRAM_ENABLED=false'   'DISCOVERY_ENABLED=false'   'SCHEDULER_ENABLED=false'   'COORDINATOR_ENABLED=false'   'PRODUCTION_CALLBACKS_ENABLED=false'   'PRODUCTION_STATE_IMPORTED=false'   'CLOUD_SHADOW_MODE=true'
do
  read_env | grep -qx "$required" || {
    echo "refusing staging DB maintenance repair; missing safe runtime flag: $required" >&2
    exit 4
  }
done

old_json="$(
  docker exec -i "$H" python - <<'PY'
import json
from app.database import get_setting
print(json.dumps(get_setting("orchestration", {}) or {}, sort_keys=True))
PY
)"

current="$(
  printf '%s' "$old_json" | python3 -c 'import json,sys; print(str(bool(json.load(sys.stdin).get("maintenance_mode", False))).lower())'
)"

if [[ "$current" == "true" ]]; then
  echo "STAGING_MAINTENANCE_MODE=ALREADY_TRUE"
  exit 0
fi

image_id="$(docker inspect "$H" -f '{{.Image}}')"
db_path="$(docker exec -i "$H" python - <<'PY'
from app.database import DB_PATH
print(DB_PATH)
PY
)"
data_volume="$(docker inspect "$H" -f '{{range .Mounts}}{{if eq .Destination "/app/hunter/data"}}{{.Name}}{{end}}{{end}}')"

[[ -n "$image_id" && -n "$db_path" && -n "$data_volume" ]] || {
  echo "could not resolve staging Hunter image/database/volume" >&2
  exit 5
}

stopped=0
mutated=0

apply_setting_json() {
  local changed_by="$1"
  docker run --rm -i     --network none     --mount "type=volume,src=$data_volume,dst=/app/hunter/data"     -e "DATABASE_PATH=$db_path"     --entrypoint python     "$image_id"     -c '
import json, sys
from app.database import get_connection, save_setting
payload = json.load(sys.stdin)
save_setting("orchestration", payload, changed_by=sys.argv[1])
db = get_connection()
try:
    quick = db.execute("PRAGMA quick_check").fetchone()[0]
finally:
    db.close()
if quick != "ok":
    raise SystemExit(f"quick_check failed: {quick}")
print("OFFLINE_STAGING_DB_UPDATE=PASS")
' "$changed_by"
}

wait_healthy() {
  for i in $(seq 1 48); do
    health="$(docker inspect "$H" -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)"
    echo "[staging-maintenance] health=$health ($i/48)"
    [[ "$health" == "healthy" ]] && return 0
    sleep 5
  done
  return 1
}

rollback() {
  rc=$?
  trap - ERR
  echo "=== ROLLBACK STAGING MAINTENANCE REPAIR rc=$rc ===" >&2
  if (( mutated )); then
    docker stop "$H" >/dev/null 2>&1 || true
    printf '%s' "$old_json" | apply_setting_json "staging-maintenance-repair-rollback" || true
  fi
  if (( stopped || mutated )); then
    docker start "$H" >/dev/null 2>&1 || true
    wait_healthy || true
  fi
  echo "STAGING_MAINTENANCE_REPAIR=ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback ERR

docker stop "$H" >/dev/null
stopped=1

new_json="$(
  printf '%s' "$old_json" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
payload["maintenance_mode"] = True
print(json.dumps(payload, sort_keys=True))
'
)"

printf '%s' "$new_json" | apply_setting_json "staging-maintenance-repair"
mutated=1

docker start "$H" >/dev/null
stopped=0
wait_healthy

docker exec -i "$H" python - <<'PY'
from app.database import get_connection, get_setting
value = get_setting("orchestration", {}) or {}
assert bool(value.get("maintenance_mode")) is True, value
db = get_connection()
try:
    quick = db.execute("PRAGMA quick_check").fetchone()[0]
finally:
    db.close()
assert quick == "ok", quick
print("STAGING_MAINTENANCE_DB_VERIFY=PASS")
PY

if [[ -x "$VERIFY" ]]; then
  "$VERIFY"
fi

trap - ERR
echo "STAGING_MAINTENANCE_MODE=true"
echo "STAGING_MAINTENANCE_REPAIR=PASS"
