#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${MUNSHI_ROOT:-/opt/munshi}"
REPO="$ROOT/repo"
ENV_FILE="$ROOT/secrets/netcup-shadow.env"
PROJECT="${MUNSHI_COMPOSE_PROJECT:-munshi-netcup-shadow}"
VERIFY="/opt/munshi/bin/verify-production-runtime-contract"
PHASE=""
EXPECTED_SHA=""

while (($#)); do
  case "$1" in
    --phase) PHASE="${2:-}"; shift 2 ;;
    --expected-sha) EXPECTED_SHA="${2:-}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$PHASE" == "prepare" || "$PHASE" == "full" ]] || { echo "--phase must be prepare|full" >&2; exit 3; }
[[ -f "$ENV_FILE" ]] || { echo "production Hunter env missing: $ENV_FILE" >&2; exit 10; }
[[ -d "$REPO/.git" ]] || { echo "production Hunter repo missing" >&2; exit 11; }
if [[ -z "$EXPECTED_SHA" ]]; then EXPECTED_SHA="$(git -C "$REPO" rev-parse HEAD)"; fi
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "expected SHA invalid" >&2; exit 12; }
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$EXPECTED_SHA" ]] || { echo "Hunter production SHA mismatch" >&2; exit 13; }

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

"$VERIFY"

H="munshi-netcup-shadow-hunter-1"
N="munshi-netcup-shadow-n8n-1"
O="munshi-netcup-shadow-ollama-1"
n8n_id="$(docker inspect -f '{{.Id}}' "$N")"
n8n_started="$(docker inspect -f '{{.State.StartedAt}}' "$N")"
ollama_id="$(docker inspect -f '{{.Id}}' "$O")"
ollama_started="$(docker inspect -f '{{.State.StartedAt}}' "$O")"

get_env_value() {
  local key="$1"
  python3 - "$key" "$ENV_FILE" <<'PY'
import sys
key,path=sys.argv[1:]
value=""
with open(path,"r",encoding="utf-8") as fh:
    for raw in fh:
        line=raw.rstrip("\n")
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        k,v=line.split("=",1)
        if k==key:
            value=v
print(value)
PY
}

for key in MUNSHI_APPLY_COMMAND_SECRET MUNSHI_APPLY_HANDOFF_HMAC_SECRET MUNSHI_PRODUCTION_SUBMIT_AUTH_HMAC_SECRET MUNSHI_PRODUCTION_RECEIPT_HMAC_SECRET; do
  value="$(get_env_value "$key")"
  [[ "${#value}" -ge 32 ]] || { echo "required shared AutoApply secret missing/too short: $key" >&2; exit 14; }
done

env_backup="$ENV_FILE.pre-autoapply-$PHASE"
cp -a "$ENV_FILE" "$env_backup"

set_env_value() {
  local key="$1" value="$2"
  python3 - "$key" "$value" "$ENV_FILE" <<'PY'
import os,sys,tempfile
key,value,path=sys.argv[1:]
with open(path,"r",encoding="utf-8") as fh: lines=fh.readlines()
out=[]; found=False
for line in lines:
    if line.startswith(key+"="):
        out.append(f"{key}={value}\n"); found=True
    else:
        out.append(line)
if not found: out.append(f"{key}={value}\n")
d=os.path.dirname(path)
fd,tmp=tempfile.mkstemp(prefix=".hunter-env.",dir=d,text=True)
try:
    with os.fdopen(fd,"w",encoding="utf-8") as fh: fh.writelines(out)
    st=os.stat(path)
    os.chown(tmp,st.st_uid,st.st_gid); os.chmod(tmp,st.st_mode & 0o777)
    os.replace(tmp,path)
except Exception:
    try: os.unlink(tmp)
    except FileNotFoundError: pass
    raise
PY
}

rollback_activation() {
  rc=$?
  trap - ERR
  echo "=== ROLLBACK HUNTER AUTOAPPLY ACTIVATION phase=$PHASE rc=$rc ===" >&2
  cp -a "$env_backup" "$ENV_FILE" || true
  "${compose[@]}" up -d --no-deps --force-recreate hunter || true
  for _ in $(seq 1 48); do
    h="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$H" 2>/dev/null || true)"
    [[ "$h" == "healthy" ]] && break
    sleep 3
  done
  echo "RESULT=HUNTER_AUTOAPPLY_ACTIVATION_ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback_activation ERR

# Reversible AutoApply preparation/runtime capabilities.
for kv in   MUNSHI_ENVIRONMENT=production   MUNSHI_AUTOAPPLY_ACCEPTANCE_PASSED=true   MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED=true   MUNSHI_NATIVE_RESUME_V5_ENABLED=true   MUNSHI_APPLICATION_PLAN_V2_ENABLED=true   MUNSHI_APPLICATION_PLAN_RUNTIME_RESOLUTION_ENABLED=true   MUNSHI_APPLY_LIVE_HANDOFF_ENABLED=true   MUNSHI_APPLY_BACKGROUND_PREPARE_ENABLED=true   MUNSHI_FINAL_REVIEW_ENABLED=true   MUNSHI_APPLICATION_REVIEW_V2_ENABLED=true   MUNSHI_PREPARE_WORKER_ADAPTERS_V1C_ENABLED=true   MUNSHI_PREPARE_ATS_RUNTIME_START_ENABLED=true   MUNSHI_APPLY_INTERNAL_HTTP_ENABLED=true   MUNSHI_APPLY_INTERNAL_HTTP_BASE_URL=http://apply:8000   MUNSHI_APPLY_BASE_URL=http://apply:8000
 do
  set_env_value "${kv%%=*}" "${kv#*=}"
done

if [[ "$PHASE" == "prepare" ]]; then
  set_env_value MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED false
  set_env_value MUNSHI_FINAL_SUBMIT_ENABLED false
else
  set_env_value MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED true
  set_env_value MUNSHI_FINAL_SUBMIT_ENABLED true
fi

# Optional external notification/mail services are activated only when their
# already-provisioned runtime credentials exist. The deployment never invents
# third-party API credentials or Cloudflare signing material.
smtp_key="$(get_env_value MUNSHI_SMTP2GO_API_KEY)"
smtp_sender="$(get_env_value MUNSHI_SMTP2GO_SENDER)"
if [[ -n "$smtp_key" && -n "$smtp_sender" ]]; then
  set_env_value MUNSHI_APPLICATION_EMAIL_NOTIFICATIONS_ENABLED true
  set_env_value MUNSHI_APPLICATION_NOTIFICATION_WORKER_ENABLED true
  echo "APPLICATION_EMAIL_NOTIFICATIONS=ENABLED"
else
  echo "APPLICATION_EMAIL_NOTIFICATIONS=EXTERNAL_CREDENTIAL_NOT_CONFIGURED"
fi
mail_signing="$(get_env_value MUNSHI_MAIL_SIGNING_SECRET)"
mail_artifact="$(get_env_value MUNSHI_MAIL_ARTIFACT_HMAC_SECRET)"
if [[ "${#mail_signing}" -ge 32 && "${#mail_artifact}" -ge 32 ]]; then
  set_env_value MUNSHI_APPLICATION_MAIL_RELAY_ENABLED true
  echo "APPLICATION_MAIL_RELAY=ENABLED"
else
  echo "APPLICATION_MAIL_RELAY=EXTERNAL_ROUTER_SECRET_NOT_CONFIGURED"
fi

"${compose[@]}" config -q
"${compose[@]}" up -d --no-deps --force-recreate hunter

healthy=0
for _ in $(seq 1 60); do
  h="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$H" 2>/dev/null || true)"
  if [[ "$h" == "healthy" ]]; then healthy=1; break; fi
  sleep 3
done
[[ "$healthy" == "1" ]] || { echo "Hunter unhealthy after AutoApply activation" >&2; exit 20; }

envs="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$H")"
for kv in   'MUNSHI_ENVIRONMENT=production'   'MUNSHI_AUTOAPPLY_ACCEPTANCE_PASSED=true'   'MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED=true'   'MUNSHI_NATIVE_RESUME_V5_ENABLED=true'   'MUNSHI_APPLICATION_PLAN_V2_ENABLED=true'   'MUNSHI_APPLICATION_PLAN_RUNTIME_RESOLUTION_ENABLED=true'   'MUNSHI_APPLY_LIVE_HANDOFF_ENABLED=true'   'MUNSHI_APPLY_BACKGROUND_PREPARE_ENABLED=true'   'MUNSHI_APPLICATION_REVIEW_V2_ENABLED=true'   'MUNSHI_PREPARE_WORKER_ADAPTERS_V1C_ENABLED=true'   'MUNSHI_PREPARE_ATS_RUNTIME_START_ENABLED=true'   'MUNSHI_APPLY_INTERNAL_HTTP_ENABLED=true'   'MUNSHI_APPLY_INTERNAL_HTTP_BASE_URL=http://apply:8000'   'MUNSHI_APPLY_BASE_URL=http://apply:8000'
do
  grep -qx "$kv" <<<"$envs" || { echo "Hunter activation env mismatch: $kv" >&2; exit 21; }
done
if [[ "$PHASE" == "full" ]]; then
  grep -qx 'MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED=true' <<<"$envs"
  grep -qx 'MUNSHI_FINAL_SUBMIT_ENABLED=true' <<<"$envs"
else
  grep -qx 'MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED=false' <<<"$envs"
  grep -qx 'MUNSHI_FINAL_SUBMIT_ENABLED=false' <<<"$envs"
fi

docker exec "$H" python - <<'PY'
import urllib.request
with urllib.request.urlopen("http://apply:8000/health", timeout=5) as response:
    if response.status != 200:
        raise SystemExit(response.status)
print("HUNTER_TO_APPLY_PRIVATE_BRIDGE_HEALTH=PASS")
PY

[[ "$(docker inspect -f '{{.Id}}' "$N")" == "$n8n_id" ]]
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$N")" == "$n8n_started" ]]
[[ "$(docker inspect -f '{{.Id}}' "$O")" == "$ollama_id" ]]
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$O")" == "$ollama_started" ]]
echo "N8N_RECREATED=NO"
echo "OLLAMA_RECREATED=NO"

"$VERIFY"
rm -f "$env_backup"
trap - ERR
echo "HUNTER_AUTOAPPLY_PHASE=$PHASE"
echo "HUNTER_SUBMIT_AUTHORITY_DELIVERY=$([[ "$PHASE" == "full" ]] && echo ENABLED || echo DISABLED)"
echo "RESULT=HUNTER_AUTOAPPLY_ACTIVATION_PASS"