#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Provision a MUNSHI vault master key inside the dedicated Docker secret volume.
# The key is never printed, exported, or written into an env file.
#
# Usage:
#   bash deploy/netcup/provision_secure_vault_key.sh \
#     --project munshi-netcup-staging \
#     --compose compose.yaml \
#     --compose compose.netcup-shadow.yaml
#
# After provisioning, the Hunter runtime must receive:
#   MUNSHI_VAULT_KEY_FILE=/run/munshi-secrets/vault.key
#   MUNSHI_VAULT_REQUIRE_KEY_FILE=true

project=""
compose_files=()
image=""

while (($#)); do
  case "$1" in
    --project)
      project="${2:-}"
      shift 2
      ;;
    --compose)
      compose_files+=("${2:-}")
      shift 2
      ;;
    --image)
      image="${2:-}"
      shift 2
      ;;
    -h|--help)
      sed -n '1,24p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

[[ -n "$project" ]] || { echo "--project is required" >&2; exit 2; }
((${#compose_files[@]})) || compose_files=(compose.yaml)
command -v docker >/dev/null || { echo "docker is required" >&2; exit 3; }

docker compose version >/dev/null

compose=(docker compose -p "$project")
for file in "${compose_files[@]}"; do
  [[ -f "$file" ]] || { echo "compose file not found: $file" >&2; exit 4; }
  compose+=(-f "$file")
done

"${compose[@]}" config -q

if [[ -z "$image" ]]; then
  image="$("${compose[@]}" images -q hunter 2>/dev/null | head -1 || true)"
fi
[[ -n "$image" ]] || {
  echo "Hunter image is not built. Build staging/production Hunter first or pass --image." >&2
  exit 5
}
docker image inspect "$image" >/dev/null

volume="${project}_hunter_secrets"
docker volume inspect "$volume" >/dev/null 2>&1 || docker volume create "$volume" >/dev/null

# Write/validate entirely inside the Docker volume. No key material crosses stdout.
docker run --rm \
  --network none \
  --user 0:0 \
  --mount "type=volume,src=${volume},dst=/vault" \
  --entrypoint python \
  "$image" - <<'PY'
from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

path = Path("/vault/vault.key")

if path.exists():
    raw = path.read_text(encoding="ascii").strip()
    try:
        key = base64.urlsafe_b64decode(raw.encode("ascii"))
    except Exception as error:
        raise SystemExit("Existing vault key is malformed; refusing to replace it.") from error
    if len(key) != 32:
        raise SystemExit("Existing vault key is not a 32-byte AES-256 key; refusing to replace it.")
else:
    encoded = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    temp = path.with_suffix(".tmp")
    temp.write_text(encoded + "\n", encoding="ascii")
    os.chown(temp, 10001, 10001)
    os.chmod(temp, 0o400)
    os.replace(temp, path)

os.chown(path, 10001, 10001)
os.chmod(path, 0o400)
stat = path.stat()
if stat.st_uid != 10001:
    raise SystemExit("Vault key owner is not Hunter uid 10001.")
if stat.st_mode & 0o077:
    raise SystemExit("Vault key has unsafe group/world permissions.")

# Validate again without revealing any bytes.
raw = path.read_text(encoding="ascii").strip()
if len(base64.urlsafe_b64decode(raw.encode("ascii"))) != 32:
    raise SystemExit("Vault key validation failed.")
print("SECURE_VAULT_KEY=PROVISIONED_AND_VALID")
PY

echo "vault_volume=$volume"
echo "vault_path=/run/munshi-secrets/vault.key"
echo "secret_value_printed=NO"
echo "RESULT=SECURE_VAULT_KEY_READY"
