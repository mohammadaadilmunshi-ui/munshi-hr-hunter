"""Audited, pre-submit-only recovery actions for the Application Center."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app import prepare_orchestrator_v1 as orchestrator
from app import product_application_flow_v2 as application_flow
from app.application_view_model_v1 import IMMUTABLE_SUBMISSION_STATES, view_for_job
from app.database import get_connection

EVENT_TYPE = "application_recovery_action_v1"
ACTOR = "streamlit:application-recovery-v1"


def _audit(*, job_id: int, action: str, status: str, detail: dict[str, Any]) -> None:
    """Write value-free recovery evidence in its own short transaction."""
    payload = {
        "action": str(action),
        "status": str(status),
        "detail": dict(detail),
        "submission_authority": False,
    }
    connection = get_connection()
    try:
        connection.execute(
            """INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
               VALUES(?,?,?,?,?)""",
            (
                int(job_id),
                EVENT_TYPE,
                ACTOR,
                str(status),
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _pre_submit(job_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    flow = application_flow.flow_for_job(int(job_id))
    state = str(flow.get("state") or "")
    review = dict(flow.get("internal_integrity_review") or {})
    if (
        state in IMMUTABLE_SUBMISSION_STATES
        or flow.get("production_receipt")
        or review.get("approval_consumed")
    ):
        raise ValueError(
            "Recovery is blocked because submission authority or receipt evidence exists."
        )
    prepare = dict(flow.get("prepare") or {})
    if not prepare.get("operation_id"):
        raise ValueError("No preparation operation is available for recovery.")
    return flow, prepare


def recover_expired_lease(job_id: int) -> dict[str, Any]:
    _flow, prepare = _pre_submit(int(job_id))
    _audit(
        job_id=int(job_id),
        action="recover_expired_lease",
        status="requested",
        detail={"operation_id": prepare["operation_id"]},
    )
    result = orchestrator.recover_expired_leases(
        operation_id=str(prepare["operation_id"]),
        job_id=int(job_id),
        limit=20,
    )
    _audit(
        job_id=int(job_id),
        action="recover_expired_lease",
        status="completed",
        detail=result,
    )
    return {**result, "submission_authority": False}


def cancel_preparation(job_id: int, *, quarantine: bool = False) -> dict[str, Any]:
    _flow, prepare = _pre_submit(int(job_id))
    action = "quarantine_application" if quarantine else "cancel_preparation"
    _audit(
        job_id=int(job_id),
        action=action,
        status="requested",
        detail={"operation_id": prepare["operation_id"]},
    )
    result = orchestrator.cancel_prepare(
        operation_id=str(prepare["operation_id"]),
        reason_code=("APPLICATION_QUARANTINED" if quarantine else "CANCELLED_BY_USER"),
    )
    _audit(
        job_id=int(job_id),
        action=action,
        status="completed",
        detail={
            "operation_id": result["operation_id"],
            "cancelled_tasks": int(result.get("cancelled_tasks") or 0),
        },
    )
    return result


def retry_preparation(job_id: int) -> dict[str, Any]:
    flow, prepare = _pre_submit(int(job_id))
    if str(flow.get("state") or "") not in {"FAILED_SAFELY", "BLOCKED", "CANCELLED"}:
        raise ValueError("Preparation can only be retried after a safe terminal stop.")
    _audit(
        job_id=int(job_id),
        action="retry_preparation",
        status="requested",
        detail={"prior_operation_id": prepare["operation_id"]},
    )
    result = application_flow.start_prepare_for_job(
        int(job_id),
        engine=str(prepare.get("engine") or "NATIVE"),
    )
    _audit(
        job_id=int(job_id),
        action="retry_preparation",
        status="completed",
        detail={"replayed": bool(result.get("replayed"))},
    )
    return result


def restart_stale_preparation(job_id: int) -> dict[str, Any]:
    model = view_for_job(int(job_id))
    if not model.stale:
        raise ValueError("Preparation is not stale; refresh state before restarting it.")
    _flow, prepare = _pre_submit(int(job_id))
    _audit(
        job_id=int(job_id),
        action="restart_stale_preparation",
        status="requested",
        detail={"prior_operation_id": prepare["operation_id"]},
    )
    orchestrator.cancel_prepare(
        operation_id=str(prepare["operation_id"]),
        reason_code="STALE_PRE_SUBMIT_RESTART",
    )
    result = application_flow.start_prepare_for_job(
        int(job_id),
        engine=str(prepare.get("engine") or "NATIVE"),
    )
    _audit(
        job_id=int(job_id),
        action="restart_stale_preparation",
        status="completed",
        detail={"new_operation_created": not bool(result.get("replayed"))},
    )
    return result


_ACTIONS: dict[str, Callable[[int], dict[str, Any]]] = {
    "recover_expired_lease": recover_expired_lease,
    "cancel_preparation": cancel_preparation,
    "retry_preparation": retry_preparation,
    "restart_stale_preparation": restart_stale_preparation,
    "quarantine_application": lambda job_id: cancel_preparation(job_id, quarantine=True),
}


def execute(action: str, *, job_id: int) -> dict[str, Any]:
    handler = _ACTIONS.get(str(action))
    if handler is None:
        raise ValueError("Unsupported recovery action.")
    return handler(int(job_id))
