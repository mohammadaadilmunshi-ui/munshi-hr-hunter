"""Read-only preview of verified employer-link evidence for existing LinkedIn jobs.

No database mutations, no network, no browser, no hidden/applicant answers.
Optional --proposals accepts previously VERIFIED link-resolution JSON objects;
an arbitrary proposal can never grant submission or write authorization.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.linkedin_employer_link_resolver_v1 import LinkResolution
from app.linkedin_verified_url_evidence_v5 import (
    latest_verified_for_job,
    validate_verified_evidence,
)
from scripts.linkedin_link_resolution_dryrun_v1 import read_only_connection

_COLUMNS = "id,source,company_name,title,job_url,ats_job_id,apply_url"


def load_proposals(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("Verified-link proposals JSON missing or too large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) > 5000:
        raise ValueError("Expected a bounded list of verified-link proposals")
    if any(not isinstance(x, dict) for x in payload):
        raise ValueError("Invalid proposal record")
    return tuple(payload)


def preview_backfill(
    connection: Any,
    proposals: tuple[dict[str, Any], ...] = (),
    *,
    now: datetime | None = None,
    sample_limit: int = 12,
) -> dict[str, Any]:
    now_utc = now or datetime.now(timezone.utc)
    results: Counter[str] = Counter()
    sample: list[dict[str, Any]] = []
    indexed: dict[int, dict[str, Any]] = {}
    for p in proposals:
        try:
            job_id = int(p.get("job_id") or 0)
            if job_id < 1 or job_id in indexed:
                raise ValueError("Invalid/duplicate proposed job identity")
        except (ValueError, TypeError) as error:
            raise ValueError("Proposal requires unique positive job_id") from error
        indexed[job_id] = p

    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM jobs "
        "WHERE lower(coalesce(source,'')) LIKE '%linkedin%' ORDER BY id"
    ).fetchall()
    for row in rows:
        job = dict(row)
        job_id = int(job["id"])
        proposed = indexed.pop(job_id, None)
        if not proposed:
            status = "NO_VERIFIED_EVIDENCE_SUPPLIED"
        else:
            try:
                raw = proposed.get("resolution")
                if not isinstance(raw, dict):
                    raise TypeError("Proposal missing resolution result")
                resolution = LinkResolution(**raw)
                verified = datetime.fromisoformat(
                    str(proposed.get("verified_at") or "")
                )
                proposal = validate_verified_evidence(
                    job, resolution, verified_at=verified, now=now_utc
                )
                existing = latest_verified_for_job(connection, job, now=now_utc)
                if (
                    existing
                    and existing["employer_apply_url"] != proposal["employer_apply_url"]
                ):
                    status = "RECONCILIATION_REQUIRED"
                elif existing and existing["evidence_id"] == proposal["evidence_id"]:
                    status = "ALREADY_PRESENT"
                else:
                    status = "WOULD_INSERT_IF_SEPARATELY_AUTHORIZED"
            except (TypeError, ValueError, KeyError):
                status = "REJECTED_INVALID_OR_UNVERIFIED_EVIDENCE"
        results[status] += 1
        if proposed is not None and len(sample) < sample_limit:
            sample.append({"job_id": job_id, "status": status})

    unrecognized_ids = len(indexed)
    return {
        "contract": "linkedin-verified-url-backfill-dryrun-v5",
        "mode": "READ_ONLY_NO_WRITES_NO_NETWORK",
        "linkedin_jobs_inspected": len(rows),
        "status_counts": dict(sorted(results.items())),
        "proposals_without_current_linkedin_job": unrecognized_ids,
        "sample_job_ids_only": sample,
        "actual_inserts": 0,
        "actual_updates": 0,
        "applications_submitted": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--proposals", type=Path)
    args = parser.parse_args()
    proposals = load_proposals(args.proposals) if args.proposals else ()
    with read_only_connection(args.db) as db:
        result = preview_backfill(db, proposals)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
