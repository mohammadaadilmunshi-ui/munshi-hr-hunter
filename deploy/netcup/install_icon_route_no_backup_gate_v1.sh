#!/usr/bin/env bash
# Netcup root-console-only install of the reviewed, icon-route-only SSH gate.
# Installs tiny control files; no app start, DB backup, DB copy or migration.
set -Eeuo pipefail
umask 077

[[ "$(id -u)" == 0 ]] || { echo "BLOCKED: root console authorization required" >&2; exit 2; }
ROOT=/opt/munshi
LIVE=b910c4976f3a3c81f57cb89950a6314cdc6b8048
PREVIOUS_GATE_SHA=7a6c0f1e786f2c81722164b972565a5e400bfe1a615d86b6e4b204f975632798
NEW_GATE_SHA=d5264b5ff54eb3eb4d7f21c7b169d03415db44e6e80b8841b9c2e5db7d7d8c59
NEW_SCRIPT_SHA=b6ad6b95acd3bd45228e08e8be8f6d0053df08f6fe2ee661a4955e03f34b450c
SRC="$ROOT/runtime/icon-route-20261118"
GATE="$ROOT/bin/github-deploy-gateway"
DEPLOY="$ROOT/bin/deploy-icon-route-no-backup-release"
OLD="$ROOT/bin/.github-deploy-gateway.pre-icon-route-20261118"
NEW="$ROOT/bin/.github-deploy-gateway.icon-route-new"

[[ -d "$SRC" && -d "$ROOT/bin" ]] || { echo "BLOCKED: reviewed staging directory absent"; exit 3; }
# Root can inspect the exact munshi-owned repository without global Git exceptions.
live_head="$(git -c safe.directory="$ROOT/repo" -C "$ROOT/repo" rev-parse --verify HEAD)" ||
  { echo "BLOCKED: unable to read live Git commit"; exit 4; }
[[ "$live_head" == "$LIVE" ]] || { echo "BLOCKED: changed live commit"; exit 4; }
live_dirty="$(git -c safe.directory="$ROOT/repo" -C "$ROOT/repo" status --porcelain)" ||
  { echo "BLOCKED: unable to read Git status"; exit 5; }
[[ -z "$live_dirty" ]] || { echo "BLOCKED: dirty production source"; exit 5; }
[[ "$(docker inspect -f '{{.State.Health.Status}}' munshi-netcup-shadow-hunter-1)" == healthy ]] ||
  { echo "BLOCKED: live Hunter not healthy"; exit 6; }
[[ -z "$(ps -eo args | grep -E '[d]eploy-production-release --commit|[d]eploy-code-only-release --commit|[d]eploy-crown-no-backup-release --commit|[d]eploy-icon-route-no-backup-release --commit' || true)" ]] ||
  { echo "BLOCKED: another release is active"; exit 7; }
[[ ! -e "$DEPLOY" && ! -e "$OLD" && ! -e "$NEW" ]] ||
  { echo "BLOCKED: this one-time gate was already staged/installed"; exit 8; }

echo "$PREVIOUS_GATE_SHA  $GATE" | sha256sum -c --status
echo "$NEW_GATE_SHA  $SRC/github_deploy_gateway.sh" | sha256sum -c --status
echo "$NEW_SCRIPT_SHA  $SRC/deploy_icon_route_no_backup_release_v1.sh" | sha256sum -c --status
bash -n "$SRC/github_deploy_gateway.sh"
bash -n "$SRC/deploy_icon_route_no_backup_release_v1.sh"

# Retain only the tiny previous SSH gateway for exact control rollback.
install -o root -g root -m 0755 "$GATE" "$OLD"
install -o root -g root -m 0755 "$SRC/deploy_icon_route_no_backup_release_v1.sh" "$DEPLOY"
install -o root -g root -m 0755 "$SRC/github_deploy_gateway.sh" "$NEW"
mv -f "$NEW" "$GATE"

echo "$NEW_GATE_SHA  $GATE" | sha256sum -c --status
echo "$NEW_SCRIPT_SHA  $DEPLOY" | sha256sum -c --status
echo "ICON_ROUTE_GATE_INSTALLED=YES"
echo "DATABASE_BACKUP=NONE"
echo "DATABASE_MIGRATION=NONE"
echo "APP_NOT_RESTARTED=YES"
echo "GATEWAY_ROLLBACK_SOURCE=$OLD"
