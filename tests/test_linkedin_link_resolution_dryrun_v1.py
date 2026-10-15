from __future__ import annotations

import sqlite3

import pytest

from scripts.linkedin_link_resolution_dryrun_v1 import audit, read_only_connection


def fixture_database(path):
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE jobs (id INTEGER PRIMARY KEY, source TEXT, company_name TEXT,"
            " title TEXT, location_raw TEXT, job_url TEXT, apply_url TEXT, ats_job_id TEXT)"
        )
        db.executemany(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    1,
                    "JobSpy/linkedin",
                    "Example Co",
                    "HR Analyst",
                    "NY",
                    "https://linkedin.com/jobs/view/4555000000",
                    None,
                    "4555000000",
                ),
                (
                    2,
                    "Greenhouse",
                    "Example Co",
                    "HR Analyst",
                    "NY",
                    "https://job-boards.greenhouse.io/example/jobs/555",
                    "https://job-boards.greenhouse.io/example/jobs/555",
                    "555",
                ),
                (
                    3,
                    "JobSpy/linkedin",
                    "Other Co",
                    "Recruiter",
                    "NJ",
                    "https://linkedin.com/jobs/view/4555000001",
                    "https://linkedin.com/jobs/view/4555000001",
                    "4555000001",
                ),
            ],
        )


def test_readonly_audit_finds_candidates_without_mutating_jobs(tmp_path):
    path = tmp_path / "hunter.db"
    fixture_database(path)
    before = path.read_bytes()
    with read_only_connection(path) as db:
        result = audit(db)
        with pytest.raises(sqlite3.OperationalError):
            db.execute("UPDATE jobs SET title='changed'")
    assert result["total_linkedin_inspected"] == 2
    assert result["status_counts"] == {"CANDIDATE": 1, "UNKNOWN": 1}
    assert result["auto_submittable"] == 0
    assert path.read_bytes() == before


def test_missing_db_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        read_only_connection(path)
    assert not path.exists()
