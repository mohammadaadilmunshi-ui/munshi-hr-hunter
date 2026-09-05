#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${MUNSHI_ROOT:-/opt/munshi}"
SOURCE_ROOT="${MUNSHI_DEPLOY_SOURCE_ROOT:-}"
SOURCE_SHA="${MUNSHI_DEPLOY_SOURCE_SHA:-}"
TARGET_GATEWAY="$ROOT/bin/github-deploy-gateway"
TARGET_EDGE_HELPER="$ROOT/bin/apply-dashboard-device-auth-edge"
PROD_VERIFY="$ROOT/bin/verify-production-runtime-contract"
STAGING_VERIFY="$ROOT/bin/verify-staging-runtime-contract"

while (($#)); do
  case "$1" in
    --source-root) SOURCE_ROOT="${2:-}"; shift 2 ;;
    --source-sha) SOURCE_SHA="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: sudo $0 --source-root /path/to/approved/worktree --source-sha <40-char-sha>"
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$EUID" -eq 0 ]] || { echo "run with sudo/root" >&2; exit 10; }
[[ -n "$SOURCE_ROOT" && -d "$SOURCE_ROOT/.git" ]] || { echo "approved --source-root required" >&2; exit 11; }
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "full lowercase --source-sha required" >&2; exit 12; }
[[ "$(git -C "$SOURCE_ROOT" rev-parse HEAD)" == "$SOURCE_SHA" ]] || { echo "approved source SHA mismatch" >&2; exit 13; }
[[ -z "$(git -C "$SOURCE_ROOT" status --porcelain)" ]] || { echo "approved source worktree dirty" >&2; exit 14; }
[[ -x "$PROD_VERIFY" && -x "$STAGING_VERIFY" ]] || { echo "runtime verifier missing" >&2; exit 15; }

for rel in deploy/netcup/github_deploy_gateway.sh deploy/netcup/apply_dashboard_device_auth_edge.sh; do
  [[ -f "$SOURCE_ROOT/$rel" ]] || { echo "missing approved source: $rel" >&2; exit 16; }
  bash -n "$SOURCE_ROOT/$rel"
done

grep -Fq '/opt/munshi/bin/apply-dashboard-device-auth-edge' "$SOURCE_ROOT/deploy/netcup/github_deploy_gateway.sh"
grep -Fq 'RESULT=DASHBOARD_DEVICE_AUTH_EDGE_PASS' "$SOURCE_ROOT/deploy/netcup/apply_dashboard_device_auth_edge.sh"
grep -Fq 'N8N_OLLAMA_IDENTITIES_UNCHANGED=PASS' "$SOURCE_ROOT/deploy/netcup/apply_dashboard_device_auth_edge.sh"

"$PROD_VERIFY" >/dev/null
"$STAGING_VERIFY" >/dev/null
echo "PREINSTALL_RUNTIME_CONTRACTS=PASS"

backup_dir="$(mktemp -d /tmp/munshi-auth-edge-gateway.XXXXXX)"
gateway_had=0
helper_had=0
[[ -e "$TARGET_GATEWAY" ]] && { cp -a "$TARGET_GATEWAY" "$backup_dir/github-deploy-gateway"; gateway_had=1; }
[[ -e "$TARGET_EDGE_HELPER" ]] && { cp -a "$TARGET_EDGE_HELPER" "$backup_dir/apply-dashboard-device-auth-edge"; helper_had=1; }

rollback() {
  rc=$?
  trap - ERR
  echo "=== ROLLBACK DASHBOARD AUTH EDGE GATEWAY rc=$rc ===" >&2
  if (( gateway_had )); then cp -a "$backup_dir/github-deploy-gateway" "$TARGET_GATEWAY" || true; else rm -f "$TARGET_GATEWAY" || true; fi
  if (( helper_had )); then cp -a "$backup_dir/apply-dashboard-device-auth-edge" "$TARGET_EDGE_HELPER" || true; else rm -f "$TARGET_EDGE_HELPER" || true; fi
  "$PROD_VERIFY" || true
  "$STAGING_VERIFY" || true
  rm -rf "$backup_dir" || true
  echo "RESULT=DASHBOARD_AUTH_EDGE_GATEWAY_INSTALL_ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback ERR

install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/netcup/github_deploy_gateway.sh" "$ROOT/bin/.github-deploy-gateway.new"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/netcup/apply_dashboard_device_auth_edge.sh" "$ROOT/bin/.apply-dashboard-device-auth-edge.new"

mv -f "$ROOT/bin/.github-deploy-gateway.new" "$TARGET_GATEWAY"
mv -f "$ROOT/bin/.apply-dashboard-device-auth-edge.new" "$TARGET_EDGE_HELPER"

bash -n "$TARGET_GATEWAY"
bash -n "$TARGET_EDGE_HELPER"
grep -Fq '/opt/munshi/bin/apply-dashboard-device-auth-edge' "$TARGET_GATEWAY"

"$PROD_VERIFY" >/dev/null
"$STAGING_VERIFY" >/dev/null
if command -v sshd >/dev/null 2>&1; then sshd -t; fi

trap - ERR
rm -rf "$backup_dir"
echo "APPROVED_SOURCE_SHA=$SOURCE_SHA"
echo "DEPLOY_GATEWAY=$TARGET_GATEWAY"
echo "AUTH_EDGE_HELPER=$TARGET_EDGE_HELPER"
echo "AUTHORIZED_KEYS_CHANGED=NO"
echo "PRODUCTION_DEPLOYMENT_PERFORMED=NO"
echo "STAGING_DEPLOYMENT_PERFORMED=NO"
echo "EDGE_CUTOVER_PERFORMED=NO"
echo "RESULT=DASHBOARD_AUTH_EDGE_GATEWAY_INSTALLED"
