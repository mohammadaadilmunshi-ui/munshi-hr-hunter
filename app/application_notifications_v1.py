"""Durable, user-controlled application notifications and evidence projection.

This module deliberately has no application submission authority. It projects only
from Hunter's verified production receipt and independently-correlated employer-mail
evidence ledgers. SMTP delivery is default-off and notification payloads never contain
verification links, OTPs, passwords, credential references, or submit authority.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.database import get_connection
from app.integration_credentials_v1 import smtp2go_configured, smtp2go_credentials
from app.notification_email_design_v1 import notification_email
from app.tenant_foundation import OwnerContext, current_owner, ensure_schema as ensure_owner_schema

SMTP2GO_API_URL = "https://api.smtp2go.com/v3/email/send"
LEASE_SECONDS = 120
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_ALLOWED_EVENTS = {"SUBMISSION_VERIFIED", "EMPLOYER_EMAIL_CONFIRMED"}
_SECRET_MARKERS = (
    "verification code",
    "otp",
    "password",
    "credref:",
    "authorization_id",
    "claim_digest",
    "authority_digest",
    "token=",
)


class NotificationError(RuntimeError):
    """Safe notification preference/outbox failure."""


class NotificationDeliveryError(RuntimeError):
    """Delivery failed after the outbox item entered the irreversible boundary."""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _enabled(name: str) -> bool:
    return str(os.getenv(name) or "").strip().casefold() in {"1", "true", "yes", "on"}


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _clean_email(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if len(text) > 320 or not _EMAIL.fullmatch(text):
        raise ValueError("Notification email address is invalid.")
    return text


def _safe_copy(value: object, *, maximum: int) -> str:
    text = " ".join(str(value or "").split())[:maximum]
    lowered = text.casefold()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise NotificationError("Notification payload contains prohibited secret material.")
    return text


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_owner_schema(connection)
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS application_notification_preferences_v1(
              tenant_id TEXT NOT NULL,
              user_id TEXT NOT NULL,
              destination_email TEXT,
              submission_email_enabled INTEGER NOT NULL DEFAULT 0 CHECK(submission_email_enabled IN (0,1)),
              employer_confirmation_email_enabled INTEGER NOT NULL DEFAULT 0 CHECK(employer_confirmation_email_enabled IN (0,1)),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(tenant_id,user_id),
              FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS application_notification_outbox_v1(
              notification_id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              user_id TEXT NOT NULL,
              event_kind TEXT NOT NULL CHECK(event_kind IN ('SUBMISSION_VERIFIED','EMPLOYER_EMAIL_CONFIRMED')),
              source_id TEXT NOT NULL,
              application_id TEXT NOT NULL,
              job_id INTEGER NOT NULL,
              destination_email TEXT NOT NULL,
              subject TEXT NOT NULL,
              body_text TEXT NOT NULL,
              delivery_state TEXT NOT NULL DEFAULT 'PENDING'
                CHECK(delivery_state IN ('PENDING','CLAIMED','SENT','AMBIGUOUS','CANCELLED')),
              attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
              claim_token_hash TEXT,
              lease_expires_at TEXT,
              provider_message_id TEXT,
              last_error_class TEXT,
              last_error_text TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              sent_at TEXT,
              UNIQUE(tenant_id,user_id,event_kind,source_id),
              FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE CASCADE,
              FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_application_notification_outbox_delivery_v1
              ON application_notification_outbox_v1(delivery_state,created_at);
            CREATE INDEX IF NOT EXISTS idx_application_notification_outbox_owner_v1
              ON application_notification_outbox_v1(tenant_id,user_id,created_at DESC);
            """
        )
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def get_preference(*, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM application_notification_preferences_v1
               WHERE tenant_id=? AND user_id=?""",
            (owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            return {
                "destination_email": None,
                "submission_email_enabled": False,
                "employer_confirmation_email_enabled": False,
            }
        return {
            "destination_email": row["destination_email"],
            "submission_email_enabled": bool(row["submission_email_enabled"]),
            "employer_confirmation_email_enabled": bool(row["employer_confirmation_email_enabled"]),
        }
    finally:
        if owns:
            connection.close()


def save_preference(
    *,
    destination_email: object,
    submission_email_enabled: bool,
    employer_confirmation_email_enabled: bool,
) -> dict[str, Any]:
    email = _clean_email(destination_email)
    if (submission_email_enabled or employer_confirmation_email_enabled) and email is None:
        raise ValueError("An email address is required when notifications are enabled.")
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO application_notification_preferences_v1(
              tenant_id,user_id,destination_email,submission_email_enabled,
              employer_confirmation_email_enabled,updated_at
            ) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(tenant_id,user_id) DO UPDATE SET
              destination_email=excluded.destination_email,
              submission_email_enabled=excluded.submission_email_enabled,
              employer_confirmation_email_enabled=excluded.employer_confirmation_email_enabled,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                owner.tenant_id,
                owner.user_id,
                email,
                int(bool(submission_email_enabled)),
                int(bool(employer_confirmation_email_enabled)),
            ),
        )
        _sync_outbox(connection, owner=owner)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_preference()


def _notification_copy(event_kind: str, *, company: object, title: object) -> tuple[str, str]:
    company_text = _safe_copy(company, maximum=160) or "Employer"
    title_text = _safe_copy(title, maximum=200) or "application"
    if event_kind == "SUBMISSION_VERIFIED":
        return (
            f"{company_text} — {title_text} | Application submitted",
            f"SUBMITTED ✓\n\nMUNSHI verified the submission of your {title_text} application to {company_text}.",
        )
    if event_kind == "EMPLOYER_EMAIL_CONFIRMED":
        return (
            f"{company_text} — {title_text} | Confirmation received",
            f"EMPLOYER EMAIL CONFIRMED ✓\n\nMUNSHI correlated an employer application-confirmation email for your {title_text} application to {company_text}.",
        )
    raise ValueError("Unsupported notification event kind.")


def _queue(
    connection: sqlite3.Connection,
    *,
    owner: OwnerContext,
    event_kind: str,
    source_id: str,
    application_id: str,
    job_id: int,
    destination_email: str,
    company: object,
    title: object,
) -> bool:
    if event_kind not in _ALLOWED_EVENTS:
        raise ValueError("Unsupported notification event kind.")
    subject, body = _notification_copy(event_kind, company=company, title=title)
    material = f"{owner.tenant_id}\n{owner.user_id}\n{event_kind}\n{source_id}".encode()
    notification_id = "appnote_" + hashlib.sha256(material).hexdigest()[:32]
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO application_notification_outbox_v1(
          notification_id,tenant_id,user_id,event_kind,source_id,application_id,job_id,
          destination_email,subject,body_text
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            notification_id,
            owner.tenant_id,
            owner.user_id,
            event_kind,
            source_id,
            application_id,
            int(job_id),
            destination_email,
            subject,
            body,
        ),
    )
    return cursor.rowcount == 1


def _sync_outbox(connection: sqlite3.Connection, *, owner: OwnerContext) -> int:
    ensure_schema(connection)
    preference = connection.execute(
        """SELECT * FROM application_notification_preferences_v1
           WHERE tenant_id=? AND user_id=?""",
        (owner.tenant_id, owner.user_id),
    ).fetchone()
    if preference is None or not preference["destination_email"]:
        return 0
    queued = 0
    destination = str(preference["destination_email"])
    if bool(preference["submission_email_enabled"]) and _table_exists(
        connection, "production_submission_receipts_v1"
    ) and _table_exists(connection, "application_plans_v2"):
        rows = connection.execute(
            """
            SELECT r.receipt_id,r.application_id,p.job_id,j.company_name,j.title
            FROM production_submission_receipts_v1 r
            JOIN application_plans_v2 p ON p.plan_id=r.plan_id
            JOIN jobs j ON j.id=p.job_id
            WHERE r.tenant_id=? AND r.user_id=?
            ORDER BY r.verified_at
            """,
            (owner.tenant_id, owner.user_id),
        ).fetchall()
        for row in rows:
            queued += int(
                _queue(
                    connection,
                    owner=owner,
                    event_kind="SUBMISSION_VERIFIED",
                    source_id=str(row["receipt_id"]),
                    application_id=str(row["application_id"]),
                    job_id=int(row["job_id"]),
                    destination_email=destination,
                    company=row["company_name"],
                    title=row["title"],
                )
            )
    if bool(preference["employer_confirmation_email_enabled"]) and _table_exists(
        connection, "application_mail_confirmation_evidence_v2"
    ) and _table_exists(connection, "production_submission_receipts_v1"):
        rows = connection.execute(
            """
            SELECT e.evidence_id,e.application_id,e.job_id,j.company_name,j.title
            FROM application_mail_confirmation_evidence_v2 e
            JOIN production_submission_receipts_v1 r ON r.receipt_id=e.receipt_id
            JOIN jobs j ON j.id=e.job_id
            WHERE r.tenant_id=? AND r.user_id=?
            ORDER BY e.confirmed_at
            """,
            (owner.tenant_id, owner.user_id),
        ).fetchall()
        for row in rows:
            queued += int(
                _queue(
                    connection,
                    owner=owner,
                    event_kind="EMPLOYER_EMAIL_CONFIRMED",
                    source_id=str(row["evidence_id"]),
                    application_id=str(row["application_id"]),
                    job_id=int(row["job_id"]),
                    destination_email=destination,
                    company=row["company_name"],
                    title=row["title"],
                )
            )
    return queued


def sync_notification_outbox() -> int:
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        queued = _sync_outbox(connection, owner=current_owner(connection))
        connection.commit()
        return queued
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def application_evidence_rows(*, limit: int = 100) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        if not _table_exists(connection, "production_submission_receipts_v1") or not _table_exists(
            connection, "application_plans_v2"
        ):
            return []
        owner = current_owner(connection)
        confirmation_join = (
            "LEFT JOIN application_mail_confirmation_evidence_v2 e ON e.receipt_id=r.receipt_id"
            if _table_exists(connection, "application_mail_confirmation_evidence_v2")
            else "LEFT JOIN (SELECT NULL AS receipt_id,NULL AS confirmed_at) e ON 1=0"
        )
        rows = connection.execute(
            f"""
            SELECT r.application_id,r.receipt_id,r.provider,r.provider_application_id,
                   r.verified_at,p.job_id,j.company_name,j.title,e.confirmed_at
            FROM production_submission_receipts_v1 r
            JOIN application_plans_v2 p ON p.plan_id=r.plan_id
            JOIN jobs j ON j.id=p.job_id
            {confirmation_join}
            WHERE r.tenant_id=? AND r.user_id=?
            ORDER BY r.verified_at DESC
            LIMIT ?
            """,
            (owner.tenant_id, owner.user_id, max(1, min(int(limit), 500))),
        ).fetchall()
        return [
            {
                "application_id": str(row["application_id"]),
                "job_id": int(row["job_id"]),
                "company": row["company_name"],
                "title": row["title"],
                "provider": row["provider"],
                "provider_application_id": row["provider_application_id"],
                "submitted": True,
                "submitted_label": "SUBMITTED ✓",
                "submitted_at": row["verified_at"],
                "employer_email_confirmed": row["confirmed_at"] is not None,
                "employer_email_label": (
                    "EMPLOYER EMAIL CONFIRMED ✓" if row["confirmed_at"] is not None else "Employer email pending"
                ),
                "employer_email_confirmed_at": row["confirmed_at"],
            }
            for row in rows
        ]
    finally:
        connection.close()


class SMTP2GOAdapter:
    """Minimal SMTP2GO HTTPS adapter with env-first encrypted-vault fallback."""

    def __init__(self, *, client: Any | None = None) -> None:
        self.api_key, self.sender = smtp2go_credentials()
        self.api_url = str(os.getenv("MUNSHI_SMTP2GO_API_URL") or SMTP2GO_API_URL).strip()
        self.client = client
        if len(self.api_key) < 16:
            raise NotificationError("SMTP2GO API key is not configured.")
        if _clean_email(self.sender) is None:
            raise NotificationError("SMTP2GO sender is not configured.")
        if not self.api_url.startswith("https://"):
            raise NotificationError("SMTP2GO API URL must use HTTPS.")

    def send(self, message: dict[str, Any]) -> str:
        # The canonical outbox remains plain-text and immutable for retries /
        # auditing; both HTML and text are rendered from the same verified event.
        html_body, text_body = notification_email(message)
        payload = {
            "api_key": self.api_key,
            "to": [str(message["destination_email"])],
            "sender": f"MUNSHI <{self.sender}>",
            "subject": _safe_copy(message["subject"], maximum=300),
            "text_body": text_body,
            "html_body": html_body,
        }
        client = self.client or httpx.Client(timeout=15.0)
        owns_client = self.client is None
        try:
            response = client.post(self.api_url, json=payload)
            response.raise_for_status()
            decoded = response.json()
            data = decoded.get("data") if isinstance(decoded, dict) else None
            if not isinstance(data, dict):
                raise NotificationDeliveryError("SMTP2GO returned an invalid send response.")
            # SMTP2GO may return HTTP 200 with failed > 0; never mark these SENT.
            try:
                failures = int(data.get("failed") or 0)
                succeeded = int(data.get("succeeded", 1))
            except (ValueError, TypeError):
                raise NotificationDeliveryError("SMTP2GO returned invalid delivery counts.")
            if data.get("error") or failures != 0 or succeeded < 1 or data.get("failures"):
                raise NotificationDeliveryError("SMTP2GO did not accept the message.")
            identifier = data.get("email_id") or data.get("email_ids") or decoded.get("request_id")
            if isinstance(identifier, list):
                identifier = identifier[0] if len(identifier) == 1 else None
            if not identifier or not isinstance(identifier, str):
                raise NotificationDeliveryError("SMTP2GO did not confirm a message identifier.")
            return _safe_copy(identifier, maximum=240)
        finally:
            if owns_client:
                client.close()


def _recover_stale_claims(connection: sqlite3.Connection, *, now: datetime) -> int:
    """Never auto-retry a claim whose network result may be unknown."""
    return connection.execute(
        """
        UPDATE application_notification_outbox_v1
        SET delivery_state='AMBIGUOUS',claim_token_hash=NULL,lease_expires_at=NULL,
            last_error_class='STALE_CLAIM',
            last_error_text='Delivery result unknown after worker interruption; manual review required.',
            updated_at=?
        WHERE delivery_state='CLAIMED' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?
        """,
        (_iso(now), _iso(now)),
    ).rowcount


def drain_notification_outbox(*, limit: int = 20, adapter: Any | None = None) -> dict[str, int]:
    if not (_enabled("MUNSHI_APPLICATION_EMAIL_NOTIFICATIONS_ENABLED") or smtp2go_configured()):
        return {"sent": 0, "ambiguous": 0, "pending": 0}
    adapter = adapter or SMTP2GOAdapter()
    sent = 0
    ambiguous = 0
    for _ in range(max(1, min(int(limit), 100))):
        now = _now()
        connection = get_connection()
        claim_token = secrets.token_urlsafe(32)
        claim_hash = hashlib.sha256(claim_token.encode()).hexdigest()
        try:
            ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            _recover_stale_claims(connection, now=now)
            row = connection.execute(
                """SELECT * FROM application_notification_outbox_v1
                   WHERE delivery_state='PENDING' ORDER BY created_at,notification_id LIMIT 1"""
            ).fetchone()
            if row is None:
                connection.commit()
                break
            notification_id = str(row["notification_id"])
            if connection.execute(
                """
                UPDATE application_notification_outbox_v1
                SET delivery_state='CLAIMED',attempt_count=attempt_count+1,
                    claim_token_hash=?,lease_expires_at=?,updated_at=?
                WHERE notification_id=? AND delivery_state='PENDING'
                """,
                (
                    claim_hash,
                    _iso(now + timedelta(seconds=LEASE_SECONDS)),
                    _iso(now),
                    notification_id,
                ),
            ).rowcount != 1:
                connection.rollback()
                continue
            claimed = connection.execute(
                "SELECT * FROM application_notification_outbox_v1 WHERE notification_id=?",
                (notification_id,),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()

        try:
            provider_message_id = adapter.send(dict(claimed))
        except Exception as error:
            connection = get_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                changed = connection.execute(
                    """
                    UPDATE application_notification_outbox_v1
                    SET delivery_state='AMBIGUOUS',claim_token_hash=NULL,lease_expires_at=NULL,
                        last_error_class=?,last_error_text=?,updated_at=?
                    WHERE notification_id=? AND delivery_state='CLAIMED' AND claim_token_hash=?
                    """,
                    (
                        error.__class__.__name__[:120],
                        "SMTP delivery result is unknown; automatic retry disabled.",
                        _iso(_now()),
                        notification_id,
                        claim_hash,
                    ),
                ).rowcount
                connection.commit()
                ambiguous += int(changed == 1)
            finally:
                connection.close()
            continue

        connection = get_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE application_notification_outbox_v1
                SET delivery_state='SENT',provider_message_id=?,sent_at=?,
                    claim_token_hash=NULL,lease_expires_at=NULL,last_error_class=NULL,
                    last_error_text=NULL,updated_at=?
                WHERE notification_id=? AND delivery_state='CLAIMED' AND claim_token_hash=?
                """,
                (
                    _safe_copy(provider_message_id, maximum=240),
                    _iso(_now()),
                    _iso(_now()),
                    notification_id,
                    claim_hash,
                ),
            ).rowcount
            connection.commit()
            sent += int(changed == 1)
        finally:
            connection.close()
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        pending = connection.execute(
            """SELECT COUNT(*) FROM application_notification_outbox_v1
               WHERE tenant_id=? AND user_id=? AND delivery_state='PENDING'""",
            (owner.tenant_id, owner.user_id),
        ).fetchone()[0]
    finally:
        connection.close()
    return {"sent": sent, "ambiguous": ambiguous, "pending": int(pending)}
