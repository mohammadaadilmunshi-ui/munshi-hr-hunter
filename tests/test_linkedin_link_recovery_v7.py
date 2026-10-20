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


def cross_source_fixture(*, conflicting=False):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE jobs(id INTEGER,source TEXT,company_name TEXT,title TEXT,"
        "location_raw TEXT,description_raw TEXT,job_url TEXT,apply_url TEXT,"
        "ats_job_id TEXT,first_seen_at TEXT)"
    )
    c.execute(
        "CREATE TABLE ats_company_registry(company_name TEXT,enabled INTEGER,"
        "health_status TEXT)"
    )
    c.executemany(
        "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "JobSpy/linkedin", "Example Employer", "HR Analyst", "New York, NY",
             "Analyze workforce data", "https://www.linkedin.com/jobs/view/123456789",
             None, "123456789", "2026-10-10 00:00:00"),
            (2, "JobSpy/indeed", "Example Employer", "HR Analyst", "New York, NY",
             "Analyze workforce data", "https://www.indeed.com/viewjob?jk=abc",
             "https://jobs.example.com/apply?tenant=2&job=123", "123", "2026-10-11 00:00:00"),
            *(([(3, "JobSpy/indeed", "Example Employer", "HR Analyst", "New York, NY",
                   "Analyze workforce data", "https://www.indeed.com/viewjob?jk=def",
                   "https://jobs.example.com/apply?tenant=2&job=456", "456",
                   "2026-10-11 00:00:00")]) if conflicting else []),
        ],
    )
    c.commit()
    c.execute("PRAGMA query_only=ON")
    return c


def test_cross_source_recovers_candidate_without_approval_or_db_write():
    c = cross_source_fixture()
    r = recover_links(c, now=NOW, public_get=False)
    assert r["total_jobs"] == 1
    assert r["status_counts"] == {"OFFLINE_ONLY": 1}
    assert r["unverified_candidate_count"] == 1
    lead = r["unverified_candidate_proposals"][0]
    assert lead["candidate_status"] == "UNVERIFIED_CROSS_SOURCE"
    assert lead["candidate_resolution"]["employer_apply_url"].endswith("&job=123")
    assert not r["unsigned_proposals"]
    assert r["requires_signed_import"] is True
    assert r["database_writes"] == 0
    assert c.execute("SELECT apply_url FROM jobs WHERE id=1").fetchone()[0] is None


def test_official_only_never_calls_linkedin_and_keeps_candidates():
    def forbidden(_url):
        raise AssertionError("No LinkedIn requests permitted in official-only mode")

    r = recover_links(
        cross_source_fixture(), now=NOW, public_get=True,
        linkedin_get=False, fetcher=forbidden,
    )
    assert r["unverified_candidate_count"] == 1
    assert r["status_counts"] == {"OFFICIAL_ONLY_NO_LINKEDIN_REQUEST": 1}
    assert r["applications_submitted"] == 0


def test_ambiguous_cross_source_urls_do_not_become_auto_apply_targets():
    r = recover_links(cross_source_fixture(conflicting=True), now=NOW)
    assert r["unverified_candidate_count"] == 0
    assert r["records"]["1"]["candidate_status"] == "AMBIGUOUS_CROSS_SOURCE"
    assert not r["unsigned_proposals"]


def test_existing_official_source_discovers_board_without_guessing(monkeypatch):
    from app import linkedin_link_recovery_v7 as recovery
    from app.linkedin_employer_link_resolver_v1 import LinkResolution

    c = cross_source_fixture()
    c.execute("PRAGMA query_only=OFF")
    c.execute(
        "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
        (4, "Greenhouse/example", "Example Employer", "Other Role", "New York, NY",
         "Different job", "https://job-boards.greenhouse.io/example/jobs/678",
         "https://job-boards.greenhouse.io/example/jobs/678", "678",
         "2026-10-11 00:00:00"),
    )
    c.commit()
    c.execute("PRAGMA query_only=ON")
    calls = []

    def fake_official(job, *, provider, board_name, verified_employer_name, opener):
        calls.append((provider, board_name, verified_employer_name))
        return LinkResolution(
            linkedin_job_url=job["job_url"],
            employer_job_url="https://job-boards.greenhouse.io/example/jobs/123",
            employer_apply_url="https://job-boards.greenhouse.io/example/jobs/123",
            ats_platform="Greenhouse", resolution_status="VERIFIED",
            resolution_source="official_public_ats",
            evidence={"identity_evidence": "strong", "needs_employer_confirmation": False,
                      "matching_fields": ["company_name", "title", "location"]},
        )

    monkeypatch.setattr(recovery, "resolve_from_verified_board", fake_official)
    r = recovery.recover_links(c, now=NOW, public_get=True, linkedin_get=False)
    assert calls == [("Greenhouse", "example", "Example Employer")]
    assert r["status_counts"] == {"VERIFIED_PROPOSAL": 1}
    assert len(r["unsigned_proposals"]) == 1
    assert r["database_writes"] == 0
    assert r["requires_signed_import"] is True


def test_supervised_recovery_is_opt_in_bounded_and_official_only(monkeypatch):
    import runpy
    from pathlib import Path

    supervisor = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "docker" / "hunter-supervisor.py")
    )
    monkeypatch.delenv("MUNSHI_LINKEDIN_RECOVERY_AUTO_ENABLED", raising=False)
    assert supervisor["linkedin_recovery_lane"]("python") is None
    monkeypatch.setenv("MUNSHI_LINKEDIN_RECOVERY_AUTO_ENABLED", "true")
    monkeypatch.setenv("MUNSHI_LINKEDIN_RECOVERY_INTERVAL_SECONDS", "60")
    monkeypatch.delenv("CLOUD_SHADOW_MODE", raising=False)
    lane = supervisor["linkedin_recovery_lane"]("python")
    assert lane is not None
    assert lane.name == "linkedin-employer-link-recovery"
    assert lane.command == [
        "python", "-m", "app.linkedin_link_recovery_v7",
        "--automatic", "--official-only", "--limit", "2000",
    ]
    assert lane.repeat is True and lane.required is False
    assert lane.interval == 21600
    monkeypatch.setenv("CLOUD_SHADOW_MODE", "true")
    monkeypatch.setenv("PRODUCTION_STATE_IMPORTED", "false")
    monkeypatch.setenv("PRODUCTION_CALLBACKS_ENABLED", "false")
    monkeypatch.setenv("HUNTER_ENABLE_TELEGRAM", "false")
    monkeypatch.setenv("HUNTER_ENABLE_DISCOVERY_SCHEDULER", "false")
    monkeypatch.setenv("HUNTER_ENABLE_COORDINATOR", "false")
    assert supervisor["linkedin_recovery_lane"]("python") is None


def test_automatic_recovery_keeps_checkpoint_on_mounted_data_volume(monkeypatch, tmp_path):
    import sys
    import sqlite3
    from app import database
    from scripts import linkedin_link_recovery_v7 as cli
    from app import linkedin_link_recovery_v7 as runtime_cli

    db_path = tmp_path / "hunter.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO sentinel VALUES (1)")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    observed = {}

    def safe_read_only_preview(connection, **kwargs):
        observed["query_only"] = connection.execute("PRAGMA query_only").fetchone()[0]
        observed.update(kwargs)
        return {"processed_jobs": 0, "database_writes": 0, "applications_submitted": 0}

    monkeypatch.setattr(runtime_cli, "recover_links", safe_read_only_preview)
    monkeypatch.setattr(sys, "argv", ["recover", "--automatic"])
    assert cli.main() == 0
    assert observed["query_only"] == 1
    assert observed["public_get"] is True
    assert observed["linkedin_get"] is False
    checkpoints = list((tmp_path / "linkedin-link-recovery-v8").glob("*.json"))
    assert len(checkpoints) == 1
    assert checkpoints[0].stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sentinel").fetchone()[0] == 1
