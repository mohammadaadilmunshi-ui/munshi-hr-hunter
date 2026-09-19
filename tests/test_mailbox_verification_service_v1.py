from __future__ import annotations

import base64
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app import mailbox_verification_service as service
from app.mailbox_crypto import encrypt_content


def _factory(path):
    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection
    return connect


def _setup(path, monkeypatch, *, body: str, kind: str = "link"):
    monkeypatch.setenv("MUNSHI_VAULT_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())
    monkeypatch.setenv("MUNSHI_APPLICATION_MAIL_RELAY_ENABLED", "true")
    monkeypatch.setenv("MUNSHI_MAIL_SIGNING_SECRET", "s" * 40)
    connect = _factory(path)
    monkeypatch.setattr(service, "get_connection", connect)

    connection = connect()
    connection.executescript(
        """
        CREATE TABLE ats_verification_requests(
          request_id TEXT PRIMARY KEY,tenant_id TEXT,user_id TEXT,account_id TEXT,
          application_key TEXT,provider TEXT,recipient_email TEXT,
          expected_hosts_json TEXT,expected_sender_domains_json TEXT,state TEXT,
          sender_domain TEXT,observed_link_host TEXT,reason_code TEXT,
          created_at TEXT,expires_at TEXT,updated_at TEXT
        );
        CREATE TABLE inbound_mail_messages(
          inbound_id TEXT PRIMARY KEY,identity_id TEXT,tenant_id TEXT,user_id TEXT,
          verification_request_id TEXT,application_key TEXT,correlation_state TEXT,
          message_id TEXT,envelope_from TEXT,envelope_to TEXT,sender_domain TEXT,
          subject TEXT,source_date TEXT,signed_at TEXT,received_at TEXT,
          raw_sha256 TEXT,raw_size INTEGER,headers_json TEXT,text_body TEXT,html_body TEXT,
          attachment_count INTEGER,attachments_json TEXT,router_version TEXT,
          content_ciphertext BLOB,content_nonce BLOB,content_algorithm TEXT
        );
        """
    )
    now = datetime.now(UTC)
    connection.execute(
        """INSERT INTO ats_verification_requests VALUES(
        'req-1','tenant-1','user-1','account-1','application-1','WORKDAY',
        'u_abcdefghijklmnop@mail.munshi.systems',?,?, 'WAITING',
        NULL,NULL,NULL,?,?,?)""",
        (
            json.dumps(["verify.example.com"]),
            json.dumps(["mail.example.com"]),
            now.isoformat(),
            (now + timedelta(minutes=30)).isoformat(),
            now.isoformat(),
        ),
    )
    ciphertext, nonce, algorithm = encrypt_content(
        identity_id="identity-1",
        inbound_id="inbound-1",
        raw_sha256="a" * 64,
        subject="Verification",
        headers=[],
        text_body=body,
        html_body=None,
    )
    connection.execute(
        """INSERT INTO inbound_mail_messages VALUES(
        'inbound-1','identity-1','tenant-1','user-1','req-1','application-1','UNIQUE',
        '<message-1@example>','sender@mail.example.com','u_abcdefghijklmnop@mail.munshi.systems',
        'mail.example.com','[encrypted]',NULL,?,?,?,123,'[]',NULL,NULL,0,'[]','v1',?,?,?)""",
        (
            now.isoformat(),
            now.isoformat(),
            "a" * 64,
            ciphertext,
            nonce,
            algorithm,
        ),
    )
    connection.commit()
    connection.close()
    return connect


def test_link_artifact_is_encrypted_leased_and_consumed_once(tmp_path, monkeypatch):
    url = "https://verify.example.com/activate?token=secret-token-123"
    connect = _setup(
        tmp_path / "mailbox.sqlite",
        monkeypatch,
        body=f"Verify your account: {url}",
    )

    claimed = service.claim_artifact(
        tenant_id="tenant-1",
        user_id="user-1",
        request_id="req-1",
        application_key="application-1",
        account_id="account-1",
        expected_kind="EMAIL_VERIFICATION_LINK",
    )
    assert claimed["artifact"] == url
    assert claimed["inbound_id"] == "inbound-1"
    assert claimed["lease_token"]
    assert claimed["lease_expires_at"]

    connection = connect()
    row = connection.execute(
        "SELECT ciphertext,consumed_at FROM mailbox_verification_artifacts WHERE artifact_id=?",
        (claimed["artifact_id"],),
    ).fetchone()
    assert row is not None
    assert url.encode() not in bytes(row["ciphertext"])
    assert row["consumed_at"] is None
    connection.close()

    consumed = service.consume_artifact(
        tenant_id="tenant-1",
        user_id="user-1",
        artifact_id=claimed["artifact_id"],
        request_id="req-1",
        application_key="application-1",
        account_id="account-1",
        lease_token=claimed["lease_token"],
    )
    assert consumed["state"] == "CONSUMED"

    with pytest.raises(service.MailboxVerificationError):
        service.consume_artifact(
            tenant_id="tenant-1",
            user_id="user-1",
            artifact_id=claimed["artifact_id"],
            request_id="req-1",
            application_key="application-1",
            account_id="account-1",
            lease_token=claimed["lease_token"],
        )


def test_code_artifact_requires_exact_correlated_code(tmp_path, monkeypatch):
    _setup(
        tmp_path / "code.sqlite",
        monkeypatch,
        body="Your verification code is 482913. Enter this code to continue.",
    )
    claimed = service.claim_artifact(
        tenant_id="tenant-1",
        user_id="user-1",
        request_id="req-1",
        application_key="application-1",
        account_id="account-1",
        expected_kind="EMAIL_VERIFICATION_CODE",
    )
    assert claimed["artifact"] == "482913"


def test_mailbox_runtime_is_mandatory_and_fails_closed(monkeypatch):
    monkeypatch.setenv("MUNSHI_VAULT_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())
    monkeypatch.setenv("MUNSHI_MAIL_SIGNING_SECRET", "s" * 40)
    monkeypatch.delenv("MUNSHI_APPLICATION_MAIL_RELAY_ENABLED", raising=False)
    health = service.runtime_health()
    assert health["mandatory"] is True
    assert health["ready"] is False
    assert health["relay_ready"] is False
