#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

DEPLOY="/opt/munshi/bin/deploy-production-release"
ORIGINAL="${SSH_ORIGINAL_COMMAND:-}"

[[ -x "$DEPLOY" ]] || {
  echo "deployment wrapper unavailable" >&2
  exit 70
}

if [[ "$ORIGINAL" =~ ^/opt/munshi/bin/deploy-production-release\ --commit\ ([0-9a-f]{40})\ --branch\ ([A-Za-z0-9._/-]+)$ ]]; then
  commit="${BASH_REMATCH[1]}"
  branch="${BASH_REMATCH[2]}"
else
  echo "request rejected by MUNSHI GitHub deployment gateway" >&2
  exit 71
fi

exec "$DEPLOY" --commit "$commit" --branch "$branch"
