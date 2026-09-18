"""Apply the additive Phase 1–7 schema set to isolated staging only.

The Netcup staging deployment wrapper creates and verifies a SQLite backup before
calling this module. This runner refuses production-like environments, applies
only idempotent/additive phase schemas, and performs an integrity check before
commit.
"""
from __future__ import annotations

import importlib
import os

from app.database import get_connection


MIGRATIONS = (
    "migrations.015_tenant_foundation",
    "migrations.016_candidate_digital_twin",
    "migrations.017_candidate_artifacts",
    "migrations.018_native_resume_shadow",
    "migrations.019_application_answer_brain",
    "migrations.020_career_preferences_policy",
    "migrations.021_relationship_intelligence",
    "migrations.027_native_resume_studio",
    "migrations.028_native_resume_studio_v2",
    "migrations.029_native_resume_studio_v3",
    "migrations.030_profile_workspace_v1",
    "migrations.031_phase45_truth_bindings",
    "migrations.032_phase67_intelligence_snapshots",
    "migrations.033_phase47_integrity_bindings",
)

REQUIRED_TABLES = {
    "tenants",
    "tenant_memberships",
    "candidate_digital_twin_facts",
    "candidate_artifacts",
    "application_answer_vault",
    "career_preferences",
    "relationship_contacts",
    "native_resume_sources",
    "native_resume_versions",
    "native_resume_truth_bindings",
    "native_resume_job_bindings",
    "opportunity_intelligence_evaluations",
    "relationship_strategy_snapshots",
}


def _truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().casefold() in {"1", "true", "yes", "on"}


def assert_isolated_staging() -> None:
    if not _truthy("CLOUD_SHADOW_MODE"):
        raise RuntimeError("Phase 1-7 migration is restricted to CLOUD_SHADOW_MODE staging.")
    if _truthy("PRODUCTION_STATE_IMPORTED"):
        raise RuntimeError("Refusing Phase 1-7 staging migration with production state imported.")
    if _truthy("PRODUCTION_CALLBACKS_ENABLED"):
        raise RuntimeError("Refusing Phase 1-7 staging migration while production callbacks are enabled.")


def apply() -> dict[str, object]:
    assert_isolated_staging()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for module_name in MIGRATIONS:
            module = importlib.import_module(module_name)
            module.apply(connection)

        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise RuntimeError("Phase 1-7 schema verification failed; missing: " + ", ".join(missing))

        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise RuntimeError("Phase 1-7 foreign-key check failed.")
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick.casefold() != "ok":
            raise RuntimeError(f"Phase 1-7 SQLite quick_check failed: {quick}")

        connection.commit()
        return {
            "migrations": list(MIGRATIONS),
            "required_tables": sorted(REQUIRED_TABLES),
            "quick_check": quick,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    result = apply()
    print(f"PHASE17_MIGRATIONS_APPLIED={len(result['migrations'])}")
    print("PHASE17_REQUIRED_TABLES=PASS")
    print("PHASE17_DB_QUICK_CHECK=PASS")
