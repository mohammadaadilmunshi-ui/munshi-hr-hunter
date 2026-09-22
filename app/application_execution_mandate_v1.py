"""Single customer package approval before unattended ATS execution.

This is the only user-facing approval boundary for canonical AutoApply.

The mandate is created from the immutable preflight package before a browser
session starts.  Browser/form snapshots created later are internal integrity
evidence only; they may derive submit authority from this mandate but must never
ask the customer to approve again.
"""
from __future__ import annotations

import copy
import json
import sqlite3
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from app import application_plan_v2
from app import application_preflight_package_v1 as preflight_v1
from app.database import get_connection
from app.phase67_common import sha256_json
from app.tenant_foundation import (
    current_owner,
    current_owner_readonly,
    ensure_schema as ensure_tenant_schema,
)

MANDATE_VERSION = "munshi-application-execution-mandate-v1"

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS application_execution_mandates_v1(
        mandate_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        preflight_package_id TEXT NOT NULL,
        preflight_package_digest TEXT NOT NULL CHECK(length(preflight_package_digest)=64),
        resume_version_id TEXT NOT NULL,
        resume_sha256 TEXT NOT NULL CHECK(length(resume_sha256)=64),
        cover_letter_decision TEXT NOT NULL,
        cover_letter_id TEXT,
        cover_letter_sha256 TEXT,
        review_digest TEXT NOT NULL CHECK(length(review_digest)=64),
        review_json TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        revoked_at TEXT,
        revocation_reason TEXT,
        UNIQUE(tenant_id,user_id,idempotency_key),
        UNIQUE(tenant_id,user_id,review_digest),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_execution_mandate_preflight
       ON application_execution_mandates_v1(
          tenant_id,user_id,preflight_package_id,approved_at DESC
       );""",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        preflight_v1.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _text(value: Any, label: str, maximum: int = 4000) -> str:
    result = " ".join(str(value or "").split())
    if not result or len(result) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return result


def _digest(value: Any, label: str) -> str:
    result = str(value or "").strip().lower()
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest.")
    return result


def package_review(
    *,
    preflight_package_id: str,
    cover_letter_decision: str,
    cover_letter: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the exact candidate-facing package review without mutating state."""
    package = preflight_v1.get_preflight_package(str(preflight_package_id))
    freshness = preflight_v1.preflight_package_freshness(str(preflight_package_id))
    if str(package.get("status") or "") != "READY_TO_APPLY":
        raise ValueError("Preflight package is not ready for the single customer approval.")
    if freshness.get("fresh") is not True:
        raise ValueError("Preflight package is stale and cannot be approved.")

    snapshot = dict(package["snapshot"])
    job = dict(snapshot["job"])
    resume = dict(snapshot["resume"])
    truth = dict(snapshot["application_truth"])
    answer_inventory = dict(snapshot["answer_inventory"])
    decision = _text(cover_letter_decision, "Cover-letter decision", 40).upper()

    cover: dict[str, Any] | None = None
    if cover_letter is not None:
        cover = {
            "cover_letter_id": _text(
                cover_letter.get("cover_letter_id"), "Cover-letter id", 240
            ),
            "artifact_sha256": _digest(
                cover_letter.get("artifact_sha256"), "Cover-letter digest"
            ),
            "page_count": int(cover_letter.get("page_count") or 0),
        }
        if cover["page_count"] != 1:
            raise ValueError("Reviewed cover letter must be exactly one page.")

    reviewed = {
        "version": MANDATE_VERSION,
        "application_id": str(package["application_id"]),
        "job_id": int(package["job_id"]),
        "preflight_package_id": str(package["package_id"]),
        "preflight_package_digest": str(package["package_digest"]),
        "job": {
            key: job.get(key)
            for key in (
                "id",
                "company_name",
                "title",
                "location_raw",
                "job_url",
                "apply_url",
            )
        },
        "resume": {
            "version_id": str(package["resume_version_id"]),
            "sha256": str(package["resume_sha256"]),
        },
        "cover_letter": {
            "decision": decision,
            "artifact": cover,
        },
        # The user approves the policy and current reusable-answer inventory once;
        # live normal ATS questions remain autonomous after this point.
        "application_answers": {
            "candidate_truth_binding": copy.deepcopy(
                answer_inventory.get("candidate_truth_binding")
            ),
            "reusable_answer_metadata": copy.deepcopy(
                list(answer_inventory.get("answers") or [])
            ),
            "excluded_answer_metadata": copy.deepcopy(
                list(answer_inventory.get("excluded_answers") or [])
            ),
        },
        "application_truth": {
            "projection_digest": str(truth.get("projection_digest") or ""),
            "protected_fact_keys": sorted(
                str(value) for value in truth.get("protected_fact_keys") or []
            ),
            "unresolved_fact_keys": sorted(
                str(value) for value in truth.get("unresolved_fact_keys") or []
            ),
        },
        "execution_policy": {
            "normal_runtime_questions": "AUTONOMOUS_FROM_CURRENT_TRUTH_OR_POLICY",
            "account_creation": "AUTONOMOUS_WHEN_PROVIDER_REQUIRES",
            "email_verification": "AUTONOMOUS_WHEN_ORDINARY_EMAIL_CONTROL",
            "protected_human_checkpoints": [
                "CAPTCHA",
                "SMS_MFA",
                "PASSKEY",
                "BIOMETRIC",
                "IDENTITY_VERIFICATION",
            ],
            "post_approval_user_review": False,
            "post_approval_user_submit_prompt": False,
            "final_submit": "AUTOMATIC_AFTER_INTERNAL_INTEGRITY_AND_ONE_USE_AUTHORITY",
        },
        "submission_authority": False,
    }
    reviewed["review_digest"] = sha256_json(reviewed)
    return reviewed


def approve_package(
    *,
    preflight_package_id: str,
    cover_letter_decision: str,
    expected_review_digest: str,
    idempotency_key: str,
    cover_letter: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Record the one and only customer Approve & Submit decision."""
    key = _text(idempotency_key, "Execution mandate idempotency key", 240)
    expected = _digest(expected_review_digest, "Expected package review digest")
    review = package_review(
        preflight_package_id=preflight_package_id,
        cover_letter_decision=cover_letter_decision,
        cover_letter=cover_letter,
    )
    if str(review["review_digest"]) != expected:
        raise ValueError("Displayed package review changed before approval.")

    mandate_id = "execution-mandate-" + expected[:32]
    cover = review["cover_letter"]["artifact"]
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            """SELECT * FROM application_execution_mandates_v1
               WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if prior is not None:
            if str(prior["review_digest"]) != expected:
                raise ValueError(
                    "Execution mandate idempotency key belongs to another package review."
                )
            connection.commit()
            return _row(prior)

        connection.execute(
            """INSERT INTO application_execution_mandates_v1(
                   mandate_id,tenant_id,user_id,application_id,job_id,
                   preflight_package_id,preflight_package_digest,
                   resume_version_id,resume_sha256,cover_letter_decision,
                   cover_letter_id,cover_letter_sha256,review_digest,review_json,
                   idempotency_key
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mandate_id,
                owner.tenant_id,
                owner.user_id,
                review["application_id"],
                int(review["job_id"]),
                review["preflight_package_id"],
                review["preflight_package_digest"],
                review["resume"]["version_id"],
                review["resume"]["sha256"],
                review["cover_letter"]["decision"],
                cover["cover_letter_id"] if cover else None,
                cover["artifact_sha256"] if cover else None,
                expected,
                json.dumps(
                    review, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ),
                key,
            ),
        )
        row = connection.execute(
            "SELECT * FROM application_execution_mandates_v1 WHERE mandate_id=?",
            (mandate_id,),
        ).fetchone()
        connection.commit()
        assert row is not None
        return _row(row)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["review"] = json.loads(str(data.pop("review_json")))
    data["approved"] = data.get("revoked_at") is None
    data["submission_authority"] = False
    return data


def current_for_preflight(preflight_package_id: str) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        owner = current_owner_readonly(connection)
        row = connection.execute(
            """SELECT * FROM application_execution_mandates_v1
               WHERE tenant_id=? AND user_id=? AND preflight_package_id=?
                 AND revoked_at IS NULL
               ORDER BY datetime(approved_at) DESC,rowid DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id, str(preflight_package_id)),
        ).fetchone()
        if row is None:
            return None
        result = _row(row)
        # Current package freshness is revalidated on every use.
        review = package_review(
            preflight_package_id=str(preflight_package_id),
            cover_letter_decision=str(result["cover_letter_decision"]),
            cover_letter=(
                {
                    "cover_letter_id": result["cover_letter_id"],
                    "artifact_sha256": result["cover_letter_sha256"],
                    "page_count": (
                        (result["review"].get("cover_letter") or {})
                        .get("artifact", {})
                        .get("page_count")
                    ),
                }
                if result.get("cover_letter_id")
                else None
            ),
        )
        if str(review["review_digest"]) != str(result["review_digest"]):
            return None
        return result
    finally:
        connection.close()


def mandate_authorizes_internal_review(
    *,
    mandate_id: str,
    review_record: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate that a later browser snapshot is within the earlier user mandate."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM application_execution_mandates_v1
               WHERE tenant_id=? AND user_id=? AND mandate_id=? AND revoked_at IS NULL""",
            (owner.tenant_id, owner.user_id, str(mandate_id)),
        ).fetchone()
        if row is None:
            raise PermissionError("Current execution mandate was not found.")
        mandate = _row(row)
    finally:
        connection.close()

    review = dict(review_record.get("review") or {})
    destination = str(review.get("destination_url") or "")
    package_job = dict(mandate["review"]["job"])
    approved_destination = str(
        package_job.get("apply_url") or package_job.get("job_url") or ""
    )
    plan_id = str(review_record.get("plan_id") or "")
    plan = application_plan_v2.get_application_plan(plan_id)
    plan_snapshot = dict(plan.get("snapshot") or {})
    plan_job = dict(plan_snapshot.get("job") or {})
    provider_policy = dict(plan_snapshot.get("provider_policy") or {})
    allowed_hosts = {
        str(value).strip().casefold()
        for value in provider_policy.get("allowed_hosts") or []
        if str(value).strip()
    }
    approved_host = str(urlsplit(approved_destination).hostname or "").casefold()
    destination_host = str(urlsplit(destination).hostname or "").casefold()
    destination_allowed = bool(
        destination_host
        and (
            destination_host == approved_host
            or destination_host in allowed_hosts
        )
    )
    if (
        str(review_record.get("application_id") or "")
        != str(mandate["application_id"])
        or int(plan.get("job_id") or 0) != int(mandate["job_id"])
        or str(plan_job.get("id") or "") != str(package_job.get("id") or "")
        or str(review_record.get("resume_artifact_sha256") or "")
        != str(mandate["resume_sha256"])
        or not destination_allowed
    ):
        raise PermissionError(
            "Internal browser review exceeds the approved execution mandate."
        )

    reviewed_cover = (review.get("cover_letter") or {})
    mandate_cover = (mandate["review"].get("cover_letter") or {}).get("artifact")
    if reviewed_cover:
        if not mandate_cover or str(reviewed_cover.get("sha256") or "") != str(
            mandate_cover.get("artifact_sha256") or ""
        ):
            raise PermissionError(
                "Browser preparation introduced an unapproved cover-letter artifact."
            )
    return mandate
