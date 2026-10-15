"""Resolve one stored LinkedIn job against an enrolled official ATS board.

Read-only CLI. The public GET switch is explicit, bounded, and makes zero
LinkedIn requests. It never writes to jobs/evidence or submits an application.

 python -m scripts.linkedin_employer_link_pilot_v6 \
   --db /path/to/hunter.db \
   --linkedin-url https://www.linkedin.com/jobs/view/4455550001 \
   --public-ats-get
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from typing import Any

from app.linkedin_employer_careers_v2 import resolve_from_known_company_boards
from app.linkedin_url_contract import canonical_linkedin_job_url
from scripts.linkedin_link_resolution_dryrun_v1 import read_only_connection


def read_one_linkedin_job(
    connection: sqlite3.Connection, linkedin_url: str
) -> dict[str, Any] | None:
    """Require one exact stored source URL match; never guess a job record."""
    canonical = canonical_linkedin_job_url(linkedin_url)
    if not canonical:
        raise ValueError("A LinkedIn /jobs/view/<numeric-id> URL is required")
    posting_id = canonical.split("/")[-1]
    cols = (
        "id,source,company_name,title,location_raw,description_raw,"
        "job_url,apply_url,ats_job_id"
    )
    candidates = connection.execute(
        f"SELECT {cols} FROM jobs "
        "WHERE lower(coalesce(source,'')) LIKE '%linkedin%' "
        "AND (ats_job_id=? OR job_url LIKE ?) ORDER BY id DESC LIMIT 25",
        (posting_id, f"%{posting_id}%"),
    ).fetchall()
    matched = []
    for row in candidates:
        job = dict(zip(cols.split(","), tuple(row)))
        actual = canonical_linkedin_job_url(
            job.get("job_url"), job_id=job.get("ats_job_id")
        )
        if actual == canonical:
            matched.append(job)
    if not matched:
        return None
    if len(matched) > 1:
        raise ValueError("More than one stored job has this LinkedIn identity")
    return matched[0]


def resolve_single(
    connection: sqlite3.Connection,
    linkedin_url: str,
    *,
    allow_public_get: bool = False,
    provider_opener: Any = None,
) -> dict[str, Any]:
    job = read_one_linkedin_job(connection, linkedin_url)
    if job is None:
        return {
            "resolution_status": "UNKNOWN",
            "reason": "linkedin_job_not_in_hunter",
            "linkedin_job_url": canonical_linkedin_job_url(linkedin_url),
            "employer_apply_url": None,
            "source": "readonly_pilot_v6",
            "database_writes": 0,
            "applications_submitted": 0,
        }
    cols = "company_name,ats_type,board_token,careers_url,enabled,health_status"
    boards = [
        dict(zip(cols.split(","), tuple(row)))
        for row in connection.execute(
            f"SELECT {cols} FROM ats_company_registry "
            "WHERE lower(company_name)=lower(?)",
            (job["company_name"],),
        )
    ]
    result = resolve_from_known_company_boards(
        job,
        boards,
        allow_network=allow_public_get,
        provider_opener=provider_opener,
    )
    return {
        "job_id": job["id"],
        "linkedin_job_url": result.linkedin_job_url,
        "employer_job_url": result.employer_job_url,
        "employer_apply_url": result.employer_apply_url,
        "ats_platform": result.ats_platform,
        "resolution_status": result.resolution_status,
        "resolution_source": result.resolution_source,
        "evidence": result.evidence,
        "public_ats_get_enabled": allow_public_get,
        "source": "readonly_pilot_v6",
        "database_writes": 0,
        "applications_submitted": 0,
        "submit_authorized": False,
        "note": (
            "External ATS equivalence is not the exact LinkedIn Apply tracking "
            "redirect; employer destination must be current and rechecked "
            "before any later submission."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--linkedin-url", required=True)
    parser.add_argument("--public-ats-get", action="store_true")
    args = parser.parse_args()
    with read_only_connection(args.db) as db:
        result = resolve_single(
            db, args.linkedin_url, allow_public_get=args.public_ats_get
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
