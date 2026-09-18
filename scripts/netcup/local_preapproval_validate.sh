#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
CANONICAL_SHA=501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f
cd "$ROOT"
render_dir=""
smoke_env=""
smoke_project=""
test_state=$(mktemp -d "${TMPDIR:-/tmp}/munshi-stage8b-pytest.XXXXXX")
generated_repo_artifacts=()
for candidate in \
  "$ROOT/data/hunter.db" "$ROOT/data/hunter.db-wal" "$ROOT/data/hunter.db-shm" \
  "$ROOT/data/munshi_runtime_recovery_backoff.json"; do
  [[ -e "$candidate" ]] || generated_repo_artifacts+=("$candidate")
done
cleanup() {
  if [[ -n "$smoke_project" && "$smoke_project" == munshi-stage8b-preapproval-* ]]; then
    docker compose --project-name "$smoke_project" --env-file "$smoke_env" -f compose.yaml -f compose.netcup-shadow.yaml down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  [[ -n "$render_dir" && "$render_dir" == *munshi-stage8b-render.* ]] && rm -rf "$render_dir"
  [[ "$test_state" == *munshi-stage8b-pytest.* ]] && rm -rf "$test_state"
  [[ -n "$smoke_env" ]] && rm -f "$smoke_env"
  for artifact in "${generated_repo_artifacts[@]}"; do
    [[ "$artifact" == "$ROOT/data/"* ]] && rm -f "$artifact"
  done
  return 0
}
trap cleanup EXIT
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN=$(command -v python3.12)
else
  printf 'ERROR: Python 3.12 is required for local validation\n' >&2
  exit 1
fi

printf 'LOCAL_GATE: Python compilation\n'
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m compileall -q app integrations scripts tests
printf 'LOCAL_GATE: source validators\n'
"$PYTHON_BIN" scripts/validate_container_environment_contract.py
"$PYTHON_BIN" scripts/validate_n8n_portability.py
"$PYTHON_BIN" scripts/validate_docker_foundation.py
"$PYTHON_BIN" scripts/validate_netcup_shadow.py
"$PYTHON_BIN" scripts/audit_linux_compatibility.py
printf 'LOCAL_GATE: unit tests\n'
HUNTER_API_SECRET=synthetic-pytest-only DATABASE_PATH="$test_state/hunter.db" AADIL_HR_HUNTER_RUNTIME="$test_state/runtime" AADIL_HR_HUNTER_LOGS="$test_state/logs" N8N_USER_FOLDER="$test_state/n8n" "$PYTHON_BIN" - <<'PY'
import runpy

from app.database import get_connection, initialize_database

initialize_database()
runpy.run_path("migrations/003_control_center_v3.py")["migrate"]()
connection = get_connection()
try:
    connection.executemany(
        """
        INSERT OR IGNORE INTO source_health (
            source_name, source_tier, enabled, cadence_minutes, cost_mode,
            health_status
        ) VALUES (?, 1, 0, 360, 'free', 'not_configured')
        """,
        [("USAJobs",), ("Personio",)],
    )
    connection.commit()
finally:
    connection.close()
PY
HUNTER_API_SECRET=synthetic-pytest-only DATABASE_PATH="$test_state/hunter.db" AADIL_HR_HUNTER_RUNTIME="$test_state/runtime" AADIL_HR_HUNTER_LOGS="$test_state/logs" N8N_USER_FOLDER="$test_state/n8n" PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m pytest -q
printf 'LOCAL_GATE: shell syntax\n'
while IFS= read -r script; do bash -n "$script"; done < <(find scripts/netcup -type f -name '*.sh' -print | sort)
printf 'LOCAL_GATE: renderer\n'
render_dir=$(mktemp -d "${TMPDIR:-/tmp}/munshi-stage8b-render.XXXXXX")
"$PYTHON_BIN" scripts/render_n8n_deployment_workflow.py --output "$render_dir/cloud-shadow.json"
grep -q 'http://hunter:8000/api/hr-agent/score' "$render_dir/cloud-shadow.json"
! grep -Eq 'http://(127\.0\.0\.1|localhost):(8000|5678|11434)' "$render_dir/cloud-shadow.json"
[[ "$(shasum -a 256 n8n/workflows/canonical_hr_hunter_workflow.json | awk '{print $1}')" == "$CANONICAL_SHA" ]]

printf 'LOCAL_GATE: Compose configuration\n'
HUNTER_API_SECRET=synthetic-config-only N8N_ENCRYPTION_KEY=synthetic-config-only docker compose -f compose.yaml -f compose.netcup-shadow.yaml config -q

if [[ "${NETCUP_SKIP_LOCAL_DOCKER_SMOKE:-false}" == true ]]; then
  printf 'LOCAL_GATE: isolated Docker smoke explicitly skipped by NETCUP_SKIP_LOCAL_DOCKER_SMOKE=true\n'
elif ! docker info >/dev/null 2>&1; then
  printf 'LOCAL_GATE: isolated Docker smoke unavailable because the Mac Docker daemon is stopped; it was not started\n'
else
  smoke_project="munshi-stage8b-preapproval-$(date -u +%Y%m%d%H%M%S)-$$"
  smoke_env=$(mktemp "${TMPDIR:-/tmp}/munshi-stage8b-smoke.XXXXXX")
  umask 077
  {
    printf 'COMPOSE_PROJECT_NAME=%s\n' "$smoke_project"
    printf 'HUNTER_API_SECRET=synthetic-stage8b-%s\n' "$$"
    printf 'N8N_ENCRYPTION_KEY=synthetic-stage8b-key-%s\n' "$$"
    printf 'HUNTER_FASTAPI_PORT_MAPPING=127.0.0.1::8000\nHUNTER_STREAMLIT_PORT_MAPPING=127.0.0.1::8501\nN8N_PORT_MAPPING=127.0.0.1::5678\n'
    printf 'HUNTER_ENABLE_TELEGRAM=false\nHUNTER_ENABLE_DISCOVERY_SCHEDULER=false\nHUNTER_ENABLE_COORDINATOR=false\n'
  } > "$smoke_env"
  printf 'LOCAL_GATE: isolated Docker smoke project %s\n' "$smoke_project"
  docker compose --project-name "$smoke_project" --env-file "$smoke_env" -f compose.yaml -f compose.netcup-shadow.yaml up --build -d hunter n8n ollama
  healthy=0
  for attempt in $(seq 1 60); do
    healthy=0
    for service in hunter n8n ollama; do
      cid=$(docker compose --project-name "$smoke_project" --env-file "$smoke_env" -f compose.yaml -f compose.netcup-shadow.yaml ps -q "$service")
      if [[ -n "$cid" && "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid")" == healthy ]]; then
        healthy=$((healthy + 1))
      fi
    done
    [[ "$healthy" == 3 ]] && break
    sleep 5
  done
  [[ "$healthy" == 3 ]]
  docker compose --project-name "$smoke_project" --env-file "$smoke_env" -f compose.yaml -f compose.netcup-shadow.yaml exec -T hunter python -c "import urllib.request; assert urllib.request.urlopen('http://n8n:5678/healthz',timeout=10).status==200"
  docker compose --project-name "$smoke_project" --env-file "$smoke_env" -f compose.yaml -f compose.netcup-shadow.yaml exec -T hunter python -c "import urllib.request,json; assert 'models' in json.load(urllib.request.urlopen('http://ollama:11434/api/tags',timeout=10))"
  for service in hunter n8n ollama; do
    cid=$(docker compose --project-name "$smoke_project" --env-file "$smoke_env" -f compose.yaml -f compose.netcup-shadow.yaml ps -q "$service")
    [[ "$(docker inspect -f '{{.Name}}' "$cid")" == "/${smoke_project}-"* ]]
  done
  docker compose --project-name "$smoke_project" --env-file "$smoke_env" -f compose.yaml -f compose.netcup-shadow.yaml down --volumes --remove-orphans
  smoke_project=""
fi

[[ "$(shasum -a 256 n8n/workflows/canonical_hr_hunter_workflow.json | awk '{print $1}')" == "$CANONICAL_SHA" ]]
printf 'RESULT: GO_STAGE8B_LOCAL_VALIDATION\n'
printf 'PRODUCTION_MAC_MUTATIONS: 0\n'
