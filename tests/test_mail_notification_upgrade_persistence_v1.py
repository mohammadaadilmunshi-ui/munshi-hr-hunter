from __future__ import annotations

import importlib.util
from pathlib import Path

from app import application_notifications_runtime_v2 as notifications
from app import database
from app.ats_managed_identity import prepare_managed_account


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    "052_inbound_mail_relay_v1.py",
    "053_mail_intelligence_v2.py",
    "054_application_notifications_v1.py",
)


def _apply_migration(filename: str, connection) -> None:
    path = ROOT / "migrations" / filename
    spec = importlib.util.spec_from_file_location(f"migration_{filename[:-3]}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load migration {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.apply(connection)


def _run_mail_upgrade() -> None:
    connection = database.get_connection()
    try:
        for filename in MIGRATIONS:
            _apply_migration(filename, connection)
        connection.commit()
    finally:
        connection.close()


def test_clean_upgrade_and_restart_preserve_account_mapping_and_notification_state(
    hunter_db, monkeypatch
) -> None:
    monkeypatch.setenv("MUNSHI_APPLICATION_MAIL_DOMAIN", "mail.munshi.systems")
    monkeypatch.setenv("MUNSHI_APPLICATION_MAIL_RELAY_ENABLED", "false")
    monkeypatch.setenv(
        "MUNSHI_VAULT_KEY",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )

    # Clean upgrade: all three additive migrations must install on the supported
    # initialized Hunter database without mutating any production database.
    _run_mail_upgrade()

    account = prepare_managed_account(
        provider="workday",
        account_scope="wd5.myworkdayjobs.com/example",
        label="Example Workday",
    )
    preference = notifications.save_preference(
        destination_email="candidate@example.com",
        submission_email_enabled=True,
        employer_confirmation_email_enabled=True,
    )

    account_id = str(account["account_id"])
    identity_id = str(account["identity_id"])
    expected_ref = f"ats-secret://{account_id}/password"

    connection = database.get_connection()
    try:
        binding_before = connection.execute(
            """SELECT identity_id,secret_ref,password_mode,password_version
               FROM ats_managed_account_bindings WHERE account_id=?""",
            (account_id,),
        ).fetchone()
        identity_before = connection.execute(
            """SELECT email_address,relay_state FROM ats_application_identities
               WHERE identity_id=?""",
            (identity_id,),
        ).fetchone()
        table_names = {
            str(row[0])
            for row in connection.execute(
                """SELECT name FROM sqlite_master WHERE type='table' AND name IN (
                     'inbound_mail_messages','mail_events_v2','mail_one_time_artifacts_v2',
                     'application_mail_confirmation_evidence_v2',
                     'application_notification_preferences_v1','application_notification_outbox_v1'
                   )"""
            ).fetchall()
        }
    finally:
        connection.close()

    assert binding_before["identity_id"] == identity_id
    assert binding_before["secret_ref"] == expected_ref
    assert binding_before["password_mode"] == "MUNSHI_GENERATED_UNIQUE"
    assert identity_before["email_address"].startswith("u_")
    assert identity_before["email_address"].endswith("@mail.munshi.systems")
    assert table_names == {
        "inbound_mail_messages",
        "mail_events_v2",
        "mail_one_time_artifacts_v2",
        "application_mail_confirmation_evidence_v2",
        "application_notification_preferences_v1",
        "application_notification_outbox_v1",
    }

    # Supported upgrade/restart re-entry: rerun every migration twice. DDL must be
    # idempotent and durable account/credential references + user preference must
    # survive unchanged.
    _run_mail_upgrade()
    _run_mail_upgrade()
    assert notifications.get_preference() == preference

    connection = database.get_connection()
    try:
        binding_after = connection.execute(
            """SELECT identity_id,secret_ref,password_mode,password_version
               FROM ats_managed_account_bindings WHERE account_id=?""",
            (account_id,),
        ).fetchone()
        identity_after = connection.execute(
            """SELECT email_address,relay_state FROM ats_application_identities
               WHERE identity_id=?""",
            (identity_id,),
        ).fetchone()
        outbox_count = connection.execute(
            "SELECT COUNT(*) FROM application_notification_outbox_v1"
        ).fetchone()[0]
    finally:
        connection.close()

    assert tuple(binding_after) == tuple(binding_before)
    assert tuple(identity_after) == tuple(identity_before)
    assert outbox_count == 0
