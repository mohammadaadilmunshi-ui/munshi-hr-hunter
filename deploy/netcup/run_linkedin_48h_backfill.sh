#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${MUNSHI_ROOT:-/opt/munshi}"
REPO="$ROOT/repo"
H="${MUNSHI_HUNTER_CONTAINER:-munshi-netcup-shadow-hunter-1}"

expected_sha=""
mode="dry-run"

while (($#)); do
  case "$1" in
    --expected-sha) expected_sha="${2:-}"; shift 2 ;;
    --apply) mode="apply"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "--expected-sha must be a full lowercase 40-character Git SHA" >&2
  exit 3
}
[[ -d "$REPO/.git" ]] || { echo "production Hunter repo missing" >&2; exit 4; }

actual_sha="$(git -C "$REPO" rev-parse HEAD)"
[[ "$actual_sha" == "$expected_sha" ]] || {
  echo "production Hunter SHA mismatch: expected=$expected_sha actual=$actual_sha" >&2
  exit 5
}

running="$(docker inspect -f '{{.State.Running}}' "$H")"
health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$H")"
[[ "$running" == "true" && "$health" == "healthy" ]] || {
  echo "production Hunter is not healthy" >&2
  exit 6
}

for relative in   app/linkedin_recent_backfill.py   scripts/linkedin_recent_backfill.py
do
  host_sha="$(sha256sum "$REPO/$relative" | awk '{print $1}')"
  container_sha="$(docker exec "$H" sha256sum "/app/hunter/$relative" | awk '{print $1}')"
  [[ "$host_sha" == "$container_sha" ]] || {
    echo "production container/source mismatch for $relative" >&2
    exit 7
  }
done

db_path="$(docker exec "$H" python - <<'PY'
from app.database import DB_PATH
print(DB_PATH)
PY
)"
[[ -n "$db_path" ]] || { echo "Hunter DB path unresolved" >&2; exit 8; }

docker exec -i "$H" python - "$db_path" <<'PY'
import sqlite3
import sys
path = sys.argv[1]
db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
try:
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=60000")
    quick = str(db.execute("PRAGMA quick_check").fetchone()[0])
finally:
    db.close()
if quick != "ok":
    raise SystemExit(f"pre-backfill quick_check failed: {quick}")
print("LINKEDIN_48H_PRECHECK_DB=PASS")
PY

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
receipt_dir="$ROOT/runtime/linkedin-48h-backfill"
mkdir -p "$receipt_dir"
chmod 750 "$receipt_dir"
dry_log="$receipt_dir/$stamp-$expected_sha-dry-run.log"

docker exec "$H" python /app/hunter/scripts/linkedin_recent_backfill.py   --db-path "$db_path"   --hours 48   --environment production | tee "$dry_log"

grep -Fq 'LINKEDIN_48H_BACKFILL_MODE=DRY_RUN' "$dry_log"

echo "LINKEDIN_48H_DRY_RUN_RECEIPT=$dry_log"
echo "LINKEDIN_48H_EXPECTED_SHA=$expected_sha"

if [[ "$mode" == "dry-run" ]]; then
  echo "PRODUCTION_DB_MUTATED=NO"
  echo "RESULT=LINKEDIN_48H_DRY_RUN_PASS"
  exit 0
fi

apply_log="$receipt_dir/$stamp-$expected_sha-apply.log"
docker exec "$H" python /app/hunter/scripts/linkedin_recent_backfill.py   --db-path "$db_path"   --hours 48   --environment production   --apply   --authorization AUTHORIZED_LINKEDIN_48H_BACKFILL | tee "$apply_log"

grep -Fq 'LINKEDIN_48H_BACKFILL_MODE=APPLY' "$apply_log"

verify_log="$receipt_dir/$stamp-$expected_sha-post-verify.log"
docker exec "$H" python /app/hunter/scripts/linkedin_recent_backfill.py   --db-path "$db_path"   --hours 48   --environment production | tee "$verify_log"

grep -Fq 'LINKEDIN_48H_BACKFILL_MODE=DRY_RUN' "$verify_log"
grep -Fq 'LINKEDIN_48H_CHANGED=0' "$verify_log"

docker exec -i "$H" python - "$db_path" <<'PY'
import sqlite3
import sys
path = sys.argv[1]
db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
try:
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=60000")
    quick = str(db.execute("PRAGMA quick_check").fetchone()[0])
    bad = db.execute(
        """
        SELECT COUNT(*)
        FROM jobs
        WHERE lower(COALESCE(source,'')) LIKE '%linkedin%'
          AND datetime(first_seen_at) >= datetime('now','-48 hours')
          AND lower(COALESCE(status,'')) NOT LIKE 'quarantined%'
          AND (
            job_url IS NULL
            OR lower(job_url) NOT LIKE 'https://www.linkedin.com/jobs/view/%'
          )
        """
    ).fetchone()[0]
finally:
    db.close()
if quick != "ok":
    raise SystemExit(f"post-backfill quick_check failed: {quick}")
if bad:
    raise SystemExit(
        f"post-backfill found {bad} recent LinkedIn rows visible without canonical URL"
    )
print("LINKEDIN_48H_POSTCHECK_DB=PASS")
print("LINKEDIN_48H_VISIBLE_BROKEN_LINKS=0")
PY

echo "LINKEDIN_48H_APPLY_RECEIPT=$apply_log"
echo "LINKEDIN_48H_VERIFY_RECEIPT=$verify_log"
echo "PRODUCTION_DB_MUTATED=YES"
echo "RESULT=LINKEDIN_48H_BACKFILL_PASS"
