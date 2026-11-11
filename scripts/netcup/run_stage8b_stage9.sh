#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

host="${NETCUP_HOST:-}"
identity="${NETCUP_SSH_IDENTITY:-$HOME/.ssh/munshi_netcup_ed25519}"
ssh_user="${NETCUP_SSH_USER:-munshi}"
prepare_only=false
do_bootstrap=false
do_deploy=false
do_verify=false
do_benchmark=false
do_reboot=false
do_report=false
do_cleanup=false
endurance_hours=""

usage() {
  printf 'Usage: %s [--prepare-only] [--host HOST] [--identity KEY] [--ssh-user USER] [--bootstrap] [--deploy] [--verify] [--benchmark] [--reboot-proof] [--endurance-hours 1|6|24|48|72] [--report] [--cleanup-shadow]\n' "$0"
}

while (($#)); do
  case "$1" in
    --prepare-only) prepare_only=true; shift ;;
    --host) host=${2:-}; shift 2 ;;
    --identity) identity=${2:-}; shift 2 ;;
    --ssh-user) ssh_user=${2:-}; shift 2 ;;
    --bootstrap) do_bootstrap=true; shift ;;
    --deploy) do_deploy=true; shift ;;
    --verify) do_verify=true; shift ;;
    --benchmark) do_benchmark=true; shift ;;
    --reboot-proof) do_reboot=true; shift ;;
    --endurance-hours) endurance_hours=${2:-}; shift 2 ;;
    --report) do_report=true; shift ;;
    --cleanup-shadow) do_cleanup=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) netcup_die "unknown argument: $1" ;;
  esac
done

"$SCRIPT_DIR/local_preapproval_validate.sh"
if [[ -z "$host" || "$prepare_only" == true ]]; then
  printf 'RESULT: WAITING_FOR_NETCUP_PROVISIONING\n'
  printf 'NEXT_COMMAND: NETCUP_HOST=<HOST> %q --host <HOST> --identity %q --bootstrap --deploy --verify --benchmark --reboot-proof --endurance-hours 1 --report\n' "$0" "$identity"
  printf 'PRODUCTION_MAC_MUTATIONS: 0\n'
  exit 0
fi

netcup_validate_host "$host"
netcup_validate_identity "$identity"
if [[ "$do_bootstrap" == true ]]; then
  "$SCRIPT_DIR/bootstrap_netcup_host.sh" --host "$host" --identity "$identity" --ssh-user root
  ssh_user=munshi
fi
if [[ "$do_deploy" == true ]]; then "$SCRIPT_DIR/deploy_shadow.sh" --host "$host" --identity "$identity" --ssh-user "$ssh_user"; fi
if [[ "$do_verify" == true ]]; then netcup_ssh "$host" "$identity" "$ssh_user" 'cd /opt/munshi/repo && scripts/netcup/verify_shadow.sh'; fi
if [[ "$do_benchmark" == true ]]; then netcup_ssh "$host" "$identity" "$ssh_user" 'cd /opt/munshi/repo && scripts/netcup/benchmark_host.sh'; fi
if [[ "$do_reboot" == true ]]; then "$SCRIPT_DIR/reboot_proof.sh" --host "$host" --identity "$identity" --ssh-user "$ssh_user"; fi
if [[ -n "$endurance_hours" ]]; then netcup_ssh "$host" "$identity" "$ssh_user" "cd /opt/munshi/repo && scripts/netcup/endurance_watch.sh --hours '$endurance_hours'"; fi
if [[ "$do_report" == true ]]; then netcup_ssh "$host" "$identity" "$ssh_user" 'cd /opt/munshi/repo && scripts/netcup/endurance_report.sh'; fi
if [[ "$do_cleanup" == true ]]; then
  netcup_ssh "$host" "$identity" "$ssh_user" 'set -eu; cd /opt/munshi/repo; docker compose --project-name munshi-netcup-shadow --env-file /opt/munshi/secrets/netcup-shadow.env -f compose.yaml -f compose.netcup-shadow.yaml down --remove-orphans'
  printf 'RESULT: GO_NETCUP_SHADOW_STOPPED_STATE_RETAINED\n'
fi
