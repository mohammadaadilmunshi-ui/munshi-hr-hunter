"""One-LinkedIn-URL read-only pilot contract."""

from __future__ import annotations

import sqlite3

import pytest

from scripts.linkedin_employer_link_pilot_v6 import resolve_single
from tests.test_linkedin_employer_careers_v2 import FakeOpener, linkedin


def db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE jobs(id INTEGER PRIMARY KEY, source TEXT, "
        "company_name TEXT, title TEXT, location_raw TEXT, description_raw TEXT, "
        "job_url TEXT, apply_url TEXT, ats_job_id TEXT)"
    )
    j = linkedin()
    c.execute(
        "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
        (
            101,
            "JobSpy/linkedin",
            j["company_name"],
            j["title"],
            j["location_raw"],
            j["description_raw"],
            j["job_url"],
            j["apply_url"],
            j["ats_job_id"],
        ),
    )
    c.execute(
        "CREATE TABLE ats_company_registry("
        "company_name TEXT, ats_type TEXT, board_token TEXT, "
        "careers_url TEXT, enabled INTEGER, health_status TEXT)"
    )
    c.execute(
        "INSERT INTO ats_company_registry VALUES (?,?,?,?,?,?)",
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


def test_without_network_opt_in_always_unknown():
    with db() as c:
        c.execute("PRAGMA query_only=ON")
        r = resolve_single(c, "https://www.linkedin.com/jobs/view/4455550001")
        assert r["resolution_status"] == "UNKNOWN"
        assert r["employer_apply_url"] is None
        assert r["database_writes"] == 0


def test_public_ats_get_can_resolve_employer_destination_read_only():
    body = {
        "jobs": [
            {
                "title": "HR Analyst",
                "location": "New York, NY",
                "descriptionPlain": linkedin()["description_raw"],
                "applyUrl": "https://jobs.ashbyhq.com/example/ABC/apply",
                "jobUrl": "https://jobs.ashbyhq.com/example/ABC",
                "isListed": True,
            }
        ]
    }
    opener = FakeOpener(body)
    with db() as c:
        c.execute("PRAGMA query_only=ON")
        r = resolve_single(
            c,
            "https://linkedin.com/jobs/view/4455550001",
            allow_public_get=True,
            provider_opener=opener,
        )
        assert r["resolution_status"] == "VERIFIED"
        assert r["linkedin_job_url"] == "https://www.linkedin.com/jobs/view/4455550001"
        assert r["employer_apply_url"] == "https://jobs.ashbyhq.com/example/ABC/apply"
        assert r["applications_submitted"] == 0
        assert r["submit_authorized"] is False
        assert len(opener.urls) == 1


def test_unknown_linkedin_id_does_not_leak_or_pick_other_job():
    with db() as c:
        c.execute("PRAGMA query_only=ON")
        r = resolve_single(c, "https://linkedin.com/jobs/view/5550001001")
        assert r["reason"] == "linkedin_job_not_in_hunter"
        assert r["employer_apply_url"] is None


def test_duplicate_identity_fails_closed():
    with db() as c:
        sample = linkedin()
        c.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
            (
                102,
                "LinkedIn",
                sample["company_name"],
                sample["title"],
                sample["location_raw"],
                sample["description_raw"],
                sample["job_url"],
                None,
                sample["ats_job_id"],
            ),
        )
        with pytest.raises(ValueError, match="More than one"):
            resolve_single(c, "https://linkedin.com/jobs/view/4455550001")


def test_invalid_or_unsafe_source_id_rejected():
    with db() as c, pytest.raises(ValueError, match="LinkedIn"):
        resolve_single(c, "https://attacker.example.org/jobs/view/4455550001")
