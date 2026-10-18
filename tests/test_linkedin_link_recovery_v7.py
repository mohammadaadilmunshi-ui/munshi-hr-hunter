import sqlite3
from datetime import UTC, datetime

import pytest

from app.linkedin_apply_button_v7 import ApplyButtonResult
from app.linkedin_link_recovery_v7 import recover_links

NOW = datetime(2026, 10, 18, 5, tzinfo=UTC)


def fixture(count=130):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE jobs(id INTEGER,source TEXT,company_name TEXT,title TEXT,location_raw TEXT,description_raw TEXT,job_url TEXT,apply_url TEXT,ats_job_id TEXT,first_seen_at TEXT)"
    )
    c.execute(
        "CREATE TABLE ats_company_registry(company_name TEXT,enabled INTEGER,health_status TEXT)"
    )
    for i in range(1, count + 1):
        c.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                i,
                "JobSpy/linkedin",
                "Acme",
                "HR Analyst",
                "New York, NY",
                "Description",
                f"https://www.linkedin.com/jobs/view/{100000 + i}",
                None,
                str(100000 + i),
                "2026-10-01 00:00:00",
            ),
        )
    c.commit()
    c.execute("PRAGMA query_only=ON")
    return c


def unavailable(url):
    return ApplyButtonResult(url, None, "UNAVAILABLE", {})


def test_whole_30_day_cohort_resumes_beyond_first_100():
    c = fixture()
    result = recover_links(c, now=NOW, limit=100, public_get=True, fetcher=unavailable)
    assert result["total_jobs"] == 130
    assert result["processed_jobs"] == 100
    result = recover_links(
        c, now=NOW, checkpoint=result, public_get=True, fetcher=unavailable
    )
    assert result["processed_jobs"] == 130
    assert result["remaining_jobs"] == 0
    assert result["database_writes"] == 0
    assert (
        c.execute("SELECT count(*) FROM jobs WHERE apply_url IS NOT NULL").fetchone()[0]
        == 0
    )


def test_auth_wall_stops_repeated_requests_but_counts_unchecked_separately():
    calls = []

    def auth(url):
        calls.append(url)
        return ApplyButtonResult(url, None, "AUTH_REQUIRED", {})

    result = recover_links(fixture(12), now=NOW, public_get=True, fetcher=auth)
    assert len(calls) == 5
    assert result["status_counts"] == {
        "AUTH_REQUIRED": 5,
        "AUTH_REQUIRED_NOT_CHECKED": 7,
    }
    assert not result["unsigned_proposals"]


def test_offline_preview_does_not_prevent_later_online_scan():
    c = fixture(2)
    first = recover_links(c, now=NOW)
    assert first["status_counts"] == {"OFFLINE_ONLY": 2}
    second = recover_links(
        c, now=NOW, checkpoint=first, public_get=True, fetcher=unavailable
    )
    assert second["status_counts"] == {"UNAVAILABLE": 2}


def test_rate_limit_stops_and_leaves_remaining_jobs_for_resume():
    result = recover_links(
        fixture(9),
        now=NOW,
        public_get=True,
        fetcher=lambda url: ApplyButtonResult(url, None, "RATE_LIMITED", {}),
    )
    assert result["processed_jobs"] == 9
    assert result["remaining_jobs"] == 0
    assert result["status_counts"] == {"RATE_LIMITED": 1, "RATE_LIMITED_NOT_CHECKED": 8}
    assert result["stop_reason"] == "LINKEDIN_RATE_LIMITED"


def test_captured_url_remains_unsigned_proposal():
    result = recover_links(
        fixture(1),
        now=NOW,
        public_get=True,
        fetcher=lambda url: ApplyButtonResult(
            url,
            "https://careers.example.org/apply?job=12",
            "CAPTURED",
            {"response_sha256": "a" * 64},
        ),
    )
    proposal = result["unsigned_proposals"][0]
    assert proposal["resolution"]["employer_apply_url"].endswith("?job=12")
    assert result["requires_signed_import"] is True
    assert result["applications_submitted"] == 0


@pytest.mark.parametrize("args", [{"days": 0}, {"days": 32}, {"limit": 2001}])
def test_unbounded_scan_rejected(args):
    with pytest.raises(ValueError):
        recover_links(fixture(0), now=NOW, **args)
