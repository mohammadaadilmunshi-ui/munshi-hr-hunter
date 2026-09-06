#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Configure MUNSHI_VAULT_KEY on the Netcup production runtime without exposing
# the key to stdout, Git, SQLite, process arguments, or browser output.
#
# This script is intended to run ON the Netcup host as user `munshi` after the
# candidate code has been deployed.  It changes only the production env file and
# recreates Hunter only. n8n and Ollama identity/start times must remain unchanged.

ROOT="${MUNSHI_ROOT:-/opt/munshi}"
REPO="$ROOT/repo"
ENV_FILE="$ROOT/secrets/netcup-shadow.env"
PROJECT="${MUNSHI_COMPOSE_PROJECT:-munshi-netcup-shadow}"
VERIFY="/opt/munshi/bin/verify-production-runtime-contract"

H="munshi-netcup-shadow-hunter-1"
N="munshi-netcup-shadow-n8n-1"
O="munshi-netcup-shadow-ollama-1"

compose=(
  docker compose
  --project-name "$PROJECT"
  --env-file "$ENV_FILE"
  -f "$REPO/compose.yaml"
  -f "$REPO/compose.netcup-shadow.yaml"
  -f "$ROOT/runtime/stage10-imported.override.yaml"
  -f "$ROOT/runtime/stage12-production.override.yaml"
  -f "$ROOT/runtime/stage12-n8n-runtime-repair.override.yaml"
)

[[ -x "$VERIFY" ]] || { echo "production verifier missing: $VERIFY" >&2; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo "production env file missing: $ENV_FILE" >&2; exit 3; }
[[ -d "$REPO/.git" ]] || { echo "production repository missing: $REPO" >&2; exit 4; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
env_backup="$ROOT/secrets/netcup-shadow.env.pre-vault-$stamp"
env_changed=0
hunter_recreated=0

wait_hunter() {
  local healthy=0 health=""
  for _ in $(seq 1 48); do
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$H" 2>/dev/null || true)"
    if [[ "$health" == "healthy" ]]; then healthy=1; break; fi
    sleep 5
  done
  [[ "$healthy" == "1" ]]
}

rollback() {
  rc=$?
  trap - ERR
  echo "=== VAULT ACTIVATION ROLLBACK rc=$rc ===" >&2
  if (( env_changed )) && [[ -f "$env_backup" ]]; then
    cp -p "$env_backup" "$ENV_FILE" || true
    chmod 600 "$ENV_FILE" || true
    echo "VAULT_ENV_ROLLBACK=RESTORED" >&2
  fi
  if (( hunter_recreated )); then
    "${compose[@]}" up -d --no-deps --force-recreate hunter || true
    wait_hunter || true
  fi
  "$VERIFY" || true
  echo "RESULT=VAULT_ACTIVATION_ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback ERR

echo "=== PRECHECK PRODUCTION CONTRACT ==="
"$VERIFY"

[[ -z "$(git -C "$REPO" status --porcelain)" ]] || {
  echo "dirty production repository; refusing vault activation" >&2
  exit 10
}

active_deploy="$(
  ps -eo args= \
    | grep -E '[/]opt/munshi/bin/deploy-production-release --commit|deploy[/]netcup[/]deploy_production_release\.sh --commit' \
    || true
)"
[[ -z "$active_deploy" ]] || {
  echo "production deployment is active; refusing vault activation" >&2
  exit 11
}

n8n_id_before="$(docker inspect -f '{{.Id}}' "$N")"
n8n_started_before="$(docker inspect -f '{{.State.StartedAt}}' "$N")"
ollama_id_before="$(docker inspect -f '{{.Id}}' "$O")"
ollama_started_before="$(docker inspect -f '{{.State.StartedAt}}' "$O")"

# Determine whether the secrets file already contains a syntactically valid key.
# Only the state is printed; the key itself never leaves Python memory.
env_key_state="$(python3 - "$ENV_FILE" <<'PY'
import base64
import sys
from pathlib import Path

path = Path(sys.argv[1])
values = []
for line in path.read_text(encoding="utf-8").splitlines():
    if line.startswith("MUNSHI_VAULT_KEY="):
        values.append(line.split("=", 1)[1].strip())
value = values[-1] if values else ""
if not value:
    print("MISSING")
else:
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception:
        print("INVALID")
    else:
        print("VALID" if len(decoded) == 32 else "INVALID")
PY
)"
echo "PRODUCTION_ENV_VAULT_KEY_STATE=$env_key_state"

if [[ "$env_key_state" == "INVALID" ]]; then
  echo "A non-empty but invalid MUNSHI_VAULT_KEY already exists. Refusing to overwrite it." >&2
  exit 12
fi

container_vault="$(docker exec "$H" python - <<'PY'
from app.secure_vault import vault_available
print("YES" if vault_available() else "NO")
PY
)"
echo "CONTAINER_VAULT_AVAILABLE_BEFORE=$container_vault"

if [[ "$env_key_state" == "MISSING" ]]; then
  encrypted_records="$(docker exec "$H" python - <<'PY'
import sqlite3
from app.database import DB_PATH

db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
db.execute("PRAGMA query_only=ON")
try:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='credential_secret'"
    ).fetchone()
    count = 0 if not exists else int(db.execute("SELECT COUNT(*) FROM credential_secret").fetchone()[0])
finally:
    db.close()
print(count)
PY
)"
  echo "EXISTING_ENCRYPTED_RECORD_COUNT=$encrypted_records"
  [[ "$encrypted_records" == "0" ]] || {
    echo "Encrypted records exist but no vault key is configured. Refusing to create a replacement key." >&2
    exit 13
  }

  cp -p "$ENV_FILE" "$env_backup"
  chmod 600 "$env_backup"

  # Generate and write the 32-byte URL-safe Base64 key atomically.  The key is
  # never emitted by this script.
  python3 - "$ENV_FILE" <<'PY'
import base64
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
lines = [line for line in lines if not line.startswith("MUNSHI_VAULT_KEY=")]
key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
tmp = path.with_name(path.name + ".vault-new")
tmp.write_text("\n".join(lines + [f"MUNSHI_VAULT_KEY={key}"]) + "\n", encoding="utf-8")
os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
os.replace(tmp, path)
PY
  env_changed=1
  echo "PRODUCTION_ENV_VAULT_KEY_CREATED=YES"
else
  echo "PRODUCTION_ENV_VAULT_KEY_CREATED=NO"
fi

# If the key is already valid in the env file but Hunter has not loaded it yet,
# this same Hunter-only recreation safely activates it.
echo "=== RECREATE PRODUCTION HUNTER ONLY ==="
"${compose[@]}" config -q
"${compose[@]}" up -d --no-deps --force-recreate hunter
hunter_recreated=1
wait_hunter

echo "=== VERIFY VAULT ==="
docker exec "$H" python - <<'PY'
import os
from app.secure_vault import _aesgcm, _key, vault_available

assert vault_available()
key = _key()
assert len(key) == 32
AESGCM = _aesgcm()
nonce = os.urandom(12)
aad = b"munshi-vault-runtime-self-test"
plaintext = b"vault-ready"
ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
assert AESGCM(key).decrypt(nonce, ciphertext, aad) == plaintext
print("PRODUCTION_VAULT_AVAILABLE=PASS")
print("PRODUCTION_VAULT_AES_GCM_SELF_TEST=PASS")
PY

[[ "$(docker inspect -f '{{.Id}}' "$N")" == "$n8n_id_before" ]]
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$N")" == "$n8n_started_before" ]]
[[ "$(docker inspect -f '{{.Id}}' "$O")" == "$ollama_id_before" ]]
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$O")" == "$ollama_started_before" ]]
echo "PRODUCTION_N8N_RECREATED=NO"
echo "PRODUCTION_OLLAMA_RECREATED=NO"

"$VERIFY"

echo "VAULT_ENV_BACKUP=$env_backup"
echo "RESULT=PRODUCTION_VAULT_ACTIVATION_PASS"
trap - ERR
