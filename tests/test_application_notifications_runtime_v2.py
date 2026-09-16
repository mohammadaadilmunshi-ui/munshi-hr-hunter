from __future__ import annotations

import pytest

from app import application_notifications_runtime_v2 as notifications
from app import database


def _seed_receipt() -> None:
    connection = database.get_connection()
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS application_plans_v2(
                   plan_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,user_id TEXT NOT NULL,
                   application_id TEXT NOT NULL,job_id INTEGER NOT NULL,provider TEXT NOT NULL)"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS production_submission_receipts_v1(
                   receipt_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,user_id TEXT NOT NULL,
                   application_id TEXT NOT NULL,plan_id TEXT NOT NULL,provider TEXT NOT NULL,
                   provider_application_id TEXT NOT NULL,verified_at TEXT NOT NULL)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO jobs(
                   id,job_fingerprint,source,company_name,title,apply_url
               ) VALUES(43,'notification-v2-job-43','test','Example Two','Workforce Analyst',
                        'https://boards.greenhouse.io/example/jobs/43')"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO application_plans_v2
                   (plan_id,tenant_id,user_id,application_id,job_id,provider)
               VALUES('plan-notification-v2','default','local-owner','application-v2',43,'GREENHOUSE')"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO production_submission_receipts_v1
                   (receipt_id,tenant_id,user_id,application_id,plan_id,provider,
                    provider_application_id,verified_at)
               VALUES('receipt-notification-v2','default','local-owner','application-v2',
                      'plan-notification-v2','GREENHOUSE','gh-v2-1',
                      '2026-09-16T18:00:00+00:00')"""
        )
        connection.commit()
    finally:
        connection.close()


def test_preference_and_outbox_write_are_atomic(hunter_db, monkeypatch) -> None:
    _seed_receipt()

    def explode(*_args, **_kwargs):
        raise RuntimeError("synthetic projection failure")

    monkeypatch.setattr(notifications, "_sync_outbox_transaction", explode)
    with pytest.raises(RuntimeError, match="synthetic projection failure"):
        notifications.save_preference(
            destination_email="candidate@example.com",
            submission_email_enabled=True,
            employer_confirmation_email_enabled=False,
        )

    connection = database.get_connection()
    try:
        preference_count = connection.execute(
            "SELECT COUNT(*) FROM application_notification_preferences_v1"
        ).fetchone()[0]
        outbox_count = connection.execute(
            "SELECT COUNT(*) FROM application_notification_outbox_v1"
        ).fetchone()[0]
    finally:
        connection.close()
    assert preference_count == 0
    assert outbox_count == 0


def test_notification_cycle_projects_after_restart_and_delivery_flag_stays_fail_closed(
    hunter_db, monkeypatch
) -> None:
    _seed_receipt()
    notifications.save_preference(
        destination_email="candidate@example.com",
        submission_email_enabled=True,
        employer_confirmation_email_enabled=False,
    )

    # Simulate a fresh process re-entering idempotent schema + projection.
    notifications.ensure_schema()
    monkeypatch.setenv("MUNSHI_APPLICATION_EMAIL_NOTIFICATIONS_ENABLED", "false")

    class Adapter:
        calls = 0

        def send(self, _message):
            self.calls += 1
            return "must-not-send"

    adapter = Adapter()
    result = notifications.notification_cycle(adapter=adapter)
    assert result == {"queued": 0, "sent": 0, "ambiguous": 0, "pending": 0}
    assert adapter.calls == 0

    connection = database.get_connection()
    try:
        row = connection.execute(
            """SELECT event_kind,delivery_state,destination_email
               FROM application_notification_outbox_v1"""
        ).fetchone()
    finally:
        connection.close()
    assert row["event_kind"] == "SUBMISSION_VERIFIED"
    assert row["delivery_state"] == "PENDING"
    assert row["destination_email"] == "candidate@example.com"
