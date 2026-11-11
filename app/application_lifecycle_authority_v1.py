"""Canonical Hunter application lifecycle authority and CRM read model."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.database import get_connection
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema


STATES = frozenset(
    {
        "DISCOVERED",
        "SCORED",
        "PURSUIT_APPROVED",
        "PREPARING",
        "NEEDS_INPUT",
        "READY_TO_APPLY",
        "PLAN_ACCEPTED",
        "PREPARING_ATS",
        "READY_FOR_REVIEW",
        "READY_TO_SUBMIT",
        "SUBMITTING",
        "SUBMITTED",
        "VERIFIED",
        "AWAITING_RESPONSE",
        "ASSESSMENT",
        "INTERVIEW",
        "OFFER",
        "REJECTED",
        "WITHDRAWN",
        "FAILED_SAFELY",
        "BLOCKED",
        "SUBMISSION_UNVERIFIED",
    }
)

TERMINAL_STATES = frozenset({"REJECTED", "WITHDRAWN"})

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "DISCOVERED": frozenset({"SCORED", "BLOCKED", "WITHDRAWN"}),
    "SCORED": frozenset({"PURSUIT_APPROVED", "BLOCKED", "WITHDRAWN"}),
    "PURSUIT_APPROVED": frozenset({"PREPARING", "BLOCKED", "WITHDRAWN"}),
    "PREPARING": frozenset(
        {
            "NEEDS_INPUT",
            "READY_TO_APPLY",
            "PLAN_ACCEPTED",
            "PREPARING_ATS",
            "READY_FOR_REVIEW",
            "FAILED_SAFELY",
            "BLOCKED",
            "WITHDRAWN",
        }
    ),
    "NEEDS_INPUT": frozenset(
        {
            "PREPARING",
            "PREPARING_ATS",
            "READY_FOR_REVIEW",
            "FAILED_SAFELY",
            "BLOCKED",
            "WITHDRAWN",
        }
    ),
    "READY_TO_APPLY": frozenset(
        {
            "PLAN_ACCEPTED",
            "PREPARING_ATS",
            "READY_FOR_REVIEW",
            "FAILED_SAFELY",
            "BLOCKED",
            "WITHDRAWN",
        }
    ),
    "PLAN_ACCEPTED": frozenset(
        {
            "PREPARING_ATS",
            "NEEDS_INPUT",
            "READY_FOR_REVIEW",
            "FAILED_SAFELY",
            "BLOCKED",
            "WITHDRAWN",
        }
    ),
    "PREPARING_ATS": frozenset(
        {"NEEDS_INPUT", "READY_FOR_REVIEW", "FAILED_SAFELY", "BLOCKED", "WITHDRAWN"}
    ),
    "READY_FOR_REVIEW": frozenset(
        {"NEEDS_INPUT", "PREPARING_ATS", "READY_TO_SUBMIT", "BLOCKED", "WITHDRAWN"}
    ),
    "READY_TO_SUBMIT": frozenset({"SUBMITTING", "BLOCKED", "WITHDRAWN"}),
    "SUBMITTING": frozenset(
        {"SUBMITTED", "SUBMISSION_UNVERIFIED", "FAILED_SAFELY", "BLOCKED"}
    ),
    "SUBMITTED": frozenset({"VERIFIED", "SUBMISSION_UNVERIFIED"}),
    "SUBMISSION_UNVERIFIED": frozenset({"VERIFIED", "FAILED_SAFELY"}),
    "VERIFIED": frozenset({"AWAITING_RESPONSE"}),
    "AWAITING_RESPONSE": frozenset(
        {"ASSESSMENT", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"}
    ),
    "ASSESSMENT": frozenset({"INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"}),
    "INTERVIEW": frozenset({"OFFER", "REJECTED", "WITHDRAWN"}),
    "OFFER": frozenset({"WITHDRAWN", "REJECTED"}),
    "FAILED_SAFELY": frozenset({"PREPARING", "BLOCKED", "WITHDRAWN"}),
    "BLOCKED": frozenset({"PREPARING", "NEEDS_INPUT", "WITHDRAWN"}),
    "REJECTED": frozenset(),
    "WITHDRAWN": frozenset(),
}

CURRENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS application_lifecycle_records (
    application_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    provider_application_id TEXT,
    state TEXT NOT NULL,
    verified_receipt_id TEXT,
    state_version INTEGER NOT NULL DEFAULT 0,
    submitted_at TEXT,
    verified_at TEXT,
    awaiting_response_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(state IN (
      'DISCOVERED','SCORED','PURSUIT_APPROVED','PREPARING','NEEDS_INPUT',
      'READY_TO_APPLY','PLAN_ACCEPTED','PREPARING_ATS','READY_FOR_REVIEW',
      'READY_TO_SUBMIT','SUBMITTING','SUBMITTED','VERIFIED',
      'AWAITING_RESPONSE','ASSESSMENT','INTERVIEW','OFFER','REJECTED',
      'WITHDRAWN','FAILED_SAFELY','BLOCKED','SUBMISSION_UNVERIFIED'
    )),
    UNIQUE(tenant_id,user_id,provider,provider_application_id),
    FOREIGN KEY(tenant_id,user_id)
      REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
);
"""

HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS application_lifecycle_history_v1 (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    source TEXT NOT NULL,
    reason TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id,user_id,application_id,idempotency_key),
    UNIQUE(tenant_id,user_id,application_id,state_version),
    FOREIGN KEY(tenant_id,user_id)
      REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
);
"""


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        connection.execute(CURRENT_SCHEMA)
        connection.execute(HISTORY_SCHEMA)
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_application_lifecycle_history_owner_app
               ON application_lifecycle_history_v1(
                   tenant_id,user_id,application_id,state_version
               )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_application_lifecycle_current_owner_state
               ON application_lifecycle_records(
                   tenant_id,user_id,state,updated_at DESC
               )"""
        )
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _canonical(value: Mapping[str, Any] | None) -> str:
    return json.dumps(
        dict(value or {}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _text(value: Any, label: str, maximum: int = 500) -> str:
    result = " ".join(str(value or "").split())
    if not result or len(result) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return result


def _optional_text(value: Any, maximum: int = 2000) -> str | None:
    if value is None:
        return None
    result = " ".join(str(value).split())
    if not result:
        return None
    if len(result) > maximum:
        raise ValueError(f"Text value must be at most {maximum} characters.")
    return result


def _state(value: Any) -> str:
    result = _text(value, "Lifecycle state", 80).upper()
    if result not in STATES:
        raise ValueError(f"Unsupported lifecycle state: {result}")
    return result


def _timestamp(value: Any | None) -> str:
    if value is None:
        return datetime.now(UTC).isoformat()
    text = _text(value, "occurred_at", 100)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("occurred_at is invalid.") from error
    if parsed.tzinfo is None:
        raise ValueError("occurred_at must include a timezone.")
    return parsed.astimezone(UTC).isoformat()


def _event_id(owner: Any, application_id: str, idempotency_key: str) -> str:
    raw = f"{owner.tenant_id}:{owner.user_id}:{application_id}:{idempotency_key}"
    return "lifecycle-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _history_by_idempotency(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    application_id: str,
    idempotency_key: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT * FROM application_lifecycle_history_v1
           WHERE tenant_id=? AND user_id=? AND application_id=? AND idempotency_key=?""",
        (owner.tenant_id, owner.user_id, application_id, idempotency_key),
    ).fetchone()


def _assert_exact_replay(
    row: sqlite3.Row,
    *,
    to_state: str,
    source: str,
    reason: str | None,
    provenance_json: str,
) -> None:
    if (
        str(row["to_state"]) != to_state
        or str(row["source"]) != source
        or row["reason"] != reason
        or str(row["provenance_json"]) != provenance_json
    ):
        raise ValueError("Lifecycle idempotency key was reused with conflicting data.")


def _append_history(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    application_id: str,
    from_state: str | None,
    to_state: str,
    state_version: int,
    source: str,
    reason: str | None,
    provenance_json: str,
    idempotency_key: str,
    occurred_at: str,
) -> None:
    connection.execute(
        """INSERT INTO application_lifecycle_history_v1(
               event_id,tenant_id,user_id,application_id,from_state,to_state,
               state_version,source,reason,provenance_json,idempotency_key,occurred_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _event_id(owner, application_id, idempotency_key),
            owner.tenant_id,
            owner.user_id,
            application_id,
            from_state,
            to_state,
            state_version,
            source,
            reason,
            provenance_json,
            idempotency_key,
            occurred_at,
        ),
    )


def _owned_record(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    application_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT * FROM application_lifecycle_records
           WHERE application_id=? AND tenant_id=? AND user_id=?""",
        (application_id, owner.tenant_id, owner.user_id),
    ).fetchone()


def initialize_application(
    *,
    application_id: str,
    job_id: int,
    provider: str,
    source: str,
    idempotency_key: str,
    provenance: Mapping[str, Any] | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    application_id = _text(application_id, "application_id", 240)
    provider = _text(provider, "provider", 120).upper()
    source = _text(source, "source", 120)
    idempotency_key = _text(idempotency_key, "idempotency_key", 240)
    provenance_json = _canonical(provenance)
    occurred = _timestamp(occurred_at)

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")

        prior = _history_by_idempotency(
            connection,
            owner=owner,
            application_id=application_id,
            idempotency_key=idempotency_key,
        )
        if prior is not None:
            _assert_exact_replay(
                prior,
                to_state="DISCOVERED",
                source=source,
                reason=None,
                provenance_json=provenance_json,
            )
            current = _owned_record(
                connection, owner=owner, application_id=application_id
            )
            connection.commit()
            return {
                "application_id": application_id,
                "state": str(current["state"]),
                "state_version": int(current["state_version"]),
                "replayed": True,
            }

        if _owned_record(connection, owner=owner, application_id=application_id):
            raise ValueError("Application lifecycle already exists.")

        if connection.execute("SELECT 1 FROM jobs WHERE id=?", (int(job_id),)).fetchone() is None:
            raise LookupError("Lifecycle job does not exist.")

        connection.execute(
            """INSERT INTO application_lifecycle_records(
                   application_id,tenant_id,user_id,job_id,provider,state,state_version
               ) VALUES (?,?,?,?,?,'DISCOVERED',1)""",
            (
                application_id,
                owner.tenant_id,
                owner.user_id,
                int(job_id),
                provider,
            ),
        )
        _append_history(
            connection,
            owner=owner,
            application_id=application_id,
            from_state=None,
            to_state="DISCOVERED",
            state_version=1,
            source=source,
            reason=None,
            provenance_json=provenance_json,
            idempotency_key=idempotency_key,
            occurred_at=occurred,
        )
        connection.commit()
        return {
            "application_id": application_id,
            "state": "DISCOVERED",
            "state_version": 1,
            "replayed": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _transition_with_connection(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    application_id: str,
    to_state: str,
    expected_state_version: int,
    source: str,
    idempotency_key: str,
    reason: str | None,
    provenance_json: str,
    occurred_at: str,
) -> dict[str, Any]:
    prior = _history_by_idempotency(
        connection,
        owner=owner,
        application_id=application_id,
        idempotency_key=idempotency_key,
    )
    if prior is not None:
        _assert_exact_replay(
            prior,
            to_state=to_state,
            source=source,
            reason=reason,
            provenance_json=provenance_json,
        )
        current = _owned_record(
            connection, owner=owner, application_id=application_id
        )
        if current is None:
            raise RuntimeError("Lifecycle history exists without a current record.")
        return {
            "application_id": application_id,
            "state": str(current["state"]),
            "state_version": int(current["state_version"]),
            "replayed": True,
        }

    row = _owned_record(connection, owner=owner, application_id=application_id)
    if row is None:
        raise LookupError("Application lifecycle was not found for this owner.")

    current_state = str(row["state"])
    current_version = int(row["state_version"])

    if current_version != int(expected_state_version):
        raise RuntimeError(
            f"Lifecycle version conflict: expected {expected_state_version}, "
            f"current {current_version}."
        )
    if current_state in TERMINAL_STATES:
        raise ValueError(f"Terminal lifecycle state {current_state} cannot transition.")
    if to_state == current_state:
        raise ValueError("No-op lifecycle transitions are not permitted.")
    if to_state not in ALLOWED_TRANSITIONS[current_state]:
        raise ValueError(
            f"Illegal lifecycle transition: {current_state} -> {to_state}."
        )

    new_version = current_version + 1
    updated = connection.execute(
        """UPDATE application_lifecycle_records
              SET state=?,state_version=?,updated_at=CURRENT_TIMESTAMP
            WHERE application_id=? AND tenant_id=? AND user_id=?
              AND state=? AND state_version=?""",
        (
            to_state,
            new_version,
            application_id,
            owner.tenant_id,
            owner.user_id,
            current_state,
            current_version,
        ),
    )
    if int(updated.rowcount or 0) != 1:
        raise RuntimeError("Lifecycle state changed concurrently; retry from fresh state.")

    _append_history(
        connection,
        owner=owner,
        application_id=application_id,
        from_state=current_state,
        to_state=to_state,
        state_version=new_version,
        source=source,
        reason=reason,
        provenance_json=provenance_json,
        idempotency_key=idempotency_key,
        occurred_at=occurred_at,
    )
    return {
        "application_id": application_id,
        "state": to_state,
        "state_version": new_version,
        "replayed": False,
    }


def transition_application_state(
    *,
    application_id: str,
    to_state: str,
    expected_state_version: int,
    source: str,
    idempotency_key: str,
    reason: str | None = None,
    provenance: Mapping[str, Any] | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    application_id = _text(application_id, "application_id", 240)
    target = _state(to_state)
    source = _text(source, "source", 120)
    key = _text(idempotency_key, "idempotency_key", 240)
    clean_reason = _optional_text(reason)
    provenance_json = _canonical(provenance)
    occurred = _timestamp(occurred_at)

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        result = _transition_with_connection(
            connection,
            owner=owner,
            application_id=application_id,
            to_state=target,
            expected_state_version=int(expected_state_version),
            source=source,
            idempotency_key=key,
            reason=clean_reason,
            provenance_json=provenance_json,
            occurred_at=occurred,
        )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record_verified_receipt(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    application_id: str,
    job_id: int,
    provider: str,
    provider_application_id: str,
    receipt_id: str,
    submitted_at: str,
    verified_at: str,
    provenance: Mapping[str, Any] | None = None,
) -> str:
    """Record independently verified Apply proof and reach AWAITING_RESPONSE."""
    ensure_schema(connection)

    application_id = _text(application_id, "application_id", 240)
    provider = _text(provider, "provider", 120).upper()
    provider_application_id = _text(
        provider_application_id, "provider_application_id", 500
    )
    receipt_id = _text(receipt_id, "receipt_id", 240)
    verified_time = _timestamp(verified_at)
    _timestamp(submitted_at)

    row = _owned_record(connection, owner=owner, application_id=application_id)
    if row is not None:
        if int(row["job_id"]) != int(job_id):
            raise ValueError("Verified receipt lifecycle job binding changed.")
        if str(row["provider"]).upper() != provider:
            raise ValueError("Verified receipt lifecycle provider binding changed.")
        if row["provider_application_id"] not in (None, provider_application_id):
            raise ValueError("Verified receipt lifecycle provider identity changed.")
        if row["verified_receipt_id"] not in (None, receipt_id):
            raise ValueError("Application is already bound to another verified receipt.")

    provenance_payload = dict(provenance or {})
    provenance_payload.update(
        {
            "receipt_id": receipt_id,
            "provider": provider,
            "provider_application_id": provider_application_id,
            "verification_status": "VERIFIED",
            "submission_authority": False,
        }
    )
    provenance_json = _canonical(provenance_payload)

    if row is None:
        connection.execute(
            """INSERT INTO application_lifecycle_records(
                   application_id,tenant_id,user_id,job_id,provider,
                   provider_application_id,state,verified_receipt_id,state_version,
                   submitted_at,verified_at
               ) VALUES (?,?,?,?,?,?,'VERIFIED',?,1,?,?)""",
            (
                application_id,
                owner.tenant_id,
                owner.user_id,
                int(job_id),
                provider,
                provider_application_id,
                receipt_id,
                submitted_at,
                verified_at,
            ),
        )
        _append_history(
            connection,
            owner=owner,
            application_id=application_id,
            from_state=None,
            to_state="VERIFIED",
            state_version=1,
            source="verified_apply_receipt",
            reason="Independent Apply verification received.",
            provenance_json=provenance_json,
            idempotency_key=f"receipt:{receipt_id}:VERIFIED",
            occurred_at=verified_time,
        )
    else:
        current_state = str(row["state"])
        if current_state in {
            "AWAITING_RESPONSE",
            "ASSESSMENT",
            "INTERVIEW",
            "OFFER",
            "REJECTED",
            "WITHDRAWN",
        }:
            connection.execute(
                """UPDATE application_lifecycle_records
                      SET provider_application_id=COALESCE(provider_application_id,?),
                          verified_receipt_id=COALESCE(verified_receipt_id,?),
                          submitted_at=COALESCE(submitted_at,?),
                          verified_at=COALESCE(verified_at,?),
                          updated_at=CURRENT_TIMESTAMP
                    WHERE application_id=? AND tenant_id=? AND user_id=?""",
                (
                    provider_application_id,
                    receipt_id,
                    submitted_at,
                    verified_at,
                    application_id,
                    owner.tenant_id,
                    owner.user_id,
                ),
            )
            return current_state

        if current_state not in {"SUBMITTED", "SUBMISSION_UNVERIFIED", "VERIFIED"}:
            raise ValueError(
                f"Verified receipt cannot reconcile lifecycle from {current_state}."
            )

        connection.execute(
            """UPDATE application_lifecycle_records
                  SET provider_application_id=COALESCE(provider_application_id,?),
                      verified_receipt_id=COALESCE(verified_receipt_id,?),
                      submitted_at=COALESCE(submitted_at,?),
                      verified_at=COALESCE(verified_at,?),
                      updated_at=CURRENT_TIMESTAMP
                WHERE application_id=? AND tenant_id=? AND user_id=?""",
            (
                provider_application_id,
                receipt_id,
                submitted_at,
                verified_at,
                application_id,
                owner.tenant_id,
                owner.user_id,
            ),
        )

        if current_state != "VERIFIED":
            _transition_with_connection(
                connection,
                owner=owner,
                application_id=application_id,
                to_state="VERIFIED",
                expected_state_version=int(row["state_version"]),
                source="verified_apply_receipt",
                idempotency_key=f"receipt:{receipt_id}:VERIFIED",
                reason="Independent Apply verification received.",
                provenance_json=provenance_json,
                occurred_at=verified_time,
            )

    row = _owned_record(connection, owner=owner, application_id=application_id)
    if row is None:
        raise RuntimeError("Lifecycle record disappeared during receipt reconciliation.")

    if str(row["state"]) == "VERIFIED":
        transition = _transition_with_connection(
            connection,
            owner=owner,
            application_id=application_id,
            to_state="AWAITING_RESPONSE",
            expected_state_version=int(row["state_version"]),
            source="verified_apply_receipt",
            idempotency_key=f"receipt:{receipt_id}:AWAITING_RESPONSE",
            reason="Verified submission is now awaiting employer response.",
            provenance_json=provenance_json,
            occurred_at=verified_time,
        )
        connection.execute(
            """UPDATE application_lifecycle_records
                  SET awaiting_response_at=COALESCE(awaiting_response_at,?)
                WHERE application_id=? AND tenant_id=? AND user_id=?""",
            (
                verified_at,
                application_id,
                owner.tenant_id,
                owner.user_id,
            ),
        )
        return str(transition["state"])

    return str(row["state"])


def application_crm_read_model(application_id: str) -> dict[str, Any]:
    application_id = _text(application_id, "application_id", 240)
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT
                   a.*,
                   j.company_name,
                   j.title AS job_title,
                   j.location_raw,
                   j.apply_url,
                   j.source AS job_source,
                   r.receipt_digest,
                   r.plan_id,
                   r.review_id,
                   r.approval_id,
                   r.resume_sha256,
                   r.cover_letter_sha256,
                   r.verification_status
               FROM application_lifecycle_records AS a
               JOIN jobs AS j ON j.id=a.job_id
               LEFT JOIN verified_submission_receipts_v1 AS r
                 ON r.receipt_id=a.verified_receipt_id
                AND r.tenant_id=a.tenant_id
                AND r.user_id=a.user_id
              WHERE a.application_id=? AND a.tenant_id=? AND a.user_id=?""",
            (application_id, owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Application CRM record was not found for this owner.")

        timeline = [
            dict(item)
            for item in connection.execute(
                """SELECT event_id,from_state,to_state,state_version,source,reason,
                          provenance_json,idempotency_key,occurred_at,created_at
                     FROM application_lifecycle_history_v1
                    WHERE tenant_id=? AND user_id=? AND application_id=?
                    ORDER BY state_version ASC,created_at ASC""",
                (owner.tenant_id, owner.user_id, application_id),
            ).fetchall()
        ]
        for item in timeline:
            item["provenance"] = json.loads(item.pop("provenance_json"))

        result = dict(row)
        result["timeline"] = timeline
        result["terminal"] = str(result["state"]) in TERMINAL_STATES
        result["next_allowed_states"] = sorted(
            ALLOWED_TRANSITIONS[str(result["state"])]
        )
        result["submission_authority"] = False
        return result
    finally:
        connection.close()


def list_application_crm(
    *,
    states: list[str] | tuple[str, ...] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if int(limit) < 1 or int(limit) > 500:
        raise ValueError("CRM limit must be between 1 and 500.")
    normalized_states = [_state(value) for value in (states or [])]

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        sql = """
            SELECT
                a.application_id,a.job_id,a.provider,a.provider_application_id,
                a.state,a.state_version,a.submitted_at,a.verified_at,
                a.awaiting_response_at,a.updated_at,
                j.company_name,j.title AS job_title,j.location_raw,j.apply_url
              FROM application_lifecycle_records AS a
              JOIN jobs AS j ON j.id=a.job_id
             WHERE a.tenant_id=? AND a.user_id=?
        """
        params: list[Any] = [owner.tenant_id, owner.user_id]
        if normalized_states:
            placeholders = ",".join("?" for _ in normalized_states)
            sql += f" AND a.state IN ({placeholders})"
            params.extend(normalized_states)
        sql += " ORDER BY a.updated_at DESC LIMIT ?"
        params.append(int(limit))
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()
