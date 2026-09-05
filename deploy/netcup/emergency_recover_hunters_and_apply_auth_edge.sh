#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[[ "${1:-}" == "--apply" ]] || {
  echo "Usage: $0 --apply" >&2
  echo "Exact-state recovery for the 2026-09-05 trusted-device auth rollout only." >&2
  exit 2
}

PROD_SHA="380896964d12199936ee7c676e39352a1a68cec8"
PROD_BRANCH="fix/dashboard-device-auth-prod-v1"
STAGING_SHA="1785c022a6a9d3205fcfc36b2aed120494044158"
STAGING_BRANCH="fix/dashboard-device-auth-staging-v1"

PROD_TEMP_SHA="e55ca0a82d8ede6a5053c0a5705e5bb0e1979a90"
STAGING_TEMP_SHA="2fd6814c6685bb21adb1d0d7fd60249680da1c1c"

PROD_ROLLBACK="munshi-netcup-shadow-hunter:rollback-deploy-20260905T022334Z"
STAGING_ROLLBACK="munshi-netcup-staging-hunter:staging-rollback-20260905T021130Z"

PROD_ROOT="/opt/munshi"
PROD_REPO="$PROD_ROOT/repo"
PROD_ENV="$PROD_ROOT/secrets/netcup-shadow.env"
STAGING_ROOT="/home/munshi/munshi-staging-v1"
STAGING_REPO="$STAGING_ROOT/repo"
STAGING_ENV="$STAGING_ROOT/staging.env"
STAGING_OVERRIDE="$STAGING_ROOT/staging.override.yaml"
EDGE="munshi-staging-edge-caddy"

PROD_H="munshi-netcup-shadow-hunter-1"
PROD_N="munshi-netcup-shadow-n8n-1"
PROD_O="munshi-netcup-shadow-ollama-1"
STG_H="munshi-netcup-staging-hunter-1"
STG_N="munshi-netcup-staging-n8n-1"
STG_O="munshi-netcup-staging-ollama-1"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${HOME:-/home/munshi}/MUNSHI_AUTH_RECOVERY_${STAMP}.log"
exec > >(tee "$REPORT") 2>&1

echo "================================================================"
echo " MUNSHI — EXACT-STATE HUNTER RECOVERY + TRUSTED-DEVICE EDGE V1"
echo "================================================================"
echo "UTC=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "PRODUCTION_TARGET_SHA=$PROD_SHA"
echo "STAGING_TARGET_SHA=$STAGING_SHA"
echo "REPORT=$REPORT"

for tool in docker git python3 curl sha256sum; do
  command -v "$tool" >/dev/null || { echo "MISSING_TOOL=$tool" >&2; exit 10; }
done

docker info >/dev/null 2>&1 || { echo "DOCKER_ACCESS=FAIL" >&2; exit 11; }

for path in \
  "$PROD_REPO/.git" \
  "$PROD_ENV" \
  "$PROD_ROOT/runtime/stage10-imported.override.yaml" \
  "$PROD_ROOT/runtime/stage12-production.override.yaml" \
  "$PROD_ROOT/runtime/stage12-n8n-runtime-repair.override.yaml" \
  "$STAGING_REPO/.git" \
  "$STAGING_ENV" \
  "$STAGING_OVERRIDE"
do
  [[ -e "$path" ]] || { echo "MISSING_RUNTIME_PATH=$path" >&2; exit 12; }
done

for c in "$PROD_H" "$PROD_N" "$PROD_O" "$STG_H" "$STG_N" "$STG_O" "$EDGE"; do
  docker inspect "$c" >/dev/null 2>&1 || { echo "MISSING_RUNTIME_OBJECT=$c" >&2; exit 13; }
done

docker image inspect "$PROD_ROLLBACK" >/dev/null 2>&1 || { echo "MISSING_PROD_ROLLBACK_IMAGE=$PROD_ROLLBACK" >&2; exit 14; }
docker image inspect "$STAGING_ROLLBACK" >/dev/null 2>&1 || { echo "MISSING_STAGING_ROLLBACK_IMAGE=$STAGING_ROLLBACK" >&2; exit 15; }

[[ -z "$(git -C "$PROD_REPO" status --porcelain)" ]] || { echo "PRODUCTION_REPO_DIRTY=YES" >&2; exit 16; }
[[ -z "$(git -C "$STAGING_REPO" status --porcelain)" ]] || { echo "STAGING_REPO_DIRTY=YES" >&2; exit 17; }

prod_head="$(git -C "$PROD_REPO" rev-parse HEAD)"
stg_head="$(git -C "$STAGING_REPO" rev-parse HEAD)"
echo "PRODUCTION_HEAD_BEFORE=$prod_head"
echo "STAGING_HEAD_BEFORE=$stg_head"
case "$prod_head" in "$PROD_SHA"|"$PROD_TEMP_SHA") ;; *) echo "UNEXPECTED_PRODUCTION_HEAD=$prod_head" >&2; exit 18;; esac
case "$stg_head" in "$STAGING_SHA"|"$STAGING_TEMP_SHA") ;; *) echo "UNEXPECTED_STAGING_HEAD=$stg_head" >&2; exit 19;; esac

git -C "$PROD_REPO" cat-file -e "$PROD_SHA^{commit}"
git -C "$STAGING_REPO" cat-file -e "$STAGING_SHA^{commit}"

echo "=== CAPTURE NON-HUNTER IDENTITIES ==="
prod_n_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$PROD_N")"
prod_o_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$PROD_O")"
stg_n_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$STG_N")"
stg_o_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$STG_O")"
edge_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}|{{.RestartCount}}' "$EDGE")"
echo "NON_HUNTER_BASELINE=CAPTURED"

prod_image_name="$(docker inspect -f '{{.Config.Image}}' "$PROD_H")"
stg_image_name="$(docker inspect -f '{{.Config.Image}}' "$STG_H")"
prod_volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/hunter/data"}}{{.Name}}{{end}}{{end}}' "$PROD_H")"
stg_volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/hunter/data"}}{{.Name}}{{end}}{{end}}' "$STG_H")"
[[ -n "$prod_image_name" && -n "$stg_image_name" && -n "$prod_volume" && -n "$stg_volume" ]] || {
  echo "HUNTER_IMAGE_OR_VOLUME_RESOLUTION=FAIL" >&2
  exit 20
}

echo "=== QUIESCE ONLY THE TWO HUNTER CONTAINERS ==="
docker stop -t 20 "$PROD_H" >/dev/null 2>&1 || true
docker stop -t 20 "$STG_H" >/dev/null 2>&1 || true
echo "HUNTERS_QUIESCED=PASS"

backup_sqlite_volume() {
  local label="$1" volume="$2" image="$3" backup_dir="$4"
  mkdir -p "$backup_dir"
  chmod 700 "$backup_dir" 2>/dev/null || true
  local free backup name uid gid
  free="$(df -PB1 "$backup_dir" | awk 'NR==2 {print $4}')"
  [[ "$free" =~ ^[0-9]+$ ]] || { echo "${label}_BACKUP_FREE_SPACE_UNRESOLVED" >&2; return 1; }
  (( free >= 2147483648 )) || { echo "${label}_BACKUP_FREE_SPACE_TOO_LOW=$free" >&2; return 1; }
  backup="$backup_dir/hunter-auth-recovery-$STAMP.db"
  name="$(basename "$backup")"
  uid="$(id -u)"; gid="$(id -g)"
  timeout 1500s docker run --rm -i \
    --network none \
    --user 0:0 \
    --mount "type=volume,src=$volume,dst=/app/hunter/data" \
    --mount "type=bind,src=$backup_dir,dst=/backup" \
    --entrypoint python \
    "$image" \
    - "/app/hunter/data/hunter.db" "/backup/$name" "$uid" "$gid" <<'PY'
import os, sqlite3, sys
src, dst, uid, gid = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
source.execute("PRAGMA query_only=ON")
source.execute("PRAGMA busy_timeout=60000")
dest = sqlite3.connect(dst)
try:
    source.backup(dest)
    result = dest.execute("PRAGMA quick_check").fetchone()[0]
finally:
    dest.close(); source.close()
if result != "ok":
    raise SystemExit(f"backup quick_check failed: {result}")
os.chown(dst, uid, gid)
os.chmod(dst, 0o600)
print("BACKUP_QUICK_CHECK=PASS")
PY
  [[ -f "$backup" ]]
  python3 - "$backup" <<'PY'
import sqlite3, sys
p=sys.argv[1]
db=sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=60)
try:
    assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
finally:
    db.close()
PY
  echo "${label}_DB_BACKUP=$backup"
}

echo "=== WAL-SAFE DATABASE BACKUPS ==="
backup_sqlite_volume "PRODUCTION" "$prod_volume" "$PROD_ROLLBACK" "$PROD_ROOT/backups"
backup_sqlite_volume "STAGING" "$stg_volume" "$STAGING_ROLLBACK" "$STAGING_ROOT/backups"
echo "DATABASE_RECOVERY_BACKUPS=PASS"

prod_compose=(
  docker compose
  --project-name munshi-netcup-shadow
  --env-file "$PROD_ENV"
  -f "$PROD_REPO/compose.yaml"
  -f "$PROD_REPO/compose.netcup-shadow.yaml"
  -f "$PROD_ROOT/runtime/stage10-imported.override.yaml"
  -f "$PROD_ROOT/runtime/stage12-production.override.yaml"
  -f "$PROD_ROOT/runtime/stage12-n8n-runtime-repair.override.yaml"
)
stg_compose=(
  docker compose
  --project-name munshi-netcup-staging
  --env-file "$STAGING_ENV"
  -f "$STAGING_REPO/compose.yaml"
  -f "$STAGING_REPO/compose.netcup-shadow.yaml"
  -f "$STAGING_OVERRIDE"
)

wait_healthy() {
  local container="$1" label="$2"
  local healthy=0
  for _ in $(seq 1 60); do
    state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
    if [[ "$state" == "healthy" ]]; then healthy=1; break; fi
    sleep 5
  done
  [[ "$healthy" == "1" ]] || {
    echo "${label}_HUNTER_RECOVERY_HEALTH=FAIL" >&2
    docker logs --tail 120 "$container" >&2 || true
    return 1
  }
  echo "${label}_HUNTER_RECOVERY_HEALTH=PASS"
}

echo "=== RESTORE EXACT PRODUCTION AUTH-ONLY RELEASE ==="
git -C "$PROD_REPO" checkout -q -B "$PROD_BRANCH" "$PROD_SHA"
[[ "$(git -C "$PROD_REPO" rev-parse HEAD)" == "$PROD_SHA" ]]
[[ -z "$(git -C "$PROD_REPO" status --porcelain)" ]]
docker tag "$PROD_ROLLBACK" "$prod_image_name"
"${prod_compose[@]}" config -q
"${prod_compose[@]}" up -d --no-deps --force-recreate hunter
wait_healthy "$PROD_H" "PRODUCTION"

echo "=== RESTORE EXACT STAGING AUTH RELEASE ==="
git -C "$STAGING_REPO" checkout -q -B "$STAGING_BRANCH" "$STAGING_SHA"
[[ "$(git -C "$STAGING_REPO" rev-parse HEAD)" == "$STAGING_SHA" ]]
[[ -z "$(git -C "$STAGING_REPO" status --porcelain)" ]]
docker tag "$STAGING_ROLLBACK" "$stg_image_name"
"${stg_compose[@]}" config -q
"${stg_compose[@]}" up -d --no-deps --force-recreate hunter
wait_healthy "$STG_H" "STAGING"

for c in "$PROD_H" "$STG_H"; do
  if docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" | grep -Eq '^MUNSHI_REMOTE_(EDGE_GATEWAY|RECOVERY)_BOOTSTRAP=1$'; then
    echo "TEMPORARY_BOOTSTRAP_FLAG_STILL_ACTIVE=$c" >&2
    exit 30
  fi
done
echo "TEMPORARY_PRIVILEGED_BOOTSTRAP_REMOVED=PASS"

[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$PROD_N")" == "$prod_n_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$PROD_O")" == "$prod_o_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$STG_N")" == "$stg_n_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$STG_O")" == "$stg_o_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}|{{.RestartCount}}' "$EDGE")" == "$edge_before" ]]
echo "N8N_OLLAMA_EDGE_IDENTITIES_UNCHANGED_AFTER_RECOVERY=PASS"

curl -fsS --max-time 10 http://127.0.0.1:8000/health >/dev/null
curl -fsS --max-time 10 http://127.0.0.1:8501/_stcore/health >/dev/null
curl -fsS --max-time 10 http://127.0.0.1:18000/health >/dev/null
curl -fsS --max-time 10 http://127.0.0.1:18501/_stcore/health >/dev/null
echo "RECOVERED_HTTP_HEALTH=PASS"

for c in "$PROD_H" "$STG_H"; do
  docker exec -i "$c" python - <<'PY'
from app.database import get_connection
from app.device_auth import cookie_name, mint_device_token, session_ttl_seconds, verify_device_token
assert session_ttl_seconds() == 30 * 24 * 60 * 60
assert cookie_name() == "__Host-munshi_device_session"
token = mint_device_token("recovery-verifier", now=2_000_000_000)
payload = verify_device_token(token, now=2_000_000_001)
assert payload and payload["sub"] == "recovery-verifier"
connection = get_connection()
try:
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
finally:
    connection.close()
print("DEVICE_AUTH_AND_DB_INTEGRITY=PASS")
PY
done

echo "=== TRUSTED-DEVICE CADDY CUTOVER ==="
mount_json="$(docker inspect "$EDGE")"
mapfile -t mount_fields < <(
  printf '%s' "$mount_json" | python3 -c '
import json, os, sys
obj=json.load(sys.stdin)[0]
target="/etc/caddy/Caddyfile"
c=[]
for m in obj.get("Mounts",[]):
    dest=(m.get("Destination") or "").rstrip("/")
    if target == dest or target.startswith(dest + "/"):
        c.append((len(dest),m))
if not c: raise SystemExit("no Caddy bind mount")
m=max(c,key=lambda x:x[0])[1]
if m.get("Type") != "bind": raise SystemExit("Caddy config is not a bind mount")
src=m.get("Source") or ""; dest=(m.get("Destination") or "").rstrip("/")
rel=os.path.relpath(target,dest)
print(src if rel=="." else os.path.join(src,rel))
'
)
CADDY_SOURCE="${mount_fields[0]}"
[[ -f "$CADDY_SOURCE" && -r "$CADDY_SOURCE" && -w "$CADDY_SOURCE" ]] || {
  echo "CADDY_SOURCE_NOT_WRITABLE=$CADDY_SOURCE" >&2; exit 40;
}

caddy_backup="$STAGING_ROOT/backups/caddy-pre-trusted-device-$STAMP.Caddyfile"
candidate="$(mktemp /tmp/munshi-trusted-device-caddy.XXXXXX)"
cp "$CADDY_SOURCE" "$caddy_backup"
chmod 600 "$caddy_backup"
cp "$CADDY_SOURCE" "$candidate"

hash_count_before="$(grep -Ec '^[[:space:]]+[A-Za-z0-9._@-]+[[:space:]]+\$2[aby]\$' "$CADDY_SOURCE" || true)"
[[ "$hash_count_before" -ge 2 ]] || { echo "EXISTING_PASSWORD_HASH_PROOF=FAIL" >&2; exit 41; }

python3 - "$candidate" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); lines=p.read_text().splitlines(keepends=True)
sites={"dashboard.munshi.systems":(8000,8501),"staging-dashboard.munshi.systems":(18000,18501)}
def site(host):
    s=next((i for i,x in enumerate(lines) if x.strip()==f"{host} {{"),None)
    if s is None: raise SystemExit(f"missing site {host}")
    d=0
    for e in range(s,len(lines)):
        d += lines[e].count("{")-lines[e].count("}")
        if e>s and d==0: return s,e
    raise SystemExit(f"unterminated site {host}")
def auth_body(block):
    s=next((i for i,x in enumerate(block) if x.strip()=="basic_auth {"),None)
    if s is None: raise SystemExit("missing basic_auth")
    d=0
    for e in range(s,len(block)):
        d += block[e].count("{")-block[e].count("}")
        if e>s and d==0:
            body=["            "+x.strip()+"\n" for x in block[s+1:e] if x.strip()]
            if not body: raise SystemExit("empty basic_auth")
            return body
    raise SystemExit("unterminated basic_auth")
def render(host,api,ui,old):
    joined="".join(old)
    if "@device_login path /_munshi-auth/login" in joined:
        return old
    if joined.count("basic_auth {") != 1: raise SystemExit(f"{host}: basic_auth count")
    auth=auth_body(old)
    return [
      f"{host} {{\n","    encode zstd gzip\n","\n",
      "    @device_login path /_munshi-auth/login\n","    handle @device_login {\n","        basic_auth {\n",*auth,"        }\n","\n",
      f"        reverse_proxy 127.0.0.1:{api} {{\n","            header_up X-Munshi-Auth-User {http.auth.user.id}\n","        }\n","    }\n","\n",
      "    @device_auth_api path /_munshi-auth/*\n","    handle @device_auth_api {\n",f"        reverse_proxy 127.0.0.1:{api}\n","    }\n","\n",
      "    handle {\n",f"        forward_auth 127.0.0.1:{api} {{\n","            uri /_munshi-auth/verify\n","            header_up X-Munshi-Original-URI {uri}\n","            copy_headers X-Munshi-Auth-User\n","        }\n","\n",f"        reverse_proxy 127.0.0.1:{ui}\n","    }\n","}\n"]
repls=[]
for h,(api,ui) in sites.items():
    s,e=site(h); repls.append((s,e,render(h,api,ui,lines[s:e+1])))
out=lines[:]
for s,e,new in sorted(repls,reverse=True): out[s:e+1]=new
text="".join(out)
for h,(api,ui) in sites.items():
    assert text.count(f"{h} {{")==1
    assert f"forward_auth 127.0.0.1:{api}" in text
    assert f"reverse_proxy 127.0.0.1:{ui}" in text
p.write_text(text)
PY

hash_count_after="$(grep -Ec '^[[:space:]]+[A-Za-z0-9._@-]+[[:space:]]+\$2[aby]\$' "$candidate" || true)"
[[ "$hash_count_after" == "$hash_count_before" ]] || { echo "PASSWORD_HASH_COUNT_CHANGED" >&2; exit 42; }
[[ "$(grep -c '@device_login path /_munshi-auth/login' "$candidate")" == "2" ]]
grep -Fq 'forward_auth 127.0.0.1:8000' "$candidate"
grep -Fq 'forward_auth 127.0.0.1:18000' "$candidate"

docker cp "$candidate" "$EDGE:/tmp/auth-recovery-caddy-$STAMP.Caddyfile" >/dev/null
docker exec "$EDGE" caddy validate --config "/tmp/auth-recovery-caddy-$STAMP.Caddyfile" --adapter caddyfile >/dev/null
docker exec "$EDGE" rm -f "/tmp/auth-recovery-caddy-$STAMP.Caddyfile" >/dev/null 2>&1 || true
echo "CADDY_CANDIDATE_VALIDATE=PASS"

caddy_modified=0
rollback_caddy() {
  rc=$?
  trap - ERR
  if (( caddy_modified )); then
    echo "ROLLING_BACK_CADDY=YES" >&2
    cp "$caddy_backup" "$CADDY_SOURCE" || true
    docker exec "$EDGE" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || true
    docker exec "$EDGE" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || true
  fi
  rm -f "$candidate" || true
  echo "CADDY_BACKUP=$caddy_backup" >&2
  echo "RESULT=AUTH_EDGE_CUTOVER_ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback_caddy ERR

cp "$candidate" "$CADDY_SOURCE"
caddy_modified=1
docker exec "$EDGE" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
docker exec "$EDGE" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
echo "CADDY_RELOAD=PASS"

verify_edge() {
  local host="$1"
  local root_code login_code headers
  headers="$(mktemp)"
  root_code="$(curl -ksS --max-time 15 -D "$headers" -o /dev/null -w '%{http_code}' "https://$host/" || true)"
  [[ "$root_code" == "303" ]] || { echo "$host ROOT_EXPECTED_303_GOT=$root_code" >&2; rm -f "$headers"; return 1; }
  grep -Eiq '^location: /_munshi-auth/login\?next=' "$headers" || { echo "$host LOGIN_REDIRECT_HEADER=FAIL" >&2; rm -f "$headers"; return 1; }
  : > "$headers"
  login_code="$(curl -ksS --max-time 15 -D "$headers" -o /dev/null -w '%{http_code}' "https://$host/_munshi-auth/login?next=%2F" || true)"
  [[ "$login_code" == "401" ]] || { echo "$host LOGIN_EXPECTED_401_GOT=$login_code" >&2; rm -f "$headers"; return 1; }
  grep -Eiq '^www-authenticate: Basic' "$headers" || { echo "$host BASIC_CHALLENGE_HEADER=FAIL" >&2; rm -f "$headers"; return 1; }
  rm -f "$headers"
  echo "$host TRUSTED_DEVICE_EDGE_CONTRACT=PASS"
}

verify_edge dashboard.munshi.systems
verify_edge staging-dashboard.munshi.systems

[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$PROD_N")" == "$prod_n_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$PROD_O")" == "$prod_o_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$STG_N")" == "$stg_n_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$STG_O")" == "$stg_o_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}|{{.RestartCount}}' "$EDGE")" == "$edge_before" ]]

echo "FINAL_N8N_MUTATION=NO"
echo "FINAL_OLLAMA_MUTATION=NO"
echo "CADDY_CONTAINER_RECREATED=NO"
echo "PRODUCTION_SHA=$PROD_SHA"
echo "STAGING_SHA=$STAGING_SHA"
echo "DEVICE_SESSION_TTL_SECONDS=2592000"
echo "DEVICE_COOKIE=__Host-munshi_device_session"
echo "CADDY_BACKUP=$caddy_backup"
echo "RESULT=MUNSHI_AUTH_RECOVERY_AND_EDGE_CUTOVER_PASS"
trap - ERR
rm -f "$candidate"
