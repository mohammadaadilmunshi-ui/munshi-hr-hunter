#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PRODUCTION_DEPLOY="/opt/munshi/bin/deploy-production-release"
STAGING_DEPLOY="/opt/munshi/bin/deploy-staging-release"
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

echo "request rejected by MUNSHI GitHub deployment gateway" >&2
exit 71
