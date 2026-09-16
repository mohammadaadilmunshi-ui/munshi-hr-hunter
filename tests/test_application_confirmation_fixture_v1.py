from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from app import database, inbound_mail, mail_intelligence_v2
from app.ats_managed_identity import prepare_managed_account


def _minimal_receipt_schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS production_submission_receipts_v1(
               receipt_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,user_id TEXT NOT NULL,
               application_id TEXT NOT NULL,plan_id TEXT NOT NULL,provider TEXT NOT NULL,
               provider_application_id TEXT NOT NULL,verified_at TEXT NOT NULL)"""
    )


def test_confirmation_email_fixture_creates_independent_evidence_and_never_submits(
    hunter_db, monkeypatch
) -> None:
    monkeypatch.setenv("MUNSHI_APPLICATION_MAIL_DOMAIN", "mail.munshi.systems")
    monkeypatch.setenv("MUNSHI_APPLICATION_MAIL_RELAY_ENABLED", "1")
    monkeypatch.setattr(
        mail_intelligence_v2.production_receipt,
        "ensure_schema",
        _minimal_receipt_schema,
    )
    account = prepare_managed_account(
        provider="greenhouse",
        account_scope="boards.greenhouse.io/example",
        label="Example Greenhouse",
    )
    recipient = str(account["application_email"])

    connection = database.get_connection()
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS application_plans_v2(
                   plan_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,user_id TEXT NOT NULL,
                   application_id TEXT NOT NULL,job_id INTEGER NOT NULL,provider TEXT NOT NULL)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO jobs(
                   id,job_fingerprint,source,company_name,title,apply_url
               ) VALUES(42,'confirmation-fixture-job-42','test','Example',
                        'People Analytics Analyst',
                        'https://boards.greenhouse.io/example/jobs/42')"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO application_plans_v2
                   (plan_id,tenant_id,user_id,application_id,job_id,provider)
               VALUES('plan-confirmation-fixture','default','local-owner',
                      'application-confirmation-fixture',42,'GREENHOUSE')"""
        )
        _minimal_receipt_schema(connection)
        connection.execute(
            """INSERT OR IGNORE INTO production_submission_receipts_v1(
                   receipt_id,tenant_id,user_id,application_id,plan_id,provider,
                   provider_application_id,verified_at
               ) VALUES('receipt-confirmation-fixture','default','local-owner',
                        'application-confirmation-fixture','plan-confirmation-fixture',
                        'GREENHOUSE','gh-555123','2026-09-16T17:00:00+00:00')"""
        )
        connection.commit()
    finally:
        connection.close()

    fixture = (
        Path(__file__).parent / "fixtures" / "application_confirmation_email.eml"
    ).read_bytes().replace(b"{{RECIPIENT}}", recipient.encode())
    message_id = "<application-confirmation-fixture@example.test>"
    parsed = inbound_mail.parse_message(fixture, signed_message_id=message_id)
    result = mail_intelligence_v2.store_verified_message_v2(
        recipient=recipient,
        sender="no-reply@greenhouse.io",
        message_id=message_id,
        signed_at="2026-09-16T17:10:00+00:00",
        raw_sha256=hashlib.sha256(fixture).hexdigest(),
        parsed=parsed,
        router_class="APPLICATION_CONFIRMATION",
        now=datetime(2026, 9, 16, 17, 10, tzinfo=UTC),
    )

    assert result["classification"] == "APPLICATION_CONFIRMATION"
    assert result["application_email_confirmed"] is True
    assert result["submission_triggered"] is False

    connection = database.get_connection()
    try:
        evidence = connection.execute(
            """SELECT application_id,receipt_id,job_id,provider,binding_json
               FROM application_mail_confirmation_evidence_v2"""
        ).fetchone()
        event = connection.execute(
            "SELECT application_id,job_id,classification FROM mail_events_v2 WHERE mail_event_id=?",
            (result["mail_event_id"],),
        ).fetchone()
    finally:
        connection.close()
    assert evidence["application_id"] == "application-confirmation-fixture"
    assert evidence["receipt_id"] == "receipt-confirmation-fixture"
    assert evidence["job_id"] == 42
    assert evidence["provider"] == "GREENHOUSE"
    assert '"employer_or_sender":true' in evidence["binding_json"]
    assert '"ats_context":true' in evidence["binding_json"]
    assert event["application_id"] == "application-confirmation-fixture"
    assert event["classification"] == "APPLICATION_CONFIRMATION"
