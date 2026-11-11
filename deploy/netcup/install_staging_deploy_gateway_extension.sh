#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${MUNSHI_ROOT:-/opt/munshi}"
PROD_REPO="$ROOT/repo"
STATUS_FILE="$ROOT/runtime/stage13-endurance-v2.status"
SOURCE_ROOT="${MUNSHI_DEPLOY_SOURCE_ROOT:-}"
SOURCE_SHA="${MUNSHI_DEPLOY_SOURCE_SHA:-}"

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

[[ "$EUID" -eq 0 ]] || { echo "run as root" >&2; exit 10; }
[[ -n "$SOURCE_ROOT" && -d "$SOURCE_ROOT/.git" ]] || { echo "--source-root must be an approved Git worktree" >&2; exit 11; }
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "--source-sha must be a full lowercase Git SHA" >&2; exit 12; }
[[ "$(git -C "$SOURCE_ROOT" rev-parse HEAD)" == "$SOURCE_SHA" ]] || { echo "approved source SHA mismatch" >&2; exit 13; }
[[ -z "$(git -C "$SOURCE_ROOT" status --porcelain)" ]] || { echo "approved source worktree is dirty" >&2; exit 14; }
[[ -d "$PROD_REPO/.git" && -z "$(git -C "$PROD_REPO" status --porcelain)" ]] || { echo "production repository missing or dirty" >&2; exit 15; }
grep -qx 'STATE=PASS' "$STATUS_FILE" || { echo "Stage 13 is not PASS" >&2; exit 16; }

for rel in \
  deploy/netcup/deploy_staging_release.sh \
  deploy/netcup/verify_staging_runtime_contract.sh \
  deploy/netcup/github_deploy_gateway.sh
do
  [[ -f "$SOURCE_ROOT/$rel" ]] || { echo "missing approved source: $rel" >&2; exit 17; }
  bash -n "$SOURCE_ROOT/$rel"
done

prod_verify="$ROOT/bin/verify-production-runtime-contract"
prod_deploy="$ROOT/bin/deploy-production-release"
gateway_target="$ROOT/bin/github-deploy-gateway"
staging_deploy="$ROOT/bin/deploy-staging-release"
staging_verify="$ROOT/bin/verify-staging-runtime-contract"

for path in "$prod_verify" "$prod_deploy" "$gateway_target"; do
  [[ -x "$path" ]] || { echo "existing hardened production gateway component missing: $path" >&2; exit 18; }
done

"$prod_verify"

backup_dir="$(mktemp -d /tmp/munshi-staging-gateway-extension.XXXXXX)"
gateway_had=0
staging_deploy_had=0
staging_verify_had=0

[[ -e "$gateway_target" ]] && { cp -a "$gateway_target" "$backup_dir/github-deploy-gateway"; gateway_had=1; }
[[ -e "$staging_deploy" ]] && { cp -a "$staging_deploy" "$backup_dir/deploy-staging-release"; staging_deploy_had=1; }
[[ -e "$staging_verify" ]] && { cp -a "$staging_verify" "$backup_dir/verify-staging-runtime-contract"; staging_verify_had=1; }

rollback() {
  rc=$?
  trap - ERR
  echo "=== ROLLBACK STAGING GATEWAY EXTENSION rc=$rc ===" >&2
  if (( gateway_had )); then cp -a "$backup_dir/github-deploy-gateway" "$gateway_target" || true; fi
  if (( staging_deploy_had )); then cp -a "$backup_dir/deploy-staging-release" "$staging_deploy" || true; else rm -f "$staging_deploy" || true; fi
  if (( staging_verify_had )); then cp -a "$backup_dir/verify-staging-runtime-contract" "$staging_verify" || true; else rm -f "$staging_verify" || true; fi
  "$prod_verify" || true
  rm -rf "$backup_dir"
  echo "RESULT=STAGING_GATEWAY_EXTENSION_ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback ERR

install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/netcup/deploy_staging_release.sh" "$ROOT/bin/.deploy-staging-release.new"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/netcup/verify_staging_runtime_contract.sh" "$ROOT/bin/.verify-staging-runtime-contract.new"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/netcup/github_deploy_gateway.sh" "$ROOT/bin/.github-deploy-gateway.new"

mv -f "$ROOT/bin/.deploy-staging-release.new" "$staging_deploy"
mv -f "$ROOT/bin/.verify-staging-runtime-contract.new" "$staging_verify"
mv -f "$ROOT/bin/.github-deploy-gateway.new" "$gateway_target"

"$prod_verify"
"$staging_verify"
sshd -t

trap - ERR
rm -rf "$backup_dir"

echo "APPROVED_SOURCE_SHA=$SOURCE_SHA"
echo "PRODUCTION_GATEWAY_PRESERVED=PASS"
echo "STAGING_DEPLOY_WRAPPER=$staging_deploy"
echo "STAGING_VERIFIER=$staging_verify"
echo "GATEWAY=$gateway_target"
echo "AUTHORIZED_KEYS_CHANGED=NO"
echo "PRODUCTION_DEPLOYMENT_PERFORMED=NO"
echo "STAGING_DEPLOYMENT_PERFORMED=NO"
echo "RESULT=STAGING_GATEWAY_EXTENSION_INSTALLED"
