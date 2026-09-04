from __future__ import annotations

import importlib
import sqlite3

from app.database import SCHEMA_SQL, ensure_job_detail_columns, ensure_operational_columns
from app.staging_fixtures import seed


def test_disposable_staging_like_database_applies_015_through_023_in_order(tmp_path) -> None:
    path = tmp_path / "staging-like.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        # Representative pre-modernization base schema, then only the additive
        # modernization sequence is applied in numeric order.
        connection.executescript(SCHEMA_SQL)
        ensure_job_detail_columns(connection)
        ensure_operational_columns(connection)
        for number, name in ((15, "tenant_foundation"), (16, "candidate_digital_twin"), (17, "candidate_artifacts"), (18, "native_resume_shadow"), (19, "application_answer_brain"), (20, "career_preferences_policy"), (21, "relationship_intelligence"), (22, "native_application_preparation"), (23, "staging_synthetic_fixtures")):
            importlib.import_module(f"migrations.{number:03d}_{name}").apply(connection)
        connection.commit()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"tenants", "candidate_digital_twin_facts", "candidate_artifacts", "native_resume_shadow_runs", "application_answer_vault", "career_preferences", "relationship_contacts", "native_application_preparations", "staging_synthetic_jobs"} <= tables
        # The fixture schema keeps durable isolation metadata in its own table
        # and cannot seed without a positive staging identity.
        seed(connection, identity="staging", database_path=path, dry_run=False)
        assert connection.execute("SELECT COUNT(*) FROM staging_synthetic_jobs").fetchone()[0] > 0
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE source != 'staging_fixture'").fetchone()[0] == 0
    finally:
        connection.close()
