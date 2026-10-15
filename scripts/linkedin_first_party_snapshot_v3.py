"""Offline inspection of approved employer-owned careers HTML snapshots.

Example:
 python -m scripts.linkedin_first_party_snapshot_v3 \
     --approved-site /secure/path/site.json --html /secure/path/careers.html

No network calls, no database mutations, and no application submission.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.linkedin_first_party_careers_v3 import (
    ApprovedEmployerSite,
    candidate_job_urls_from_official_html,
    discover_from_approved_html,
)


def inspect_snapshot(approval: dict, html: str) -> dict:
    site = ApprovedEmployerSite(
        company_name=str(approval.get("company_name") or ""),
        official_domain=str(approval.get("official_domain") or ""),
        careers_url=str(approval.get("careers_url") or ""),
        approval_source=str(approval.get("approval_source") or ""),
        approval_reference=str(approval.get("approval_reference") or ""),
    )
    ats = discover_from_approved_html(site, html)
    pages = candidate_job_urls_from_official_html(site, html)
    return {
        "contract": "linkedin-employer-site-snapshot-v3",
        "mode": "OFFLINE_READ_ONLY",
        "company_name": ats.company_name,
        "verified_site_provenance": ats.evidence["source"],
        "ats_board_hint_count": len(ats.board_hints),
        "ats_board_hints": [hint.to_dict() for hint in ats.board_hints],
        "employer_job_page_hint_count": len(pages),
        "employer_job_pages": pages[:50],
        "employer_apply_urls_verified": 0,
        "status": "HINTS_ONLY",
        "network_requests": 0,
        "database_writes": 0,
        "note": "An operator approval reference is a label, not cryptographic authorization. "
        "Actual ownership checks and signed approvals are required before "
        "this data can enter production resolution.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-site", type=Path, required=True)
    parser.add_argument("--html", type=Path, required=True)
    args = parser.parse_args()
    # This command does not elevate the supplied approval to real trust.
    approval = json.loads(args.approved_site.read_text(encoding="utf-8"))
    if args.html.stat().st_size > 3_000_000:
        raise ValueError("Snapshot exceeds inspection cap")
    html = args.html.read_text(encoding="utf-8")
    print(json.dumps(inspect_snapshot(approval, html), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
