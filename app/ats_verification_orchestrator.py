"""ATS verification-mail orchestration without inbound-mail/network authority.

This module models the safe state machine needed by a future MUNSHI mail relay.
It intentionally stores no email body, OTP, verification token, or single-use URL.
A relay may submit *metadata* about a received message; ordinary expected link
verification can be marked ready for a secure resolver, while OTP/MFA/security
messages are forced into a user checkpoint.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import uuid4

from app.ats_managed_identity import ensure_application_identity
from app.tenant_foundation import current_owner


class VerificationOrchestrationError(RuntimeError):
    pass


def _host(value: str) -> str:
    candidate = str(value or "").strip().lower()
    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    if not parsed.hostname:
        raise VerificationOrchestrationError("Verification host is invalid.")
    return parsed.hostname.lower()


def _domain_matches(observed: str, expected_parent: str) -> bool:
    """Match an exact trusted domain or one of its true subdomains."""
    try:
        observed_domain = _host(observed)
        expected_domain = _host(expected_parent)
    except VerificationOrchestrationError:
        return False
    return (
        observed_domain == expected_domain
        or observed_domain.endswith("." + expected_domain)
    )


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS ats_verification_requests(
          request_id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          account_id TEXT NOT NULL,
          application_key TEXT,
          provider TEXT NOT NULL,
          recipient_email TEXT NOT NULL,
          expected_hosts_json TEXT NOT NULL,
          expected_sender_domains_json TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN (
            'WAITING','LINK_READY','SECURITY_CHECKPOINT','CONSUMED','EXPIRED','REJECTED'
          )),
          sender_domain TEXT,
          observed_link_host TEXT,
          reason_code TEXT,
          created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(account_id) REFERENCES ats_credential_accounts(account_id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_ats_verification_owner_state
        ON ats_verification_requests(tenant_id,user_id,state,expires_at);

        CREATE INDEX IF NOT EXISTS idx_ats_verification_recipient
        ON ats_verification_requests(recipient_email,state,created_at DESC);
        """
    )


def begin_verification(
    *,
    account_id: str,
    provider: str,
    expected_link_hosts: Iterable[str],
    expected_sender_domains: Iterable[str],
    application_key: str | None = None,
    ttl_minutes: int = 30,
) -> dict[str, Any]:
    from app.database import get_connection
    import json

    if not 5 <= int(ttl_minutes) <= 1440:
        raise VerificationOrchestrationError("Verification TTL must be between 5 and 1440 minutes.")
    hosts = sorted({_host(value) for value in expected_link_hosts})
    senders = sorted({_host(value) for value in expected_sender_domains})
    if not hosts or not senders:
        raise VerificationOrchestrationError(
            "Expected verification hosts and sender domains are required."
        )
    owner = current_owner()
    identity = ensure_application_identity()
    now = datetime.now(UTC)
    request_id = str(uuid4())
    connection = get_connection()
    try:
        ensure_schema(connection)
        owned = connection.execute(
            """
            SELECT 1 FROM ats_credential_accounts
            WHERE account_id=? AND tenant_id=? AND user_id=?
            """,
            (account_id, owner.tenant_id, owner.user_id),
        ).fetchone()
        if owned is None:
            raise LookupError("ATS credential account is unavailable.")
        connection.execute(
            """
            INSERT INTO ats_verification_requests(
              request_id,tenant_id,user_id,account_id,application_key,provider,
              recipient_email,expected_hosts_json,expected_sender_domains_json,
              state,created_at,expires_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?, 'WAITING',?,?,?)
            """,
            (
                request_id,
                owner.tenant_id,
                owner.user_id,
                account_id,
                str(application_key).strip() if application_key else None,
                str(provider or "").strip().upper(),
                identity["email_address"],
                json.dumps(hosts, separators=(",", ":")),
                json.dumps(senders, separators=(",", ":")),
                now.isoformat(),
                (now + timedelta(minutes=int(ttl_minutes))).isoformat(),
                now.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "request_id": request_id,
        "recipient_email": identity["email_address"],
        "provider": str(provider or "").strip().upper(),
        "state": "WAITING",
        "expected_link_hosts": hosts,
        "expected_sender_domains": senders,
        "mail_relay_ready": identity["relay_ready"],
    }


def observe_message_metadata(
    *,
    request_id: str,
    recipient_email: str,
    sender_domain: str,
    observed_link_host: str | None,
    contains_security_code: bool = False,
) -> dict[str, Any]:
    """Classify relay metadata; never accepts/stores body, OTP, or verification URL."""
    from app.database import get_connection
    import json

    owner = current_owner()
    now = datetime.now(UTC)
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM ats_verification_requests
            WHERE request_id=? AND tenant_id=? AND user_id=?
            """,
            (request_id, owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Verification request is unavailable.")
        if row["state"] != "WAITING":
            raise VerificationOrchestrationError("Verification request is no longer waiting.")
        if datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00")) <= now:
            connection.execute(
                "UPDATE ats_verification_requests SET state='EXPIRED',reason_code='TTL_EXPIRED',updated_at=? WHERE request_id=?",
                (now.isoformat(), request_id),
            )
            connection.commit()
            return {"request_id": request_id, "state": "EXPIRED", "auto_follow_allowed": False}
        if str(recipient_email or "").strip().lower() != str(row["recipient_email"]).lower():
            raise VerificationOrchestrationError("Verification recipient does not match the managed identity.")
        sender = _host(sender_domain)
        expected_senders = set(json.loads(row["expected_sender_domains_json"]))
        if not any(
            _domain_matches(sender, expected_domain)
            for expected_domain in expected_senders
        ):
            next_state = "REJECTED"
            reason = "UNEXPECTED_SENDER_DOMAIN"
            link_host = None
        elif contains_security_code:
            next_state = "SECURITY_CHECKPOINT"
            reason = "OTP_OR_SECURITY_CODE"
            link_host = None
        else:
            expected = set(json.loads(row["expected_hosts_json"]))
            link_host = _host(observed_link_host or "") if observed_link_host else None
            if link_host is None or not any(
                _domain_matches(link_host, expected_domain)
                for expected_domain in expected
            ):
                next_state = "REJECTED"
                reason = "UNEXPECTED_VERIFICATION_HOST"
            else:
                next_state = "LINK_READY"
                reason = "EXPECTED_ORDINARY_EMAIL_VERIFICATION"
        connection.execute(
            """
            UPDATE ats_verification_requests
            SET state=?,sender_domain=?,observed_link_host=?,reason_code=?,updated_at=?
            WHERE request_id=?
            """,
            (next_state, sender, link_host, reason, now.isoformat(), request_id),
        )
        connection.commit()
        return {
            "request_id": request_id,
            "state": next_state,
            "reason_code": reason,
            "auto_follow_allowed": next_state == "LINK_READY",
            "security_checkpoint": next_state == "SECURITY_CHECKPOINT",
            "verification_url_stored": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def mark_consumed(*, request_id: str) -> dict[str, Any]:
    from app.database import get_connection

    owner = current_owner()
    now = datetime.now(UTC).isoformat()
    connection = get_connection()
    try:
        ensure_schema(connection)
        changed = connection.execute(
            """
            UPDATE ats_verification_requests
            SET state='CONSUMED',reason_code='VERIFICATION_COMPLETED',updated_at=?
            WHERE request_id=? AND tenant_id=? AND user_id=? AND state='LINK_READY'
            """,
            (now, request_id, owner.tenant_id, owner.user_id),
        ).rowcount
        connection.commit()
        if changed != 1:
            raise VerificationOrchestrationError("Verification request is not ready to consume.")
    finally:
        connection.close()
    return {"request_id": request_id, "state": "CONSUMED"}
