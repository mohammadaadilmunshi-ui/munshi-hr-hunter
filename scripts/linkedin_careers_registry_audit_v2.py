"""Read-only coverage of LinkedIn jobs against existing healthy ATS boards."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from app.linkedin_employer_careers_v2 import registered_company_board_coverage
from scripts.linkedin_link_resolution_dryrun_v1 import read_only_connection


def audit_registry(connection: sqlite3.Connection) -> dict:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ats_company_registry'"
    ).fetchone():
        raise ValueError("ATS company registry is unavailable")
    jobs = [
        {"company_name": row[0]}
        for row in connection.execute(
            "SELECT company_name FROM jobs "
            "WHERE lower(coalesce(source,'')) LIKE '%linkedin%'"
        )
    ]
    cols = "company_name,ats_type,board_token,careers_url,enabled,health_status"
    registry = [
        dict(zip(cols.split(","), row))
        for row in connection.execute(f"SELECT {cols} FROM ats_company_registry")
    ]
    result = registered_company_board_coverage(jobs, registry)
    result.update(
        {
            "contract": "linkedin-employer-careers-registry-v2",
            "mode": "READ_ONLY",
            "network_requests": 0,
            "linked_jobs_submitted": 0,
            "source": "ats_company_registry",
            "note": "Board coverage is not validated employer job links. Official ATS corroboration is required.",
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    with read_only_connection(args.db) as connection:
        print(json.dumps(audit_registry(connection), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
