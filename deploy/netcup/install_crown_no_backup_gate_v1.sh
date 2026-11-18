#!/usr/bin/env bash
# One-time Netcup CONSOLE ADMIN install. Installs two reviewed, root-owned
# deploy gate files, not the app. No database backup, copy or migration.
# Must be run by an authenticated administrator (root), not an SSH forced key.
set -Eeuo pipefail
umask 077

[[ "$(id -u)" == 0 ]] || { echo "BLOCKED: an authorized root console is required" >&2; exit 2; }
ROOT=/opt/munshi
LIVE=31c6c5b7c2942b7c0416ce1dc2f0dd11898f8d5a
OLD_GATE_SHA=12bec74f157de85e91c02764ba648d4b8cdd35785412e8baaf0de0f16fa4df8a
NEW_GATE_SHA=7a6c0f1e786f2c81722164b972565a5e400bfe1a615d86b6e4b204f975632798
ONE_TIME_SHA=10a5a1352273d18800522ea0073281bb8ff4a47ded2f389a484ff35a38ef5276
SRC="$ROOT/runtime/crown-no-backup-20261117"
GATE="$ROOT/bin/github-deploy-gateway"
DEPLOY="$ROOT/bin/deploy-crown-no-backup-release"

[[ -d "$SRC" && -d "$ROOT/bin" ]] || { echo "BLOCKED: reviewed stage or bin missing"; exit 3; }
[[ "$(git -C "$ROOT/repo" rev-parse HEAD)" == "$LIVE" ]] ||
  { echo "BLOCKED: live commit changed"; exit 4; }
[[ -z "$(git -C "$ROOT/repo" status --porcelain)" ]] ||
  { echo "BLOCKED: live working tree is dirty"; exit 5; }
[[ "$(docker inspect -f '{{.State.Health.Status}}' munshi-netcup-shadow-hunter-1)" == healthy ]] ||
  { echo "BLOCKED: Hunter not healthy"; exit 6; }
[[ -z "$(ps -eo args | grep -E '[d]eploy-production-release --commit|[d]eploy-code-only-release --commit|[d]eploy-crown-no-backup-release --commit' || true)" ]] ||
  { echo "BLOCKED: another production deploy is active"; exit 7; }
[[ ! -e "$DEPLOY" ]] || { echo "BLOCKED: crown gate already installed"; exit 8; }

echo "$ONE_TIME_SHA  $SRC/deploy_crown_no_backup_release_v1.sh" | sha256sum -c --status
echo "$NEW_GATE_SHA  $SRC/github_deploy_gateway.sh" | sha256sum -c --status
echo "$OLD_GATE_SHA  $GATE" | sha256sum -c --status
bash -n "$SRC/deploy_crown_no_backup_release_v1.sh"
bash -n "$SRC/github_deploy_gateway.sh"

# Only two tiny deployment controls change. Snapshotting this 1.7 KB gateway
# is NOT a database backup; it permits exact rollback of SSH routing.
PREV="$ROOT/bin/.github-deploy-gateway.pre-crown-20261117"
[[ ! -e "$PREV" ]] || { echo "BLOCKED: previous gateway snapshot already exists"; exit 9; }
install -o root -g root -m 0755 "$GATE" "$PREV"
install -o root -g root -m 0755 "$SRC/deploy_crown_no_backup_release_v1.sh" "$DEPLOY"

NEW_TMP="$ROOT/bin/.github-deploy-gateway.crown-new"
[[ ! -e "$NEW_TMP" ]] || { echo "BLOCKED: a staged gateway file already exists"; exit 10; }
install -o root -g root -m 0755 "$SRC/github_deploy_gateway.sh" "$NEW_TMP"
mv -f "$NEW_TMP" "$GATE"

echo "$NEW_GATE_SHA  $GATE" | sha256sum -c --status
echo "$ONE_TIME_SHA  $DEPLOY" | sha256sum -c --status
echo "BRAND_GATE_INSTALLED=YES"
echo "DATABASE_BACKUP=NONE"
echo "DATABASE_MIGRATION=NONE"
echo "APP_NOT_RESTARTED=YES"
echo "GATEWAY_ROLLBACK_SOURCE=$PREV"
