"""Communication-aware, transaction-safe application notification runtime V3.

V3 extends the stable V2 receipt/confirmation runtime with a read-only recruiting
communication center and opt-in sanitized notifications for uniquely correlated
recruiter/interview/assessment/rejection/offer events.

It never forwards raw inbound mail and has no application submission or reply
authority. SMTP delivery remains delegated to the V1 durable outbox and stays
feature-gated/default-off.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from app import application_notifications_runtime_v2 as v2
from app import application_notifications_v1 as storage
from app.database import get_connection
from app.tenant_foundation import OwnerContext, current_owner

NotificationError = storage.NotificationError
NotificationDeliveryError = storage.NotificationDeliveryError
SMTP2GOAdapter = storage.SMTP2GOAdapter
application_evidence_rows = storage.application_evidence_rows
drain_notification_outbox = storage.drain_notification_outbox

COMMUNICATION_EVENT_KINDS = (
    "RECRUITER",
    "INTERVIEW",
    "ASSESSMENT",
    "REJECTION",
    "OFFER",
)
_ALL_EVENT_KINDS = (
    "SUBMISSION_VERIFIED",
    "EMPLOYER_EMAIL_CONFIRMED",
    *COMMUNICATION_EVENT_KINDS,
)


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _outbox_supports_communications(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        ("application_notification_outbox_v1",),
    ).fetchone()
    if row is None or row[0] is None:
        return False
    sql = str(row[0]).upper()
    return all(f"'{kind}'" in sql for kind in COMMUNICATION_EVENT_KINDS)


def _create_upgraded_outbox(connection: sqlite3.Connection) -> None:
    allowed = ",".join(f"'{kind}'" for kind in _ALL_EVENT_KINDS)
    connection.execute(
        f"""
        CREATE TABLE application_notification_outbox_v1(
          notification_id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          event_kind TEXT NOT NULL CHECK(event_kind IN ({allowed})),
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
          FOREIGN KEY(tenant_id,user_id)
            REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE CASCADE,
          FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT
        )
        """
    )


def _upgrade_schema(connection: sqlite3.Connection) -> None:
    """Upgrade V1/V2 storage transactionally and idempotently."""
    connection.execute("SAVEPOINT application_notifications_v3")
    try:
        columns = _column_names(
            connection, "application_notification_preferences_v1"
        )
        if "communication_email_enabled" not in columns:
            connection.execute(
                """
                ALTER TABLE application_notification_preferences_v1
                ADD COLUMN communication_email_enabled INTEGER NOT NULL DEFAULT 0
                CHECK(communication_email_enabled IN (0,1))
                """
            )

        if not _outbox_supports_communications(connection):
            legacy = "application_notification_outbox_v1_legacy_055"
            if storage._table_exists(connection, legacy):
                connection.execute(f"DROP TABLE {legacy}")
            connection.execute(
                f"ALTER TABLE application_notification_outbox_v1 RENAME TO {legacy}"
            )
            _create_upgraded_outbox(connection)
            connection.execute(
                f"""
                INSERT INTO application_notification_outbox_v1(
                  notification_id,tenant_id,user_id,event_kind,source_id,
                  application_id,job_id,destination_email,subject,body_text,
                  delivery_state,attempt_count,claim_token_hash,lease_expires_at,
                  provider_message_id,last_error_class,last_error_text,created_at,
                  updated_at,sent_at
                )
                SELECT
                  notification_id,tenant_id,user_id,event_kind,source_id,
                  application_id,job_id,destination_email,subject,body_text,
                  delivery_state,attempt_count,claim_token_hash,lease_expires_at,
                  provider_message_id,last_error_class,last_error_text,created_at,
                  updated_at,sent_at
                FROM {legacy}
                """
            )
            connection.execute(f"DROP TABLE {legacy}")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_application_notification_outbox_delivery_v1
                ON application_notification_outbox_v1(delivery_state,created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_application_notification_outbox_owner_v1
                ON application_notification_outbox_v1(
                  tenant_id,user_id,created_at DESC
                )
                """
            )
        connection.execute("RELEASE application_notifications_v3")
    except Exception:
        connection.execute("ROLLBACK TO application_notifications_v3")
        connection.execute("RELEASE application_notifications_v3")
        raise


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        v2.ensure_schema(connection)
        _upgrade_schema(connection)
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
            """
            SELECT destination_email,submission_email_enabled,
                   employer_confirmation_email_enabled,communication_email_enabled
            FROM application_notification_preferences_v1
            WHERE tenant_id=? AND user_id=?
            """,
            (owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            return {
                "destination_email": None,
                "submission_email_enabled": False,
                "employer_confirmation_email_enabled": False,
                "communication_email_enabled": False,
            }
        return {
            "destination_email": row["destination_email"],
            "submission_email_enabled": bool(row["submission_email_enabled"]),
            "employer_confirmation_email_enabled": bool(
                row["employer_confirmation_email_enabled"]
            ),
            "communication_email_enabled": bool(row["communication_email_enabled"]),
        }
    finally:
        if owns:
            connection.close()


def _communication_copy(
    category: str,
    *,
    company: object,
    title: object,
) -> tuple[str, str]:
    if category not in COMMUNICATION_EVENT_KINDS:
        raise ValueError("Unsupported application communication category.")
    company_text = storage._safe_copy(company, maximum=160) or "an employer"
    title_text = storage._safe_copy(title, maximum=200) or "your application"
    labels = {
        "RECRUITER": "Recruiter update",
        "INTERVIEW": "Interview update",
        "ASSESSMENT": "Assessment update",
        "REJECTION": "Application status update",
        "OFFER": "Offer update",
    }
    label = labels[category]
    return (
        f"MUNSHI: {label.lower()} from {company_text}",
        (
            f"{label}\n\nMUNSHI received a {category.lower()} communication "
            f"correlated to your {title_text} application at {company_text}. "
            "Open MUNSHI to review the communication status."
        ),
    )


def _queue_communication(
    connection: sqlite3.Connection,
    *,
    owner: OwnerContext,
    communication_id: str,
    application_id: str,
    job_id: int,
    category: str,
    destination_email: str,
    company: object,
    title: object,
) -> bool:
    subject, body = _communication_copy(category, company=company, title=title)
    material = (
        f"{owner.tenant_id}\n{owner.user_id}\n{category}\n{communication_id}"
    ).encode()
    notification_id = "appnote_" + hashlib.sha256(material).hexdigest()[:32]
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO application_notification_outbox_v1(
          notification_id,tenant_id,user_id,event_kind,source_id,application_id,
          job_id,destination_email,subject,body_text
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            notification_id,
            owner.tenant_id,
            owner.user_id,
            category,
            communication_id,
            application_id,
            int(job_id),
            destination_email,
            subject,
            body,
        ),
    )
    return cursor.rowcount == 1


def _sync_communication_outbox(
    connection: sqlite3.Connection,
    *,
    owner: OwnerContext,
    destination_email: str,
) -> int:
    if not (
        storage._table_exists(connection, "application_communication_events_v2")
        and storage._table_exists(connection, "mail_events_v2")
    ):
        return 0
    rows = connection.execute(
        """
        SELECT c.communication_id,c.application_id,c.job_id,c.category,
               j.company_name,j.title
        FROM application_communication_events_v2 c
        JOIN mail_events_v2 m ON m.mail_event_id=c.mail_event_id
        JOIN jobs j ON j.id=c.job_id
        WHERE m.tenant_id=? AND m.user_id=?
          AND c.application_id IS NOT NULL
          AND c.job_id IS NOT NULL
          AND c.auto_reply_allowed=0
          AND m.correlation_state='UNIQUE'
        ORDER BY c.received_at,c.communication_id
        """,
        (owner.tenant_id, owner.user_id),
    ).fetchall()
    queued = 0
    for row in rows:
        category = str(row["category"]).upper()
        if category not in COMMUNICATION_EVENT_KINDS:
            continue
        queued += int(
            _queue_communication(
                connection,
                owner=owner,
                communication_id=str(row["communication_id"]),
                application_id=str(row["application_id"]),
                job_id=int(row["job_id"]),
                category=category,
                destination_email=destination_email,
                company=row["company_name"],
                title=row["title"],
            )
        )
    return queued


def _sync_relay_verified_employer_outbox(
    connection: sqlite3.Connection, *,
    owner: OwnerContext, destination_email: str,
) -> int:
    """Queue only independently verified managed-inbox receipts.

    A provider's own sent status cannot enter this projection. Deduplication
    uses the owned route and immutable evidence digest, never email body text.
    """
    if not (
        storage._table_exists(connection, "application_provider_routes_v1")
        and storage._table_exists(connection, "tsenta_external_dispatch_claims_v1")
    ):
        return 0
    verified = connection.execute(
        """SELECT r.route_id,r.application_id,r.job_id,
                  r.receipt_evidence_digest,j.company_name,j.title
           FROM application_provider_routes_v1 r
           JOIN tsenta_external_dispatch_claims_v1 d
             ON d.route_id=r.route_id AND d.tenant_id=r.tenant_id
            AND d.user_id=r.user_id AND d.job_id=r.job_id
           JOIN jobs j ON j.id=r.job_id
           WHERE r.tenant_id=? AND r.user_id=?
             AND r.provider='TSENTA'
             AND r.state='EMPLOYER_CONFIRMED'
             AND r.receipt_method='CORRELATED_EMPLOYER_EMAIL'
             AND length(r.receipt_evidence_digest)=64
             AND d.state='ACCEPTED'
             AND r.provider_application_id_sha256=d.tsenta_application_id_sha256
           ORDER BY r.employer_confirmed_at,r.route_id""",
        (owner.tenant_id,owner.user_id),
    ).fetchall()
    queued=0
    for row in verified:
        queued+=int(storage._queue(
            connection, owner=owner, event_kind="EMPLOYER_EMAIL_CONFIRMED",
            source_id="relay:"+str(row["route_id"])+":"+str(row["receipt_evidence_digest"]),
            application_id=str(row["application_id"]),job_id=int(row["job_id"]),
            destination_email=destination_email,
            company=row["company_name"],title=row["title"],
        ))
    return queued


def _sync_outbox_transaction(
    connection: sqlite3.Connection,
    *,
    owner: OwnerContext,
) -> int:
    """Project authoritative evidence inside an existing write transaction."""
    queued = v2._sync_outbox_transaction(connection, owner=owner)
    preference = connection.execute(
        """
        SELECT destination_email,employer_confirmation_email_enabled,
               communication_email_enabled
        FROM application_notification_preferences_v1
        WHERE tenant_id=? AND user_id=?
        """,
        (owner.tenant_id, owner.user_id),
    ).fetchone()
    if preference is None or not preference["destination_email"]:
        return queued
    if bool(preference["employer_confirmation_email_enabled"]):
        queued+=_sync_relay_verified_employer_outbox(
            connection,owner=owner,
            destination_email=str(preference["destination_email"]),
        )
    if not bool(preference["communication_email_enabled"]):
        return queued
    return queued + _sync_communication_outbox(
        connection,
        owner=owner,
        destination_email=str(preference["destination_email"]),
    )


def save_preference(
    *,
    destination_email: object,
    submission_email_enabled: bool,
    employer_confirmation_email_enabled: bool,
    communication_email_enabled: bool = False,
) -> dict[str, Any]:
    email = storage._clean_email(destination_email)
    if (
        submission_email_enabled
        or employer_confirmation_email_enabled
        or communication_email_enabled
    ) and email is None:
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
              employer_confirmation_email_enabled,communication_email_enabled,
              updated_at
            ) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(tenant_id,user_id) DO UPDATE SET
              destination_email=excluded.destination_email,
              submission_email_enabled=excluded.submission_email_enabled,
              employer_confirmation_email_enabled=
                excluded.employer_confirmation_email_enabled,
              communication_email_enabled=excluded.communication_email_enabled,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                owner.tenant_id,
                owner.user_id,
                email,
                int(bool(submission_email_enabled)),
                int(bool(employer_confirmation_email_enabled)),
                int(bool(communication_email_enabled)),
            ),
        )
        _sync_outbox_transaction(connection, owner=owner)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_preference()


def sync_notification_outbox() -> int:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        connection.execute("BEGIN IMMEDIATE")
        queued = _sync_outbox_transaction(connection, owner=owner)
        connection.commit()
        return queued
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def application_communication_rows(*, limit: int = 100) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        if not (
            storage._table_exists(connection, "application_communication_events_v2")
            and storage._table_exists(connection, "mail_events_v2")
        ):
            return []
        owner = current_owner(connection)
        rows = connection.execute(
            """
            SELECT c.communication_id,c.application_id,c.job_id,c.category,
                   c.sender_domain,c.received_at,c.auto_reply_allowed,
                   m.correlation_state,j.company_name,j.title
            FROM application_communication_events_v2 c
            JOIN mail_events_v2 m ON m.mail_event_id=c.mail_event_id
            LEFT JOIN jobs j ON j.id=c.job_id
            WHERE m.tenant_id=? AND m.user_id=?
            ORDER BY c.received_at DESC,c.communication_id DESC
            LIMIT ?
            """,
            (
                owner.tenant_id,
                owner.user_id,
                max(1, min(int(limit), 500)),
            ),
        ).fetchall()
        return [
            {
                "communication_id": str(row["communication_id"]),
                "application_id": row["application_id"],
                "job_id": row["job_id"],
                "category": str(row["category"]),
                "sender_domain": row["sender_domain"],
                "received_at": row["received_at"],
                "company": row["company_name"],
                "title": row["title"],
                "correlation": (
                    "Matched"
                    if row["application_id"] is not None
                    and row["job_id"] is not None
                    and str(row["correlation_state"]) == "UNIQUE"
                    else "Unmatched"
                ),
                "auto_reply_allowed": False,
            }
            for row in rows
        ]
    finally:
        connection.close()


def notification_cycle(
    *,
    limit: int = 20,
    adapter: Any | None = None,
) -> dict[str, int]:
    queued = sync_notification_outbox()
    delivery = drain_notification_outbox(limit=limit, adapter=adapter)
    return {
        "queued": int(queued),
        "sent": int(delivery["sent"]),
        "ambiguous": int(delivery["ambiguous"]),
        "pending": int(delivery["pending"]),
    }
