#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

STAGING_ROOT="${MUNSHI_STAGING_ROOT:-/home/munshi/munshi-staging-v1}"
PROJECT="${MUNSHI_STAGING_PROJECT:-munshi-netcup-staging}"

job_id=""
phase=""
expected_hunter_sha=""
expected_apply_sha=""
timeout_seconds="900"

while (($#)); do
  case "$1" in
    --job-id) job_id="${2:-}"; shift 2 ;;
    --phase) phase="${2:-}"; shift 2 ;;
    --expected-hunter-sha) expected_hunter_sha="${2:-}"; shift 2 ;;
    --expected-apply-sha) expected_apply_sha="${2:-}"; shift 2 ;;
    --timeout-seconds) timeout_seconds="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$job_id" =~ ^[1-9][0-9]*$ ]] || { echo "--job-id must be a positive integer" >&2; exit 3; }
[[ "$phase" == "pre-approval" || "$phase" == "post-approval-observe" ]] || {
  echo "--phase must be pre-approval or post-approval-observe" >&2
  exit 4
}
[[ "$expected_hunter_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "--expected-hunter-sha must be a full lowercase SHA" >&2
  exit 5
}
[[ "$expected_apply_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "--expected-apply-sha must be a full lowercase SHA" >&2
  exit 6
}
[[ "$timeout_seconds" =~ ^[0-9]+$ ]] || { echo "--timeout-seconds invalid" >&2; exit 7; }
[[ "$PROJECT" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { echo "--project invalid" >&2; exit 8; }

repo="$STAGING_ROOT/repo"
[[ -d "$repo/.git" ]] || { echo "staging Hunter repository missing" >&2; exit 9; }
actual_hunter_sha="$(git -C "$repo" rev-parse HEAD)"
[[ "$actual_hunter_sha" == "$expected_hunter_sha" ]] || {
  echo "Hunter staging SHA mismatch: expected=$expected_hunter_sha actual=$actual_hunter_sha" >&2
  exit 10
}

mapfile -t hunter_ids < <(
  docker ps -q     --filter "label=com.docker.compose.project=$PROJECT"     --filter "label=com.docker.compose.service=hunter"
)
[[ "${#hunter_ids[@]}" -eq 1 ]] || {
  echo "expected exactly one staging Hunter container" >&2
  exit 11
}
H="${hunter_ids[0]}"

hunter_env="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$H")"
grep -Fxq 'CLOUD_SHADOW_MODE=true' <<<"$hunter_env"
grep -Fxq 'PRODUCTION_CALLBACKS_ENABLED=false' <<<"$hunter_env"
grep -Fxq 'PRODUCTION_STATE_IMPORTED=false' <<<"$hunter_env"
grep -Fxq 'MUNSHI_FINAL_SUBMIT_ENABLED=false' <<<"$hunter_env"
grep -Fxq 'MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED=false' <<<"$hunter_env"

hunter_networks="$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' "$H")"
[[ -n "$hunter_networks" ]] || { echo "Hunter staging network unresolved" >&2; exit 12; }

apply_matches=()
while IFS= read -r id; do
  [[ -n "$id" ]] || continue
  revision="$(docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$id" 2>/dev/null || true)"
  [[ "$revision" == "$expected_apply_sha" ]] || continue
  shared=0
  while IFS= read -r network; do
    [[ -n "$network" ]] || continue
    if docker inspect -f '{{json .NetworkSettings.Networks}}' "$id" | grep -Fq "\"$network\""; then
      shared=1
      break
    fi
  done <<<"$hunter_networks"
  (( shared )) && apply_matches+=("$id")
done < <(docker ps -q)

[[ "${#apply_matches[@]}" -eq 1 ]] || {
  echo "expected exactly one Apply staging container with revision $expected_apply_sha on Hunter staging network" >&2
  exit 13
}
A="${apply_matches[0]}"

apply_env="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$A")"
grep -Fxq 'MUNSHI_FINAL_SUBMIT_ENABLED=false' <<<"$apply_env"
if grep -Eq '^MUNSHI_PRODUCTION_SUBMIT_ENABLED=(1|true|yes|on)$' <<<"$apply_env"; then
  echo "Apply production submit flag is enabled" >&2
  exit 14
fi

echo "HUNTER_STAGING_SHA=$actual_hunter_sha"
echo "APPLY_STAGING_SHA=$expected_apply_sha"
echo "FINAL_SUBMIT_ENABLED=false"
echo "CUSTOMER_APPROVAL_CREATED_BY_CANARY=NO"
echo "EMPLOYER_SUBMIT_ACTION_AVAILABLE_TO_CANARY=NO"

docker exec "$H" python /app/hunter/scripts/staging_autoapply_canary.py   --job-id "$job_id"   --phase "$phase"   --timeout-seconds "$timeout_seconds"

echo "RESULT=STAGING_AUTOAPPLY_CANARY_PASS"
