from __future__ import annotations

from scripts.linkedin_first_party_snapshot_v3 import inspect_snapshot


def approval():
    return {
        "company_name": "Example Corp",
        "official_domain": "example.com",
        "careers_url": "https://careers.example.com/jobs",
        "approval_source": "human_verified",
        "approval_reference": "operator-check-20261015",
    }


def test_snapshot_report_never_claims_verified_application_destination():
    html = """
    <a href="https://jobs.ashbyhq.com/example/123">ATS</a>
    <a href="/jobs/hr-analyst">HR Analyst</a>
    """
    report = inspect_snapshot(approval(), html)
    assert report["ats_board_hint_count"] == 1
    assert report["ats_board_hints"][0]["provider"] == "Ashby"
    assert report["employer_job_page_hint_count"] == 1
    assert report["employer_apply_urls_verified"] == 0
    assert report["status"] == "HINTS_ONLY"
    assert report["network_requests"] == 0
    assert report["database_writes"] == 0
