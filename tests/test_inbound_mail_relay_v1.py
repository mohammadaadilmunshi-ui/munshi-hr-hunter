from __future__ import annotations

import base64
import hashlib
import hmac
import time
from email import policy
from email.message import EmailMessage

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database, inbound_mail
from app.ats_managed_identity import prepare_managed_account
from app.ats_verification_orchestrator import begin_verification


MAIL_SECRET = "test-mail-signing-secret-" + ("x" * 40)


def _setup_active_identity(monkeypatch):
    monkeypatch.setenv(
        "MUNSHI_VAULT_KEY",
        base64.urlsafe_b64encode(b"i" * 32).decode(),
    )
    monkeypatch.setenv(
        "MUNSHI_APPLICATION_MAIL_DOMAIN",
        "mail.munshi.systems",
    )
    monkeypatch.setenv(
        "MUNSHI_APPLICATION_MAIL_RELAY_ENABLED",
        "1",
    )
    monkeypatch.setenv(
        "MUNSHI_MAIL_SIGNING_SECRET",
        MAIL_SECRET,
    )
    return prepare_managed_account(
        provider="workday",
        account_scope="example.myworkdayjobs.com/example",
        label="Example Workday",
    )


def _client() -> TestClient:
    application = FastAPI()
    application.include_router(inbound_mail.router)
    return TestClient(application)


def _raw_email(
    *,
    recipient: str,
    sender: str,
    message_id: str,
    subject: str = "Verify your application account",
    body: str = "Use the secure verification link in this message.",
    attachment_count: int = 0,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content(body)
    for index in range(attachment_count):
        message.add_attachment(
            f"attachment-{index}".encode("utf-8"),
            maintype="application",
            subtype="octet-stream",
            filename=f"attachment-{index}.bin",
        )
    return message.as_bytes(policy=policy.SMTP)


def _headers(
    *,
    raw: bytes,
    recipient: str,
    sender: str,
    message_id: str,
    timestamp: int | None = None,
    secret: str = MAIL_SECRET,
) -> dict[str, str]:
    timestamp_value = str(
        int(time.time()) if timestamp is None else int(timestamp)
    )
    digest = hashlib.sha256(raw).hexdigest()
    material = inbound_mail.canonical_signature_material(
        timestamp_value=timestamp_value,
        recipient=recipient,
        sender=sender,
        message_id=message_id,
        raw_sha256=digest,
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        material,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "message/rfc822",
        "X-Munshi-Version": "v1",
        "X-Munshi-Timestamp": timestamp_value,
        "X-Munshi-Recipient": recipient,
        "X-Munshi-Sender": sender,
        "X-Munshi-Message-ID": message_id,
        "X-Munshi-Signature": f"v1={signature}",
    }


def _post(
    client: TestClient,
    *,
    raw: bytes,
    recipient: str,
    sender: str,
    message_id: str,
    timestamp: int | None = None,
    secret: str = MAIL_SECRET,
):
    return client.post(
        "/api/mail/inbound",
        content=raw,
        headers=_headers(
            raw=raw,
            recipient=recipient,
            sender=sender,
            message_id=message_id,
            timestamp=timestamp,
            secret=secret,
        ),
    )


def test_signed_known_alias_is_stored_and_uniquely_correlated(
    hunter_db,
    monkeypatch,
):
    account = _setup_active_identity(monkeypatch)
    verification = begin_verification(
        account_id=account["account_id"],
        provider="workday",
        expected_link_hosts=["wd5.myworkday.com"],
        expected_sender_domains=["myworkday.com"],
        application_key="application-mail-1",
    )
    recipient = account["application_email"]
    sender = "no-reply@myworkday.com"
    message_id = "<munshi-mail-1@example.test>"
    raw = _raw_email(
        recipient=recipient,
        sender=sender,
        message_id=message_id,
    )

    response = _post(
        _client(),
        raw=raw,
        recipient=recipient,
        sender=sender,
        message_id=message_id,
    )

    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["duplicate"] is False
    assert result["correlation_state"] == "UNIQUE"
    assert result["application_key"] == "application-mail-1"
    assert result["submission_triggered"] is False

    connection = database.get_connection()
    try:
        stored = connection.execute(
            """
            SELECT *
            FROM inbound_mail_messages
            WHERE inbound_id=?
            """,
            (result["inbound_id"],),
        ).fetchone()
        request = connection.execute(
            """
            SELECT state,application_key
            FROM ats_verification_requests
            WHERE request_id=?
            """,
            (verification["request_id"],),
        ).fetchone()

        assert stored is not None
        assert stored["tenant_id"] == "default"
        assert stored["user_id"] == "local-owner"
        assert stored["envelope_to"] == recipient
        assert stored["message_id"] == message_id
        assert stored["application_key"] == "application-mail-1"
        assert stored["verification_request_id"] == verification["request_id"]
        assert stored["correlation_state"] == "UNIQUE"
        assert stored["subject"] == "[encrypted]"
        assert stored["headers_json"] == "[]"
        assert stored["text_body"] is None
        assert stored["html_body"] is None
        assert stored["content_ciphertext"] is not None
        assert stored["content_nonce"] is not None
        assert stored["content_algorithm"] == "aes-gcm-v1"
        assert b"secure verification link" not in bytes(stored["content_ciphertext"])
        assert stored["attachment_count"] == 0

        assert request is not None
        assert request["state"] == "WAITING"
        assert request["application_key"] == "application-mail-1"
    finally:
        connection.close()


def test_retry_is_idempotent_and_changed_content_with_same_id_is_rejected(
    hunter_db,
    monkeypatch,
):
    account = _setup_active_identity(monkeypatch)
    recipient = account["application_email"]
    sender = "notifications@example.com"
    message_id = "<munshi-dedupe@example.test>"
    first_raw = _raw_email(
        recipient=recipient,
        sender=sender,
        message_id=message_id,
        body="first body",
    )
    client = _client()

    first = _post(
        client,
        raw=first_raw,
        recipient=recipient,
        sender=sender,
        message_id=message_id,
    )
    duplicate = _post(
        client,
        raw=first_raw,
        recipient=recipient,
        sender=sender,
        message_id=message_id,
    )

    changed_raw = _raw_email(
        recipient=recipient,
        sender=sender,
        message_id=message_id,
        body="different body",
    )
    changed = _post(
        client,
        raw=changed_raw,
        recipient=recipient,
        sender=sender,
        message_id=message_id,
    )

    assert first.status_code == 200
    assert first.json()["duplicate"] is False
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["inbound_id"] == first.json()["inbound_id"]
    assert changed.status_code == 409

    connection = database.get_connection()
    try:
        count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM inbound_mail_messages
            WHERE message_id=?
            """,
            (message_id,),
        ).fetchone()["count"]
        assert count == 1
    finally:
        connection.close()


def test_bad_signature_stale_timestamp_and_unknown_alias_fail_closed(
    hunter_db,
    monkeypatch,
):
    account = _setup_active_identity(monkeypatch)
    recipient = account["application_email"]
    sender = "security@example.com"
    message_id = "<munshi-auth@example.test>"
    raw = _raw_email(
        recipient=recipient,
        sender=sender,
        message_id=message_id,
    )
    client = _client()

    bad_signature = _post(
        client,
        raw=raw,
        recipient=recipient,
        sender=sender,
        message_id=message_id,
        secret="different-secret-" + ("y" * 40),
    )
    stale = _post(
        client,
        raw=raw,
        recipient=recipient,
        sender=sender,
        message_id=message_id,
        timestamp=int(time.time()) - inbound_mail.TIMESTAMP_SKEW_SECONDS - 10,
    )

    unknown_recipient = "u_unknown@mail.munshi.systems"
    unknown_raw = _raw_email(
        recipient=unknown_recipient,
        sender=sender,
        message_id="<munshi-unknown@example.test>",
    )
    unknown = _post(
        client,
        raw=unknown_raw,
        recipient=unknown_recipient,
        sender=sender,
        message_id="<munshi-unknown@example.test>",
    )

    assert bad_signature.status_code == 401
    assert stale.status_code == 401
    assert unknown.status_code == 404

    connection = database.get_connection()
    try:
        table = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table' AND name='inbound_mail_messages'
            """
        ).fetchone()
        if table is not None:
            assert connection.execute(
                "SELECT COUNT(*) AS count FROM inbound_mail_messages"
            ).fetchone()["count"] == 0
    finally:
        connection.close()


def test_ambiguous_verification_never_guesses_application(
    hunter_db,
    monkeypatch,
):
    account = _setup_active_identity(monkeypatch)
    recipient = account["application_email"]

    first = begin_verification(
        account_id=account["account_id"],
        provider="workday",
        expected_link_hosts=["wd5.myworkday.com"],
        expected_sender_domains=["myworkday.com"],
        application_key="application-a",
    )
    second = begin_verification(
        account_id=account["account_id"],
        provider="workday",
        expected_link_hosts=["wd5.myworkday.com"],
        expected_sender_domains=["myworkday.com"],
        application_key="application-b",
    )
    sender = "no-reply@myworkday.com"
    message_id = "<munshi-ambiguous@example.test>"
    raw = _raw_email(
        recipient=recipient,
        sender=sender,
        message_id=message_id,
    )

    response = _post(
        _client(),
        raw=raw,
        recipient=recipient,
        sender=sender,
        message_id=message_id,
    )

    assert response.status_code == 200
    result = response.json()
    assert result["correlation_state"] == "AMBIGUOUS"
    assert result["application_key"] is None
    assert result["submission_triggered"] is False

    connection = database.get_connection()
    try:
        stored = connection.execute(
            """
            SELECT application_key,verification_request_id
            FROM inbound_mail_messages
            WHERE inbound_id=?
            """,
            (result["inbound_id"],),
        ).fetchone()
        states = connection.execute(
            """
            SELECT request_id,state
            FROM ats_verification_requests
            WHERE request_id IN (?,?)
            ORDER BY request_id
            """,
            (first["request_id"], second["request_id"]),
        ).fetchall()

        assert stored["application_key"] is None
        assert stored["verification_request_id"] is None
        assert len(states) == 2
        assert {row["state"] for row in states} == {"WAITING"}
    finally:
        connection.close()


def test_attachment_and_raw_size_limits_are_enforced(
    hunter_db,
    monkeypatch,
):
    account = _setup_active_identity(monkeypatch)
    recipient = account["application_email"]
    sender = "attachments@example.com"
    client = _client()

    monkeypatch.setattr(inbound_mail, "MAX_ATTACHMENTS", 1)
    raw = _raw_email(
        recipient=recipient,
        sender=sender,
        message_id="<munshi-attachments@example.test>",
        attachment_count=2,
    )
    too_many = _post(
        client,
        raw=raw,
        recipient=recipient,
        sender=sender,
        message_id="<munshi-attachments@example.test>",
    )
    assert too_many.status_code == 413

    monkeypatch.setattr(inbound_mail, "MAX_RAW_BYTES", 128)
    oversized_raw = _raw_email(
        recipient=recipient,
        sender=sender,
        message_id="<munshi-oversized@example.test>",
        body="z" * 1024,
    )
    oversized = _post(
        client,
        raw=oversized_raw,
        recipient=recipient,
        sender=sender,
        message_id="<munshi-oversized@example.test>",
    )
    assert oversized.status_code == 413


def test_signed_message_id_must_match_rfc822_message_id(
    hunter_db,
    monkeypatch,
):
    account = _setup_active_identity(monkeypatch)
    recipient = account["application_email"]
    sender = "security@example.com"
    raw = _raw_email(
        recipient=recipient,
        sender=sender,
        message_id="<actual@example.test>",
    )

    response = _post(
        _client(),
        raw=raw,
        recipient=recipient,
        sender=sender,
        message_id="<signed-but-different@example.test>",
    )

    assert response.status_code == 400


def test_inbound_route_is_registered_through_existing_proxy_router():
    from app.hr_agent_proxy import router as application_router

    paths = {
        getattr(route, "path", None)
        for route in application_router.routes
    }
    assert "/api/mail/inbound" in paths


def test_main_api_mounts_signed_inbound_mail_router(monkeypatch):
    monkeypatch.setenv("HUNTER_API_SECRET", "test-hunter-api-secret")
    from app.api import app

    routes = {(route.path, ",".join(sorted(route.methods or []))) for route in app.routes}
    assert any(path == "/api/mail/inbound" and "POST" in methods for path, methods in routes)
