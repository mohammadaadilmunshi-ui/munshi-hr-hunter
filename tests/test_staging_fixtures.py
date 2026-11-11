from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from app import database
from app.staging_fixtures import FIXTURES, assert_staging_identity, fixture_metadata, seed


def _tags() -> set[str]:
    return {tag for fixture in FIXTURES for tag in fixture.scenario["tags"]}


def test_fixture_catalog_is_fictional_and_covers_staging_matrix() -> None:
    tags = _tags()
    required = {
        "HR Analyst", "People Analytics", "Talent Acquisition", "HRIS", "Compensation", "HRBP",
        "seniority_rejection", "unrelated_role_rejection", "remote", "hybrid", "onsite",
        "salary_present", "salary_missing", "sponsorship_uncertain", "sponsorship_restricted",
        "Greenhouse-like", "Lever-like", "Ashby-like", "Workday-like", "missing_required_application_question",
        "expected_salary_question", "veteran_self_ID", "disability_self_ID", "duplicate_posting",
        "malformed_partial_JD", "very_long_JD", "weak_evidence_match", "prepared_artifact_present", "prepared_artifact_missing",
    }
    assert required <= tags
    assert all("TEST/STAGING" in fixture.scenario["employer_marking"] for fixture in FIXTURES)
    assert all(".invalid" in f"https://staging-fixtures.invalid{fixture.apply_path}" for fixture in FIXTURES)
    assert fixture_metadata() == {"environment": "staging", "synthetic": True, "is_test_data": True, "source": "staging_fixture", "external_actions_disabled": True, "fixture_version": "staging-release-1"}


def test_seed_is_dry_by_default_and_requires_positive_staging_identity(hunter_db) -> None:
    connection = database.get_connection()
    try:
        with pytest.raises(RuntimeError, match="positively established"):
            seed(connection, identity="development", database_path=hunter_db)
        outcome = seed(connection, identity="staging", database_path=hunter_db)
        assert outcome["dry_run"] is True
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        connection.close()


def test_seed_is_idempotent_and_cleanup_is_limited_to_owned_fixture_jobs(hunter_db) -> None:
    connection = database.get_connection()
    try:
        real_id = connection.execute("INSERT INTO jobs(job_fingerprint,source,company_name,title) VALUES ('real-preserved','manual','Real Employer','HR Analyst')").lastrowid
        connection.commit()
        first = seed(connection, identity="staging", database_path=hunter_db, dry_run=False)
        assert first["seeded"] == len(FIXTURES)
        assert connection.execute("SELECT COUNT(*) FROM staging_synthetic_jobs").fetchone()[0] == len(FIXTURES)
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE source='staging_fixture'").fetchone()[0] == len(FIXTURES)
        # All action flags are explicit zero and no synthetic job creates an event/receipt.
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE source='staging_fixture' AND (telegram_sent != 0 OR sent_to_n8n != 0 OR already_applied != 0)").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM n8n_results").fetchone()[0] == 0
        seed(connection, identity="staging", database_path=hunter_db, dry_run=False)
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE id=?", (real_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE source='staging_fixture'").fetchone()[0] == len(FIXTURES)
        rows = connection.execute("SELECT j.company_name,j.apply_url,s.environment,s.synthetic,s.is_test_data,s.source,s.external_actions_disabled FROM jobs j JOIN staging_synthetic_jobs s ON s.job_id=j.id").fetchall()
        assert all("TEST/STAGING" in row[0] and ".invalid" in row[1] and tuple(row[2:]) == ("staging", 1, 1, "staging_fixture", 1) for row in rows)
        # Canonical totals must consume this additive view, which excludes the
        # ledger-owned fixtures without reclassifying actual jobs.
        assert connection.execute("SELECT COUNT(*) FROM canonical_jobs_v1").fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize("path", ["/tmp/production/hunter.db", "/tmp/prod.sqlite", "/tmp/dashboard.munshi.systems.db"])
def test_seed_refuses_production_like_paths(path: str) -> None:
    with pytest.raises(RuntimeError, match="production-like"):
        assert_staging_identity(identity="staging", database_path=path)


def test_seed_refuses_explicit_production_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MUNSHI_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="production environment"):
        assert_staging_identity(identity="staging", database_path=tmp_path / "staging.db")


def test_fixture_module_has_no_authority_imports() -> None:
    source = (Path(__file__).resolve().parent.parent / "app" / "staging_fixtures.py").read_text(encoding="utf-8")
    modules = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) and node.module}
    prohibited = {"app.n8n_dispatch", "app.gmail_integration", "app.api", "app.telegram_dispatch", "app.relationship_intelligence", "app.native_application_preparation"}
    assert not modules & prohibited
