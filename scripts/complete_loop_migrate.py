# Guarded deployment-time Complete Loop migration runner.
from __future__ import annotations

import argparse
import importlib
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path

MIGRATIONS = (
    "migrations.041_complete_application_loop_v1",
    "migrations.042_application_plan_transport_v2",
    "migrations.043_application_execution_artifacts",
    "migrations.044_n8n_preflight_callback_contract",
    "migrations.045_cover_letter_artifact_v1",
    "migrations.046_application_plan_revisions_v2",
    "migrations.047_synthetic_submit_commands_v1",
    "migrations.048_verified_submission_receipts_v1",
    "migrations.049_application_lifecycle_authority_v1",
    "migrations.050_prepare_orchestrator_v1",
    "migrations.051_native_cover_letter_writer_v1",
    "migrations.052_inbound_mail_relay_v1",
    "migrations.053_mail_intelligence_v2",
    "migrations.054_application_notifications_v1",
    "migrations.055_application_communication_notifications_v1",
    "migrations.056_mandatory_mailbox_verification_v1",
    "migrations.057_universal_brain_v1",
    "migrations.058_tsenta_provider_learning_foundation_v1",
    "migrations.059_tsenta_backend_oauth_v1",
)

REQUIRED_TABLES = {
    "prepare_operations_v1",
    "prepare_tasks_v1",
    "application_plans_v2",
    "apply_plan_handoffs_v2",
    "application_reviews_v2",
    "application_review_approvals_v2",
    "application_submit_commands_v1",
    "verified_submission_receipts_v1",
    "application_lifecycle_records",
    "ats_application_identities",
    "ats_managed_account_bindings",
    "ats_verification_requests",
    "inbound_mail_messages",
    "mail_events_v2",
    "mail_one_time_artifacts_v2",
    "application_mail_confirmation_evidence_v2",
    "application_communication_events_v2",
    "application_notification_preferences_v1",
    "application_notification_outbox_v1",
    "mailbox_verification_artifacts",
    "mailbox_verification_audit",
    "candidate_knowledge_revisions",
    "candidate_knowledge_facts",
    "candidate_knowledge_evidence",
    "candidate_knowledge_relations",
    "candidate_knowledge_conflicts",
    "candidate_derived_fact_cache",
    "application_semantic_binding_revisions",
    "application_semantic_bindings",
    "application_provider_routes_v1",
    "application_provider_route_events_v1",
    "tsenta_observations_v1",
    "tsenta_oauth_intents_v1",
    "tsenta_oauth_connections_v1",
}
REQUIRED_CALLBACK_COLUMNS = {"payload_sha256", "execution_artifact_id"}
REQUIRED_NOTIFICATION_PREFERENCE_COLUMNS = {
    "destination_email",
    "submission_email_enabled",
    "employer_confirmation_email_enabled",
    "communication_email_enabled",
}
PRODUCTION_AUTHORIZATION = "AUTHORIZED_GUARDED_COMPLETE_LOOP_MIGRATION"


def _truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().casefold() in {"1", "true", "yes", "on"}


def _assert_environment(environment: str, *, verify_only: bool, authorization: str | None) -> None:
    if environment == "staging":
        if not _truthy("CLOUD_SHADOW_MODE"):
            raise RuntimeError("Complete Loop staging migration requires CLOUD_SHADOW_MODE=true.")
        if _truthy("PRODUCTION_STATE_IMPORTED"):
            raise RuntimeError("Complete Loop staging migration refuses imported production state.")
        if _truthy("PRODUCTION_CALLBACKS_ENABLED"):
            raise RuntimeError("Complete Loop staging migration refuses production callbacks.")
        return

    if environment != "production":
        raise RuntimeError(f"Unsupported migration environment: {environment!r}")
    if not verify_only:
        if authorization != PRODUCTION_AUTHORIZATION:
            raise PermissionError("Explicit production Complete Loop migration authorization is required.")
        if _truthy("CLOUD_SHADOW_MODE"):
            raise RuntimeError("Production Complete Loop migration refuses CLOUD_SHADOW_MODE=true.")


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Hunter database is missing: {path}")
    if read_only:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
        connection.execute("PRAGMA query_only=ON")
    else:
        connection = sqlite3.connect(path, timeout=60)
    connection.execute("PRAGMA busy_timeout=60000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _verify_schema(connection: sqlite3.Connection, *, full_quick_check: bool) -> dict[str, object]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise RuntimeError("Complete Loop schema missing tables: " + ", ".join(missing))

    callback_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(n8n_callback_receipts)").fetchall()
    }
    missing_callback = sorted(REQUIRED_CALLBACK_COLUMNS - callback_columns)
    if missing_callback:
        raise RuntimeError("Complete Loop callback schema missing: " + ", ".join(missing_callback))

    notification_preference_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(application_notification_preferences_v1)"
        ).fetchall()
    }
    missing_notification_preferences = sorted(
        REQUIRED_NOTIFICATION_PREFERENCE_COLUMNS - notification_preference_columns
    )
    if missing_notification_preferences:
        raise RuntimeError(
            "Application notification preference schema missing: "
            + ", ".join(missing_notification_preferences)
        )

    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise RuntimeError(f"Complete Loop FK check failed: {len(foreign_keys)} violation(s).")

    quick = "skipped"
    if full_quick_check:
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick.casefold() != "ok":
            raise RuntimeError(f"Complete Loop quick_check failed: {quick}")

    return {
        "required_tables": sorted(REQUIRED_TABLES),
        "callback_columns": sorted(REQUIRED_CALLBACK_COLUMNS),
        "notification_preference_columns": sorted(
            REQUIRED_NOTIFICATION_PREFERENCE_COLUMNS
        ),
        "foreign_key_violations": len(foreign_keys),
        "quick_check": quick,
    }


def apply(
    db_path: str | Path,
    *,
    environment: str,
    authorization: str | None = None,
    full_quick_check: bool = False,
) -> dict[str, object]:
    _assert_environment(environment, verify_only=False, authorization=authorization)
    path = Path(db_path).resolve()
    connection = _connect(path, read_only=False)
    applied: list[str] = []
    committed_segments: list[str] = []
    try:
        for module_name in MIGRATIONS:
            module = importlib.import_module(module_name)
            migrate = getattr(module, "apply", None)
            if not callable(migrate):
                raise TypeError(f"Migration has no callable apply(): {module_name}")

            # Deployment migrations 052+ intentionally call sqlite3.executescript()
            # inside their shared ensure_schema() helpers. In Python's sqlite3
            # legacy transaction mode executescript() may commit a pending
            # transaction before running its script, so a single outer transaction
            # cannot safely span the complete manifest. Production/staging deploy
            # wrappers therefore stop Hunter, create and verify a byte-for-byte DB
            # backup, and restore that backup on any migration/deploy failure.
            #
            # Keep each migration guarded by its own write lock where possible,
            # tolerate an internal commit boundary, and verify the complete schema
            # before returning success. This preserves fail-closed deployment
            # behavior without pretending executescript() is nested-transactional.
            if connection.in_transaction:
                connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            migrate(connection)
            if connection.in_transaction:
                connection.commit()
            else:
                committed_segments.append(module_name)
            applied.append(module_name)

        verification = _verify_schema(connection, full_quick_check=full_quick_check)
        if connection.in_transaction:
            connection.commit()
        return {
            "environment": environment,
            "migrations": applied,
            "committed_segments": committed_segments,
            **verification,
        }
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def verify(
    db_path: str | Path,
    *,
    environment: str,
    full_quick_check: bool = False,
) -> dict[str, object]:
    _assert_environment(environment, verify_only=True, authorization=None)
    path = Path(db_path).resolve()
    connection = _connect(path, read_only=True)
    try:
        return {
            "environment": environment,
            **_verify_schema(connection, full_quick_check=full_quick_check),
        }
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--environment", required=True, choices=("staging", "production"))
    parser.add_argument("--authorization")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--full-quick-check", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.verify_only:
        result = verify(
            args.db_path,
            environment=args.environment,
            full_quick_check=args.full_quick_check,
        )
        print("COMPLETE_LOOP_SCHEMA_VERIFY=PASS")
    else:
        result = apply(
            args.db_path,
            environment=args.environment,
            authorization=args.authorization,
            full_quick_check=args.full_quick_check,
        )
        print(f"COMPLETE_LOOP_MIGRATIONS_APPLIED={len(result['migrations'])}")
        print("COMPLETE_LOOP_TRANSACTION=PASS")
        print("COMPLETE_LOOP_TRANSACTION_MODE=SEGMENTED_BACKUP_ROLLBACK_GUARDED")
        print(f"COMPLETE_LOOP_INTERNAL_COMMIT_SEGMENTS={len(result.get('committed_segments', []))}")
        print("COMPLETE_LOOP_SCHEMA_MIGRATION=PASS")

    print("COMPLETE_LOOP_REQUIRED_TABLES=PASS")
    print("COMPLETE_LOOP_CALLBACK_COLUMNS=PASS")
    print("COMPLETE_LOOP_FOREIGN_KEYS=PASS")
    print(f"COMPLETE_LOOP_QUICK_CHECK={result['quick_check']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
