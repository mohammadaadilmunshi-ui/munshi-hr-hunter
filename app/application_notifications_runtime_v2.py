"""Transaction-safe runtime for user-facing application notifications.

V1 remains available as rollback-compatible storage/delivery code. This runtime is
what the dashboard and worker should use: schema installation occurs before any
BEGIN IMMEDIATE transaction, so SQLite DDL cannot implicitly break an active write
transaction. The authoritative evidence sources remain the verified submission
receipt ledger and independently correlated employer-mail evidence ledger.
"""
from __future__ import annotations

from typing import Any

from app import application_notifications_v1 as storage
from app.database import get_connection
from app.tenant_foundation import OwnerContext, current_owner

NotificationError = storage.NotificationError
NotificationDeliveryError = storage.NotificationDeliveryError
SMTP2GOAdapter = storage.SMTP2GOAdapter
application_evidence_rows = storage.application_evidence_rows
drain_notification_outbox = storage.drain_notification_outbox
ensure_schema = storage.ensure_schema
get_preference = storage.get_preference


def _sync_outbox_transaction(
    connection: Any,
    *,
    owner: OwnerContext,
) -> int:
    """Project authoritative evidence into the outbox inside an existing txn.

    Callers MUST install schema before opening the transaction. This helper performs
    no DDL and therefore cannot implicitly commit or alter the caller's transaction.
    """
    preference = connection.execute(
        """SELECT * FROM application_notification_preferences_v1
           WHERE tenant_id=? AND user_id=?""",
        (owner.tenant_id, owner.user_id),
    ).fetchone()
    if preference is None or not preference["destination_email"]:
        return 0

    queued = 0
    destination = str(preference["destination_email"])
    if bool(preference["submission_email_enabled"]) and storage._table_exists(
        connection, "production_submission_receipts_v1"
    ) and storage._table_exists(connection, "application_plans_v2"):
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
                storage._queue(
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

    if bool(preference["employer_confirmation_email_enabled"]) and storage._table_exists(
        connection, "application_mail_confirmation_evidence_v2"
    ) and storage._table_exists(connection, "production_submission_receipts_v1"):
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
                storage._queue(
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


def save_preference(
    *,
    destination_email: object,
    submission_email_enabled: bool,
    employer_confirmation_email_enabled: bool,
) -> dict[str, Any]:
    email = storage._clean_email(destination_email)
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


def notification_cycle(*, limit: int = 20, adapter: Any | None = None) -> dict[str, int]:
    """One idempotent projection + delivery cycle for a supervised worker.

    Delivery remains feature-gated inside V1's drain function. If outbound email is
    disabled, projection still stays current while zero network calls occur.
    """
    queued = sync_notification_outbox()
    delivery = drain_notification_outbox(limit=limit, adapter=adapter)
    return {
        "queued": int(queued),
        "sent": int(delivery["sent"]),
        "ambiguous": int(delivery["ambiguous"]),
        "pending": int(delivery["pending"]),
    }
