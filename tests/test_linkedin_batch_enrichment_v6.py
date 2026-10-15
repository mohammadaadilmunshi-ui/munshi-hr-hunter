"""Batch discovery must never modify jobs or hand candidates to Apply."""

from __future__ import annotations

import sqlite3

import pytest

from app.linkedin_batch_enrichment_v6 import preview_link_enrichment
from tests.test_linkedin_employer_careers_v2 import FakeOpener, linkedin


def connection():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE jobs(id INTEGER PRIMARY KEY,source TEXT,company_name TEXT,"
        "title TEXT,location_raw TEXT,description_raw TEXT,job_url TEXT,"
        "apply_url TEXT,ats_job_id TEXT)"
    )
    c.execute(
        "CREATE TABLE ats_company_registry(company_name TEXT,ats_type TEXT,"
        "board_token TEXT,careers_url TEXT,enabled INTEGER,health_status TEXT)"
    )
    row = linkedin()
    for id_number in (101, 102):
        c.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
            (
                id_number,
                "JobSpy/linkedin",
                row["company_name"],
                row["title"],
                row["location_raw"],
                row["description_raw"],
                f"https://www.linkedin.com/jobs/view/4455550{id_number}",
                None,
                str(4455550000 + id_number),
            ),
        )
    c.execute(
        "INSERT INTO ats_company_registry VALUES(?,?,?,?,?,?)",
        (
            "Example Co",
            "ashby",
            "example",
            "https://jobs.ashbyhq.com/example",
            1,
            "healthy",
        ),
    )
    c.commit()
    return c


def opener():
    job = linkedin()
    return FakeOpener(
        {
            "jobs": [
                {
                    "title": "HR Analyst",
                    "location": "New York, NY",
                    "descriptionPlain": job["description_raw"],
                    "applyUrl": "https://jobs.ashbyhq.com/example/ABC/apply",
                    "jobUrl": "https://jobs.ashbyhq.com/example/ABC",
                    "isListed": True,
                }
            ]
        }
    )


def test_default_offline_never_calls_external_ats():
    with connection() as c:
        c.execute("PRAGMA query_only=ON")
        r = preview_link_enrichment(c, opener=opener())
        assert r["status_counts"] == {"READY_FOR_OFFICIAL_ATS_GET": 2}
        assert r["network_requests"] == 0
        assert r["unsigned_verified_proposals"] == []
        assert r["database_writes"] == 0


def test_two_jobs_same_board_only_one_public_feed_read_and_no_write():
    c = connection()
    c.execute("PRAGMA query_only=ON")
    fake = opener()
    r = preview_link_enrichment(c, allow_public_get=True, opener=fake)
    assert r["status_counts"] == {"VERIFIED": 2}
    assert r["jobs_scanned"] == 2
    assert r["network_requests"] == 1
    assert r["public_api_cache_hits"] == 1
    assert len(r["unsigned_verified_proposals"]) == 2
    assert all(
        x["proof_status"] == "UNSIGNED_PROPOSAL_ONLY"
        for x in r["unsigned_verified_proposals"]
    )
    assert r["database_writes"] == 0
    assert r["applications_submitted"] == 0
    assert r["autorun_or_scheduler_installed"] is False
    assert len(fake.urls) == 1
    assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
    c.close()


def test_unknown_employer_board_fails_closed():
    with connection() as c:
        c.execute("UPDATE ats_company_registry SET health_status='unhealthy'")
        c.execute("PRAGMA query_only=ON")
        r = preview_link_enrichment(c, allow_public_get=True, opener=opener())
        assert r["status_counts"] == {"NO_VERIFIED_ATS_BOARD": 2}
        assert r["network_requests"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"max_jobs": 0},
        {"max_jobs": 101},
        {"max_boards": 0},
        {"max_boards": 21},
    ],
)
def test_unbounded_batch_is_rejected(bad):
    with connection() as c, pytest.raises(ValueError):
        preview_link_enrichment(c, **bad)


def test_no_known_board_cannot_invent_employer_destination():
    with connection() as c:
        c.execute("UPDATE jobs SET company_name='Unmapped' WHERE id=102")
        c.execute("PRAGMA query_only=ON")
        result = preview_link_enrichment(c, allow_public_get=True, opener=opener())
        assert result["status_counts"] == {"NO_VERIFIED_ATS_BOARD": 1, "VERIFIED": 1}


def test_public_ats_cache_rejects_http_and_post():
    from urllib.request import Request

    from app.linkedin_batch_enrichment_v6 import ATSReadCache

    cache = ATSReadCache(opener=opener())
    with pytest.raises(ValueError):
        cache.open(
            Request("http://api.ashbyhq.com/posting-api/job-board/example"), timeout=7
        )
    with pytest.raises(ValueError):
        cache.open(Request("https://attacker.example.com/jobs"), timeout=7)
    with pytest.raises(ValueError):
        cache.open(
            Request(
                "https://api.ashbyhq.com/posting-api/job-board/example", method="POST"
            ),
            timeout=7,
        )
    assert cache.network_requests == 0
