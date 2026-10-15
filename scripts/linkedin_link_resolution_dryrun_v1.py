"""Read-only LinkedIn→employer link recovery audit. No network calls or mutations.

Run: python -m scripts.linkedin_link_resolution_dryrun_v1 --db /path/to/hunter.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

from app.linkedin_employer_link_resolver_v1 import normalize, resolve_stored

FIELDS = (
    "id",
    "source",
    "company_name",
    "title",
    "location_raw",
    "description_raw",
    "job_url",
    "apply_url",
    "ats_job_id",
    "requisition_id",
    "external_requisition_id",
    "ref_number",
)


def read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError("Hunter database does not exist")
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=4)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def audit(connection: sqlite3.Connection, limit: int | None = None) -> dict:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
    required = {"id", "source", "company_name", "title", "job_url", "apply_url"}
    if not required.issubset(columns):
        raise ValueError("Hunter jobs schema is missing required fields")
    selection = ", ".join(
        field if field in columns else f"NULL AS {field}" for field in FIELDS
    )
    other = connection.execute(
        f"SELECT {selection} FROM jobs WHERE lower(coalesce(source,'')) NOT LIKE '%linkedin%'"
    )
    candidates_by_identity = defaultdict(list)
    for row in other:
        item = dict(row)
        if not item.get("apply_url"):
            continue
        candidates_by_identity[
            (normalize(item.get("company_name")), normalize(item.get("title")))
        ].append(item)

    sql = (
        f"SELECT {selection} FROM jobs WHERE lower(coalesce(source,'')) LIKE '%linkedin%'"
        " ORDER BY id"
    )
    if limit is not None:
        sql += f" LIMIT {max(0, int(limit))}"
    rows = connection.execute(sql)
    states: Counter[str] = Counter()
    original_external = 0
    for row in rows:
        job = dict(row)
        identity = (normalize(job.get("company_name")), normalize(job.get("title")))
        result = resolve_stored(job, candidates_by_identity.get(identity, []))
        states[result.resolution_status] += 1
        if result.resolution_source == "stored_direct_url":
            original_external += 1

    return {
        "contract": "linkedin-employer-link-audit-v1",
        "mode": "READ_ONLY",
        "network_requests": 0,
        "database_writes": 0,
        "total_linkedin_inspected": sum(states.values()),
        "status_counts": dict(sorted(states.items())),
        "already_external_candidate_count": original_external,
        "verified_destinations": states["VERIFIED"],
        "auto_submittable": 0,
        "note": "Cross-source title matches are CANDIDATE leads only; no employer matching or application submission is asserted.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db", type=Path, required=True, help="Existing Hunter SQLite database"
    )
    parser.add_argument("--max-linkedin", type=int, default=None)
    args = parser.parse_args()
    with read_only_connection(args.db) as db:
        result = audit(db, limit=args.max_linkedin)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
