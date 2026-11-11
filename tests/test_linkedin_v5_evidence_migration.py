"""Regression for the additive, verification-only employer URL store."""

from __future__ import annotations

import importlib
import sqlite3

from app.linkedin_verified_url_evidence_v5 import latest_verified_for_job


def test_migration_adds_schema_without_links_or_authority():
    migration = importlib.import_module(
        "migrations.060_linkedin_verified_url_evidence_v5"
    )
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE jobs (id INTEGER PRIMARY KEY, job_url TEXT, apply_url TEXT)"
        )
        connection.execute(
            "INSERT INTO jobs VALUES (1, 'https://linkedin.com/jobs/view/123', 'https://linkedin.com/jobs/view/123')"
        )
        migration.apply(connection)
        migration.apply(connection)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM linkedin_employer_url_evidence_v5"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute(
            "SELECT job_url,apply_url FROM jobs WHERE id=1"
        ).fetchone() == (
            "https://linkedin.com/jobs/view/123",
            "https://linkedin.com/jobs/view/123",
        )
        job = {
            "id": 1,
            "source": "linkedin",
            "company_name": "Example Co",
            "title": "HR Analyst",
            "job_url": "https://linkedin.com/jobs/view/123",
            "ats_job_id": "123",
        }
        assert latest_verified_for_job(connection, job) is None
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(linkedin_employer_url_evidence_v5)"
            )
        }
        assert {
            "employer_job_url",
            "employer_apply_url",
            "job_identity_sha256",
            "submit_authorized",
            "provenance_json",
        }.issubset(columns)
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
