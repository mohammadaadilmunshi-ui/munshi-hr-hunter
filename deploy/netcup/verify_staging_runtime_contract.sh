#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${MUNSHI_STAGING_PROJECT:-munshi-netcup-staging}"
STAGING_ROOT="${MUNSHI_STAGING_ROOT:-/home/munshi/munshi-staging-v1}"
STAGING_REPO="$STAGING_ROOT/repo"
STAGING_ENV="$STAGING_ROOT/staging.env"
STAGING_OVERRIDE="$STAGING_ROOT/staging.override.yaml"

H="$PROJECT-hunter-1"
N="$PROJECT-n8n-1"
O="$PROJECT-ollama-1"

echo "=== STAGING RUNTIME CONTRACT ==="

for path in "$STAGING_REPO/.git" "$STAGING_ENV" "$STAGING_OVERRIDE"; do
  [[ -e "$path" ]] || {
    echo "missing staging runtime path: $path" >&2
    exit 10
  }
done
echo "STAGING_RUNTIME_FILES=PASS"

[[ -z "$(git -C "$STAGING_REPO" status --porcelain)" ]] || {
  echo "staging repository is dirty" >&2
  exit 11
}
echo "STAGING_REPO_HEAD=$(git -C "$STAGING_REPO" rev-parse HEAD)"
echo "STAGING_REPO_BRANCH=$(git -C "$STAGING_REPO" branch --show-current)"
echo "STAGING_REPO_CLEAN=PASS"

for c in "$H" "$N" "$O"; do
  running="$(docker inspect -f '{{.State.Running}}' "$c")"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c")"
  oom="$(docker inspect -f '{{.State.OOMKilled}}' "$c")"
  restart="$(docker inspect -f '{{.RestartCount}}' "$c")"
  echo "$c running=$running health=$health restart=$restart oom=$oom"
  [[ "$running" == "true" && "$health" == "healthy" && "$oom" == "false" ]] || {
    echo "staging container unhealthy: $c" >&2
    exit 12
  }
done
echo "STAGING_CONTAINER_HEALTH=PASS"

env_dump="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$H")"
for exact in \
  HUNTER_ENABLE_TELEGRAM=false \
  HUNTER_ENABLE_DISCOVERY_SCHEDULER=false \
  HUNTER_ENABLE_COORDINATOR=false \
  TELEGRAM_ENABLED=false \
  DISCOVERY_ENABLED=false \
  SCHEDULER_ENABLED=false \
  COORDINATOR_ENABLED=false \
  PRODUCTION_CALLBACKS_ENABLED=false \
  PRODUCTION_STATE_IMPORTED=false \
  CLOUD_SHADOW_MODE=true
do
  grep -qx "$exact" <<<"$env_dump" || {
    echo "missing staging safety env: $exact" >&2
    exit 13
  }
done
echo "STAGING_SIDE_EFFECT_FLAGS=PASS"

processes="$(docker exec "$H" sh -lc 'ps -eo args=')"
if grep -E 'app\.telegram_listener|app\.randomized_source_runner|app\.unified_hourly_coordinator|app\.stored_job_n8n_worker|app\.manual_input_worker' <<<"$processes"; then
  echo "unexpected production-capable staging process detected" >&2
  exit 14
fi
echo "STAGING_SIDE_EFFECT_PROCESSES=NONE"

for service in hunter n8n ollama; do
  c="$PROJECT-$service-1"
  while IFS='|' read -r name dest; do
    name="$(xargs <<<"$name")"
    [[ -n "$name" ]] || continue
    [[ "$name" == "$PROJECT"_* ]] || {
      echo "non-staging volume attached to $c: $name -> $dest" >&2
      exit 15
    }
  done < <(docker inspect -f '{{range .Mounts}}{{println .Name "|" .Destination}}{{end}}' "$c")
done
echo "STAGING_VOLUME_ISOLATION=PASS"

for p in 18000 18501 15678; do
  lines="$(ss -ltnH | awk -v p=":$p" '$4 ~ p"$" {print $4}')"
  [[ -n "$lines" ]] || {
    echo "staging port not listening: $p" >&2
    exit 16
  }
  if grep -Ev '^(127\.0\.0\.1|\[::1\]):' <<<"$lines" | grep -q .; then
    echo "staging port is not loopback-only: $p" >&2
    exit 17
  fi
done
echo "STAGING_RAW_PORTS_LOOPBACK_ONLY=PASS"

curl -fsS --connect-timeout 3 --max-time 5 http://127.0.0.1:18000/health >/dev/null
curl -fsS --connect-timeout 3 --max-time 5 http://127.0.0.1:18501/_stcore/health >/dev/null
curl -fsS --connect-timeout 3 --max-time 5 http://127.0.0.1:15678/healthz >/dev/null
echo "STAGING_HTTP_HEALTH=PASS"

docker exec -i "$H" python - <<'PY'
from app.database import get_connection, get_setting

targeting = get_setting("targeting", {}) or {}
authorization = get_setting("authorization", {}) or {}
orchestration = get_setting("orchestration", {}) or {}
eligibility = targeting.get("eligibility") if isinstance(targeting, dict) else {}
eligibility = eligibility if isinstance(eligibility, dict) else {}

connection = get_connection()
try:
    quick = connection.execute("PRAGMA quick_check").fetchone()[0]
finally:
    connection.close()

assert quick == "ok", quick
assert targeting.get("mode") == "OPT", targeting.get("mode")
assert eligibility.get("label") == "United States nationwide", eligibility.get("label")
assert authorization.get("authorization_mode") == "OPT", authorization.get("authorization_mode")
assert bool(orchestration.get("maintenance_mode", True)) is True

print("STAGING_DB_QUICK_CHECK=PASS")
print("STAGING_DASHBOARD_CONFIG=PASS")
PY

edge="munshi-staging-edge-caddy"
[[ "$(docker inspect -f '{{.State.Running}}' "$edge")" == "true" ]] || {
  echo "staging HTTPS edge is not running" >&2
  exit 18
}
public_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 8 https://staging-dashboard.munshi.systems/ || true)"
[[ "$public_code" == "401" ]] || {
  echo "staging public unauthenticated contract failed: code=$public_code" >&2
  exit 19
}
echo "STAGING_HTTPS_UNAUTHENTICATED=401"
echo "STAGING_HTTPS_EDGE=PASS"

echo "RESULT=STAGING_RUNTIME_CONTRACT_PASS"
