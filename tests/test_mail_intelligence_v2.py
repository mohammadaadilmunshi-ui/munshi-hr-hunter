from __future__ import annotations

import base64
import hashlib
from email import policy
from email.message import EmailMessage

import pytest

from app import database, inbound_mail, mail_intelligence_v2
from app.ats_managed_identity import prepare_managed_account
from app.ats_verification_orchestrator import begin_verification


MAIL_SECRET = "test-mail-signing-secret-" + ("m" * 40)
ARTIFACT_SECRET = "test-mail-artifact-secret-" + ("a" * 40)


def _setup(monkeypatch):
    monkeypatch.setenv(
        "MUNSHI_VAULT_KEY",
        base64.urlsafe_b64encode(b"v" * 32).decode(),
    )
    monkeypatch.setenv("MUNSHI_APPLICATION_MAIL_DOMAIN", "mail.munshi.systems")
    monkeypatch.setenv("MUNSHI_APPLICATION_MAIL_RELAY_ENABLED", "1")
    monkeypatch.setenv("MUNSHI_MAIL_SIGNING_SECRET", MAIL_SECRET)
    monkeypatch.setenv("MUNSHI_MAIL_ARTIFACT_HMAC_SECRET", ARTIFACT_SECRET)
    return prepare_managed_account(
        provider="workday",
        account_scope="wd5.myworkdayjobs.com/example",
        label="Example Workday",
    )


def _raw_email(
    *,
    recipient: str,
    sender: str,
    message_id: str,
    subject: str,
    body: str,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content(body)
    return message.as_bytes(policy=policy.SMTP)


def _ingest(
    *,
    recipient: str,
    sender: str,
    message_id: str,
    subject: str,
    body: str,
    router_class: str = "ACCOUNT_VERIFICATION",
):
    raw = _raw_email(
        recipient=recipient,
        sender=sender,
        message_id=message_id,
        subject=subject,
        body=body,
    )
    parsed = inbound_mail.parse_message(raw, signed_message_id=message_id)
    return mail_intelligence_v2.store_verified_message_v2(
        recipient=recipient,
        sender=sender,
        message_id=message_id,
        signed_at="2026-09-16T17:00:00+00:00",
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        parsed=parsed,
        router_class=router_class,
    )


def _verification(account: dict[str, object], application_key: str):
    return begin_verification(
        account_id=str(account["account_id"]),
        provider="workday",
        expected_link_hosts=["wd5.myworkday.com"],
        expected_sender_domains=["myworkday.com"],
        application_key=application_key,
    )


def test_ordinary_emailed_code_is_one_time_and_not_historical(
    hunter_db,
    monkeypatch,
):
    account = _setup(monkeypatch)
    verification = _verification(account, "application-mail-code")
    recipient = str(account["application_email"])

    result = _ingest(
        recipient=recipient,
        sender="no-reply@myworkday.com",
        message_id="<mail-v2-code@example.test>",
        subject="Verify your candidate account - code 654321",
        body="Your verification code is 654321. Enter this code to verify your email.",
    )

    assert result["classification"] == "EMAIL_VERIFICATION_CODE"
    assert result["verification_ready"] is True
    assert result["submission_triggered"] is False

    connection = database.get_connection()
    try:
        event_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(mail_events_v2)")
        }
        assert "text_body" not in event_columns
        assert "html_body" not in event_columns
        assert "raw_body" not in event_columns
        assert "verification_code" not in event_columns
        event = connection.execute(
            "SELECT * FROM mail_events_v2 WHERE mail_event_id=?",
            (result["mail_event_id"],),
        ).fetchone()
        artifact = connection.execute(
            "SELECT * FROM mail_one_time_artifacts_v2 WHERE verification_request_id=?",
            (verification["request_id"],),
        ).fetchone()
        assert event is not None
        assert "654321" not in "|".join(str(event[key]) for key in event.keys())
        assert artifact is not None
        assert artifact["state"] == "READY"
        assert artifact["ciphertext"] is not None
        assert artifact["nonce"] is not None
        assert artifact["artifact_kind"] == "EMAIL_VERIFICATION_CODE"
    finally:
        connection.close()

    claimed = mail_intelligence_v2.claim_artifact(
        request_id=str(verification["request_id"]),
        application_key="application-mail-code",
    )
    assert claimed["artifact"] == "654321"
    assert claimed["artifact_kind"] == "EMAIL_VERIFICATION_CODE"
    assert claimed["claim_token"]

    with pytest.raises(mail_intelligence_v2.MailIntelligenceError, match="not claimable"):
        mail_intelligence_v2.claim_artifact(
            request_id=str(verification["request_id"]),
            application_key="application-mail-code",
        )

    consumed = mail_intelligence_v2.consume_artifact(
        request_id=str(verification["request_id"]),
        application_key="application-mail-code",
        claim_token=str(claimed["claim_token"]),
    )
    assert consumed == {
        "request_id": verification["request_id"],
        "state": "CONSUMED",
        "artifact_retained": False,
    }

    connection = database.get_connection()
    try:
        artifact = connection.execute(
            "SELECT * FROM mail_one_time_artifacts_v2 WHERE verification_request_id=?",
            (verification["request_id"],),
        ).fetchone()
        request = connection.execute(
            "SELECT state,reason_code FROM ats_verification_requests WHERE request_id=?",
            (verification["request_id"],),
        ).fetchone()
        assert artifact["state"] == "CONSUMED"
        assert artifact["ciphertext"] is None
        assert artifact["nonce"] is None
        assert artifact["claim_token_hash"] is None
        assert request["state"] == "CONSUMED"
        # V2 does not pass through the V1 historical body store.
        assert connection.execute("SELECT COUNT(*) FROM inbound_mail_messages").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("subject", "body", "expected_kind"),
    [
        (
            "Activate your candidate account",
            "Verify your email using https://wd5.myworkday.com/verify?opaque=abc123",
            "EMAIL_VERIFICATION_LINK",
        ),
        (
            "Reset your password",
            "Reset your password using https://wd5.myworkday.com/reset?opaque=abc123",
            "PASSWORD_RESET_LINK",
        ),
        (
            "Your magic login link",
            "Use this magic login link: https://wd5.myworkday.com/magic?opaque=abc123",
            "MAGIC_LOGIN_LINK",
        ),
    ],
)
def test_candidate_controlled_links_are_one_time_artifacts(
    hunter_db,
    monkeypatch,
    subject: str,
    body: str,
    expected_kind: str,
):
    account = _setup(monkeypatch)
    verification = _verification(account, f"application-{expected_kind.lower()}")
    recipient = str(account["application_email"])

    result = _ingest(
        recipient=recipient,
        sender="no-reply@myworkday.com",
        message_id=f"<{expected_kind.lower()}@example.test>",
        subject=subject,
        body=body,
    )
    assert result["classification"] == expected_kind
    assert result["verification_ready"] is True

    claimed = mail_intelligence_v2.claim_artifact(
        request_id=str(verification["request_id"]),
        application_key=f"application-{expected_kind.lower()}",
    )
    assert claimed["artifact_kind"] == expected_kind
    assert claimed["artifact"].startswith("https://wd5.myworkday.com/")

    mail_intelligence_v2.consume_artifact(
        request_id=str(verification["request_id"]),
        application_key=f"application-{expected_kind.lower()}",
        claim_token=str(claimed["claim_token"]),
    )
    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT ciphertext,nonce,state FROM mail_one_time_artifacts_v2 WHERE verification_request_id=?",
            (verification["request_id"],),
        ).fetchone()
        assert row["state"] == "CONSUMED"
        assert row["ciphertext"] is None
        assert row["nonce"] is None
    finally:
        connection.close()


def test_totp_sms_passkey_and_identity_challenges_remain_security_intervention(
    hunter_db,
    monkeypatch,
):
    account = _setup(monkeypatch)
    verification = _verification(account, "application-security")
    recipient = str(account["application_email"])

    result = _ingest(
        recipient=recipient,
        sender="no-reply@myworkday.com",
        message_id="<security-intervention@example.test>",
        subject="Additional identity verification required",
        body=(
            "Use your authenticator app TOTP code or passkey. If unavailable, "
            "complete identity verification with a selfie."
        ),
        router_class="SECURITY_INTERVENTION",
    )

    assert result["classification"] == "SECURITY_INTERVENTION"
    assert result["verification_ready"] is False
    connection = database.get_connection()
    try:
        request = connection.execute(
            "SELECT state,reason_code FROM ats_verification_requests WHERE request_id=?",
            (verification["request_id"],),
        ).fetchone()
        assert request["state"] == "SECURITY_CHECKPOINT"
        assert request["reason_code"] == "SECURITY_INTERVENTION"
        assert connection.execute(
            "SELECT COUNT(*) FROM mail_one_time_artifacts_v2 WHERE verification_request_id=?",
            (verification["request_id"],),
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_unexpected_verification_host_fails_closed_without_artifact(
    hunter_db,
    monkeypatch,
):
    account = _setup(monkeypatch)
    verification = _verification(account, "application-bad-host")
    recipient = str(account["application_email"])

    result = _ingest(
        recipient=recipient,
        sender="no-reply@myworkday.com",
        message_id="<bad-host@example.test>",
        subject="Verify your account",
        body="Verify your account at https://evil.example/verify?opaque=abc123",
    )
    assert result["verification_ready"] is False
    connection = database.get_connection()
    try:
        request = connection.execute(
            "SELECT state,reason_code FROM ats_verification_requests WHERE request_id=?",
            (verification["request_id"],),
        ).fetchone()
        assert request["state"] == "REJECTED"
        assert request["reason_code"] == "AMBIGUOUS_OR_INVALID_EMAIL_ARTIFACT"
        assert connection.execute(
            "SELECT COUNT(*) FROM mail_one_time_artifacts_v2 WHERE verification_request_id=?",
            (verification["request_id"],),
        ).fetchone()[0] == 0
    finally:
        connection.close()
