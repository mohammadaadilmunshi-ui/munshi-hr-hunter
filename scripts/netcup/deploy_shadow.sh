#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

host="${NETCUP_HOST:-}"
identity="${NETCUP_SSH_IDENTITY:-}"
ssh_user="${NETCUP_SSH_USER:-munshi}"
requested_commit=""

while (($#)); do
  case "$1" in
    --host) host=${2:-}; shift 2 ;;
    --identity) identity=${2:-}; shift 2 ;;
    --ssh-user) ssh_user=${2:-}; shift 2 ;;
    --commit) requested_commit=${2:-}; shift 2 ;;
    -h|--help) printf 'Usage: %s --host HOST --identity KEY [--ssh-user USER] [--commit SHA]\n' "$0"; exit 0 ;;
    *) netcup_die "unknown argument: $1" ;;
  esac
done

netcup_validate_host "$host"
netcup_validate_identity "$identity"
[[ -z "$requested_commit" || "$requested_commit" =~ ^[0-9a-f]{40}$ ]] || netcup_die "--commit must be a full Git SHA"
netcup_verify_remote_identity "$host" "$identity" "$ssh_user"

netcup_ssh "$host" "$identity" "$ssh_user" "bash -s -- '$NETCUP_CANONICAL_REPO' '$NETCUP_CANONICAL_BRANCH' '$requested_commit' '$NETCUP_CANONICAL_WORKFLOW_SHA' '$NETCUP_COMPOSE_PROJECT'" <<'REMOTE'
set -euo pipefail
repo_url=$1
branch=$2
requested_commit=$3
canonical_sha=$4
project=$5
root=/opt/munshi
repo=$root/repo
env_file=$root/secrets/netcup-shadow.env
generated=$root/runtime/n8n/cloud_shadow_workflow.json

[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { printf 'RESULT: NO_GO_NETCUP_HARDWARE_MISMATCH\n'; exit 70; }
[[ "$HOME" != /Users/* && "$PWD" != *Aadil-HR-Hunter* ]] || exit 71
command -v docker >/dev/null
docker compose version >/dev/null

if [[ ! -d "$repo/.git" ]]; then
  [[ -z "$(find "$repo" -mindepth 1 -maxdepth 1 -print -quit)" ]] || { printf 'refusing non-empty non-repository %s\n' "$repo" >&2; exit 72; }
  git clone "$repo_url" "$repo"
fi
[[ "$(git -C "$repo" remote get-url origin)" == "$repo_url" ]] || { printf 'remote mismatch\n' >&2; exit 73; }
[[ -z "$(git -C "$repo" status --porcelain)" ]] || { printf 'remote working tree is dirty; refusing overwrite\n' >&2; exit 74; }
git -C "$repo" fetch --prune origin "$branch"
target=${requested_commit:-$(git -C "$repo" rev-parse "origin/$branch")}
git -C "$repo" cat-file -e "$target^{commit}"
git -C "$repo" merge-base --is-ancestor "$target" "origin/$branch"
git -C "$repo" checkout -B "$branch" "$target"

actual_sha=$(sha256sum "$repo/n8n/workflows/canonical_hr_hunter_workflow.json" | awk '{print $1}')
[[ "$actual_sha" == "$canonical_sha" ]] || { printf 'canonical workflow SHA mismatch\n' >&2; exit 75; }

umask 077
if [[ ! -f "$env_file" ]]; then
  hunter_secret=$(openssl rand -hex 32)
  n8n_key=$(openssl rand -hex 32)
  {
    printf 'COMPOSE_PROJECT_NAME=%s\n' "$project"
    printf 'HUNTER_API_SECRET=%s\n' "$hunter_secret"
    printf 'N8N_ENCRYPTION_KEY=%s\n' "$n8n_key"
    printf 'FASTAPI_BASE_URL=http://hunter:8000\nN8N_BASE_URL=http://n8n:5678\nOLLAMA_BASE_URL=http://ollama:11434\n'
    printf 'HR_AGENT_OLLAMA_MODEL=gemma3:4b\nHR_AGENT_OLLAMA_TIMEOUT_SECONDS=600\nHR_AGENT_PROCESS_TIMEOUT_SECONDS=240\n'
    printf 'HUNTER_ENABLE_TELEGRAM=false\nHUNTER_ENABLE_DISCOVERY_SCHEDULER=false\nHUNTER_ENABLE_COORDINATOR=false\n'
    printf 'TELEGRAM_ENABLED=false\nDISCOVERY_ENABLED=false\nSCHEDULER_ENABLED=false\nCOORDINATOR_ENABLED=false\n'
    printf 'PRODUCTION_CALLBACKS_ENABLED=false\nCLOUD_SHADOW_MODE=true\nPRODUCTION_STATE_IMPORTED=false\n'
  } > "$env_file"
fi
chmod 0600 "$env_file"

mkdir -p "$(dirname "$generated")" "$root/reports"
python3 "$repo/scripts/render_n8n_deployment_workflow.py" --fastapi-base-url http://hunter:8000 --ollama-base-url http://ollama:11434 --output "$generated"
[[ "$(sha256sum "$repo/n8n/workflows/canonical_hr_hunter_workflow.json" | awk '{print $1}')" == "$canonical_sha" ]]
! grep -Eq 'http://(127\.0\.0\.1|localhost):(8000|5678|11434)' "$generated"

cd "$repo"
compose=(docker compose --project-name "$project" --env-file "$env_file" -f compose.yaml -f compose.netcup-shadow.yaml)
"${compose[@]}" config >/dev/null
"${compose[@]}" pull n8n ollama
"${compose[@]}" build --pull hunter
"${compose[@]}" up -d hunter n8n ollama
"${compose[@]}" exec -T ollama ollama pull gemma3:4b </dev/null

commit=$(git rev-parse HEAD)
report="$root/reports/deployment_$(date -u +%Y%m%dT%H%M%SZ).json"
python3 - "$report" "$commit" "$canonical_sha" "$repo" "$env_file" "$project" <<'PY'
import json, platform, subprocess, sys
from datetime import datetime, timezone
path, commit, digest, repo, env_file, project = sys.argv[1:]
def output(*args):
    try: return subprocess.check_output(args, cwd=repo, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception: return 'unknown'
compose = ('docker','compose','--project-name',project,'--env-file',env_file,'-f','compose.yaml','-f','compose.netcup-shadow.yaml')
data = {
  'timestamp': datetime.now(timezone.utc).isoformat(),
  'repo_head': commit,
  'branch': 'feat/cloud-migration-foundation',
  'host': platform.node(),
  'architecture': platform.machine(),
  'os': output('sh','-c','. /etc/os-release; printf %s "$PRETTY_NAME"'),
  'docker_version': output('docker','--version'),
  'compose_version': output('docker','compose','version'),
  'n8n_version': '2.22.5',
  'canonical_workflow_sha256': digest,
  'container_status': output(*compose,'ps','--format','json'),
  'git_status': output('git','status','--short','--branch'),
  'test_summary': {'deployment_completed': True},
  'safety_status': 'PASS',
  'cloud_shadow_mode': True,
  'production_state_imported': False,
  'telegram_cloud_production': False,
  'discovery_cloud_production': False,
  'production_mac_mutations': 0,
}
with open(path, 'w', encoding='utf-8') as handle:
    json.dump(data, handle, indent=2)
    handle.write('\n')
PY
printf 'CLOUD_COMMIT=%s\n' "$commit"
printf 'DEPLOYMENT_REPORT=%s\n' "$report"
printf 'RESULT: GO_NETCUP_SHADOW_DEPLOYED\n'
REMOTE
