#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROD_ROOT="${MUNSHI_ROOT:-/opt/munshi}"
STAGING_ROOT="${MUNSHI_STAGING_ROOT:-/home/munshi/munshi-staging-v1}"
PROD_REPO="$PROD_ROOT/repo"
STAGING_REPO="$STAGING_ROOT/repo"
PROD_VERIFY="$PROD_ROOT/bin/verify-production-runtime-contract"
STAGING_VERIFY="$PROD_ROOT/bin/verify-staging-runtime-contract"
EDGE="${MUNSHI_EDGE_CONTAINER:-munshi-staging-edge-caddy}"

PROD_HUNTER="munshi-netcup-shadow-hunter-1"
PROD_N8N="munshi-netcup-shadow-n8n-1"
PROD_OLLAMA="munshi-netcup-shadow-ollama-1"
STAGING_HUNTER="munshi-netcup-staging-hunter-1"
STAGING_N8N="munshi-netcup-staging-n8n-1"
STAGING_OLLAMA="munshi-netcup-staging-ollama-1"

production_sha=""
staging_sha=""

while (($#)); do
  case "$1" in
    --production-sha) production_sha="${2:-}"; shift 2 ;;
    --staging-sha) staging_sha="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 --production-sha <40-char-sha> --staging-sha <40-char-sha>"
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$production_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "--production-sha must be a full lowercase Git SHA" >&2; exit 3; }
[[ "$staging_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "--staging-sha must be a full lowercase Git SHA" >&2; exit 4; }
[[ -x "$PROD_VERIFY" && -x "$STAGING_VERIFY" ]] || { echo "runtime verifier missing" >&2; exit 5; }
[[ -d "$PROD_REPO/.git" && -d "$STAGING_REPO/.git" ]] || { echo "production/staging repo missing" >&2; exit 6; }
[[ -z "$(git -C "$PROD_REPO" status --porcelain)" ]] || { echo "production repo dirty" >&2; exit 7; }
[[ -z "$(git -C "$STAGING_REPO" status --porcelain)" ]] || { echo "staging repo dirty" >&2; exit 8; }
[[ "$(git -C "$PROD_REPO" rev-parse HEAD)" == "$production_sha" ]] || { echo "production exact-SHA gate failed" >&2; exit 9; }
[[ "$(git -C "$STAGING_REPO" rev-parse HEAD)" == "$staging_sha" ]] || { echo "staging exact-SHA gate failed" >&2; exit 10; }

"$PROD_VERIFY" >/dev/null
"$STAGING_VERIFY" >/dev/null
echo "PRE_CUTOVER_RUNTIME_CONTRACTS=PASS"

docker inspect "$EDGE" >/dev/null 2>&1 || { echo "edge container missing: $EDGE" >&2; exit 11; }

prod_auth_code="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' -H 'X-Munshi-Original-URI: /' http://127.0.0.1:8000/_munshi-auth/verify || true)"
staging_auth_code="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' -H 'X-Munshi-Original-URI: /' http://127.0.0.1:18000/_munshi-auth/verify || true)"
[[ "$prod_auth_code" == "303" ]] || { echo "production auth backend not ready: HTTP=$prod_auth_code" >&2; exit 12; }
[[ "$staging_auth_code" == "303" ]] || { echo "staging auth backend not ready: HTTP=$staging_auth_code" >&2; exit 13; }
echo "AUTH_BACKENDS_READY=PASS"

for c in "$PROD_HUNTER" "$PROD_N8N" "$PROD_OLLAMA" "$STAGING_HUNTER" "$STAGING_N8N" "$STAGING_OLLAMA"; do
  docker inspect "$c" >/dev/null 2>&1 || { echo "required container missing: $c" >&2; exit 14; }
done

prod_n8n_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$PROD_N8N")"
prod_ollama_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$PROD_OLLAMA")"
staging_n8n_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$STAGING_N8N")"
staging_ollama_before="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$STAGING_OLLAMA")"
edge_id_before="$(docker inspect -f '{{.Id}}' "$EDGE")"
edge_started_before="$(docker inspect -f '{{.State.StartedAt}}' "$EDGE")"
edge_restarts_before="$(docker inspect -f '{{.RestartCount}}' "$EDGE")"

mount_json="$(docker inspect "$EDGE")"
mapfile -t mount_fields < <(
  printf '%s' "$mount_json" | python3 -c '
import json, os, sys
obj=json.load(sys.stdin)[0]
target="/etc/caddy/Caddyfile"
candidates=[]
for m in obj.get("Mounts", []):
    dest=(m.get("Destination") or "").rstrip("/")
    if target == dest or target.startswith(dest + "/"):
        candidates.append((len(dest), m))
if not candidates:
    raise SystemExit("no bind mount covers /etc/caddy/Caddyfile")
m=max(candidates, key=lambda x:x[0])[1]
if m.get("Type") != "bind":
    raise SystemExit("Caddy config mount is not a bind mount")
src=m.get("Source") or ""
dest=(m.get("Destination") or "").rstrip("/")
rel=os.path.relpath(target, dest)
print(src if rel == "." else os.path.join(src, rel))
print(str(bool(m.get("RW"))).lower())
'
)
CADDY_SOURCE="${mount_fields[0]}"
CADDY_RW="${mount_fields[1]}"
[[ -f "$CADDY_SOURCE" ]] || { echo "Caddy host source missing" >&2; exit 15; }
[[ -r "$CADDY_SOURCE" && -w "$CADDY_SOURCE" ]] || { echo "Caddy host source is not safely writable by deployment user" >&2; exit 16; }
echo "CADDY_CONFIG_BIND_RESOLVED=PASS"
echo "CADDY_BIND_RW=$CADDY_RW"

backup_dir="$STAGING_ROOT/backups"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$backup_dir/caddy-pre-device-auth-$stamp.Caddyfile"
candidate="$(mktemp /tmp/munshi-device-auth-caddy.XXXXXX)"
container_candidate="/tmp/munshi-device-auth-caddy-$stamp.Caddyfile"
cp "$CADDY_SOURCE" "$backup"
chmod 600 "$backup"
cp "$CADDY_SOURCE" "$candidate"

auth_user_hash_count_before="$(grep -Ec '^[[:space:]]+[A-Za-z0-9._@-]+[[:space:]]+\$2[aby]\$' "$CADDY_SOURCE" || true)"
[[ "$auth_user_hash_count_before" -ge 2 ]] || { echo "could not prove existing password hash entries before rewrite" >&2; exit 17; }

python3 - "$candidate" <<'PY'
from pathlib import Path
import sys

path=Path(sys.argv[1])
text=path.read_text(encoding="utf-8")
lines=text.splitlines(keepends=True)

sites={
    "dashboard.munshi.systems": (8000, 8501),
    "staging-dashboard.munshi.systems": (18000, 18501),
}

def find_site(host):
    start=None
    for i,line in enumerate(lines):
        if line.strip() == f"{host} {{":
            start=i; break
    if start is None:
        raise SystemExit(f"missing site block: {host}")
    depth=0
    for j in range(start,len(lines)):
        depth += lines[j].count("{") - lines[j].count("}")
        if j > start and depth == 0:
            return start,j
    raise SystemExit(f"unterminated site block: {host}")

def basic_auth_body(block):
    start=None
    for i,line in enumerate(block):
        if line.strip() == "basic_auth {":
            start=i; break
    if start is None:
        raise SystemExit("existing basic_auth block missing")
    depth=0
    for j in range(start,len(block)):
        depth += block[j].count("{") - block[j].count("}")
        if j > start and depth == 0:
            body=block[start+1:j]
            if not any(x.strip() for x in body):
                raise SystemExit("existing basic_auth body empty")
            return body
    raise SystemExit("unterminated basic_auth block")

def render(host, fastapi, streamlit, old):
    joined="".join(old)
    if "@device_login path /_munshi-auth/login" in joined:
        return old
    if joined.count("basic_auth {") != 1:
        raise SystemExit(f"{host}: expected exactly one basic_auth block")
    if joined.count(f"reverse_proxy 127.0.0.1:{streamlit}") != 1:
        raise SystemExit(f"{host}: unexpected Streamlit proxy contract")
    auth=basic_auth_body(old)
    normalized=[]
    for line in auth:
        if line.strip():
            normalized.append("            " + line.strip() + "\n")
    return [
        f"{host} {{\n",
        "    encode zstd gzip\n",
        "\n",
        "    @device_login path /_munshi-auth/login\n",
        "    handle @device_login {\n",
        "        basic_auth {\n",
        *normalized,
        "        }\n",
        "\n",
        f"        reverse_proxy 127.0.0.1:{fastapi} {{\n",
        "            header_up X-Munshi-Auth-User {http.auth.user.id}\n",
        "        }\n",
        "    }\n",
        "\n",
        "    @device_auth_api path /_munshi-auth/*\n",
        "    handle @device_auth_api {\n",
        f"        reverse_proxy 127.0.0.1:{fastapi}\n",
        "    }\n",
        "\n",
        "    handle {\n",
        f"        forward_auth 127.0.0.1:{fastapi} {{\n",
        "            uri /_munshi-auth/verify\n",
        "            header_up X-Munshi-Original-URI {uri}\n",
        "            copy_headers X-Munshi-Auth-User\n",
        "        }\n",
        "\n",
        f"        reverse_proxy 127.0.0.1:{streamlit}\n",
        "    }\n",
        "}\n",
    ]

repls=[]
for host,(fastapi,streamlit) in sites.items():
    s,e=find_site(host)
    repls.append((s,e,render(host,fastapi,streamlit,lines[s:e+1])))

out=lines[:]
for s,e,new in sorted(repls, reverse=True):
    out[s:e+1]=new
new_text="".join(out)
for host,(fastapi,streamlit) in sites.items():
    if new_text.count(f"{host} {{") != 1:
        raise SystemExit(f"{host}: duplicate/missing site block")
    if f"forward_auth 127.0.0.1:{fastapi}" not in new_text:
        raise SystemExit(f"{host}: forward_auth missing")
    if f"reverse_proxy 127.0.0.1:{streamlit}" not in new_text:
        raise SystemExit(f"{host}: Streamlit proxy missing")
path.write_text(new_text, encoding="utf-8")
PY

auth_user_hash_count_after="$(grep -Ec '^[[:space:]]+[A-Za-z0-9._@-]+[[:space:]]+\$2[aby]\$' "$candidate" || true)"
[[ "$auth_user_hash_count_after" == "$auth_user_hash_count_before" ]] || { echo "password hash entry count changed" >&2; exit 18; }
[[ "$(grep -c '@device_login path /_munshi-auth/login' "$candidate")" == "2" ]] || { echo "device login contract incomplete" >&2; exit 19; }
grep -Fq 'forward_auth 127.0.0.1:8000' "$candidate"
grep -Fq 'forward_auth 127.0.0.1:18000' "$candidate"
grep -Fq 'reverse_proxy 127.0.0.1:8501' "$candidate"
grep -Fq 'reverse_proxy 127.0.0.1:18501' "$candidate"
echo "CADDY_CANDIDATE_STATIC_CONTRACT=PASS"

docker cp "$candidate" "$EDGE:$container_candidate" >/dev/null
docker exec "$EDGE" caddy validate --config "$container_candidate" --adapter caddyfile >/dev/null
docker exec "$EDGE" rm -f "$container_candidate" >/dev/null 2>&1 || true
echo "CADDY_CANDIDATE_VALIDATE=PASS"

modified=0
cleanup() { rm -f "$candidate" >/dev/null 2>&1 || true; }
rollback() {
  rc=$?
  trap - ERR
  if (( modified )); then
    echo "=== AUTH EDGE ROLLBACK rc=$rc ===" >&2
    cp "$backup" "$CADDY_SOURCE" || true
    docker exec "$EDGE" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || true
    docker exec "$EDGE" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || true
  fi
  cleanup
  echo "RESULT=DASHBOARD_DEVICE_AUTH_EDGE_ROLLED_BACK" >&2
  exit "$rc"
}
trap rollback ERR
trap cleanup EXIT

cp "$candidate" "$CADDY_SOURCE"
modified=1
docker exec "$EDGE" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
docker exec "$EDGE" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
echo "CADDY_RELOAD=PASS"

[[ "$(docker inspect -f '{{.Id}}' "$EDGE")" == "$edge_id_before" ]]
[[ "$(docker inspect -f '{{.State.StartedAt}}' "$EDGE")" == "$edge_started_before" ]]
[[ "$(docker inspect -f '{{.RestartCount}}' "$EDGE")" == "$edge_restarts_before" ]]
echo "EDGE_RECREATED=NO"

[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$PROD_N8N")" == "$prod_n8n_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$PROD_OLLAMA")" == "$prod_ollama_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$STAGING_N8N")" == "$staging_n8n_before" ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$STAGING_OLLAMA")" == "$staging_ollama_before" ]]
echo "N8N_OLLAMA_IDENTITIES_UNCHANGED=PASS"

for host in dashboard.munshi.systems staging-dashboard.munshi.systems; do
  root_code="$(curl -ksS --max-time 15 -o /dev/null -w '%{http_code}' "https://$host/" || true)"
  login_code="$(curl -ksS --max-time 15 -o /dev/null -w '%{http_code}' "https://$host/_munshi-auth/login?next=%2F" || true)"
  [[ "$root_code" == "303" ]] || { echo "$host root expected 303, got $root_code" >&2; false; }
  [[ "$login_code" == "401" ]] || { echo "$host login expected 401, got $login_code" >&2; false; }
  echo "$host TRUSTED_DEVICE_PUBLIC_EDGE=PASS"
done

"$PROD_VERIFY" >/dev/null
"$STAGING_VERIFY" >/dev/null
echo "POST_CUTOVER_RUNTIME_CONTRACTS=PASS"
echo "EDGE_BACKUP=$backup"
echo "PRODUCTION_SHA=$production_sha"
echo "STAGING_SHA=$staging_sha"
echo "DEVICE_SESSION_DEFAULT_TTL_DAYS=30"
echo "RESULT=DASHBOARD_DEVICE_AUTH_EDGE_PASS"
trap - ERR
