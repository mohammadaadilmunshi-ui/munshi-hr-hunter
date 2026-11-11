#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

host="${NETCUP_HOST:-}"
identity="${NETCUP_SSH_IDENTITY:-}"
ssh_user="${NETCUP_SSH_USER:-munshi}"
while (($#)); do
  case "$1" in
    --host) host=${2:-}; shift 2 ;;
    --identity) identity=${2:-}; shift 2 ;;
    --ssh-user) ssh_user=${2:-}; shift 2 ;;
    -h|--help) printf 'Usage: %s --host HOST --identity KEY [--ssh-user USER]\n' "$0"; exit 0 ;;
    *) netcup_die "unknown argument: $1" ;;
  esac
done
netcup_validate_host "$host"
netcup_validate_identity "$identity"
netcup_verify_remote_identity "$host" "$identity" "$ssh_user"

before=$(netcup_ssh "$host" "$identity" "$ssh_user" 'set -eu
cd /opt/munshi/repo
printf "boot_id=%s\n" "$(cat /proc/sys/kernel/random/boot_id)"
printf "repo_head=%s\n" "$(git rev-parse HEAD)"
printf "canonical_sha=%s\n" "$(sha256sum n8n/workflows/canonical_hr_hunter_workflow.json | awk '\''{print $1}'\'')"
sudo -n systemctl is-enabled docker
')
before_boot=$(printf '%s\n' "$before" | sed -n 's/^boot_id=//p')
before_head=$(printf '%s\n' "$before" | sed -n 's/^repo_head=//p')
[[ -n "$before_boot" && "$before_head" =~ ^[0-9a-f]{40}$ ]] || netcup_die "could not capture pre-reboot state"

netcup_ssh "$host" "$identity" "$ssh_user" "sudo -n systemctl reboot" || true
sleep 10
reconnected=false
for attempt in $(seq 1 72); do
  if netcup_ssh "$host" "$identity" "$ssh_user" true >/dev/null 2>&1; then reconnected=true; break; fi
  sleep 5
done
[[ "$reconnected" == true ]] || netcup_die "SSH did not return after Netcup reboot"
netcup_verify_remote_identity "$host" "$identity" "$ssh_user"

after=$(netcup_ssh "$host" "$identity" "$ssh_user" 'set -eu
cd /opt/munshi/repo
printf "boot_id=%s\n" "$(cat /proc/sys/kernel/random/boot_id)"
printf "repo_head=%s\n" "$(git rev-parse HEAD)"
sudo -n systemctl is-active docker
for service in hunter n8n ollama; do
  cid=$(docker compose --project-name munshi-netcup-shadow --env-file /opt/munshi/secrets/netcup-shadow.env -f compose.yaml -f compose.netcup-shadow.yaml ps -q "$service")
  test -n "$cid"
  test "$(docker inspect -f '\''{{.State.Running}}'\'' "$cid")" = true
done
test -f /opt/munshi/runtime/n8n/cloud_shadow_workflow.json
')
after_boot=$(printf '%s\n' "$after" | sed -n 's/^boot_id=//p')
after_head=$(printf '%s\n' "$after" | sed -n 's/^repo_head=//p')
[[ "$after_boot" != "$before_boot" ]] || netcup_die "remote boot ID did not change"
[[ "$after_head" == "$before_head" ]] || netcup_die "cloud Git commit changed across reboot"

netcup_ssh "$host" "$identity" "$ssh_user" 'cd /opt/munshi/repo && scripts/netcup/verify_shadow.sh'
report_name="reboot_proof_$(date -u +%Y%m%dT%H%M%SZ).txt"
report_timestamp=$(date -u +%FT%TZ)
netcup_ssh "$host" "$identity" "$ssh_user" "mkdir -p /opt/munshi/reports && printf '%s\\n' 'timestamp=$report_timestamp' 'before_boot_id=$before_boot' 'after_boot_id=$after_boot' 'repo_head=$after_head' 'production_mac_mutations=0' 'result=GO_NETCUP_REBOOT_PROOF' > /opt/munshi/reports/$report_name"
printf 'REBOOT_REPORT=/opt/munshi/reports/%s\n' "$report_name"
printf 'RESULT: GO_NETCUP_REBOOT_PROOF\nPRODUCTION_MAC_MUTATIONS: 0\n'
