"""Mandatory Hunter-owned mailbox verification artifact service.

Hunter is the authority for mailbox identity, correlation, sender/application
binding, deduplication, TTL, encrypted verification material, single-use
leases, and audit. Apply receives only a short-lived artifact after an exact
plan/account/application request and must report consumption after Chromium
confirms the verification outcome.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from app.database import get_connection
from app.mailbox_crypto import decrypt_content, encrypt_content, runtime_ready as crypto_ready

ARTIFACT_TTL_SECONDS = 120
_ALLOWED_KINDS = {
    "EMAIL_VERIFICATION_CODE",
    "EMAIL_VERIFICATION_LINK",
    "PASSWORD_RESET_LINK",
    "MAGIC_LOGIN_LINK",
}
_LINK_KINDS = {
    "EMAIL_VERIFICATION_LINK",
    "PASSWORD_RESET_LINK",
    "MAGIC_LOGIN_LINK",
}
_URL_RE = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
_CODE_PATTERNS = (
    re.compile(
        r"(?i)(?:verification\s+code|security\s+code|email\s+code|passcode|one[- ]time\s+(?:code|password)|otp)"
        r"(?:\s+is)?\s*[:#-]?\s*([A-Z0-9](?:[A-Z0-9-]{2,14}[A-Z0-9]))"
    ),
    re.compile(
        r"(?i)(?:enter|use|code\s+is)[^A-Z0-9]{0,20}([0-9]{4,10})(?![0-9])"
    ),
)


class MailboxVerificationError(RuntimeError):
    pass


def _truthy(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def runtime_health() -> dict[str, Any]:
    relay = _truthy(os.getenv("MUNSHI_APPLICATION_MAIL_RELAY_ENABLED"))
    signing = len(str(os.getenv("MUNSHI_MAIL_SIGNING_SECRET") or "").encode()) >= 32
    encryption = crypto_ready()
    ready = relay and signing and encryption
    return {
        "ready": ready,
        "relay_ready": relay,
        "signing_ready": signing,
        "encryption_ready": encryption,
        "mandatory": True,
    }


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS mailbox_verification_artifacts(
          artifact_id TEXT PRIMARY KEY,
          request_id TEXT NOT NULL,
          inbound_id TEXT NOT NULL,
          tenant_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          account_id TEXT NOT NULL,
          application_key TEXT NOT NULL,
          provider TEXT NOT NULL,
          artifact_kind TEXT NOT NULL,
          artifact_digest TEXT NOT NULL,
          ciphertext BLOB NOT NULL,
          nonce BLOB NOT NULL,
          algorithm TEXT NOT NULL,
          created_at TEXT NOT NULL,
          source_expires_at TEXT NOT NULL,
          lease_token_hash TEXT,
          lease_expires_at TEXT,
          consumed_at TEXT,
          updated_at TEXT NOT NULL,
          UNIQUE(request_id,inbound_id,artifact_kind,artifact_digest),
          FOREIGN KEY(request_id) REFERENCES ats_verification_requests(request_id) ON DELETE CASCADE,
          FOREIGN KEY(inbound_id) REFERENCES inbound_mail_messages(inbound_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_mailbox_artifact_owner_request
        ON mailbox_verification_artifacts(tenant_id,user_id,request_id,created_at DESC);

        CREATE TABLE IF NOT EXISTS mailbox_verification_audit(
          audit_id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          request_id TEXT,
          inbound_id TEXT,
          artifact_id TEXT,
          application_key TEXT,
          account_id TEXT,
          event_type TEXT NOT NULL,
          reason_code TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_mailbox_audit_request
        ON mailbox_verification_audit(tenant_id,user_id,request_id,created_at DESC);
        """
    )


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current if current.tzinfo else current.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _audit(
    connection: sqlite3.Connection,
    *,
    tenant_id: str,
    user_id: str,
    event_type: str,
    request_id: str | None = None,
    inbound_id: str | None = None,
    artifact_id: str | None = None,
    application_key: str | None = None,
    account_id: str | None = None,
    reason_code: str | None = None,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    safe = dict(metadata or {})
    encoded = json.dumps(safe, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > 8192:
        raise MailboxVerificationError("Mailbox audit metadata is too large.")
    connection.execute(
        """
        INSERT INTO mailbox_verification_audit(
          audit_id,tenant_id,user_id,request_id,inbound_id,artifact_id,
          application_key,account_id,event_type,reason_code,metadata_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            str(uuid4()), tenant_id, user_id, request_id, inbound_id, artifact_id,
            application_key, account_id, event_type, reason_code, encoded, _iso(_now(now)),
        ),
    )


def _host(value: str) -> str:
    candidate = str(value or "").strip().casefold().rstrip(".")
    if "://" in candidate:
        candidate = (urlsplit(candidate).hostname or "").casefold().rstrip(".")
    if not candidate or "/" in candidate or "@" in candidate:
        raise MailboxVerificationError("Mailbox verification host is invalid.")
    return candidate


def _request(
    connection: sqlite3.Connection,
    *,
    request_id: str,
    tenant_id: str,
    user_id: str,
    application_key: str,
    account_id: str,
    now: datetime,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT * FROM ats_verification_requests
        WHERE request_id=? AND tenant_id=? AND user_id=?
          AND application_key=? AND account_id=?
        """,
        (request_id, tenant_id, user_id, application_key, account_id),
    ).fetchone()
    if row is None:
        raise LookupError("Mailbox verification request is unavailable.")
    if str(row["state"]) != "WAITING":
        raise MailboxVerificationError("Mailbox verification request is not waiting.")
    expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= now:
        connection.execute(
            "UPDATE ats_verification_requests SET state='EXPIRED',reason_code='TTL_EXPIRED',updated_at=? WHERE request_id=?",
            (_iso(now), request_id),
        )
        _audit(
            connection, tenant_id=tenant_id, user_id=user_id, request_id=request_id,
            application_key=application_key, account_id=account_id,
            event_type="REQUEST_EXPIRED", reason_code="TTL_EXPIRED", now=now,
        )
        raise MailboxVerificationError("Mailbox verification request expired.")
    return row


def _message(
    connection: sqlite3.Connection,
    *,
    request_id: str,
    tenant_id: str,
    user_id: str,
) -> sqlite3.Row:
    rows = connection.execute(
        """
        SELECT * FROM inbound_mail_messages
        WHERE tenant_id=? AND user_id=? AND verification_request_id=?
          AND correlation_state='UNIQUE'
        ORDER BY received_at DESC,inbound_id DESC
        LIMIT 2
        """,
        (tenant_id, user_id, request_id),
    ).fetchall()
    if not rows:
        raise LookupError("No correlated mailbox verification message is available.")
    if len(rows) > 1 and str(rows[0]["received_at"]) == str(rows[1]["received_at"]):
        raise MailboxVerificationError("Mailbox verification message correlation is ambiguous.")
    return rows[0]


def _content(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    if {"content_ciphertext", "content_nonce", "content_algorithm"} <= keys and row["content_ciphertext"]:
        return decrypt_content(
            identity_id=str(row["identity_id"]),
            inbound_id=str(row["inbound_id"]),
            raw_sha256=str(row["raw_sha256"]),
            ciphertext=bytes(row["content_ciphertext"]),
            nonce=bytes(row["content_nonce"]),
            algorithm=str(row["content_algorithm"]),
        )
    # Legacy read-only compatibility. New relay writes are encrypted.
    return {
        "subject": str(row["subject"] or ""),
        "headers": json.loads(str(row["headers_json"] or "[]")),
        "text_body": row["text_body"],
        "html_body": row["html_body"],
    }


def _extract_link(content: dict[str, Any], allowed_hosts: set[str]) -> str:
    combined = "\n".join(
        str(content.get(key) or "") for key in ("text_body", "html_body")
    )
    matches: list[str] = []
    for raw in _URL_RE.findall(combined):
        candidate = raw.rstrip(").,;!]'\"")
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme.casefold() != "https" or not host:
            continue
        if host in allowed_hosts:
            matches.append(candidate)
    unique = list(dict.fromkeys(matches))
    if len(unique) != 1:
        raise MailboxVerificationError(
            "Expected exactly one trusted verification link in the correlated message."
        )
    return unique[0]


def _extract_code(content: dict[str, Any]) -> str:
    combined = "\n".join(
        str(content.get(key) or "") for key in ("subject", "text_body", "html_body")
    )
    found: list[str] = []
    for pattern in _CODE_PATTERNS:
        for match in pattern.findall(combined):
            code = re.sub(r"[\s-]+", "", str(match)).upper()
            if 4 <= len(code) <= 10:
                found.append(code)
    unique = list(dict.fromkeys(found))
    if len(unique) != 1:
        raise MailboxVerificationError(
            "Expected exactly one verification code in the correlated message."
        )
    return unique[0]


def _artifact_aad(row: sqlite3.Row) -> bytes:
    return json.dumps(
        {
            "v": 1,
            "artifact_id": str(row["artifact_id"]),
            "request_id": str(row["request_id"]),
            "inbound_id": str(row["inbound_id"]),
            "tenant_id": str(row["tenant_id"]),
            "user_id": str(row["user_id"]),
            "account_id": str(row["account_id"]),
            "application_key": str(row["application_key"]),
            "kind": str(row["artifact_kind"]),
            "digest": str(row["artifact_digest"]),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _artifact_key() -> bytes:
    # mailbox_crypto derives its own domain-separated key from MUNSHI_VAULT_KEY.
    # Derive a second independent subkey for one-time artifacts.
    from app.mailbox_crypto import _key as mailbox_content_key
    return hmac.new(mailbox_content_key(), b"munshi-mailbox-artifact-v1", hashlib.sha256).digest()


def _encrypt_artifact(metadata: dict[str, str], artifact: str) -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    aad = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    return AESGCM(_artifact_key()).encrypt(nonce, artifact.encode(), aad), nonce


def _decrypt_artifact(row: sqlite3.Row) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    metadata = {
        "v": "1",
        "artifact_id": str(row["artifact_id"]),
        "request_id": str(row["request_id"]),
        "inbound_id": str(row["inbound_id"]),
        "tenant_id": str(row["tenant_id"]),
        "user_id": str(row["user_id"]),
        "account_id": str(row["account_id"]),
        "application_key": str(row["application_key"]),
        "kind": str(row["artifact_kind"]),
        "digest": str(row["artifact_digest"]),
    }
    try:
        raw = AESGCM(_artifact_key()).decrypt(
            bytes(row["nonce"]),
            bytes(row["ciphertext"]),
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode(),
        )
        return raw.decode()
    except Exception as error:
        raise MailboxVerificationError("Mailbox verification artifact could not be decrypted.") from error


def claim_artifact(
    *,
    tenant_id: str,
    user_id: str,
    request_id: str,
    application_key: str,
    account_id: str,
    expected_kind: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    health = runtime_health()
    if health["ready"] is not True:
        raise MailboxVerificationError("Mandatory mailbox runtime is unavailable.")
    kind = str(expected_kind or "").strip().upper()
    if kind not in _ALLOWED_KINDS:
        raise MailboxVerificationError("Mailbox verification artifact kind is unsupported.")
    current = _now(now)
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        request = _request(
            connection, request_id=request_id, tenant_id=tenant_id, user_id=user_id,
            application_key=application_key, account_id=account_id, now=current,
        )
        message = _message(
            connection, request_id=request_id, tenant_id=tenant_id, user_id=user_id
        )
        sender = str(message["sender_domain"] or "").casefold().rstrip(".")
        allowed_senders = {
            _host(v) for v in json.loads(str(request["expected_sender_domains_json"]))
        }
        if sender not in allowed_senders:
            _audit(
                connection, tenant_id=tenant_id, user_id=user_id, request_id=request_id,
                inbound_id=str(message["inbound_id"]), application_key=application_key,
                account_id=account_id, event_type="ARTIFACT_REJECTED",
                reason_code="UNEXPECTED_SENDER_DOMAIN", now=current,
            )
            raise MailboxVerificationError("Mailbox verification sender is not trusted.")

        content = _content(message)
        if kind in _LINK_KINDS:
            allowed_hosts = {
                _host(v) for v in json.loads(str(request["expected_hosts_json"]))
            }
            artifact = _extract_link(content, allowed_hosts)
        else:
            artifact = _extract_code(content)
        digest = hashlib.sha256(artifact.encode()).hexdigest()
        artifact_id = str(uuid4())
        metadata = {
            "v": "1", "artifact_id": artifact_id, "request_id": request_id,
            "inbound_id": str(message["inbound_id"]), "tenant_id": tenant_id,
            "user_id": user_id, "account_id": account_id,
            "application_key": application_key, "kind": kind, "digest": digest,
        }
        ciphertext, nonce = _encrypt_artifact(metadata, artifact)
        source_expiry = str(request["expires_at"])
        connection.execute(
            """
            INSERT INTO mailbox_verification_artifacts(
              artifact_id,request_id,inbound_id,tenant_id,user_id,account_id,
              application_key,provider,artifact_kind,artifact_digest,ciphertext,
              nonce,algorithm,created_at,source_expires_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(request_id,inbound_id,artifact_kind,artifact_digest) DO NOTHING
            """,
            (
                artifact_id, request_id, message["inbound_id"], tenant_id, user_id,
                account_id, application_key, request["provider"], kind, digest,
                ciphertext, nonce, "aes-gcm-v1", _iso(current), source_expiry, _iso(current),
            ),
        )
        row = connection.execute(
            """
            SELECT * FROM mailbox_verification_artifacts
            WHERE request_id=? AND inbound_id=? AND artifact_kind=? AND artifact_digest=?
            """,
            (request_id, message["inbound_id"], kind, digest),
        ).fetchone()
        if row is None:
            raise MailboxVerificationError("Mailbox verification artifact was not persisted.")
        if row["consumed_at"] is not None:
            raise MailboxVerificationError("Mailbox verification artifact was already consumed.")

        lease_token = secrets.token_urlsafe(32)
        lease_hash = hashlib.sha256(lease_token.encode()).hexdigest()
        lease_expires = current + timedelta(seconds=ARTIFACT_TTL_SECONDS)
        connection.execute(
            """
            UPDATE mailbox_verification_artifacts
            SET lease_token_hash=?,lease_expires_at=?,updated_at=?
            WHERE artifact_id=? AND consumed_at IS NULL
            """,
            (lease_hash, _iso(lease_expires), _iso(current), row["artifact_id"]),
        )
        _audit(
            connection, tenant_id=tenant_id, user_id=user_id, request_id=request_id,
            inbound_id=str(message["inbound_id"]), artifact_id=str(row["artifact_id"]),
            application_key=application_key, account_id=account_id,
            event_type="ARTIFACT_LEASED", metadata={"kind": kind, "ttl_seconds": ARTIFACT_TTL_SECONDS},
            now=current,
        )
        artifact_value = _decrypt_artifact(row)
        connection.commit()
        return {
            "artifact_id": str(row["artifact_id"]),
            "request_id": request_id,
            "inbound_id": str(message["inbound_id"]),
            "artifact_kind": kind,
            "artifact_digest": digest,
            "artifact": artifact_value,
            "lease_token": lease_token,
            "lease_expires_at": _iso(lease_expires),
            "application_key": application_key,
            "account_id": account_id,
            "provider": str(request["provider"]),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def consume_artifact(
    *,
    tenant_id: str,
    user_id: str,
    artifact_id: str,
    request_id: str,
    application_key: str,
    account_id: str,
    lease_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _now(now)
    token_hash = hashlib.sha256(str(lease_token or "").encode()).hexdigest()
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM mailbox_verification_artifacts
            WHERE artifact_id=? AND request_id=? AND tenant_id=? AND user_id=?
              AND application_key=? AND account_id=?
            """,
            (artifact_id, request_id, tenant_id, user_id, application_key, account_id),
        ).fetchone()
        if row is None:
            raise LookupError("Mailbox verification artifact is unavailable.")
        if row["consumed_at"] is not None:
            raise MailboxVerificationError("Mailbox verification artifact was already consumed.")
        expires = datetime.fromisoformat(str(row["lease_expires_at"]).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= current or not hmac.compare_digest(
            str(row["lease_token_hash"] or ""), token_hash
        ):
            _audit(
                connection, tenant_id=tenant_id, user_id=user_id, request_id=request_id,
                artifact_id=artifact_id, application_key=application_key, account_id=account_id,
                event_type="ARTIFACT_CONSUME_REJECTED", reason_code="LEASE_INVALID_OR_EXPIRED",
                now=current,
            )
            raise MailboxVerificationError("Mailbox verification artifact lease is invalid or expired.")
        connection.execute(
            """
            UPDATE mailbox_verification_artifacts
            SET consumed_at=?,lease_token_hash=NULL,lease_expires_at=NULL,updated_at=?
            WHERE artifact_id=? AND consumed_at IS NULL
            """,
            (_iso(current), _iso(current), artifact_id),
        )
        connection.execute(
            """
            UPDATE ats_verification_requests
            SET state='CONSUMED',reason_code='VERIFICATION_COMPLETED',updated_at=?
            WHERE request_id=? AND state='WAITING'
            """,
            (_iso(current), request_id),
        )
        _audit(
            connection, tenant_id=tenant_id, user_id=user_id, request_id=request_id,
            artifact_id=artifact_id, application_key=application_key, account_id=account_id,
            event_type="ARTIFACT_CONSUMED", metadata={"kind": str(row["artifact_kind"])},
            now=current,
        )
        connection.commit()
        return {"artifact_id": artifact_id, "request_id": request_id, "state": "CONSUMED"}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def cancel_request(
    *,
    tenant_id: str,
    user_id: str,
    request_id: str,
    application_key: str,
    account_id: str,
    reason_code: str = "VERIFICATION_NOT_REQUIRED",
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _now(now)
    reason = str(reason_code or "").strip().upper()
    if reason not in {"VERIFICATION_NOT_REQUIRED", "ACCOUNT_FLOW_ABORTED"}:
        raise MailboxVerificationError("Mailbox cancellation reason is invalid.")
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT state FROM ats_verification_requests
            WHERE request_id=? AND tenant_id=? AND user_id=?
              AND application_key=? AND account_id=?
            """,
            (request_id, tenant_id, user_id, application_key, account_id),
        ).fetchone()
        if row is None:
            raise LookupError("Mailbox verification request is unavailable.")
        state = str(row["state"])
        if state == "WAITING":
            connection.execute(
                """
                UPDATE ats_verification_requests
                SET state='REJECTED',reason_code=?,updated_at=?
                WHERE request_id=? AND state='WAITING'
                """,
                (reason, _iso(current), request_id),
            )
            _audit(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                request_id=request_id,
                application_key=application_key,
                account_id=account_id,
                event_type="REQUEST_CANCELLED",
                reason_code=reason,
                now=current,
            )
        connection.commit()
        return {"request_id": request_id, "state": "REJECTED" if state == "WAITING" else state}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
