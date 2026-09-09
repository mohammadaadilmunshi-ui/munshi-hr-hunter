"""Verified Apply receipt ingestion and Hunter CRM/lifecycle authority.

This module consumes only the independently VERIFIED synthetic receipt contract
currently emitted by MUNSHI Apply. It does not execute browser actions, submit
applications, call n8n/Gmail, or grant submission authority.

A verified receipt is treated as evidence that submission has already occurred.
The first valid receipt creates immutable receipt evidence and advances the
Hunter-side CRM lifecycle through VERIFIED to AWAITING_RESPONSE. Exact replay is
idempotent; conflicting replay fails closed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app import application_submit_command_v1
from app.database import get_connection
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema

RECEIPT_VERSION = "munshi-synthetic-submission-receipt-v1"
VERIFICATION_STATUS = "VERIFIED"
VERIFICATION_METHOD = "SYNTHETIC_PROVIDER_LOOKUP"

LIFECYCLE_STATES = frozenset(
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

RECEIPT_FIELDS = frozenset(
    {
        "version",
        "command_id",
        "application_id",
        "plan_id",
        "session_id",
        "review_id",
        "approval_id",
        "provider",
        "provider_application_id",
        "submitted_at",
        "verified_at",
        "verification_method",
        "verification_attempt_id",
        "verification_evidence_digest",
        "plan_digest",
        "review_digest",
        "approval_digest",
        "prepared_package_digest",
        "browser_form_digest",
        "resume_sha256",
        "cover_letter_sha256",
        "answers_digest",
        "execution_result_digest",
        "execution_chain_digest",
        "verification_status",
    }
)

_DIGEST_FIELDS = (
    "verification_evidence_digest",
    "plan_digest",
    "review_digest",
    "approval_digest",
    "prepared_package_digest",
    "browser_form_digest",
    "resume_sha256",
    "answers_digest",
    "execution_result_digest",
    "execution_chain_digest",
)

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS verified_submission_receipts_v1 (
        receipt_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        command_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        review_id TEXT NOT NULL,
        approval_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        provider_application_id TEXT NOT NULL,
        submitted_at TEXT NOT NULL,
        verified_at TEXT NOT NULL,
        verification_method TEXT NOT NULL,
        verification_attempt_id TEXT NOT NULL,
        verification_evidence_digest TEXT NOT NULL CHECK(length(verification_evidence_digest)=64),
        plan_digest TEXT NOT NULL CHECK(length(plan_digest)=64),
        review_digest TEXT NOT NULL CHECK(length(review_digest)=64),
        approval_digest TEXT NOT NULL CHECK(length(approval_digest)=64),
        prepared_package_digest TEXT NOT NULL CHECK(length(prepared_package_digest)=64),
        browser_form_digest TEXT NOT NULL CHECK(length(browser_form_digest)=64),
        resume_sha256 TEXT NOT NULL CHECK(length(resume_sha256)=64),
        cover_letter_sha256 TEXT CHECK(cover_letter_sha256 IS NULL OR length(cover_letter_sha256)=64),
        answers_digest TEXT NOT NULL CHECK(length(answers_digest)=64),
        execution_result_digest TEXT NOT NULL CHECK(length(execution_result_digest)=64),
        execution_chain_digest TEXT NOT NULL CHECK(length(execution_chain_digest)=64),
        receipt_json TEXT NOT NULL,
        receipt_digest TEXT NOT NULL CHECK(length(receipt_digest)=64),
        verification_status TEXT NOT NULL CHECK(verification_status='VERIFIED'),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,command_id),
        UNIQUE(tenant_id,user_id,receipt_digest),
        UNIQUE(tenant_id,user_id,provider,provider_application_id),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(command_id)
          REFERENCES application_submit_commands_v1(command_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_verified_submission_receipts_owner_application
       ON verified_submission_receipts_v1(tenant_id,user_id,application_id,verified_at DESC);""",
    """CREATE TABLE IF NOT EXISTS application_lifecycle_records (
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
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(verified_receipt_id)
          REFERENCES verified_submission_receipts_v1(receipt_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_application_lifecycle_owner_state
       ON application_lifecycle_records(tenant_id,user_id,state,updated_at DESC);""",
    """CREATE TABLE IF NOT EXISTS application_lifecycle_events_v1 (
        event_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        state TEXT NOT NULL,
        replay_identity TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(state IN ('VERIFIED','AWAITING_RESPONSE')),
        UNIQUE(tenant_id,user_id,replay_identity),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(receipt_id)
          REFERENCES verified_submission_receipts_v1(receipt_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_application_lifecycle_events_application
       ON application_lifecycle_events_v1(tenant_id,user_id,application_id,occurred_at);""",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        application_submit_command_v1.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def receipt_digest(receipt: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(receipt).encode("utf-8")).hexdigest()


def expected_receipt_id(receipt: Mapping[str, Any]) -> str:
    return "synthetic-submission-receipt-" + receipt_digest(receipt)[:32]


def _text(value: Any, label: str, maximum: int = 4000) -> str:
    result = " ".join(str(value or "").split())
    if not result or len(result) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return result


def _digest(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if (
        len(result) != 64
        or result != result.lower()
        or any(character not in "0123456789abcdef" for character in result)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest.")
    return result


def _time(value: Any, label: str) -> datetime:
    text = _text(value, label, 100)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is invalid.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone.")
    return parsed.astimezone(UTC)


def _validate_receipt(
    *,
    receipt_id: Any,
    receipt_digest_value: Any,
    receipt: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(receipt, Mapping):
        raise ValueError("Verified submission receipt must be an object.")
    value = dict(receipt)
    unknown = sorted(set(value) - RECEIPT_FIELDS)
    missing = sorted(RECEIPT_FIELDS - set(value))
    if unknown or missing:
        raise ValueError(
            "Verified submission receipt fields do not match the V1 contract."
        )
    if value.get("version") != RECEIPT_VERSION:
        raise ValueError("Unsupported verified submission receipt version.")
    if value.get("verification_status") != VERIFICATION_STATUS:
        raise ValueError("Only independently VERIFIED receipts may enter Hunter CRM.")
    if value.get("verification_method") != VERIFICATION_METHOD:
        raise ValueError("Synthetic receipt verification method is unsupported.")

    for key in (
        "command_id",
        "application_id",
        "plan_id",
        "session_id",
        "review_id",
        "approval_id",
        "provider",
        "provider_application_id",
        "verification_attempt_id",
    ):
        value[key] = _text(value.get(key), key, 500)

    value["provider"] = value["provider"].upper()
    for key in _DIGEST_FIELDS:
        value[key] = _digest(value.get(key), key)
    if value.get("cover_letter_sha256") is not None:
        value["cover_letter_sha256"] = _digest(
            value.get("cover_letter_sha256"), "cover_letter_sha256"
        )

    submitted = _time(value.get("submitted_at"), "submitted_at")
    verified = _time(value.get("verified_at"), "verified_at")
    if verified < submitted:
        raise ValueError("verified_at cannot be earlier than submitted_at.")

    recomputed = receipt_digest(value)
    supplied_digest = _digest(receipt_digest_value, "Receipt digest")
    if not hmac.compare_digest(recomputed, supplied_digest):
        raise ValueError("Verified submission receipt digest mismatch.")

    expected_id = "synthetic-submission-receipt-" + recomputed[:32]
    supplied_id = _text(receipt_id, "Receipt id", 240)
    if supplied_id != expected_id:
        raise ValueError("Verified submission receipt id does not match its digest.")

    return supplied_id, recomputed, value


def _command_context(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    receipt: Mapping[str, Any],
) -> tuple[sqlite3.Row, dict[str, Any]]:
    row = connection.execute(
        """SELECT * FROM application_submit_commands_v1
           WHERE command_id=? AND tenant_id=? AND user_id=?""",
        (receipt["command_id"], owner.tenant_id, owner.user_id),
    ).fetchone()
    if row is None:
        raise PermissionError(
            "Verified receipt has no owned Hunter submit-command authority."
        )

    # Revalidate the immutable signed Hunter command before accepting Apply proof.
    stored = application_submit_command_v1._stored(row)
    envelope = dict(stored["envelope"])

    expected = {
        "application_id": str(row["application_id"]),
        "plan_id": str(row["plan_id"]),
        "review_id": str(row["review_id"]),
        "approval_id": str(row["approval_id"]),
        "provider": str(row["provider"]).upper(),
        "plan_digest": str(row["plan_digest"]),
        "review_digest": str(row["review_digest"]),
        "approval_digest": str(row["approval_digest"]),
        "prepared_package_digest": str(row["prepared_package_digest"]),
        "browser_form_digest": str(row["browser_form_digest"]),
        "resume_sha256": str(row["resume_sha256"]),
        "cover_letter_sha256": row["cover_letter_sha256"],
    }
    for key, expected_value in expected.items():
        actual = receipt.get(key)
        if key == "provider":
            actual = str(actual).upper()
        if actual != expected_value:
            raise ValueError(f"Verified receipt {key} binding does not match Hunter.")

    if str(envelope.get("session_id") or "") != str(receipt["session_id"]):
        raise ValueError("Verified receipt session_id binding does not match Hunter.")
    if envelope.get("synthetic") is not True:
        raise PermissionError("Receipt command is not synthetic.")
    if envelope.get("submission_authority") is not True:
        raise PermissionError("Receipt command did not carry explicit submit authority.")

    target = str(row["target_url"])
    parsed = urlparse(target)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.casefold().endswith(".invalid")
    ):
        raise PermissionError("Receipt command target is not a synthetic .invalid target.")

    fixture = connection.execute(
        """SELECT 1
             FROM staging_synthetic_jobs AS s
             JOIN jobs AS j ON j.id=s.job_id
            WHERE s.job_id=?
              AND s.environment='staging'
              AND s.synthetic=1
              AND s.is_test_data=1
              AND s.external_actions_disabled=1
              AND s.source='staging_fixture'
              AND j.apply_url=?""",
        (int(row["fixture_job_id"]), target),
    ).fetchone()
    if fixture is None:
        raise PermissionError("Receipt command no longer maps to the synthetic fixture.")

    return row, envelope


def _event_id(replay_identity: str) -> str:
    return (
        "hunter-lifecycle-event-"
        + hashlib.sha256(replay_identity.encode("utf-8")).hexdigest()[:32]
    )


def _insert_lifecycle_event(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    application_id: str,
    receipt_id: str,
    state: str,
    occurred_at: str,
    provider: str,
    provider_application_id: str,
) -> None:
    replay_identity = f"{receipt_id}:{state}"
    evidence = {
        "receipt_id": receipt_id,
        "verification_status": VERIFICATION_STATUS,
        "provider": provider,
        "provider_application_id": provider_application_id,
        "submission_authority": False,
    }
    connection.execute(
        """INSERT OR IGNORE INTO application_lifecycle_events_v1(
               event_id,tenant_id,user_id,application_id,receipt_id,state,
               replay_identity,evidence_json,occurred_at
           ) VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            _event_id(replay_identity),
            owner.tenant_id,
            owner.user_id,
            application_id,
            receipt_id,
            state,
            replay_identity,
            _canonical(evidence),
            occurred_at,
        ),
    )


def _lifecycle_after_receipt(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    command: sqlite3.Row,
    receipt_id: str,
    receipt: Mapping[str, Any],
) -> str:
    application_id = str(receipt["application_id"])
    row = connection.execute(
        """SELECT * FROM application_lifecycle_records
           WHERE application_id=? AND tenant_id=? AND user_id=?""",
        (application_id, owner.tenant_id, owner.user_id),
    ).fetchone()

    submitted_at = str(receipt["submitted_at"])
    verified_at = str(receipt["verified_at"])
    provider = str(receipt["provider"])
    provider_application_id = str(receipt["provider_application_id"])
    fixture_job_id = int(command["fixture_job_id"])

    if row is not None:
        prior_receipt = row["verified_receipt_id"]
        if prior_receipt not in (None, receipt_id):
            raise ValueError(
                "Application lifecycle is already bound to another verified receipt."
            )
        if str(row["provider"]) != provider:
            raise ValueError("Application lifecycle provider binding changed.")
        if row["provider_application_id"] not in (None, provider_application_id):
            raise ValueError("Application lifecycle provider identity changed.")

    _insert_lifecycle_event(
        connection,
        owner=owner,
        application_id=application_id,
        receipt_id=receipt_id,
        state="VERIFIED",
        occurred_at=verified_at,
        provider=provider,
        provider_application_id=provider_application_id,
    )
    _insert_lifecycle_event(
        connection,
        owner=owner,
        application_id=application_id,
        receipt_id=receipt_id,
        state="AWAITING_RESPONSE",
        occurred_at=verified_at,
        provider=provider,
        provider_application_id=provider_application_id,
    )

    if row is None:
        connection.execute(
            """INSERT INTO application_lifecycle_records(
                   application_id,tenant_id,user_id,job_id,provider,
                   provider_application_id,state,verified_receipt_id,state_version,
                   submitted_at,verified_at,awaiting_response_at
               ) VALUES (?,?,?,?,?,?,'AWAITING_RESPONSE',?,2,?,?,?)""",
            (
                application_id,
                owner.tenant_id,
                owner.user_id,
                fixture_job_id,
                provider,
                provider_application_id,
                receipt_id,
                submitted_at,
                verified_at,
                verified_at,
            ),
        )
        state = "AWAITING_RESPONSE"
    else:
        state = str(row["state"])
        # Never regress a CRM record that has already advanced beyond waiting.
        advanced = {"ASSESSMENT", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"}
        if state not in advanced:
            connection.execute(
                """UPDATE application_lifecycle_records
                      SET provider_application_id=?,
                          state='AWAITING_RESPONSE',
                          verified_receipt_id=?,
                          state_version=CASE
                              WHEN state='AWAITING_RESPONSE' THEN state_version
                              ELSE state_version+2
                          END,
                          submitted_at=COALESCE(submitted_at,?),
                          verified_at=COALESCE(verified_at,?),
                          awaiting_response_at=COALESCE(awaiting_response_at,?),
                          updated_at=CURRENT_TIMESTAMP
                    WHERE application_id=? AND tenant_id=? AND user_id=?""",
                (
                    provider_application_id,
                    receipt_id,
                    submitted_at,
                    verified_at,
                    verified_at,
                    application_id,
                    owner.tenant_id,
                    owner.user_id,
                ),
            )
            state = "AWAITING_RESPONSE"

    # Existing Hunter duplicate prevention already reads this flag.
    if (
        connection.execute(
            "UPDATE jobs SET already_applied=1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (fixture_job_id,),
        ).rowcount
        != 1
    ):
        raise RuntimeError("Verified receipt fixture job disappeared during CRM commit.")

    return state


def ingest_verified_receipt(
    *,
    receipt_id: str,
    receipt_digest_value: str,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Ingest/replay one independently VERIFIED Apply receipt.

    This records evidence after submission. It never authorizes or performs a
    submission itself.
    """
    normalized_id, normalized_digest, normalized = _validate_receipt(
        receipt_id=receipt_id,
        receipt_digest_value=receipt_digest_value,
        receipt=receipt,
    )

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")

        command, _ = _command_context(
            connection,
            owner=owner,
            receipt=normalized,
        )

        prior_by_id = connection.execute(
            """SELECT * FROM verified_submission_receipts_v1
               WHERE receipt_id=? AND tenant_id=? AND user_id=?""",
            (normalized_id, owner.tenant_id, owner.user_id),
        ).fetchone()
        prior_by_command = connection.execute(
            """SELECT * FROM verified_submission_receipts_v1
               WHERE command_id=? AND tenant_id=? AND user_id=?""",
            (normalized["command_id"], owner.tenant_id, owner.user_id),
        ).fetchone()

        for prior in (prior_by_id, prior_by_command):
            if prior is None:
                continue
            if (
                str(prior["receipt_id"]) != normalized_id
                or str(prior["receipt_digest"]) != normalized_digest
                or str(prior["receipt_json"]) != _canonical(normalized)
            ):
                raise ValueError(
                    "Conflicting verified receipt replay for the same authority."
                )
            lifecycle = connection.execute(
                """SELECT state FROM application_lifecycle_records
                   WHERE application_id=? AND tenant_id=? AND user_id=?""",
                (
                    normalized["application_id"],
                    owner.tenant_id,
                    owner.user_id,
                ),
            ).fetchone()
            connection.commit()
            return {
                "receipt_id": normalized_id,
                "receipt_digest": normalized_digest,
                "application_id": normalized["application_id"],
                "state": str(lifecycle["state"]) if lifecycle else "VERIFIED",
                "replayed": True,
                "verification_status": VERIFICATION_STATUS,
                "submission_authority": False,
                "crm_authority": True,
            }

        provider_collision = connection.execute(
            """SELECT receipt_id FROM verified_submission_receipts_v1
               WHERE tenant_id=? AND user_id=? AND provider=?
                 AND provider_application_id=?""",
            (
                owner.tenant_id,
                owner.user_id,
                normalized["provider"],
                normalized["provider_application_id"],
            ),
        ).fetchone()
        if provider_collision is not None:
            raise ValueError(
                "Provider application identity is already bound to another receipt."
            )

        connection.execute(
            """INSERT INTO verified_submission_receipts_v1(
                   receipt_id,tenant_id,user_id,command_id,application_id,plan_id,
                   session_id,review_id,approval_id,provider,provider_application_id,
                   submitted_at,verified_at,verification_method,
                   verification_attempt_id,verification_evidence_digest,
                   plan_digest,review_digest,approval_digest,
                   prepared_package_digest,browser_form_digest,resume_sha256,
                   cover_letter_sha256,answers_digest,execution_result_digest,
                   execution_chain_digest,receipt_json,receipt_digest,
                   verification_status
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                normalized_id,
                owner.tenant_id,
                owner.user_id,
                normalized["command_id"],
                normalized["application_id"],
                normalized["plan_id"],
                normalized["session_id"],
                normalized["review_id"],
                normalized["approval_id"],
                normalized["provider"],
                normalized["provider_application_id"],
                normalized["submitted_at"],
                normalized["verified_at"],
                normalized["verification_method"],
                normalized["verification_attempt_id"],
                normalized["verification_evidence_digest"],
                normalized["plan_digest"],
                normalized["review_digest"],
                normalized["approval_digest"],
                normalized["prepared_package_digest"],
                normalized["browser_form_digest"],
                normalized["resume_sha256"],
                normalized["cover_letter_sha256"],
                normalized["answers_digest"],
                normalized["execution_result_digest"],
                normalized["execution_chain_digest"],
                _canonical(normalized),
                normalized_digest,
                VERIFICATION_STATUS,
            ),
        )

        state = _lifecycle_after_receipt(
            connection,
            owner=owner,
            command=command,
            receipt_id=normalized_id,
            receipt=normalized,
        )
        connection.commit()
        return {
            "receipt_id": normalized_id,
            "receipt_digest": normalized_digest,
            "application_id": normalized["application_id"],
            "state": state,
            "replayed": False,
            "verification_status": VERIFICATION_STATUS,
            "submission_authority": False,
            "crm_authority": True,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_verified_receipt(receipt_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM verified_submission_receipts_v1
               WHERE receipt_id=? AND tenant_id=? AND user_id=?""",
            (str(receipt_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Verified submission receipt was not found for this owner.")
        result = dict(row)
        result["receipt"] = json.loads(result.pop("receipt_json"))
        result["submission_authority"] = False
        result["crm_authority"] = True
        return result
    finally:
        connection.close()


def application_lifecycle(application_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM application_lifecycle_records
               WHERE application_id=? AND tenant_id=? AND user_id=?""",
            (str(application_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Application lifecycle was not found for this owner.")
        return dict(row)
    finally:
        connection.close()
