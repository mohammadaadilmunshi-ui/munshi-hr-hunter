from __future__ import annotations

from app import application_notifications_v1 as notifications
from app import database


def _seed_authoritative_evidence() -> None:
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
            """CREATE TABLE IF NOT EXISTS application_mail_confirmation_evidence_v2(
                   evidence_id TEXT PRIMARY KEY,mail_event_id TEXT NOT NULL UNIQUE,
                   application_id TEXT NOT NULL,receipt_id TEXT NOT NULL,job_id INTEGER NOT NULL,
                   provider TEXT NOT NULL,sender_domain TEXT,binding_json TEXT NOT NULL,
                   confirmed_at TEXT NOT NULL)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO jobs(
                   id,job_fingerprint,source,company_name,title,apply_url
               ) VALUES(42,'notification-job-42','test','Example','People Analytics Analyst',
                        'https://boards.greenhouse.io/example/jobs/42')"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO application_plans_v2
                   (plan_id,tenant_id,user_id,application_id,job_id,provider)
               VALUES('plan-notification-1','default','local-owner','application-1',42,'GREENHOUSE')"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO production_submission_receipts_v1
                   (receipt_id,tenant_id,user_id,application_id,plan_id,provider,
                    provider_application_id,verified_at)
               VALUES('receipt-notification-1','default','local-owner','application-1',
                      'plan-notification-1','GREENHOUSE','gh-555123',
                      '2026-09-16T17:00:00+00:00')"""
        )
        connection.commit()
    finally:
        connection.close()


def _seed_confirmation() -> None:
    connection = database.get_connection()
    try:
        connection.execute(
            """INSERT OR IGNORE INTO application_mail_confirmation_evidence_v2(
                   evidence_id,mail_event_id,application_id,receipt_id,job_id,provider,
                   sender_domain,binding_json,confirmed_at
               ) VALUES('evidence-confirmation-1','mail-confirmation-1','application-1',
                        'receipt-notification-1',42,'GREENHOUSE','greenhouse.io','{}',
                        '2026-09-16T17:05:00+00:00')"""
        )
        connection.commit()
    finally:
        connection.close()


def test_projection_preferences_outbox_and_restart_are_idempotent(hunter_db) -> None:
    _seed_authoritative_evidence()
    saved = notifications.save_preference(
        destination_email="candidate@example.com",
        submission_email_enabled=True,
        employer_confirmation_email_enabled=True,
    )
    assert saved["destination_email"] == "candidate@example.com"
    assert saved["submission_email_enabled"] is True

    connection = database.get_connection()
    try:
        submission = connection.execute(
            """SELECT event_kind,delivery_state,body_text FROM application_notification_outbox_v1
               WHERE event_kind='SUBMISSION_VERIFIED'"""
        ).fetchone()
        assert submission["delivery_state"] == "PENDING"
        assert "SUBMITTED ✓" in submission["body_text"]
    finally:
        connection.close()

    _seed_confirmation()
    assert notifications.sync_notification_outbox() == 1
    assert notifications.sync_notification_outbox() == 0

    rows = notifications.application_evidence_rows()
    assert rows[0]["submitted_label"] == "SUBMITTED ✓"
    assert rows[0]["employer_email_label"] == "EMPLOYER EMAIL CONFIRMED ✓"

    # Simulate process restart / additive upgrade re-entry. Existing preference and
    # outbox rows must survive, and schema installation must remain idempotent.
    notifications.ensure_schema()
    notifications.ensure_schema()
    restarted = notifications.get_preference()
    assert restarted == saved
    connection = database.get_connection()
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM application_notification_outbox_v1"
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 2


def test_feature_flag_off_never_delivers(hunter_db, monkeypatch) -> None:
    _seed_authoritative_evidence()
    notifications.save_preference(
        destination_email="candidate@example.com",
        submission_email_enabled=True,
        employer_confirmation_email_enabled=False,
    )

    class Adapter:
        calls = 0

        def send(self, _message):
            self.calls += 1
            return "should-not-run"

    adapter = Adapter()
    monkeypatch.setenv("MUNSHI_APPLICATION_EMAIL_NOTIFICATIONS_ENABLED", "false")
    assert notifications.drain_notification_outbox(adapter=adapter) == {
        "sent": 0,
        "ambiguous": 0,
        "pending": 0,
    }
    assert adapter.calls == 0


def test_successful_delivery_is_durable_and_not_replayed(hunter_db, monkeypatch) -> None:
    _seed_authoritative_evidence()
    notifications.save_preference(
        destination_email="candidate@example.com",
        submission_email_enabled=True,
        employer_confirmation_email_enabled=False,
    )
    monkeypatch.setenv("MUNSHI_APPLICATION_EMAIL_NOTIFICATIONS_ENABLED", "true")

    class Adapter:
        def __init__(self):
            self.calls = 0
            self.messages = []

        def send(self, message):
            self.calls += 1
            self.messages.append(dict(message))
            return "smtp2go-message-1"

    adapter = Adapter()
    first = notifications.drain_notification_outbox(adapter=adapter)
    second = notifications.drain_notification_outbox(adapter=adapter)
    assert first["sent"] == 1
    assert second["sent"] == 0
    assert adapter.calls == 1
    serialized = str(adapter.messages[0]).lower()
    assert "credref:" not in serialized
    assert "verification code" not in serialized
    assert "claim_digest" not in serialized

    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT delivery_state,attempt_count,provider_message_id FROM application_notification_outbox_v1"
        ).fetchone()
    finally:
        connection.close()
    assert row["delivery_state"] == "SENT"
    assert row["attempt_count"] == 1
    assert row["provider_message_id"] == "smtp2go-message-1"


def test_unknown_delivery_result_becomes_ambiguous_and_is_never_auto_retried(
    hunter_db, monkeypatch
) -> None:
    _seed_authoritative_evidence()
    notifications.save_preference(
        destination_email="candidate@example.com",
        submission_email_enabled=True,
        employer_confirmation_email_enabled=False,
    )
    monkeypatch.setenv("MUNSHI_APPLICATION_EMAIL_NOTIFICATIONS_ENABLED", "true")

    class Adapter:
        def __init__(self):
            self.calls = 0

        def send(self, _message):
            self.calls += 1
            raise RuntimeError("synthetic unknown network outcome")

    adapter = Adapter()
    first = notifications.drain_notification_outbox(adapter=adapter)
    second = notifications.drain_notification_outbox(adapter=adapter)
    assert first["ambiguous"] == 1
    assert second["ambiguous"] == 0
    assert adapter.calls == 1

    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT delivery_state,attempt_count,last_error_text FROM application_notification_outbox_v1"
        ).fetchone()
    finally:
        connection.close()
    assert row["delivery_state"] == "AMBIGUOUS"
    assert row["attempt_count"] == 1
    assert "automatic retry disabled" in row["last_error_text"]


def test_smtp2go_adapter_uses_environment_secret_without_persisting_it(
    hunter_db, monkeypatch
) -> None:
    monkeypatch.setenv("MUNSHI_SMTP2GO_API_KEY", "smtp2go-test-key-abcdefghijklmnop")
    monkeypatch.setenv("MUNSHI_SMTP2GO_SENDER", "notifications@munshi.systems")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"email_id": "smtp-id-1"}}

    class Client:
        def __init__(self):
            self.payload = None

        def post(self, url, json):
            assert url == notifications.SMTP2GO_API_URL
            self.payload = dict(json)
            return Response()

    client = Client()
    adapter = notifications.SMTP2GOAdapter(client=client)
    message_id = adapter.send(
        {
            "destination_email": "candidate@example.com",
            "subject": "MUNSHI status",
            "body_text": "SUBMITTED ✓",
        }
    )
    assert message_id == "smtp-id-1"
    assert client.payload["api_key"] == "smtp2go-test-key-abcdefghijklmnop"

    connection = database.get_connection()
    try:
        notifications.ensure_schema(connection)
        durable = "|".join(
            str(row[0])
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
            ).fetchall()
        )
    finally:
        connection.close()
    assert "smtp2go-test-key-abcdefghijklmnop" not in durable
