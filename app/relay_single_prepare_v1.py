"""One-click, exact-job Relay preparation consent with one durable dispatch claim.

An explicit Prepare & submit click authorizes one *future verified package* for
one new Prepare operation. It never grants global autonomous consent and cannot
retry an uncertain provider outcome. No tables, credentials or resumes are copied.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.database import get_connection
from app.tenant_foundation import current_owner_readonly

_PREFIX = "relay-one-click-prepare-v1-job-"
_LIFETIME = timedelta(hours=6)


def _now() -> datetime:
    return datetime.now(UTC)


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _key(job_id: int) -> str:
    resolved = int(job_id)
    if resolved < 1:
        raise ValueError("A valid exact job is required.")
    return _PREFIX + str(resolved)


def _load(con: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    owner = current_owner_readonly(con)
    row = con.execute(
        "SELECT value_json,user_confirmed FROM candidate_digital_twin_preferences "
        "WHERE tenant_id=? AND user_id=? AND preference_key=?",
        (owner.tenant_id, owner.user_id, _key(job_id)),
    ).fetchone()
    if not row or int(row["user_confirmed"]) != 1:
        return {}
    try:
        payload = json.loads(str(row["value_json"]))
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save(con: sqlite3.Connection, job_id: int, record: dict[str, Any]) -> None:
    owner = current_owner_readonly(con)
    con.execute(
        "INSERT INTO candidate_digital_twin_preferences "
        "(preference_id,tenant_id,user_id,preference_key,value_json,"
        "provenance,confidence,user_confirmed) VALUES (?,?,?,?,?,?,1,1) "
        "ON CONFLICT(tenant_id,user_id,preference_key) DO UPDATE SET "
        "value_json=excluded.value_json,user_confirmed=1,updated_at=CURRENT_TIMESTAMP",
        (str(uuid4()), owner.tenant_id, owner.user_id, _key(job_id),
         json.dumps(record, sort_keys=True, separators=(",", ":")),
         "EXPLICIT_ONE_CLICK_RELAY_PREPARE"),
    )


def _bindings(job_id: int) -> tuple[str, str]:
    from app.phase67_common import safe_owned_job_snapshot
    from app.phase45_truth_binding import current_candidate_profile_snapshot
    job = safe_owned_job_snapshot(job_id)
    profile = current_candidate_profile_snapshot()
    jd = str(job.get("job_snapshot_sha256") or "")
    candidate = str(profile.get("profile_digest") or "")
    if not _valid_digest(jd) or not _valid_digest(candidate):
        raise PermissionError("Exact job and Candidate Truth bindings are missing.")
    return jd, candidate


def preflight_job(job_id: int) -> None:
    """Fail before starting Prepare if Relay ownership would be unsafe."""
    from app.tsenta_backend_readiness_v1 import connection_status
    from app.application_provider_ui_v1 import verified_apply_destination
    if connection_status().get("execution_ready") is not True:
        raise PermissionError("MUNSHI Relay execution is not verified.")
    from app.product_application_flow_v2 import flow_for_job

    existing = flow_for_job(int(job_id), summary=True)
    if any((
        existing.get("state"), existing.get("prepare"),
        existing.get("lifecycle"), existing.get("provider_route"),
        existing.get("production_receipt"),
    )):
        raise PermissionError(
            "This job already has an application or provider record. "
            "Open its existing package instead of starting a new one."
        )
    con = get_connection()
    try:
        con.execute("PRAGMA query_only=ON")
        row = con.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if row is None:
            raise LookupError("The exact job is missing.")
        job = dict(row)
        if (
            job.get("primary_decision") != "ELIGIBLE"
            or job.get("status") != "found"
            or job.get("hard_rejection_reason")
            or int(job.get("already_applied") or 0)
            or int(job.get("cpt_trapdoor") or 0)
        ):
            raise PermissionError("This job is not eligible for Relay submission.")
        if not verified_apply_destination(job, con):
            raise PermissionError("A verified employer application link is required.")
        if _load(con, int(job_id)):
            raise PermissionError(
                "This job already has a one-click Relay record. "
                "Check its existing outcome instead of authorizing another attempt."
            )
    finally:
        con.close()
    _bindings(int(job_id))


def reserve_before_prepare(job_id: int) -> dict[str, Any]:
    """Persist an exclusive exact-job intent BEFORE starting any worker.

    The worker must not take the default Native route during the tiny window
    between durable Prepare creation and binding the operation identity.
    """
    job_id = int(job_id)
    preflight_job(job_id)
    job_sha, profile_sha = _bindings(job_id)
    current = _now()
    record = {
        "version": 1, "status": "PENDING_PREPARE",
        "authorization_id": "relay-prepare-" + uuid4().hex,
        "job_id": job_id, "operation_id": "",
        "job_snapshot_sha256": job_sha, "profile_digest": profile_sha,
        "authorized_at": current.isoformat(),
        "expires_at": (current + _LIFETIME).isoformat(),
    }
    con = get_connection()
    try:
        con.execute("BEGIN IMMEDIATE")
        if _load(con, job_id):
            raise PermissionError("One-click Relay consent already exists for this job.")
        _save(con, job_id, record)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {"status": "PENDING_PREPARE",
            "authorization_id": record["authorization_id"]}


def authorize_new_operation(job_id: int, operation_id: str) -> dict[str, Any]:
    """Attach a NEW durable Prepare to a pre-reserved exact-job intent."""
    from app.prepare_orchestrator_v1 import prepare_operation
    job_id = int(job_id)
    operation_id = str(operation_id or "").strip()
    if not operation_id:
        raise ValueError("Missing durable Prepare operation.")
    op = prepare_operation(operation_id)
    if int(op.get("job_id") or 0) != job_id or op.get("engine") != "NATIVE":
        raise PermissionError("Relay consent must bind to one new Native Prepare.")
    try:
        created = datetime.fromisoformat(str(op["created_at"]))
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
    except (KeyError, TypeError, ValueError) as error:
        raise PermissionError("Prepare creation time is missing.") from error
    age = (_now() - created).total_seconds()
    if age < -30 or age > 180:
        raise PermissionError("Only a newly created exact-job Prepare can be authorized.")
    job_sha, profile_sha = _bindings(job_id)
    con = get_connection()
    try:
        con.execute("BEGIN IMMEDIATE")
        record = _load(con, job_id)
        if (
            record.get("status") != "PENDING_PREPARE"
            or record.get("operation_id")
            or record.get("job_snapshot_sha256") != job_sha
            or record.get("profile_digest") != profile_sha
            or datetime.fromisoformat(str(record["expires_at"])) <= _now()
        ):
            raise PermissionError("No fresh, matching one-click Relay reservation.")
        record = {
            **record, "status": "REQUESTED", "operation_id": operation_id,
            "operation_created_at": str(op.get("created_at") or ""),
        }
        _save(con, job_id, record)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {"status": "REQUESTED", "operation_id": operation_id,
            "authorization_id": record["authorization_id"]}


def status(job_id: int) -> dict[str, str]:
    """Read-only display state; never creates authority from a UI rerender."""
    con = get_connection()
    try:
        con.execute("PRAGMA query_only=ON")
        record = _load(con, int(job_id))
        stage = str(record.get("status") or "NONE")
        if stage in {"REQUESTED", "PENDING_PREPARE"}:
            try:
                if datetime.fromisoformat(str(record["expires_at"])) <= _now():
                    stage = "EXPIRED"
            except (KeyError, TypeError, ValueError):
                stage = "INVALID"
        return {
            "status": stage,
            "operation_id": str(record.get("operation_id") or ""),
        }
    except (sqlite3.Error, TypeError, ValueError):
        return {"status": "UNAVAILABLE", "operation_id": ""}
    finally:
        con.close()


def validate_claimed_intent(
    *, job_id: int, operation_id: str, review_digest: str,
    preflight_package_id: str,
) -> bool:
    """Recheck an immutable one-time claim at the Relay dispatch boundary."""
    if not _valid_digest(review_digest) or not preflight_package_id:
        return False
    con = get_connection()
    try:
        con.execute("PRAGMA query_only=ON")
        record = _load(con, int(job_id))
        return bool(
            record.get("version") == 1
            and record.get("status") == "CLAIMED"
            and record.get("operation_id") == operation_id
            and record.get("review_digest") == review_digest
            and record.get("preflight_package_id") == preflight_package_id
            and datetime.fromisoformat(str(record["expires_at"])) > _now()
        )
    except (ValueError, TypeError, KeyError, sqlite3.Error):
        return False
    finally:
        con.close()


def maybe_dispatch(operation_id: str) -> dict[str, Any]:
    """Consume a one-use intent only when exact package and job are ready.

    The claim is committed BEFORE the outbound provider call. A crashed,
    timed-out or unknown provider response never authorizes another attempt.
    """
    from app.prepare_orchestrator_v1 import prepare_operation
    from app.product_application_flow_v2 import flow_for_job, approve_and_submit_for_job
    operation = prepare_operation(str(operation_id))
    job_id = int(operation["job_id"])
    con = get_connection()
    try:
        con.execute("PRAGMA query_only=ON")
        record = _load(con, job_id)
    finally:
        con.close()
    if not record:
        return {"authorized": False, "reason": "no_one_click_intent"}
    if record.get("status") != "REQUESTED":
        return {"authorized": False, "reason": "intent_already_claimed_or_expired"}
    if (
        record.get("operation_id") != str(operation_id)
        or record.get("operation_created_at") != operation.get("created_at")
        or operation.get("engine") != "NATIVE"
    ):
        return {"authorized": False, "reason": "not_the_consented_operation"}
    if datetime.fromisoformat(str(record["expires_at"])) <= _now():
        return {"authorized": False, "reason": "intent_expired"}
    flow = flow_for_job(job_id)
    review = dict(flow.get("package_review") or {})
    preflight = dict((flow.get("artifacts") or {}).get("preflight") or {})
    digest = str(review.get("review_digest") or "")
    package_id = str(preflight.get("preflight_package_id") or "")
    if (
        flow.get("state") != "READY_FOR_APPROVAL"
        or not _valid_digest(digest)
        or preflight.get("preflight_status") != "READY_TO_APPLY"
        or not package_id
        or str((flow.get("prepare") or {}).get("operation_id") or "") != str(operation_id)
    ):
        return {"authorized": False, "reason": "package_not_exactly_ready"}
    if _bindings(job_id) != (
        record["job_snapshot_sha256"], record["profile_digest"]
    ):
        return {"authorized": False, "reason": "job_or_profile_changed"}
    from app.tsenta_backend_readiness_v1 import connection_status
    if connection_status().get("execution_ready") is not True:
        return {"authorized": False, "reason": "relay_not_verified"}
    con = get_connection()
    try:
        con.execute("BEGIN IMMEDIATE")
        now_record = _load(con, job_id)
        if (
            now_record != record
            or now_record.get("status") != "REQUESTED"
            or datetime.fromisoformat(str(now_record["expires_at"])) <= _now()
        ):
            con.rollback()
            return {"authorized": False, "reason": "claim_already_taken"}
        claimed = {**now_record, "status": "CLAIMED",
                   "claimed_at": _now().isoformat(), "review_digest": digest,
                   "preflight_package_id": package_id}
        _save(con, job_id, claimed)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    # From here on, no automatic retry is allowed under any circumstances.
    try:
        result = approve_and_submit_for_job(
            job_id, execution_provider="TSENTA",
            approval_source="ONE_CLICK_PREPARE_RELAY",
            consent_slot=str(operation_id),
        )
    except Exception as error:
        return {"authorized": True, "status": "OUTCOME_NEEDS_RECONCILIATION",
                "error_type": type(error).__name__, "retry_allowed": False}
    return {"authorized": True, "status": str(result.get("status") or "UNKNOWN"),
            "retry_allowed": False}
