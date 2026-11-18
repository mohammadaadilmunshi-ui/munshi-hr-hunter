#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PRODUCTION_DEPLOY="/opt/munshi/bin/deploy-production-release"
STAGING_DEPLOY="/opt/munshi/bin/deploy-staging-release"
CODE_ONLY_DEPLOY="/opt/munshi/bin/deploy-code-only-release"
CODE_ONLY_BASE="cc9dbf29428551a258bbbb619b99071c445ff055"
ORIGINAL="${SSH_ORIGINAL_COMMAND:-}"

if [[ "$ORIGINAL" =~ ^/opt/munshi/bin/deploy-production-release\ --commit\ ([0-9a-f]{40})\ --branch\ ([A-Za-z0-9._/-]+)$ ]]; then
  [[ -x "$PRODUCTION_DEPLOY" ]] || {
    echo "production deployment wrapper unavailable" >&2
    exit 70
  }
  commit="${BASH_REMATCH[1]}"
  branch="${BASH_REMATCH[2]}"
  exec "$PRODUCTION_DEPLOY" --commit "$commit" --branch "$branch"
fi

if [[ "$ORIGINAL" =~ ^/opt/munshi/bin/deploy-staging-release\ --commit\ ([0-9a-f]{40})\ --branch\ ([A-Za-z0-9._/-]+)$ ]]; then
  [[ -x "$STAGING_DEPLOY" ]] || {
    echo "staging deployment wrapper unavailable" >&2
    exit 72
  }
  commit="${BASH_REMATCH[1]}"
  branch="${BASH_REMATCH[2]}"
  exec "$STAGING_DEPLOY" --commit "$commit" --branch "$branch"
fi

# Strict one-time migration-free release command. No user-supplied paths,
# shell fragments or variant environment flags can pass the forced gateway.
if [[ "$ORIGINAL" =~ ^/opt/munshi/bin/deploy-code-only-release\ --commit\ ([0-9a-f]{40})\ --branch\ (feat/tsenta-connector-completion-20261015)$ ]]; then
  [[ -x "$CODE_ONLY_DEPLOY" ]] || {
    echo "code-only deployment wrapper unavailable" >&2
    exit 73
  }
  commit="${BASH_REMATCH[1]}"
  branch="${BASH_REMATCH[2]}"
  exec "$CODE_ONLY_DEPLOY" --commit "$commit" --branch "$branch" --expected-base "$CODE_ONLY_BASE"
fi

# Exactly one pre-reviewed image-only release for the MUNSHI crown and loader.
# This is disabled until an administrator installs the signed, root-owned
# matching script. No generic shell, SCP, or installer command is exposed.
CROWN_ONLY_COMMAND="/opt/munshi/bin/deploy-crown-no-backup-release --commit b910c4976f3a3c81f57cb89950a6314cdc6b8048 --branch feat/tsenta-connector-completion-20261015"
if [[ "$ORIGINAL" == "$CROWN_ONLY_COMMAND" ]]; then
  [[ -x /opt/munshi/bin/deploy-crown-no-backup-release ]] || {
    echo "reviewed crown-only production script is not installed" >&2
    exit 74
  }
  exec /opt/munshi/bin/deploy-crown-no-backup-release \
    --commit b910c4976f3a3c81f57cb89950a6314cdc6b8048 \
    --branch feat/tsenta-connector-completion-20261015 \
    --expected-base 31c6c5b7c2942b7c0416ce1dc2f0dd11898f8d5a
fi

# One-time, audited icon 404 routing correction. The previous crown release
# remains available, but this new command must match all bytes exactly.
ICON_ROUTE_COMMAND="/opt/munshi/bin/deploy-icon-route-no-backup-release --commit 7998acf6f89a3f9dd00d574f14777efa31772ab3 --branch feat/tsenta-connector-completion-20261015"
if [[ "$ORIGINAL" == "$ICON_ROUTE_COMMAND" ]]; then
  [[ -x /opt/munshi/bin/deploy-icon-route-no-backup-release ]] || {
    echo "reviewed icon routing release gate not installed" >&2
    exit 75
  }
  exec /opt/munshi/bin/deploy-icon-route-no-backup-release \
    --commit 7998acf6f89a3f9dd00d574f14777efa31772ab3 \
    --branch feat/tsenta-connector-completion-20261015 \
    --expected-base b910c4976f3a3c81f57cb89950a6314cdc6b8048
fi

echo "request rejected by MUNSHI GitHub deployment gateway" >&2
exit 71
