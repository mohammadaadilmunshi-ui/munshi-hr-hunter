from __future__ import annotations

from pathlib import Path

import pytest

from scripts import complete_loop_migrate


EXPECTED_MIGRATIONS = (
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
    "migrations.057_linkedin_recent_url_backfill_v1",
)


def test_complete_loop_migration_manifest_is_exact() -> None:
    assert complete_loop_migrate.MIGRATIONS == EXPECTED_MIGRATIONS
    assert len(complete_loop_migrate.MIGRATIONS) == 17
    assert complete_loop_migrate.REQUIRED_CALLBACK_COLUMNS == {
        "payload_sha256",
        "execution_artifact_id",
    }
    assert {
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
    }.issubset(complete_loop_migrate.REQUIRED_TABLES)
    assert complete_loop_migrate.REQUIRED_NOTIFICATION_PREFERENCE_COLUMNS == {
        "destination_email",
        "submission_email_enabled",
        "employer_confirmation_email_enabled",
        "communication_email_enabled",
    }
    assert complete_loop_migrate.REQUIRED_INBOUND_MAIL_ENCRYPTION_COLUMNS == {
        "content_ciphertext",
        "content_nonce",
        "content_algorithm",
    }


def test_runner_is_transactional_and_production_write_is_explicit() -> None:
    source = Path("scripts/complete_loop_migrate.py").read_text(encoding="utf-8")
    assert 'connection.execute("BEGIN IMMEDIATE")' in source
    assert "connection.in_transaction" in source
    assert "connection.rollback()" in source
    assert "SEGMENTED_BACKUP_ROLLBACK_GUARDED" in source
    assert "AUTHORIZED_GUARDED_COMPLETE_LOOP_MIGRATION" in source
    assert "--verify-only" in source


def test_complete_loop_runner_tolerates_executescript_commit_boundary(
    hunter_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUD_SHADOW_MODE", "false")
    result = complete_loop_migrate.apply(
        hunter_db,
        environment="production",
        authorization=complete_loop_migrate.PRODUCTION_AUTHORIZATION,
        full_quick_check=True,
    )
    assert len(result["migrations"]) == 17
    assert "migrations.052_inbound_mail_relay_v1" in result["migrations"]
    assert "migrations.056_mandatory_mailbox_verification_v1" in result["migrations"]
    assert "migrations.057_linkedin_recent_url_backfill_v1" in result["migrations"]
    assert result["quick_check"] == "ok"


def test_hunter_image_packages_complete_loop_runner() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts/complete_loop_migrate.py ./scripts/complete_loop_migrate.py" in dockerfile


def _assert_db_restore_contract(source: str, restore_name: str) -> None:
    assert restore_name in source
    assert "migration_attempted=1" in source
    assert "stop hunter" in source
    assert "complete_loop_migrate.py" in source
    assert "-wal" in source
    assert "-shm" in source
    assert "ROLLBACK_DB_RESTORE=PASS" in source
    assert "type=volume" in source
    assert "foreign_key_check" in source
    assert "quick_check" in source


def test_staging_deploy_runs_migration_offline_and_restores_db_on_rollback() -> None:
    source = Path("deploy/netcup/deploy_staging_release.sh").read_text(encoding="utf-8")
    _assert_db_restore_contract(source, "restore_staging_db")
    stop_at = source.index("=== STOP STAGING HUNTER FOR COMPLETE-LOOP MIGRATION ===")
    migrate_at = source.index("/app/hunter/scripts/complete_loop_migrate.py")
    restart_at = source.index("=== RECREATE STAGING HUNTER ONLY ===")
    assert stop_at < migrate_at < restart_at
    assert "--environment staging" in source
    assert "STAGING_COMPLETE_LOOP_SCHEMA_READY=YES" in source
    assert "force-recreate n8n" not in source
    assert "force-recreate ollama" not in source


def test_production_deploy_runs_migration_offline_and_restores_db_on_rollback() -> None:
    source = Path("deploy/netcup/deploy_production_release.sh").read_text(encoding="utf-8")
    _assert_db_restore_contract(source, "restore_production_db")
    snapshot_at = source.index("=== PREPARE CURRENT STORAGE-SAFE ROLLBACK SNAPSHOT ===")
    migrate_at = source.index("/app/hunter/scripts/complete_loop_migrate.py")
    restart_at = source.index("=== RECREATE HUNTER ONLY ===")
    assert snapshot_at < migrate_at < restart_at
    assert "prepare_storage_safe_backup" in source
    assert '"${compose[@]}" stop hunter' in source
    assert "hunter_stopped=1" in source
    assert "PRODUCTION_DB_PREBACKUP_QUICK_CHECK=PASS" in source
    assert "PRODUCTION_DB_RAW_SHA256=" in source
    assert "--environment production" in source
    assert "AUTHORIZED_GUARDED_COMPLETE_LOOP_MIGRATION" in source
    assert "PRODUCTION_COMPLETE_LOOP_SCHEMA_READY=YES" in source
    assert 'rc="${1:-$trapped_rc}"' in source
    assert "force-recreate n8n" not in source
    assert "force-recreate ollama" not in source


def test_deployment_ci_runs_complete_loop_deployment_tests() -> None:
    source = Path(".github/workflows/deployment-ci-reusable.yml").read_text(encoding="utf-8")
    assert "tests/test_complete_loop_deployment_migrations.py" in source


def test_production_verify_only_allows_normal_shadow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_SHADOW_MODE", "true")
    complete_loop_migrate._assert_environment(
        "production", verify_only=True, authorization=None
    )


def test_production_mutation_still_rejects_shadow_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_SHADOW_MODE", "true")
    with pytest.raises(RuntimeError, match="refuses CLOUD_SHADOW_MODE=true"):
        complete_loop_migrate._assert_environment(
            "production",
            verify_only=False,
            authorization=complete_loop_migrate.PRODUCTION_AUTHORIZATION,
        )


def test_production_mutation_still_requires_explicit_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUD_SHADOW_MODE", "false")
    with pytest.raises(PermissionError, match="Explicit production Complete Loop migration authorization"):
        complete_loop_migrate._assert_environment(
            "production", verify_only=False, authorization=None
        )