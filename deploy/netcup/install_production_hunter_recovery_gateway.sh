#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${MUNSHI_ROOT:-/opt/munshi}"
SOURCE_ROOT="${MUNSHI_RECOVERY_SOURCE_ROOT:-}"
SOURCE_SHA="${MUNSHI_RECOVERY_SOURCE_SHA:-}"
EXPECTED_PROD_SHA="380896964d12199936ee7c676e39352a1a68cec8"
TEMP_PROD_SHA="e55ca0a82d8ede6a5053c0a5705e5bb0e1979a90"

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
[[ -n "$SOURCE_ROOT" && -d "$SOURCE_ROOT/.git" ]] || { echo "approved source worktree required" >&2; exit 11; }
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "full lowercase source SHA required" >&2; exit 12; }
[[ "$(git -C "$SOURCE_ROOT" rev-parse HEAD)" == "$SOURCE_SHA" ]] || { echo "source SHA mismatch" >&2; exit 13; }
[[ -z "$(git -C "$SOURCE_ROOT" status --porcelain)" ]] || { echo "source worktree dirty" >&2; exit 14; }

PROD_REPO="$ROOT/repo"
[[ -d "$PROD_REPO/.git" ]] || { echo "production repo missing" >&2; exit 15; }
[[ -z "$(git -C "$PROD_REPO" status --porcelain)" ]] || { echo "production repo dirty" >&2; exit 16; }
prod_head="$(git -C "$PROD_REPO" rev-parse HEAD)"
case "$prod_head" in
  "$EXPECTED_PROD_SHA"|"$TEMP_PROD_SHA") ;;
  *) echo "unexpected production head: $prod_head" >&2; exit 17 ;;
esac

gateway_src="$SOURCE_ROOT/deploy/netcup/github_deploy_gateway.sh"
recovery_src="$SOURCE_ROOT/deploy/netcup/recover_production_hunter.sh"
[[ -f "$gateway_src" && -f "$recovery_src" ]] || { echo "approved recovery source incomplete" >&2; exit 18; }
bash -n "$gateway_src"
bash -n "$recovery_src"
grep -Fq '/opt/munshi/bin/recover-production-hunter' "$gateway_src"
grep -Fq 'RESULT=PRODUCTION_HUNTER_RECOVERY_PASS' "$recovery_src"
grep -Fq 'DATABASE_RESTORED_OR_REPLACED=NO' "$recovery_src"
grep -Fq 'N8N_RECREATED=NO' "$recovery_src"
grep -Fq 'OLLAMA_RECREATED=NO' "$recovery_src"
grep -Fq 'CADDY_RECREATED=NO' "$recovery_src"
! grep -Eq 'docker (rm|compose .* down).*(n8n|ollama|caddy)' "$recovery_src"

gateway_target="$ROOT/bin/github-deploy-gateway"
recovery_target="$ROOT/bin/recover-production-hunter"
[[ -x "$gateway_target" ]] || { echo "existing gateway missing" >&2; exit 19; }
install -d -o root -g root -m 0755 "$ROOT/bin"

backup_dir="$(mktemp -d /tmp/munshi-production-recovery-gateway.XXXXXX)"
cp -a "$gateway_target" "$backup_dir/github-deploy-gateway"
recovery_had=0
[[ -e "$recovery_target" ]] && { cp -a "$recovery_target" "$backup_dir/recover-production-hunter"; recovery_had=1; }

rollback() {
  rc=$?
  trap - ERR
  cp -a "$backup_dir/github-deploy-gateway" "$gateway_target" || true
  if (( recovery_had )); then
    cp -a "$backup_dir/recover-production-hunter" "$recovery_target" || true
  else
    rm -f "$recovery_target" || true
  fi
  rm -rf "$backup_dir"
  echo "RESULT=PRODUCTION_RECOVERY_GATEWAY_INSTALL_ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback ERR

install -o root -g root -m 0755 "$gateway_src" "$ROOT/bin/.github-deploy-gateway.recovery.new"
install -o root -g root -m 0755 "$recovery_src" "$ROOT/bin/.recover-production-hunter.new"
mv -f "$ROOT/bin/.recover-production-hunter.new" "$recovery_target"
mv -f "$ROOT/bin/.github-deploy-gateway.recovery.new" "$gateway_target"

bash -n "$gateway_target"
bash -n "$recovery_target"
grep -Fq '/opt/munshi/bin/recover-production-hunter' "$gateway_target"
[[ -x "$recovery_target" ]]
if command -v sshd >/dev/null 2>&1; then sshd -t; fi

# Installation changes only two root-owned executable files. It intentionally
# does not run the unhealthy production verifier or touch containers/data.
trap - ERR
rm -rf "$backup_dir"
echo "PRODUCTION_HEAD_AT_INSTALL=$prod_head"
echo "APPROVED_RECOVERY_SOURCE_SHA=$SOURCE_SHA"
echo "AUTHORIZED_KEYS_CHANGED=NO"
echo "CONTAINERS_CHANGED=NO"
echo "DATABASE_CHANGED=NO"
echo "RESULT=PRODUCTION_HUNTER_RECOVERY_GATEWAY_INSTALLED"
