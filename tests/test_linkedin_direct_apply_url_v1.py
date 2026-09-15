from __future__ import annotations

import pandas as pd

from app import jobspy_board_common
from app import linkedin_jobspy_description_guard as guard
from tools.runners import jobspy_runner as runner


def _row(**overrides):
    row = {
        "id": "linkedin-123",
        "title": "People Analytics Specialist",
        "company": "Example Corp",
        "location": "New York, NY, US",
        "job_url": "https://www.linkedin.com/jobs/view/123456789/",
        "job_url_direct": None,
        "description": "Example role",
        "is_remote": False,
    }
    row.update(overrides)
    return row


def _outer_row(**overrides):
    row = {
        "id": "linkedin-123",
        "title": "People Analytics Specialist",
        "company_name": "Example Corp",
        "location_raw": "New York, NY, US",
        "job_url": "https://www.linkedin.com/jobs/view/123456789/",
        "apply_url": None,
        "description_raw": "Example role",
    }
    row.update(overrides)
    return row


def test_linkedin_preserves_source_url_and_uses_external_direct_url():
    direct = "https://jobs.example.com/careers/people-analytics-123"
    normalized = runner.normalize_record(
        _row(job_url_direct=direct),
        "linkedin",
    )

    assert normalized is not None
    assert normalized["job_url"] == "https://www.linkedin.com/jobs/view/123456789/"
    assert normalized["apply_url"] == direct


def test_linkedin_without_direct_url_does_not_fallback_to_linkedin():
    normalized = runner.normalize_record(_row(), "linkedin")

    assert normalized is not None
    assert normalized["job_url"].startswith("https://www.linkedin.com/")
    assert normalized["apply_url"] is None


def test_linkedin_rejects_aggregator_as_direct_application_url():
    for candidate in (
        "https://linkedin.com/jobs/view/987",
        "https://www.linkedin.com/jobs/view/987",
        "https://jobs.indeed.com/viewjob?jk=123",
        "https://www.glassdoor.com/job-listing/example",
        "https://www.ziprecruiter.com/jobs/example",
    ):
        normalized = runner.normalize_record(
            _row(job_url_direct=candidate),
            "linkedin",
        )
        assert normalized is not None
        assert normalized["apply_url"] is None


def test_non_linkedin_jobspy_fallback_behavior_is_unchanged():
    source = "https://www.indeed.com/viewjob?jk=abc123"
    normalized = runner.normalize_record(
        _row(job_url=source, job_url_direct=None),
        "indeed",
    )

    assert normalized is not None
    assert normalized["job_url"] == source
    assert normalized["apply_url"] == source


def test_linkedin_scrape_requests_detail_page_and_direct_url(monkeypatch):
    captured = {}
    direct = "https://boards.greenhouse.io/example/jobs/123"

    def fake_scrape_jobs(**kwargs):
        captured.update(kwargs)
        return pd.DataFrame([_row(job_url_direct=direct)])

    monkeypatch.setattr(runner, "scrape_jobs", fake_scrape_jobs)
    jobs = runner.scrape_site(
        "linkedin",
        search_term="people analytics",
        location="United States",
        results_wanted=1,
        hours_old=24,
        job_type=None,
        remote_only=False,
    )

    assert captured["site_name"] == "linkedin"
    assert captured["linkedin_fetch_description"] is True
    assert len(jobs) == 1
    assert jobs[0]["apply_url"] == direct


def test_non_linkedin_scrape_does_not_receive_linkedin_detail_flag(monkeypatch):
    captured = {}

    def fake_scrape_jobs(**kwargs):
        captured.update(kwargs)
        return pd.DataFrame([_row(job_url="https://www.indeed.com/viewjob?jk=abc")])

    monkeypatch.setattr(runner, "scrape_jobs", fake_scrape_jobs)
    runner.scrape_site(
        "indeed",
        search_term="people analytics",
        location="United States",
        results_wanted=1,
        hours_old=24,
        job_type=None,
        remote_only=False,
    )

    assert captured["site_name"] == "indeed"
    assert "linkedin_fetch_description" not in captured


def test_linkedin_guard_url_policy_accepts_ats_and_rejects_aggregators():
    assert (
        guard._direct_application_url(
            "https://jobs.lever.co/example/abc"
        )
        == "https://jobs.lever.co/example/abc"
    )
    assert guard._direct_application_url("https://www.linkedin.com/jobs/view/1") is None
    assert guard._direct_application_url("javascript:alert(1)") is None


def test_linkedin_guard_only_identifies_linkedin_requests():
    assert guard._linkedin_requested("linkedin") is True
    assert guard._linkedin_requested(["linkedin"]) is True
    assert guard._linkedin_requested(["indeed", "linkedin"]) is True
    assert guard._linkedin_requested("indeed") is False
    assert guard._linkedin_requested(["google", "indeed"]) is False


def test_outer_linkedin_normalizer_does_not_restore_listing_as_apply_url():
    original = jobspy_board_common.normalize_jobspy_row
    try:
        assert guard._install_board_normalizer_guard() is True
        normalized = jobspy_board_common.normalize_jobspy_row(
            _outer_row(),
            "linkedin",
        )
        assert "linkedin.com/jobs/view/123456789" in normalized["job_url"]
        assert normalized["apply_url"] is None
    finally:
        jobspy_board_common.normalize_jobspy_row = original


def test_outer_linkedin_normalizer_preserves_external_direct_url():
    original = jobspy_board_common.normalize_jobspy_row
    direct = "https://jobs.ashbyhq.com/example/abc"
    try:
        assert guard._install_board_normalizer_guard() is True
        normalized = jobspy_board_common.normalize_jobspy_row(
            _outer_row(apply_url=direct),
            "linkedin",
        )
        assert normalized["apply_url"] == direct
    finally:
        jobspy_board_common.normalize_jobspy_row = original


def test_outer_guard_leaves_non_linkedin_normalization_unchanged():
    original = jobspy_board_common.normalize_jobspy_row
    source = "https://www.indeed.com/viewjob?jk=abc123"
    try:
        baseline = original(
            _outer_row(job_url=source, apply_url=None),
            "indeed",
        )
        assert guard._install_board_normalizer_guard() is True
        normalized = jobspy_board_common.normalize_jobspy_row(
            _outer_row(job_url=source, apply_url=None),
            "indeed",
        )
        assert normalized == baseline
    finally:
        jobspy_board_common.normalize_jobspy_row = original
