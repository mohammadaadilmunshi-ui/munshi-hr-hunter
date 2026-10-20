#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PRODUCTION_DEPLOY="/opt/munshi/bin/deploy-production-release"
STAGING_DEPLOY="/opt/munshi/bin/deploy-staging-release"
CODE_ONLY_DEPLOY="/opt/munshi/bin/deploy-code-only-release"
CODE_ONLY_BASE="2405721b7bb0b1185ccd67500230b293a0e7b059"
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

echo "request rejected by MUNSHI GitHub deployment gateway" >&2
exit 71
