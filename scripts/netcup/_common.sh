#!/usr/bin/env bash

NETCUP_CANONICAL_REPO="https://github.com/mohammadaadilmunshi-ui/munshi-hr-hunter.git"
NETCUP_CANONICAL_BRANCH="feat/cloud-migration-foundation"
NETCUP_CANONICAL_WORKFLOW_SHA="501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f"
NETCUP_COMPOSE_PROJECT="munshi-netcup-shadow"
NETCUP_REMOTE_ROOT="/opt/munshi"

netcup_die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

netcup_require_command() {
  command -v "$1" >/dev/null 2>&1 || netcup_die "required command is unavailable: $1"
}

netcup_validate_host() {
  local host=${1:-}
  local host_lower
  [[ -n "$host" ]] || netcup_die "an explicit Netcup host is required"
  [[ "$host" =~ ^[A-Za-z0-9._:-]+$ ]] || netcup_die "host contains unsafe characters"
  host_lower=$(printf '%s' "$host" | LC_ALL=C tr '[:upper:]' '[:lower:]')
  case "$host_lower" in
    localhost|localhost.*|127.*|0.0.0.0|::1|\[::1\]) netcup_die "refusing localhost as a Netcup target" ;;
  esac
  [[ "$host" != *"/Users/"* && "$host" != *"Aadil-HR-Hunter"* ]] || netcup_die "refusing a Mac production path"
}

netcup_validate_identity() {
  local identity=${1:-}
  [[ -n "$identity" ]] || netcup_die "an explicit SSH identity is required"
  [[ -f "$identity" ]] || netcup_die "SSH identity does not exist: $identity"
  [[ "$identity" != *"Aadil-HR-Hunter"* ]] || netcup_die "refusing a production-tree identity path"
}

netcup_ssh() {
  local host=$1 identity=$2 user=$3
  shift 3
  ssh -i "$identity" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=20 -o ConnectTimeout=15 "$user@$host" "$@"
}

netcup_scp() {
  local host=$1 identity=$2 user=$3 source=$4 destination=$5
  scp -i "$identity" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$source" "$user@$host:$destination"
}

netcup_verify_remote_identity() {
  local host=$1 identity=$2 user=$3
  local result
  result=$(netcup_ssh "$host" "$identity" "$user" "uname -s; uname -m; printf '%s\\n' \"\${HOME:-}\"")
  [[ "$(printf '%s\n' "$result" | sed -n '1p')" == "Linux" ]] || netcup_die "remote target is not Linux"
  [[ "$(printf '%s\n' "$result" | sed -n '2p')" == "x86_64" ]] || netcup_die "remote target is not x86_64"
  [[ "$result" != *"/Users/"* && "$result" != *"Aadil-HR-Hunter"* ]] || netcup_die "remote target resolves to a Mac production path"
}

netcup_canonical_sha() {
  local root=$1
  netcup_sha256_file "$root/n8n/workflows/canonical_hr_hunter_workflow.json"
}

netcup_sha256_file() {
  local path=$1
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
  else
    netcup_die "required SHA-256 utility is unavailable (sha256sum or shasum)"
  fi
}
