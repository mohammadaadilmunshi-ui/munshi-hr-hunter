"""Fail-closed, deterministic synthetic data for an isolated staging database.

This module is deliberately a data fixture ledger, not a discovery source or
an action queue.  A fixture is only eligible to exist after the caller has
positively established the ``staging`` identity.  Its metadata lives in an
additive table so production/canonical jobs are never reclassified.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


STAGING_IDENTITY = "staging"
FIXTURE_VERSION = "staging-release-1"
SYNTHETIC_SOURCE = "staging_fixture"
_PRODUCTION_MARKERS = ("production", "prod", "dashboard.munshi.systems")


SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS staging_synthetic_jobs (
        job_id INTEGER PRIMARY KEY,
        fixture_key TEXT NOT NULL UNIQUE,
        fixture_version TEXT NOT NULL,
        environment TEXT NOT NULL CHECK(environment = 'staging'),
        synthetic INTEGER NOT NULL DEFAULT 1 CHECK(synthetic = 1),
        is_test_data INTEGER NOT NULL DEFAULT 1 CHECK(is_test_data = 1),
        source TEXT NOT NULL CHECK(source = 'staging_fixture'),
        external_actions_disabled INTEGER NOT NULL DEFAULT 1 CHECK(external_actions_disabled = 1),
        scenario_json TEXT NOT NULL,
        seeded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
    );""",
    "CREATE INDEX IF NOT EXISTS idx_staging_synthetic_jobs_version ON staging_synthetic_jobs(fixture_version);",
    """CREATE VIEW IF NOT EXISTS canonical_jobs_v1 AS
       SELECT j.* FROM jobs AS j
       WHERE NOT EXISTS (SELECT 1 FROM staging_synthetic_jobs AS s WHERE s.job_id=j.id);""",
)


@dataclass(frozen=True)
class FixtureJob:
    key: str
    title: str
    scenario: dict[str, Any]
    description: str
    salary: str | None = None
    remote_type: str | None = "remote"
    apply_path: str = "/prepare-only"


def _fixture(key: str, title: str, *tags: str, description: str = "Synthetic TEST/STAGING job. No live application endpoint.", salary: str | None = None, remote_type: str | None = "remote", apply_path: str = "/prepare-only") -> FixtureJob:
    return FixtureJob(key, title, {"tags": list(tags), "employer_marking": "TEST/STAGING fictional employer"}, description, salary, remote_type, apply_path)


# Every employer is fictional and explicitly TEST/STAGING-labelled.  Tags are
# durable scenario evidence for browser/API staging QA without adding columns
# to the canonical jobs table.
FIXTURES: tuple[FixtureJob, ...] = (
    _fixture("hr-analyst-remote-salary", "HR Analyst", "HR Analyst", "remote", "salary_present", "Greenhouse-like", salary="$75,000-$90,000"),
    _fixture("people-analytics-hybrid", "People Analytics Analyst", "People Analytics", "hybrid", "Lever-like", remote_type="hybrid"),
    _fixture("talent-acquisition-onsite", "Talent Acquisition Specialist", "Talent Acquisition", "onsite", "Ashby-like", remote_type="onsite"),
    _fixture("hris-sponsorship-uncertain", "HRIS Analyst", "HRIS", "sponsorship_uncertain", "Workday-like"),
    _fixture("compensation-salary-missing", "Compensation Analyst", "Compensation", "salary_missing"),
    _fixture("hrbp-prepared-artifact", "HR Business Partner", "HRBP", "prepared_artifact_present"),
    _fixture("seniority-rejection", "Senior Vice President, People", "seniority_rejection", "rejected"),
    _fixture("unrelated-role-rejection", "Software Platform Engineer", "unrelated_role_rejection", "rejected"),
    _fixture("sponsorship-restricted", "HR Operations Analyst", "sponsorship_restricted"),
    _fixture("missing-required-question", "Recruiting Operations Analyst", "missing_required_application_question", "NEEDS_INPUT"),
    _fixture("expected-salary", "People Programs Analyst", "expected_salary_question"),
    _fixture("veteran-self-id", "Talent Programs Analyst", "veteran_self_ID", "sensitive_answer_separate"),
    _fixture("disability-self-id", "Benefits Analyst", "disability_self_ID", "sensitive_answer_separate"),
    _fixture("duplicate-a", "HR Data Analyst", "duplicate_posting", "duplicate_group:hr-data-1"),
    _fixture("duplicate-b", "HR Data Analyst", "duplicate_posting", "duplicate_group:hr-data-1"),
    _fixture("malformed-partial-jd", "HR Coordinator", "malformed_partial_JD", description="TEST/STAGING partial JD: HR support."),
    _fixture("long-jd", "People Analytics Consultant", "very_long_JD", description="TEST/STAGING fictional job. " + "Evidence bounded requirements only. " * 450),
    _fixture("weak-evidence", "Compensation Associate", "weak_evidence_match"),
    _fixture("artifact-missing", "HR Generalist", "prepared_artifact_missing", "NEEDS_INPUT"),
)


def ensure_schema(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)


def fixture_metadata() -> dict[str, Any]:
    return {"environment": STAGING_IDENTITY, "synthetic": True, "is_test_data": True, "source": SYNTHETIC_SOURCE, "external_actions_disabled": True, "fixture_version": FIXTURE_VERSION}


def _safe_database_path(database_path: str | Path) -> Path:
    path = Path(database_path).expanduser().resolve()
    lowered = str(path).casefold()
    if any(marker in lowered for marker in _PRODUCTION_MARKERS):
        raise RuntimeError("Refusing a database path that appears production-like.")
    return path


def assert_staging_identity(*, identity: str | None, database_path: str | Path) -> Path:
    """Require an exact staging identity and reject production-looking paths."""
    if str(identity or "").strip().casefold() != STAGING_IDENTITY:
        raise RuntimeError("Synthetic fixtures require positively established staging identity.")
    configured_environment = " ".join(
        os.getenv(name, "") for name in ("MUNSHI_ENVIRONMENT", "AADIL_HR_HUNTER_ENVIRONMENT")
    ).casefold()
    if any(marker in configured_environment for marker in _PRODUCTION_MARKERS):
        raise RuntimeError("Refusing a production environment configuration.")
    return _safe_database_path(database_path)


def _fingerprint(key: str) -> str:
    return hashlib.sha256(f"{FIXTURE_VERSION}:{key}".encode()).hexdigest()


def _job_values(item: FixtureJob) -> tuple[Any, ...]:
    company = f"TEST/STAGING {item.key.replace('-', ' ').title()} Fictional Employer"
    return (_fingerprint(item.key), SYNTHETIC_SOURCE, company, item.title, item.remote_type, f"https://staging-fixtures.invalid/{item.key}", f"https://staging-fixtures.invalid{item.apply_path}", item.description, item.salary, "found")


def seed(connection: sqlite3.Connection, *, identity: str | None, database_path: str | Path, dry_run: bool = True) -> dict[str, int | bool]:
    """Idempotently replace only fixture-owned records.  Dry-run changes nothing."""
    assert_staging_identity(identity=identity, database_path=database_path)
    ensure_schema(connection)
    existing = int(connection.execute("SELECT COUNT(*) FROM staging_synthetic_jobs WHERE fixture_version=?", (FIXTURE_VERSION,)).fetchone()[0])
    result: dict[str, int | bool] = {"dry_run": dry_run, "existing": existing, "removed": existing, "seeded": len(FIXTURES)}
    if dry_run:
        return result
    # Fixture-owned IDs are selected from our additive ledger; no predicate can
    # ever select a real job merely because it shares a source/title.
    connection.execute("DELETE FROM jobs WHERE id IN (SELECT job_id FROM staging_synthetic_jobs)")
    for item in FIXTURES:
        cursor = connection.execute(
            """INSERT INTO jobs(job_fingerprint,source,company_name,title,remote_type,job_url,apply_url,description_raw,salary_raw,status,
                telegram_sent,sent_to_n8n,already_applied) VALUES (?,?,?,?,?,?,?,?,?,?,0,0,0)""",
            _job_values(item),
        )
        connection.execute(
            """INSERT INTO staging_synthetic_jobs(job_id,fixture_key,fixture_version,environment,synthetic,is_test_data,source,external_actions_disabled,scenario_json)
               VALUES (?,?,?,'staging',1,1,'staging_fixture',1,?)""",
            (int(cursor.lastrowid), item.key, FIXTURE_VERSION, json.dumps(item.scenario, sort_keys=True)),
        )
    connection.commit()
    return result


def iter_fixture_keys() -> Iterable[str]:
    return (item.key for item in FIXTURES)
