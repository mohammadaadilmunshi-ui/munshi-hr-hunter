#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

root=${MUNSHI_CLOUD_ROOT:-/opt/munshi}
repo=${MUNSHI_REPO_ROOT:-$root/repo}
env_file=${MUNSHI_SHADOW_ENV_FILE:-$root/secrets/netcup-shadow.env}
project=${COMPOSE_PROJECT_NAME:-$NETCUP_COMPOSE_PROJECT}
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || netcup_die "benchmark is remote Linux x86_64 only"
[[ "$root" == /opt/munshi ]] || netcup_die "refusing unexpected cloud root"
[[ -f "$env_file" && -d "$repo/.git" ]] || netcup_die "shadow deployment is not present"
command -v fio >/dev/null || netcup_die "fio was not installed by bootstrap"
command -v sysbench >/dev/null || netcup_die "sysbench was not installed by bootstrap"

mkdir -p "$root/reports/netcup" "$root/runtime/benchmark"
report="$root/reports/netcup/benchmark_$(date -u +%Y%m%dT%H%M%SZ).txt"
disk_probe="$root/runtime/benchmark/non_destructive_fio_probe.bin"
rm_probe() { rm -f "$disk_probe"; }
trap rm_probe EXIT
cd "$repo"
compose=(docker compose --project-name "$project" --env-file "$env_file" -f compose.yaml -f compose.netcup-shadow.yaml)

{
  printf 'timestamp=%s\n' "$(date -u +%FT%TZ)"
  printf 'repo_head=%s\n' "$(git rev-parse HEAD)"
  printf 'branch=%s\n' "$(git branch --show-current)"
  printf 'host=%s\narchitecture=%s\n' "$(hostname -f 2>/dev/null || hostname)" "$(uname -m)"
  . /etc/os-release
  printf 'os=%s\n' "$PRETTY_NAME"
  printf 'docker=%s\ncompose=%s\n' "$(docker --version)" "$(docker compose version)"
  printf 'n8n_version=%s\n' "$("${compose[@]}" exec -T n8n n8n --version | tr -d '\r')"
  printf 'canonical_workflow_sha256=%s\n' "$(sha256sum n8n/workflows/canonical_hr_hunter_workflow.json | awk '{print $1}')"
  printf 'git_status=%s\n' "$(git status --short --branch | tr '\n' ';')"
  printf 'production_mac_mutations=0\n'
  printf '\n[container_status]\n'; "${compose[@]}" ps
  printf '\n[lscpu]\n'; lscpu
  printf '\n[memory]\n'; free -h
  printf '\n[swap]\n'; swapon --show || true
  printf '\n[nvme]\n'; nvme list 2>/dev/null || true
  printf '\n[block_devices]\n'; lsblk -e7 -o NAME,MODEL,SIZE,ROTA,TYPE,FSTYPE,MOUNTPOINTS
  printf '\n[filesystem]\n'; df -hT "$root"
  printf '\n[network]\n'; ip -brief addr; ip route
  printf '\n[cpu_sysbench_30s]\n'; sysbench cpu --threads="$(nproc)" --time=30 run
  printf '\n[fio_512MiB_bounded]\n'
  fio --name=munshi-shadow --filename="$disk_probe" --size=512M --rw=randrw --rwmixread=70 --bs=128k --direct=1 --iodepth=8 --runtime=30 --time_based=1 --group_reporting=1
  printf '\n[docker_hunter_build_timing]\n'
  /usr/bin/time -f 'elapsed_seconds=%e max_rss_kib=%M' "${compose[@]}" build hunter
  printf '\n[ollama_inference_timing]\n'
  /usr/bin/time -f 'elapsed_seconds=%e max_rss_kib=%M' "${compose[@]}" exec -T ollama ollama run gemma3:4b 'Reply with exactly BENCHMARK_READY.' </dev/null
  printf '\n[chromium_startup_timing]\n'
  /usr/bin/time -f 'elapsed_seconds=%e max_rss_kib=%M' "${compose[@]}" exec -T hunter python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']); pg=b.new_page(); pg.set_content('<h1>benchmark</h1>'); assert pg.text_content('h1')=='benchmark'; b.close(); p.stop()"
} 2>&1 | tee "$report"
printf 'BENCHMARK_REPORT=%s\n' "$report"
printf 'RESULT: GO_NETCUP_BENCHMARK_COMPLETE\n'
