from __future__ import annotations

import pytest

from app import application_notifications_runtime_v3 as notifications
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


def _seed_communications() -> None:
    connection = database.get_connection()
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS mail_events_v2(
                   mail_event_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,user_id TEXT NOT NULL,
                   correlation_state TEXT NOT NULL)"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS application_communication_events_v2(
                   communication_id TEXT PRIMARY KEY,mail_event_id TEXT NOT NULL UNIQUE,
                   application_id TEXT,job_id INTEGER,category TEXT NOT NULL,
                   sender_domain TEXT,received_at TEXT NOT NULL,
                   auto_reply_allowed INTEGER NOT NULL DEFAULT 0 CHECK(auto_reply_allowed=0))"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mail_events_v2
                   (mail_event_id,tenant_id,user_id,correlation_state)
               VALUES('mail-interview-v3','default','local-owner','UNIQUE')"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO application_communication_events_v2(
                   communication_id,mail_event_id,application_id,job_id,category,
                   sender_domain,received_at,auto_reply_allowed)
               VALUES('comm-interview-v3','mail-interview-v3','application-v2',43,
                      'INTERVIEW','recruiting.example.test','2026-09-16T19:00:00+00:00',0)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mail_events_v2
                   (mail_event_id,tenant_id,user_id,correlation_state)
               VALUES('mail-unmatched-v3','default','local-owner','NONE')"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO application_communication_events_v2(
                   communication_id,mail_event_id,application_id,job_id,category,
                   sender_domain,received_at,auto_reply_allowed)
               VALUES('comm-unmatched-v3','mail-unmatched-v3',NULL,NULL,
                      'RECRUITER','unknown.example.test','2026-09-16T19:01:00+00:00',0)"""
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
            communication_email_enabled=False,
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
        communication_email_enabled=False,
    )

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


def test_correlated_communication_queues_sanitized_notification_and_unmatched_stays_read_only(
    hunter_db,
) -> None:
    _seed_receipt()
    _seed_communications()
    preference = notifications.save_preference(
        destination_email="candidate@example.com",
        submission_email_enabled=False,
        employer_confirmation_email_enabled=False,
        communication_email_enabled=True,
    )
    assert preference["communication_email_enabled"] is True

    connection = database.get_connection()
    try:
        rows = connection.execute(
            """SELECT event_kind,source_id,subject,body_text,delivery_state
               FROM application_notification_outbox_v1 ORDER BY source_id"""
        ).fetchall()
    finally:
        connection.close()

    assert len(rows) == 1
    assert rows[0]["event_kind"] == "INTERVIEW"
    assert rows[0]["source_id"] == "comm-interview-v3"
    assert rows[0]["delivery_state"] == "PENDING"
    payload = f"{rows[0]['subject']}\n{rows[0]['body_text']}".lower()
    assert "482915" not in payload
    assert "http://" not in payload
    assert "https://" not in payload
    assert "recruiting.example.test" not in payload
    assert "unknown.example.test" not in payload

    communications = notifications.application_communication_rows(limit=10)
    assert len(communications) == 2
    matched = next(
        row for row in communications if row["communication_id"] == "comm-interview-v3"
    )
    unmatched = next(
        row for row in communications if row["communication_id"] == "comm-unmatched-v3"
    )
    assert matched["correlation"] == "Matched"
    assert matched["auto_reply_allowed"] is False
    assert unmatched["correlation"] == "Unmatched"
    assert unmatched["auto_reply_allowed"] is False
