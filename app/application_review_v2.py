"""Hunter-owned Application Review V2 authority.

This module owns the immutable "WHAT WILL BE SUBMITTED" review snapshot and the
user approval binding for the Complete Application Loop.  It deliberately does
not execute a browser, upload a resume, submit an application, send email, or
grant submission authority.

Apply may prepare provider/form evidence, but Hunter remains the authority that:
- binds that evidence to the exact immutable Application Plan V2,
- masks protected/sensitive values from the review snapshot,
- creates a deterministic review digest,
- records an explicit approval against exact displayed digests,
- invalidates stale/superseded reviews, and
- exposes a single-use approval claim for a later submit-command tranche.

Consuming an approval is NOT a submit command and carries submission_authority
False.  The later submit-authority tranche must separately create and verify an
exactly-once submit command.
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from app import application_plan_v2
from app.database import get_connection
from app.phase67_common import sha256_json
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema

REVIEW_VERSION = "munshi-application-review-v2"
APPROVAL_VERSION = "munshi-application-review-approval-v2"
REVIEW_ENV = "MUNSHI_APPLICATION_REVIEW_V2_ENABLED"

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS application_reviews_v2 (
        review_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        review_version TEXT NOT NULL,
        plan_digest TEXT NOT NULL CHECK(length(plan_digest)=64),
        prepared_package_digest TEXT NOT NULL CHECK(length(prepared_package_digest)=64),
        browser_form_digest TEXT NOT NULL CHECK(length(browser_form_digest)=64),
        resume_artifact_sha256 TEXT NOT NULL CHECK(length(resume_artifact_sha256)=64),
        review_digest TEXT NOT NULL CHECK(length(review_digest)=64),
        review_json TEXT NOT NULL,
        invalidated_at TEXT,
        invalidation_reason TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,idempotency_key),
        UNIQUE(tenant_id,user_id,review_digest),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(plan_id) REFERENCES application_plans_v2(plan_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_application_reviews_v2_owner_application
       ON application_reviews_v2(tenant_id,user_id,application_id,created_at DESC);""",
    """CREATE TABLE IF NOT EXISTS application_review_approvals_v2 (
        approval_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        review_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        approval_version TEXT NOT NULL,
        review_digest TEXT NOT NULL CHECK(length(review_digest)=64),
        plan_digest TEXT NOT NULL CHECK(length(plan_digest)=64),
        prepared_package_digest TEXT NOT NULL CHECK(length(prepared_package_digest)=64),
        browser_form_digest TEXT NOT NULL CHECK(length(browser_form_digest)=64),
        approval_digest TEXT NOT NULL CHECK(length(approval_digest)=64),
        approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        consumed_at TEXT,
        consumption_key TEXT,
        UNIQUE(tenant_id,user_id,review_id),
        UNIQUE(tenant_id,user_id,idempotency_key),
        UNIQUE(tenant_id,user_id,approval_digest),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(review_id) REFERENCES application_reviews_v2(review_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_application_review_approvals_v2_review
       ON application_review_approvals_v2(review_id,approved_at DESC);""",
)


def application_review_v2_enabled() -> bool:
    return str(os.getenv(REVIEW_ENV) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        application_plan_v2.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _text(value: Any, label: str, maximum: int = 2000) -> str:
    result = " ".join(str(value or "").split())
    if not result or len(result) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return result


def _digest(value: Any, label: str) -> str:
    result = str(value or "").strip().lower()
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest.")
    return result


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a non-negative integer.")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a non-negative integer.") from error
    if result < 0:
        raise ValueError(f"{label} must be a non-negative integer.")
    return result


def _safe_review_fields(values: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("Prepared review fields must be a list.")
    result: list[dict[str, Any]] = []
    for raw in values:
        if not isinstance(raw, Mapping):
            raise ValueError("Each prepared review field must be an object.")
        sensitivity = str(raw.get("sensitivity_class") or "NORMAL").strip().upper()
        if sensitivity not in application_plan_v2.SENSITIVITY_CLASSES:
            raise ValueError("Prepared review field has an unsupported sensitivity class.")
        display = raw.get("display_value")
        if sensitivity != "NORMAL":
            display = "[protected or sensitive value]"
        elif display is None and raw.get("execution_value") is not None:
            display = raw.get("execution_value")
        result.append(
            {
                "question_key": str(raw.get("question_key") or "").strip() or None,
                "question_family": str(raw.get("question_family") or "").strip() or None,
                "display_value": copy.deepcopy(display),
                "sensitivity_class": sensitivity,
                "requires_review": bool(raw.get("requires_review")),
                "source": str(raw.get("source") or "").strip() or None,
            }
        )
    return result


def _plan_review_fields(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _safe_review_fields(
        [
            {
                "question_key": item.get("question_key"),
                "question_family": item.get("question_family"),
                "display_value": item.get("display_value"),
                "execution_value": item.get("execution_value"),
                "sensitivity_class": item.get("sensitivity_class") or "NORMAL",
                "requires_review": item.get("requires_review"),
                "source": item.get("source"),
            }
            for item in list(plan.get("answers") or [])
            if isinstance(item, Mapping)
        ]
    )


def _plan_for_review(plan_id: str) -> dict[str, Any]:
    plan = application_plan_v2.get_application_plan(str(plan_id))
    if plan.get("executable") is not True or str(plan.get("status")) != "READY_TO_APPLY":
        raise ValueError("Application Plan is not execution-ready for review.")
    if plan.get("submission_authority") is not False:
        raise ValueError("Application Plan unexpectedly carries submission authority.")
    snapshot = dict(plan.get("snapshot") or {})
    if (
        snapshot.get("executable") is not True
        or snapshot.get("submission_authority") is not False
        or str(snapshot.get("expected_state")) != "READY_TO_APPLY"
    ):
        raise ValueError("Application Plan snapshot is not READY_TO_APPLY.")
    freshness = application_plan_v2.application_plan_freshness(str(plan_id))
    if freshness.get("fresh") is not True:
        raise ValueError("Application Plan inputs are stale and cannot be reviewed.")
    return plan


def _normalized_prepared_package(
    *,
    plan_record: Mapping[str, Any],
    prepared_package: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(prepared_package, Mapping):
        raise ValueError("Prepared application package must be an object.")
    plan = dict(plan_record["snapshot"])
    application_id = _text(
        prepared_package.get("application_id"),
        "Prepared application id",
        240,
    )
    if application_id != str(plan_record["application_id"]):
        raise ValueError("Prepared package application does not match Application Plan.")

    expected_provider = str(plan_record["provider"]).strip().upper()
    provider = _text(prepared_package.get("provider"), "Prepared provider", 64).upper()
    if provider != expected_provider:
        raise ValueError("Prepared package provider does not match Application Plan.")

    form_digest = _digest(prepared_package.get("browser_form_digest"), "Browser form digest")
    resume_sha256 = _digest(prepared_package.get("resume_sha256"), "Prepared resume digest")
    expected_resume = _digest(
        dict(plan["resume"]).get("artifact_sha256"),
        "Application Plan resume digest",
    )
    if resume_sha256 != expected_resume:
        raise ValueError("Prepared resume does not match immutable Application Plan artifact.")

    if prepared_package.get("plan_digest") is not None:
        supplied_plan_digest = _digest(
            prepared_package.get("plan_digest"),
            "Prepared package plan digest",
        )
        if supplied_plan_digest != str(plan_record["plan_digest"]):
            raise ValueError("Prepared package plan digest does not match Application Plan.")

    required_fields = _non_negative_int(
        prepared_package.get("required_fields", 0),
        "Prepared required field count",
    )
    completed_required_fields = _non_negative_int(
        prepared_package.get("completed_required_fields", 0),
        "Prepared completed field count",
    )
    unresolved_count = _non_negative_int(
        prepared_package.get("unresolved_count", 0),
        "Prepared unresolved field count",
    )
    validation_errors = [
        str(value)
        for value in list(prepared_package.get("validation_errors") or [])
        if str(value).strip()
    ]
    if unresolved_count:
        raise ValueError("Prepared package still has unresolved application inputs.")
    if validation_errors:
        raise ValueError("Prepared package still has validation errors.")
    if completed_required_fields < required_fields:
        raise ValueError("Prepared package has incomplete required fields.")

    destination_url = _text(
        prepared_package.get("destination_url"),
        "Prepared destination URL",
        4000,
    )
    checkpoint_id = _text(
        prepared_package.get("checkpoint_id"),
        "Prepared checkpoint id",
        240,
    )
    review_fields = _safe_review_fields(prepared_package.get("review_fields"))
    if not review_fields:
        review_fields = _plan_review_fields(plan)

    return {
        "application_id": application_id,
        "provider": provider,
        "destination_url": destination_url,
        "browser_form_digest": form_digest,
        "resume_sha256": resume_sha256,
        "required_fields": required_fields,
        "completed_required_fields": completed_required_fields,
        "unresolved_count": unresolved_count,
        "validation_errors": [],
        "checkpoint_id": checkpoint_id,
        "review_fields": review_fields,
    }


def _review_result(
    connection: sqlite3.Connection,
    row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    data = dict(row)
    snapshot = json.loads(str(data.pop("review_json")))
    approval = connection.execute(
        """SELECT * FROM application_review_approvals_v2
           WHERE review_id=? AND tenant_id=? AND user_id=?""",
        (data["review_id"], data["tenant_id"], data["user_id"]),
    ).fetchone()
    return {
        **data,
        "review": snapshot,
        "approved": approval is not None,
        "approval_id": str(approval["approval_id"]) if approval is not None else None,
        "approval_digest": str(approval["approval_digest"]) if approval is not None else None,
        "approval_consumed": bool(approval is not None and approval["consumed_at"] is not None),
        "submission_authority": False,
    }


def get_review(review_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM application_reviews_v2
               WHERE review_id=? AND tenant_id=? AND user_id=?""",
            (str(review_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Application review is not owned by the current tenant user.")
        return _review_result(connection, row)
    finally:
        connection.close()


def create_review(
    *,
    plan_id: str,
    prepared_package: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if not application_review_v2_enabled():
        raise RuntimeError("Application Review V2 is disabled.")
    key = _text(idempotency_key, "Application review idempotency key", 240)
    plan_record = _plan_for_review(str(plan_id))
    plan = dict(plan_record["snapshot"])
    prepared = _normalized_prepared_package(
        plan_record=plan_record,
        prepared_package=prepared_package,
    )
    prepared_digest = sha256_json(prepared)

    resume = dict(plan["resume"])
    snapshot = {
        "version": REVIEW_VERSION,
        "application_id": str(plan_record["application_id"]),
        "plan_id": str(plan_record["plan_id"]),
        "job": copy.deepcopy(dict(plan["job"])),
        "provider": str(plan_record["provider"]),
        "destination_url": prepared["destination_url"],
        "resume": {
            "filename": resume.get("filename"),
            "version_id": resume.get("version_id"),
            "artifact_id": resume.get("artifact_id"),
            "sha256": resume.get("artifact_sha256"),
            "truth_status": "BOUND",
            "job_binding": copy.deepcopy(dict(resume.get("source_bindings", {}).get("job") or {})),
        },
        "application": {
            "required_fields": prepared["required_fields"],
            "completed_required_fields": prepared["completed_required_fields"],
            "unresolved": 0,
            "warnings": [],
        },
        "answers": copy.deepcopy(prepared["review_fields"]),
        "bindings": {
            "plan_digest": str(plan_record["plan_digest"]),
            "prepared_package_digest": prepared_digest,
            "browser_form_digest": prepared["browser_form_digest"],
            "resume_artifact_sha256": prepared["resume_sha256"],
            "checkpoint_id": prepared["checkpoint_id"],
        },
        "submission_readiness": "READY_FOR_REVIEW",
        "submission_authority": False,
    }
    review_digest = sha256_json(snapshot)
    review_id = "review-v2-" + review_digest[:32]

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")

        prior = connection.execute(
            """SELECT * FROM application_reviews_v2
               WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if prior is not None:
            if str(prior["review_digest"]) != review_digest:
                raise ValueError("Idempotency key belongs to a different application review.")
            connection.commit()
            return _review_result(connection, prior)

        same = connection.execute(
            """SELECT * FROM application_reviews_v2
               WHERE tenant_id=? AND user_id=? AND review_digest=?""",
            (owner.tenant_id, owner.user_id, review_digest),
        ).fetchone()
        if same is not None:
            connection.commit()
            return _review_result(connection, same)

        # A materially different review supersedes every older current review
        # for the same exact application/plan.  Existing approval records stay
        # immutable evidence, but an invalidated review can never be consumed.
        connection.execute(
            """UPDATE application_reviews_v2
               SET invalidated_at=COALESCE(invalidated_at,CURRENT_TIMESTAMP),
                   invalidation_reason=COALESCE(
                       invalidation_reason,'superseded_by_material_review_change'
                   )
               WHERE tenant_id=? AND user_id=? AND application_id=? AND plan_id=?
                 AND invalidated_at IS NULL AND review_digest<>?""",
            (
                owner.tenant_id,
                owner.user_id,
                str(plan_record["application_id"]),
                str(plan_record["plan_id"]),
                review_digest,
            ),
        )
        connection.execute(
            """INSERT INTO application_reviews_v2(
                   review_id,tenant_id,user_id,application_id,plan_id,idempotency_key,
                   review_version,plan_digest,prepared_package_digest,browser_form_digest,
                   resume_artifact_sha256,review_digest,review_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                review_id,
                owner.tenant_id,
                owner.user_id,
                str(plan_record["application_id"]),
                str(plan_record["plan_id"]),
                key,
                REVIEW_VERSION,
                str(plan_record["plan_digest"]),
                prepared_digest,
                prepared["browser_form_digest"],
                prepared["resume_sha256"],
                review_digest,
                json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM application_reviews_v2 WHERE review_id=?",
            (review_id,),
        ).fetchone()
        assert row is not None
        return _review_result(connection, row)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _invalidate(review_id: str, reason: str) -> None:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        connection.execute(
            """UPDATE application_reviews_v2
               SET invalidated_at=COALESCE(invalidated_at,CURRENT_TIMESTAMP),
                   invalidation_reason=COALESCE(invalidation_reason,?)
               WHERE review_id=? AND tenant_id=? AND user_id=?""",
            (reason, str(review_id), owner.tenant_id, owner.user_id),
        )
        connection.commit()
    finally:
        connection.close()


def revalidate_review(
    review_id: str,
    *,
    prepared_package: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not application_review_v2_enabled():
        raise RuntimeError("Application Review V2 is disabled.")
    review = get_review(review_id)
    if review["invalidated_at"] is not None:
        return {
            "review_id": str(review_id),
            "fresh": False,
            "reason": str(review["invalidation_reason"] or "review_invalidated"),
        }

    plan_id = str(review["plan_id"])
    freshness = application_plan_v2.application_plan_freshness(plan_id)
    if freshness.get("fresh") is not True:
        _invalidate(review_id, "application_plan_stale")
        return {"review_id": str(review_id), "fresh": False, "reason": "application_plan_stale"}

    plan_record = application_plan_v2.get_application_plan(plan_id)
    plan = dict(plan_record["snapshot"])
    if str(plan_record["plan_digest"]) != str(review["plan_digest"]):
        _invalidate(review_id, "application_plan_digest_changed")
        return {
            "review_id": str(review_id),
            "fresh": False,
            "reason": "application_plan_digest_changed",
        }
    current_resume = str(dict(plan["resume"]).get("artifact_sha256") or "")
    if current_resume != str(review["resume_artifact_sha256"]):
        _invalidate(review_id, "resume_artifact_changed")
        return {
            "review_id": str(review_id),
            "fresh": False,
            "reason": "resume_artifact_changed",
        }

    if prepared_package is not None:
        prepared = _normalized_prepared_package(
            plan_record=plan_record,
            prepared_package=prepared_package,
        )
        if prepared["browser_form_digest"] != str(review["browser_form_digest"]):
            _invalidate(review_id, "browser_form_changed")
            return {
                "review_id": str(review_id),
                "fresh": False,
                "reason": "browser_form_changed",
            }
        if sha256_json(prepared) != str(review["prepared_package_digest"]):
            _invalidate(review_id, "prepared_package_changed")
            return {
                "review_id": str(review_id),
                "fresh": False,
                "reason": "prepared_package_changed",
            }

    return {"review_id": str(review_id), "fresh": True, "reason": None}


def approve_review(
    *,
    review_id: str,
    idempotency_key: str,
    expected_review_digest: str,
    expected_prepared_package_digest: str,
    expected_browser_form_digest: str,
) -> dict[str, Any]:
    if not application_review_v2_enabled():
        raise RuntimeError("Application Review V2 is disabled.")
    key = _text(idempotency_key, "Application review approval idempotency key", 240)
    expected_review = _digest(expected_review_digest, "Expected review digest")
    expected_prepared = _digest(
        expected_prepared_package_digest,
        "Expected prepared package digest",
    )
    expected_form = _digest(expected_browser_form_digest, "Expected browser form digest")

    current = revalidate_review(review_id)
    if current.get("fresh") is not True:
        raise ValueError("Application review is stale and cannot be approved.")
    review = get_review(review_id)
    if (
        str(review["review_digest"]) != expected_review
        or str(review["prepared_package_digest"]) != expected_prepared
        or str(review["browser_form_digest"]) != expected_form
    ):
        raise ValueError("Approval does not match the exact displayed application review.")

    approval_material = {
        "version": APPROVAL_VERSION,
        "review_id": str(review_id),
        "review_digest": expected_review,
        "plan_digest": str(review["plan_digest"]),
        "prepared_package_digest": expected_prepared,
        "browser_form_digest": expected_form,
    }
    approval_digest = sha256_json(approval_material)

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")

        locked_review = connection.execute(
            """SELECT * FROM application_reviews_v2
               WHERE review_id=? AND tenant_id=? AND user_id=?""",
            (str(review_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if locked_review is None:
            raise LookupError("Application review is not owned by the current tenant user.")
        if locked_review["invalidated_at"] is not None:
            raise ValueError("Application review was invalidated before approval committed.")
        if (
            str(locked_review["review_digest"]) != expected_review
            or str(locked_review["prepared_package_digest"]) != expected_prepared
            or str(locked_review["browser_form_digest"]) != expected_form
        ):
            raise ValueError("Application review changed before approval committed.")

        prior_key = connection.execute(
            """SELECT * FROM application_review_approvals_v2
               WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if prior_key is not None:
            if (
                str(prior_key["review_id"]) != str(review_id)
                or str(prior_key["approval_digest"]) != approval_digest
            ):
                raise ValueError("Approval idempotency key belongs to a different review.")
            connection.commit()
            return {
                "approval_id": str(prior_key["approval_id"]),
                "review_id": str(review_id),
                "review_digest": expected_review,
                "approval_digest": approval_digest,
                "approved": True,
                "replayed": True,
                "consumed": prior_key["consumed_at"] is not None,
                "submission_authority": False,
            }

        prior_review = connection.execute(
            """SELECT * FROM application_review_approvals_v2
               WHERE tenant_id=? AND user_id=? AND review_id=?""",
            (owner.tenant_id, owner.user_id, str(review_id)),
        ).fetchone()
        if prior_review is not None:
            raise ValueError("Application review is already approved under another idempotency key.")

        approval_id = f"review-approval-{uuid4()}"
        connection.execute(
            """INSERT INTO application_review_approvals_v2(
                   approval_id,tenant_id,user_id,review_id,idempotency_key,
                   approval_version,review_digest,plan_digest,prepared_package_digest,
                   browser_form_digest,approval_digest
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                approval_id,
                owner.tenant_id,
                owner.user_id,
                str(review_id),
                key,
                APPROVAL_VERSION,
                expected_review,
                str(review["plan_digest"]),
                expected_prepared,
                expected_form,
                approval_digest,
            ),
        )
        connection.commit()
        return {
            "approval_id": approval_id,
            "review_id": str(review_id),
            "review_digest": expected_review,
            "approval_digest": approval_digest,
            "approved": True,
            "replayed": False,
            "consumed": False,
            "submission_authority": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def consume_approval(
    *,
    approval_id: str,
    consumption_key: str,
    expected_review_digest: str,
    expected_prepared_package_digest: str,
    expected_browser_form_digest: str,
) -> dict[str, Any]:
    """Atomically claim an approval once for a later submit-command tranche.

    This is not submit authority.  It only proves that exactly one downstream
    command identity may claim the current explicit user approval.
    """
    if not application_review_v2_enabled():
        raise RuntimeError("Application Review V2 is disabled.")
    key = _text(consumption_key, "Approval consumption key", 240)
    expected_review = _digest(expected_review_digest, "Expected review digest")
    expected_prepared = _digest(
        expected_prepared_package_digest,
        "Expected prepared package digest",
    )
    expected_form = _digest(expected_browser_form_digest, "Expected browser form digest")

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        approval = connection.execute(
            """SELECT a.*,r.invalidated_at,r.invalidation_reason
               FROM application_review_approvals_v2 AS a
               JOIN application_reviews_v2 AS r ON r.review_id=a.review_id
               WHERE a.approval_id=? AND a.tenant_id=? AND a.user_id=?""",
            (str(approval_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if approval is None:
            raise LookupError("Application review approval is not owned by the current tenant user.")
        if approval["invalidated_at"] is not None:
            raise ValueError("Application review approval belongs to an invalidated review.")
        if (
            str(approval["review_digest"]) != expected_review
            or str(approval["prepared_package_digest"]) != expected_prepared
            or str(approval["browser_form_digest"]) != expected_form
        ):
            raise ValueError("Approval consumption does not match exact review bindings.")
        review_id = str(approval["review_id"])
    finally:
        connection.close()

    current = revalidate_review(review_id)
    if current.get("fresh") is not True:
        raise ValueError("Application review approval is stale and cannot be consumed.")

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT a.*,r.invalidated_at
               FROM application_review_approvals_v2 AS a
               JOIN application_reviews_v2 AS r ON r.review_id=a.review_id
               WHERE a.approval_id=? AND a.tenant_id=? AND a.user_id=?""",
            (str(approval_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Application review approval is not owned by the current tenant user.")
        if row["invalidated_at"] is not None:
            raise ValueError("Application review approval was invalidated before consumption.")
        if (
            str(row["review_digest"]) != expected_review
            or str(row["prepared_package_digest"]) != expected_prepared
            or str(row["browser_form_digest"]) != expected_form
        ):
            raise ValueError("Approval bindings changed before consumption.")
        if row["consumed_at"] is not None:
            if str(row["consumption_key"] or "") != key:
                raise ValueError("Application review approval was already consumed by another command.")
            connection.commit()
            return {
                "approval_id": str(approval_id),
                "review_id": str(row["review_id"]),
                "consumed": True,
                "replayed": True,
                "consumption_key": key,
                "submission_authority": False,
            }
        connection.execute(
            """UPDATE application_review_approvals_v2
               SET consumed_at=CURRENT_TIMESTAMP,consumption_key=?
               WHERE approval_id=?""",
            (key, str(approval_id)),
        )
        connection.commit()
        return {
            "approval_id": str(approval_id),
            "review_id": str(row["review_id"]),
            "consumed": True,
            "replayed": False,
            "consumption_key": key,
            "submission_authority": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
