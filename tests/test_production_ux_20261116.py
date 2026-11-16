"""Regression tests for browsable ATS platforms, posted-date order and Relay preference."""
from __future__ import annotations

import pytest

from app import application_provider_ui_v1 as provider_ui
from app import product_pages
from app.product_state import fetch_jobs


def _job(c, ident, platform_url, posted, discovered):
    c.execute(
        """INSERT INTO jobs (
            job_fingerprint,source,company_name,title,location_raw,
            description_raw,hunter_score,date_posted,first_seen_at,apply_url
        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ident, "JobSpy/indeed", ident, "Recruiting Coordinator",
         "Remote, US", "Recruiting HR operations", 75, posted, discovered,
         platform_url),
    )


def test_sort_by_posted_date_not_discovery_timestamp(hunter_db):
    from app.database import get_connection
    c = get_connection()
    try:
        _job(c, "workable-newer", "https://apply.workable.com/j/abc123", "2026-11-13", "2026-11-14 08:00:00")
        _job(c, "greenhouse-newest", "https://boards.greenhouse.io/company/jobs/1", "2026-11-15", "2026-11-15 08:00:00")
        _job(c, "icims-older", "https://careers.example.icims.com/jobs/42", "2026-11-12", "2026-11-16 12:00:00")
        c.commit()
    finally:
        c.close()
    rows, count = fetch_jobs(page_size=10)
    assert count == 3
    assert [r["company_name"] for r in rows] == [
        "greenhouse-newest", "workable-newer", "icims-older",
    ]
    by_match, _ = fetch_jobs(sort_by="match_desc", page_size=10)
    assert len(by_match) == 3


@pytest.mark.parametrize(("platform", "expected"), [
    ("Workable", "workable-newer"),
    ("Greenhouse", "greenhouse-newest"),
    ("iCIMS", "icims-older"),
])
def test_ats_platform_filter_is_independent_from_indeed_source(hunter_db, platform, expected):
    from app.database import get_connection
    c = get_connection()
    try:
        _job(c, "workable-newer", "https://apply.workable.com/j/abc123", "2026-11-13", "2026-11-14")
        _job(c, "greenhouse-newest", "https://boards.greenhouse.io/company/jobs/1", "2026-11-15", "2026-11-15")
        _job(c, "icims-older", "https://careers.example.icims.com/jobs/42", "2026-11-12", "2026-11-16")
        c.commit()
    finally:
        c.close()
    rows, count = fetch_jobs(source="JobSpy/indeed", apply_platform=platform, page_size=10)
    assert count == 1
    assert rows[0]["company_name"] == expected


def test_unknown_platform_rejected_before_query(hunter_db):
    with pytest.raises(ValueError, match="Unknown application platform"):
        fetch_jobs(apply_platform="NotARealATS")


def test_new_job_defaults_to_newest_and_relay_only_when_connected():
    assert product_pages._filter_defaults()["sort_by"] == "newest"
    assert product_pages._filter_defaults()["apply_platform"] == ""
    ready = provider_ui.engine_selector_state(route=None, tsenta_backend={"execution_ready": True})
    offline = provider_ui.engine_selector_state(route=None, tsenta_backend={"execution_ready": False})
    assert ready["selected"] == "MUNSHI Relay"
    assert ready["locked"] is False
    assert offline["selected"] == "MUNSHI Native"
    route = {"provider": "NATIVE", "state": "PREPARATION"}
    reserved = provider_ui.engine_selector_state(route=route, tsenta_backend={"execution_ready": True})
    assert reserved["selected"] == "MUNSHI Native"
    assert reserved["locked"] is True


def test_unknown_relay_claim_disables_reselection():
    route = {
        "provider": "TSENTA", "state": "PREPARATION",
        "attempt_count": 0, "dispatch_claim_exists": True,
    }
    state = provider_ui.engine_selector_state(route=route, tsenta_backend={"execution_ready": True})
    assert state["selected"] == "MUNSHI Relay"
    assert state["locked"] is True
    assert state["can_submit_tsenta"] is False


def test_job_card_age_labels_posting_vs_discovery():
    posted = product_pages._job_age_label({
        "date_posted": "2026-11-15",
        "first_seen_at": "2026-11-16 12:00:00",
    })
    discovered = product_pages._job_age_label({
        "date_posted": "not a parseable date",
        "first_seen_at": "2026-11-16 12:00:00",
    })
    assert posted.startswith("Posted ")
    assert discovered.startswith("Discovered ")
    assert "Posted" not in discovered


def test_initialized_product_reads_do_not_run_schema_ddl(hunter_db, monkeypatch):
    from app import product_state

    def unexpected_schema_write(_connection):
        raise AssertionError("An initialized product read must not run schema DDL")

    monkeypatch.setattr(product_state, "ensure_schema", unexpected_schema_write)
    rows = product_state.job_filters()
    assert "sources" in rows
    _, count = product_state.fetch_jobs(page_size=10)
    assert count >= 0
