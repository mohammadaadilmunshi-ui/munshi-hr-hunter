#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${MUNSHI_ROOT:-/opt/munshi}"
REPO="$ROOT/repo"
STATUS_FILE="$ROOT/runtime/stage13-endurance-v2.status"
TARGET_USER="${MUNSHI_DEPLOY_SSH_USER:-munshi}"
PUBLIC_KEY_FILE=""

while (($#)); do
  case "$1" in
    --public-key-file) PUBLIC_KEY_FILE="${2:-}"; shift 2 ;;
    --target-user) TARGET_USER="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: sudo $0 --public-key-file /path/to/github-actions-deploy.pub [--target-user munshi]"
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$EUID" -eq 0 ]] || { echo "run with sudo/root" >&2; exit 10; }
[[ -n "$PUBLIC_KEY_FILE" && -f "$PUBLIC_KEY_FILE" ]] || { echo "--public-key-file is required" >&2; exit 11; }
id "$TARGET_USER" >/dev/null 2>&1 || { echo "target user does not exist: $TARGET_USER" >&2; exit 12; }

[[ -f "$STATUS_FILE" ]] || { echo "Stage 13 status file missing; refusing deploy-key activation" >&2; exit 13; }
grep -qx 'STATE=PASS' "$STATUS_FILE" || { echo "Stage 13 is not PASS; refusing deploy-key activation" >&2; exit 14; }

cd "$REPO"
[[ -z "$(git status --porcelain)" ]] || { echo "production repository is dirty; refusing" >&2; exit 15; }

for src in \
  deploy/netcup/deploy_production_release.sh \
  deploy/netcup/verify_production_runtime_contract.sh \
  deploy/netcup/github_deploy_gateway.sh
do
  [[ -f "$src" ]] || { echo "missing approved deployment source: $src" >&2; exit 16; }
  bash -n "$src"
done

read -r key_type key_blob _ < "$PUBLIC_KEY_FILE"
[[ "$key_type" == "ssh-ed25519" ]] || { echo "deployment key must be ssh-ed25519" >&2; exit 17; }
[[ "$key_blob" =~ ^[A-Za-z0-9+/=]+$ ]] || { echo "invalid public-key payload" >&2; exit 18; }

install -d -o root -g root -m 0755 "$ROOT/bin"
install -o root -g root -m 0755 deploy/netcup/deploy_production_release.sh "$ROOT/bin/deploy-production-release"
install -o root -g root -m 0755 deploy/netcup/verify_production_runtime_contract.sh "$ROOT/bin/verify-production-runtime-contract"
install -o root -g root -m 0755 deploy/netcup/github_deploy_gateway.sh "$ROOT/bin/github-deploy-gateway"

home="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
group="$(id -gn "$TARGET_USER")"
[[ -n "$home" && -d "$home" ]] || { echo "cannot resolve target user home" >&2; exit 19; }

ssh_dir="$home/.ssh"
authorized="$ssh_dir/authorized_keys"
install -d -o "$TARGET_USER" -g "$group" -m 0700 "$ssh_dir"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
if [[ -f "$authorized" ]]; then
  grep -v ' munshi-github-actions-deploy$' "$authorized" > "$tmp" || true
fi
printf 'restrict,command="/opt/munshi/bin/github-deploy-gateway" %s %s munshi-github-actions-deploy\n' "$key_type" "$key_blob" >> "$tmp"
install -o "$TARGET_USER" -g "$group" -m 0600 "$tmp" "$authorized"

"$ROOT/bin/verify-production-runtime-contract"

if command -v sshd >/dev/null 2>&1; then
  sshd -t
fi

echo "STAGE13_GATE=PASS"
echo "DEPLOY_GATEWAY=$ROOT/bin/github-deploy-gateway"
echo "DEPLOY_WRAPPER=$ROOT/bin/deploy-production-release"
echo "DEPLOY_VERIFIER=$ROOT/bin/verify-production-runtime-contract"
echo "AUTHORIZED_KEYS=$authorized"
echo "PRIVATE_KEY_INSTALLED_ON_SERVER=NO"
echo "RESULT=GITHUB_DEPLOY_KEY_GATEWAY_INSTALLED"
