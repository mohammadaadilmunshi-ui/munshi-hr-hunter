#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

root=${MUNSHI_CLOUD_ROOT:-/opt/munshi}
repo=${MUNSHI_REPO_ROOT:-$root/repo}
env_file=${MUNSHI_SHADOW_ENV_FILE:-$root/secrets/netcup-shadow.env}
project=${COMPOSE_PROJECT_NAME:-$NETCUP_COMPOSE_PROJECT}
report_dir="$root/reports"
failures=()

pass() { printf 'PASS: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; failures+=("$*"); }
check() {
  local label=$1
  shift
  if "$@"; then pass "$label"; else fail "$label"; fi
}

if [[ "$(uname -s)" != Linux ]]; then fail "host is not Linux"; fi
if [[ "$(uname -m)" != x86_64 ]]; then fail "host is not x86_64"; fi
if [[ "$repo" == /Users/* || "$repo" == *Aadil-HR-Hunter* ]]; then fail "forbidden Mac path"; fi
[[ -f /etc/os-release ]] || fail "missing /etc/os-release"
if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] && pass "Ubuntu 24.04 LTS" || fail "Ubuntu 24.04 LTS"
fi
[[ -d "$repo/.git" ]] || fail "cloud repository is missing"
[[ -f "$env_file" ]] || fail "shadow environment file is missing"
mkdir -p "$report_dir" 2>/dev/null || fail "cannot create report directory"

if [[ -f "$env_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
fi

for setting in \
  HUNTER_ENABLE_TELEGRAM HUNTER_ENABLE_DISCOVERY_SCHEDULER HUNTER_ENABLE_COORDINATOR \
  TELEGRAM_ENABLED DISCOVERY_ENABLED SCHEDULER_ENABLED COORDINATOR_ENABLED \
  PRODUCTION_CALLBACKS_ENABLED PRODUCTION_STATE_IMPORTED; do
  [[ "${!setting:-}" == false ]] && pass "$setting=false" || fail "$setting must be false"
done
[[ "${CLOUD_SHADOW_MODE:-}" == true ]] && pass "CLOUD_SHADOW_MODE=true" || fail "CLOUD_SHADOW_MODE must be true"

cd "$repo" 2>/dev/null || true
compose=(docker compose --project-name "$project" --env-file "$env_file" -f compose.yaml -f compose.netcup-shadow.yaml)
check "Docker daemon healthy" docker info
check "Docker Compose v2 healthy" docker compose version
check "Compose configuration valid" "${compose[@]}" config -q

cpu_count=$(nproc 2>/dev/null || printf 0)
mem_kib=$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || printf 0)
disk_bytes=$(lsblk -bndo SIZE,TYPE 2>/dev/null | awk '$2=="disk"{if($1>m)m=$1}END{print m+0}')
free_kib=$(df -Pk "$root" 2>/dev/null | awk 'NR==2{print $4}')
((cpu_count >= 8)) && pass "at least 8 CPUs" || fail "fewer than 8 CPUs"
((mem_kib >= 14500000)) && pass "approximately 16 GB RAM" || fail "RAM below expected class"
((disk_bytes >= 480000000000)) && pass "approximately 512 GB disk" || fail "disk below expected class"
((free_kib >= 20000000)) && pass "at least 20 GB free disk" || fail "free disk below 20 GB"
check "default network route exists" sh -c "ip route | grep -q '^default '"

for service in hunter n8n ollama; do
  cid=$("${compose[@]}" ps -q "$service" 2>/dev/null)
  if [[ -z "$cid" ]]; then fail "$service container exists"; continue; fi
  [[ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" == true ]] && pass "$service running" || fail "$service running"
  [[ "$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$cid" 2>/dev/null)" == unless-stopped ]] && pass "$service restart policy" || fail "$service restart policy"
  image=$(docker inspect -f '{{.Image}}' "$cid" 2>/dev/null)
  [[ "$(docker image inspect -f '{{.Architecture}}' "$image" 2>/dev/null)" == amd64 ]] && pass "$service image is amd64" || fail "$service image is amd64"
done

inspect_json=$(mktemp "${TMPDIR:-/tmp}/munshi-inspect.XXXXXX")
trap 'rm -f "$inspect_json"' EXIT
cids=$("${compose[@]}" ps -q 2>/dev/null)
if [[ -n "$cids" ]]; then
  # shellcheck disable=SC2086
  docker inspect $cids > "$inspect_json" 2>/dev/null || fail "container inspection"
  if python3 - "$inspect_json" <<'PY'
import json, sys
items = json.load(open(sys.argv[1], encoding='utf-8'))
allowed = {
  ('volume', '/app/hunter/data'), ('volume', '/app/hunter/.runtime'),
  ('volume', '/app/hunter/logs'), ('volume', '/home/node/.n8n'),
  ('volume', '/root/.ollama'),
}
bad = []
for item in items:
    for mount in item.get('Mounts', []):
        pair = (mount.get('Type'), mount.get('Destination'))
        if pair not in allowed:
            bad.append({'container': item.get('Name'), 'mount': mount})
    for binding in (item.get('HostConfig', {}).get('PortBindings') or {}).values():
        for entry in binding or []:
            if entry.get('HostIp') not in {'127.0.0.1', '::1'}:
                bad.append({'container': item.get('Name'), 'public_binding': entry})
    env = item.get('Config', {}).get('Env', [])
    forbidden = ('TELEGRAM_BOT_TOKEN=', 'GOOGLE_APPLICATION_CREDENTIALS=', 'GOOGLE_CREDENTIALS=', 'N8N_API_KEY=')
    for value in env:
        if value.startswith(forbidden) and value.split('=', 1)[1]:
            bad.append({'container': item.get('Name'), 'forbidden_credential_name': value.split('=',1)[0]})
if bad:
    print(json.dumps(bad, indent=2))
    raise SystemExit(1)
PY
  then pass "no unexpected mounts, public bindings, or production credentials"; else fail "unexpected mount, public binding, or credential"; fi
fi

for attempt in $(seq 1 30); do
  hunter_health=$("${compose[@]}" ps --format json hunter 2>/dev/null | tr -d '\n')
  n8n_health=$("${compose[@]}" ps --format json n8n 2>/dev/null | tr -d '\n')
  ollama_health=$("${compose[@]}" ps --format json ollama 2>/dev/null | tr -d '\n')
  if [[ "$hunter_health" == *'"Health":"healthy"'* && "$n8n_health" == *'"Health":"healthy"'* && "$ollama_health" == *'"Health":"healthy"'* ]]; then break; fi
  sleep 5
done
[[ "$hunter_health" == *'"Health":"healthy"'* ]] && pass "Hunter healthcheck" || fail "Hunter healthcheck"
[[ "$n8n_health" == *'"Health":"healthy"'* ]] && pass "n8n healthcheck" || fail "n8n healthcheck"
[[ "$ollama_health" == *'"Health":"healthy"'* ]] && pass "Ollama healthcheck" || fail "Ollama healthcheck"

check "FastAPI health" "${compose[@]}" exec -T hunter python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status == 200"
check "authenticated synthetic /api/status" "${compose[@]}" exec -T hunter python -c "import os,urllib.request,json; r=urllib.request.Request('http://127.0.0.1:8000/api/status',headers={'X-Hunter-Secret':os.environ['HUNTER_API_SECRET']}); d=json.load(urllib.request.urlopen(r,timeout=10)); assert d['success']"
check "Streamlit health" "${compose[@]}" exec -T hunter python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=10).status == 200"
check "Hunter to n8n DNS" "${compose[@]}" exec -T hunter python -c "import urllib.request; assert urllib.request.urlopen('http://n8n:5678/healthz', timeout=10).status == 200"
check "Hunter to Ollama DNS" "${compose[@]}" exec -T hunter python -c "import urllib.request,json; assert 'models' in json.load(urllib.request.urlopen('http://ollama:11434/api/tags', timeout=10))"
check "n8n to Hunter DNS" "${compose[@]}" exec -T n8n node -e "fetch('http://hunter:8000/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

n8n_version=$("${compose[@]}" exec -T n8n n8n --version 2>/dev/null | tr -d '\r\n')
[[ "$n8n_version" == 2.22.5 ]] && pass "n8n version 2.22.5" || fail "n8n version is ${n8n_version:-unknown}"
if "${compose[@]}" exec -T ollama ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -Eq '^gemma3:4b($|-)'; then pass "Ollama model inventory"; else fail "gemma3:4b missing"; fi

if "${compose[@]}" exec -T hunter python - <<'PY'
import json, urllib.request
body = json.dumps({'model':'gemma3:4b','prompt':'Return exactly the word READY','stream':False}).encode()
request = urllib.request.Request('http://ollama:11434/api/generate', data=body, headers={'Content-Type':'application/json'})
result = json.load(urllib.request.urlopen(request, timeout=600))
assert result.get('response','').strip()
PY
then pass "real synthetic Ollama generation"; else fail "real synthetic Ollama generation"; fi

if "${compose[@]}" exec -T hunter python - <<'PY'
import base64, json, os, urllib.request
resume = ('Human resources operations coordinator with recruiting, onboarding, employee records, HRIS, Excel reporting, '
          'people analytics dashboards, candidate scheduling, compliance documentation, and measurable service improvements. ') * 8
payload = {'resume_text': resume, 'company': 'Synthetic Company', 'title': 'Synthetic HR Operations Analyst'}
body = json.dumps({'hr_agent_payload_b64': base64.b64encode(json.dumps(payload).encode()).decode()}).encode()
request = urllib.request.Request('http://127.0.0.1:8000/api/hr-agent/score', data=body,
    headers={'Content-Type':'application/json','X-Hunter-Secret':os.environ['HUNTER_API_SECRET']})
proxy = json.load(urllib.request.urlopen(request, timeout=900))
assert proxy['proxy_status'] == 'completed' and proxy['exitCode'] == 0 and not proxy['stderr']
score = json.loads(proxy['stdout'])
assert score['success'] is True and score['scoring_method'] == 'direct_local_ollama_structured_json'
assert isinstance(score['overall_score'], int)
PY
then pass "HR Agent adapter/proxy real synthetic score"; else fail "HR Agent adapter/proxy real synthetic score"; fi

if "${compose[@]}" exec -T hunter python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    page = browser.new_page()
    page.set_content('<title>munshi-shadow-fixture</title><h1>ready</h1>')
    assert page.title() == 'munshi-shadow-fixture' and page.locator('h1').inner_text() == 'ready'
    browser.close()
PY
then pass "Playwright launches actual Chromium fixture"; else fail "Playwright/Chromium fixture"; fi
sleep 2
hunter_cid=$("${compose[@]}" ps -q hunter)
orphan_count=$(docker top "$hunter_cid" -eo stat,comm 2>/dev/null | awk '$1 ~ /^Z/ && $2 ~ /chrom/ {n++} END {print n+0}')
[[ "$orphan_count" == 0 ]] && pass "no Chromium zombies" || fail "Chromium zombie count: $orphan_count"

marker="stage9-$(date -u +%s)"
if "${compose[@]}" exec -T hunter python - "$marker" <<'PY'
import sqlite3, sys
path='/app/hunter/data/stage9_shadow_probe.sqlite'
db=sqlite3.connect(path)
db.execute('create table if not exists probe(value text not null)')
db.execute('delete from probe')
db.execute('insert into probe values (?)',(sys.argv[1],))
db.commit()
assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'
db.close()
PY
then pass "writable disposable Hunter SQLite"; else fail "writable disposable Hunter SQLite"; fi
"${compose[@]}" exec -T n8n sh -c "printf '%s' '$marker' > /home/node/.n8n/.stage9_shadow_marker" >/dev/null 2>&1 || fail "write n8n marker"
"${compose[@]}" restart hunter n8n >/dev/null 2>&1 || fail "restart persistence containers"
sleep 20
check "Hunter SQLite persists across restart" "${compose[@]}" exec -T hunter python -c "import sqlite3; db=sqlite3.connect('/app/hunter/data/stage9_shadow_probe.sqlite'); assert db.execute('select value from probe').fetchone()[0]=='$marker'; assert db.execute('pragma integrity_check').fetchone()[0]=='ok'"
check "n8n state persists across restart" "${compose[@]}" exec -T n8n sh -c "test \"\$(cat /home/node/.n8n/.stage9_shadow_marker)\" = '$marker'"
if "${compose[@]}" exec -T ollama ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -Eq '^gemma3:4b($|-)'; then pass "Ollama model persisted"; else fail "Ollama model persistence"; fi

canonical_actual=$(sha256sum "$repo/n8n/workflows/canonical_hr_hunter_workflow.json" 2>/dev/null | awk '{print $1}')
[[ "$canonical_actual" == "$NETCUP_CANONICAL_WORKFLOW_SHA" ]] && pass "canonical workflow SHA unchanged" || fail "canonical workflow SHA changed"
generated="$root/runtime/n8n/cloud_shadow_workflow.json"
if [[ -f "$generated" ]] && grep -q 'http://hunter:8000/api/hr-agent/score' "$generated" && grep -q 'http://ollama:11434/api/generate' "$generated" && ! grep -Eq 'http://(127\.0\.0\.1|localhost):(8000|5678|11434)' "$generated"; then
  pass "rendered cloud workflow endpoints"
else
  fail "rendered cloud workflow endpoints"
fi
repo_head=$(git -C "$repo" rev-parse HEAD 2>/dev/null || printf unknown)
[[ "$repo_head" =~ ^[0-9a-f]{40}$ ]] && pass "exact cloud Git commit recorded: $repo_head" || fail "cloud Git commit unavailable"

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
summary="$report_dir/shadow_verification_$timestamp.json"
fail_file=$(mktemp "${TMPDIR:-/tmp}/munshi-failures.XXXXXX")
printf '%s\n' "${failures[@]}" > "$fail_file"
python3 - "$summary" "$fail_file" "$repo_head" "$canonical_actual" "$n8n_version" "$repo" "$env_file" "$project" <<'PY'
import json, os, platform, subprocess, sys
from datetime import datetime, timezone
summary, failure_file, head, digest, n8n, repo, env_file, project = sys.argv[1:]
failures = [line for line in open(failure_file, encoding='utf-8').read().splitlines() if line]
def output(*args):
    try: return subprocess.check_output(args, cwd=repo, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception: return 'unknown'
compose = ('docker','compose','--project-name',project,'--env-file',env_file,'-f','compose.yaml','-f','compose.netcup-shadow.yaml')
data = {
  'timestamp': datetime.now(timezone.utc).isoformat(), 'repo_head': head,
  'branch': 'feat/cloud-migration-foundation', 'host': platform.node(),
  'architecture': platform.machine(), 'os': output('sh','-c','. /etc/os-release; printf %s "$PRETTY_NAME"'),
  'docker_version': output('docker','--version'), 'compose_version': output('docker','compose','version'),
  'n8n_version': n8n, 'canonical_workflow_sha256': digest,
  'container_status': output(*compose,'ps','--format','json'),
  'git_status': output('git','status','--short','--branch'),
  'test_summary': {'failures': failures, 'failure_count': len(failures)},
  'safety_status': 'PASS' if not failures else 'FAIL', 'production_mac_mutations': 0,
  'production_state_migration': 'NOT_STARTED', 'telegram_cloud_production': 'DISABLED',
  'discovery_cloud_production': 'DISABLED', 'cutover': 'NOT_STARTED',
}
with open(summary, 'w', encoding='utf-8') as handle:
    json.dump(data, handle, indent=2); handle.write('\n')
PY
rm -f "$fail_file"
printf 'VERIFICATION_REPORT=%s\n' "$summary"
if ((${#failures[@]})); then
  printf 'RESULT: NO_GO_STAGE9_CLOUD_SHADOW\n'
  exit 1
fi
printf 'RESULT: GO_STAGE9_CLOUD_SHADOW\n'
printf 'PRODUCTION_MAC_MUTATIONS: 0\n'
