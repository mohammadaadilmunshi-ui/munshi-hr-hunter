#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${MUNSHI_ROOT:-/opt/munshi}"
REPO="$ROOT/repo"
STATUS_FILE="$ROOT/runtime/stage13-endurance-v2.status"
TARGET_USER="${MUNSHI_DEPLOY_SSH_USER:-munshi}"
PUBLIC_KEY_FILE=""
SOURCE_ROOT="${MUNSHI_DEPLOY_SOURCE_ROOT:-$REPO}"
SOURCE_SHA="${MUNSHI_DEPLOY_SOURCE_SHA:-}"

while (($#)); do
  case "$1" in
    --public-key-file) PUBLIC_KEY_FILE="${2:-}"; shift 2 ;;
    --target-user) TARGET_USER="${2:-}"; shift 2 ;;
    --source-root) SOURCE_ROOT="${2:-}"; shift 2 ;;
    --source-sha) SOURCE_SHA="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: sudo $0 --public-key-file /path/to/github-actions-deploy.pub [--target-user munshi] [--source-root /path/to/approved/worktree] [--source-sha <40-char-sha>]"
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

[[ -d "$REPO/.git" ]] || { echo "production repository missing: $REPO" >&2; exit 15; }
[[ -z "$(git -C "$REPO" status --porcelain)" ]] || { echo "production repository is dirty; refusing" >&2; exit 16; }

[[ -d "$SOURCE_ROOT" ]] || { echo "approved deployment source root missing: $SOURCE_ROOT" >&2; exit 17; }
git -C "$SOURCE_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "approved deployment source root is not a Git worktree: $SOURCE_ROOT" >&2
  exit 18
}
[[ -z "$(git -C "$SOURCE_ROOT" status --porcelain)" ]] || {
  echo "approved deployment source worktree is dirty; refusing" >&2
  exit 19
}
source_head="$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
if [[ -n "$SOURCE_SHA" ]]; then
  [[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "--source-sha must be a full lowercase Git SHA" >&2; exit 20; }
  [[ "$source_head" == "$SOURCE_SHA" ]] || {
    echo "approved source SHA mismatch: expected=$SOURCE_SHA actual=$source_head" >&2
    exit 21
  }
else
  SOURCE_SHA="$source_head"
fi

echo "APPROVED_SOURCE_ROOT=$SOURCE_ROOT"
echo "APPROVED_SOURCE_SHA=$SOURCE_SHA"
echo "PRODUCTION_REPO_HEAD=$(git -C "$REPO" rev-parse HEAD)"
echo "PRODUCTION_REPO_CLEAN=PASS"

for rel in \
  deploy/netcup/deploy_production_release.sh \
  deploy/netcup/verify_production_runtime_contract.sh \
  deploy/netcup/github_deploy_gateway.sh
do
  src="$SOURCE_ROOT/$rel"
  [[ -f "$src" ]] || { echo "missing approved deployment source: $src" >&2; exit 22; }
  bash -n "$src"
done

read -r key_type key_blob _ < "$PUBLIC_KEY_FILE"
[[ "$key_type" == "ssh-ed25519" ]] || { echo "deployment key must be ssh-ed25519" >&2; exit 23; }
[[ "$key_blob" =~ ^[A-Za-z0-9+/=]+$ ]] || { echo "invalid public-key payload" >&2; exit 24; }
ssh-keygen -l -f "$PUBLIC_KEY_FILE" >/dev/null 2>&1 || { echo "public key failed ssh-keygen validation" >&2; exit 25; }

home="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
group="$(id -gn "$TARGET_USER")"
[[ -n "$home" && -d "$home" ]] || { echo "cannot resolve target user home" >&2; exit 26; }

ssh_dir="$home/.ssh"
authorized="$ssh_dir/authorized_keys"
install -d -o "$TARGET_USER" -g "$group" -m 0700 "$ssh_dir"
install -d -o root -g root -m 0755 "$ROOT/bin"

backup_dir="$(mktemp -d /tmp/munshi-deploy-key-install.XXXXXX)"
new_authorized="$(mktemp /tmp/munshi-authorized-keys.XXXXXX)"

deploy_target="$ROOT/bin/deploy-production-release"
verify_target="$ROOT/bin/verify-production-runtime-contract"
gateway_target="$ROOT/bin/github-deploy-gateway"

deploy_had=0
verify_had=0
gateway_had=0
authorized_had=0

[[ -e "$deploy_target" ]] && { cp -a "$deploy_target" "$backup_dir/deploy-production-release"; deploy_had=1; }
[[ -e "$verify_target" ]] && { cp -a "$verify_target" "$backup_dir/verify-production-runtime-contract"; verify_had=1; }
[[ -e "$gateway_target" ]] && { cp -a "$gateway_target" "$backup_dir/github-deploy-gateway"; gateway_had=1; }
[[ -e "$authorized" ]] && { cp -a "$authorized" "$backup_dir/authorized_keys"; authorized_had=1; }

rollback_install() {
  rc=$?
  trap - ERR
  echo "=== ROLLBACK GITHUB DEPLOY KEY INSTALL rc=$rc ===" >&2

  if (( deploy_had )); then cp -a "$backup_dir/deploy-production-release" "$deploy_target" || true; else rm -f "$deploy_target" || true; fi
  if (( verify_had )); then cp -a "$backup_dir/verify-production-runtime-contract" "$verify_target" || true; else rm -f "$verify_target" || true; fi
  if (( gateway_had )); then cp -a "$backup_dir/github-deploy-gateway" "$gateway_target" || true; else rm -f "$gateway_target" || true; fi

  if (( authorized_had )); then
    cp -a "$backup_dir/authorized_keys" "$authorized" || true
    chown "$TARGET_USER:$group" "$authorized" || true
    chmod 0600 "$authorized" || true
  else
    rm -f "$authorized" || true
  fi

  rm -f "$new_authorized" || true
  rm -rf "$backup_dir" || true
  echo "RESULT=GITHUB_DEPLOY_KEY_INSTALL_ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback_install ERR

install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/netcup/deploy_production_release.sh" "$ROOT/bin/.deploy-production-release.new"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/netcup/verify_production_runtime_contract.sh" "$ROOT/bin/.verify-production-runtime-contract.new"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/netcup/github_deploy_gateway.sh" "$ROOT/bin/.github-deploy-gateway.new"

mv -f "$ROOT/bin/.deploy-production-release.new" "$deploy_target"
mv -f "$ROOT/bin/.verify-production-runtime-contract.new" "$verify_target"
mv -f "$ROOT/bin/.github-deploy-gateway.new" "$gateway_target"

if [[ -f "$authorized" ]]; then
  grep -v ' munshi-github-actions-deploy$' "$authorized" > "$new_authorized" || true
fi
printf 'restrict,command="/opt/munshi/bin/github-deploy-gateway" %s %s munshi-github-actions-deploy\n' "$key_type" "$key_blob" >> "$new_authorized"
install -o "$TARGET_USER" -g "$group" -m 0600 "$new_authorized" "$authorized"

"$verify_target"

if command -v sshd >/dev/null 2>&1; then
  sshd -t
fi

trap - ERR
rm -f "$new_authorized"
rm -rf "$backup_dir"

echo "STAGE13_GATE=PASS"
echo "APPROVED_SOURCE_SHA=$SOURCE_SHA"
echo "DEPLOY_GATEWAY=$gateway_target"
echo "DEPLOY_WRAPPER=$deploy_target"
echo "DEPLOY_VERIFIER=$verify_target"
echo "AUTHORIZED_KEYS=$authorized"
echo "PRIVATE_KEY_INSTALLED_ON_SERVER=NO"
echo "INSTALL_TRANSACTION=PASS"
echo "RESULT=GITHUB_DEPLOY_KEY_GATEWAY_INSTALLED"
