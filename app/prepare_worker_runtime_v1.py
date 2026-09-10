'''Feature-gated runtime dispatcher for durable Prepare Orchestrator tasks.

This process is preparation-only. It delegates task state transitions to the
existing adapters/orchestrator. It never grants final-review or submit authority.
'''
from __future__ import annotations

import json
import os
import socket
from typing import Any, Callable

from app import answer_brain_v2
from app import native_resume_service_v2
from app import prepare_orchestrator_v1 as orchestrator
from app import prepare_worker_adapters_v1a as v1a
from app import prepare_worker_adapters_v1b as v1b
from app import prepare_worker_adapters_v1c as v1c
from app.database import get_connection
from app.tenant_foundation import current_owner

RUNTIME_VERSION = "munshi-prepare-worker-runtime-v1"
SUBMISSION_AUTHORITY = False


def _worker_id(suffix: str) -> str:
    return f"prepare-runtime-v1:{socket.gethostname()}:{os.getpid()}:{suffix}"


def running_operation_ids(limit: int = 20) -> list[str]:
    bounded = max(1, min(int(limit), 100))
    connection = get_connection()
    try:
        owner = current_owner(connection)
        rows = connection.execute(
            '''SELECT operation_id
               FROM prepare_operations_v1
               WHERE tenant_id=? AND user_id=?
                 AND status IN ('QUEUED','RUNNING')
               ORDER BY created_at ASC
               LIMIT ?''',
            (owner.tenant_id, owner.user_id, bounded),
        ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        connection.close()


def _safe_call(
    label: str,
    function: Callable[..., dict[str, Any]],
    *,
    operation_id: str,
) -> dict[str, Any]:
    try:
        result = function(
            operation_id=operation_id,
            worker_id=_worker_id(label),
        )
        return {
            "step": label,
            "ok": True,
            "claimed": bool(result.get("claimed")),
            "reason": result.get("reason"),
            "task_status": result.get("task_status"),
            "operation_status": result.get("operation_status"),
        }
    except Exception as error:
        # Do not echo exception text because provider errors can contain secrets.
        return {
            "step": label,
            "ok": False,
            "error_type": type(error).__name__,
        }


def process_operation(operation_id: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    if v1a.workers_enabled():
        try:
            writer = native_resume_service_v2.writer_status()
        except Exception as error:
            writer = {
                "configured": False,
                "key_source": "status_error",
                "error_type": type(error).__name__,
            }

        if writer.get("configured") is True:
            steps.append(
                _safe_call(
                    "native-resume",
                    v1a.run_native_resume_once,
                    operation_id=operation_id,
                )
            )
        else:
            steps.append(
                {
                    "step": "native-resume",
                    "ok": True,
                    "claimed": False,
                    "reason": "writer_not_configured",
                    "key_source": writer.get("key_source"),
                }
            )

        steps.append(
            _safe_call(
                "recruiter-evidence",
                v1a.run_recruiter_evidence_once,
                operation_id=operation_id,
            )
        )

    if v1b.workers_enabled():
        steps.append(
            _safe_call(
                "cover-letter-decision",
                v1b.run_cover_letter_decision_once,
                operation_id=operation_id,
            )
        )
        if answer_brain_v2.application_answer_brain_enabled():
            steps.append(
                _safe_call(
                    "answer-resolution",
                    v1b.run_answer_resolution_once,
                    operation_id=operation_id,
                )
            )
        else:
            steps.append(
                {
                    "step": "answer-resolution",
                    "ok": True,
                    "claimed": False,
                    "reason": "answer_brain_disabled",
                }
            )

    if v1c.workers_enabled():
        steps.append(
            _safe_call(
                "application-preflight",
                v1c.run_application_preflight_once,
                operation_id=operation_id,
            )
        )
        if v1c.ats_runtime_enabled():
            steps.append(
                _safe_call(
                    "ats-form-preparation",
                    v1c.run_ats_form_preparation_once,
                    operation_id=operation_id,
                )
            )
        else:
            steps.append(
                {
                    "step": "ats-form-preparation",
                    "ok": True,
                    "claimed": False,
                    "reason": "ats_runtime_start_disabled",
                }
            )

    state = orchestrator.prepare_operation(operation_id)
    return {
        "operation_id": operation_id,
        "operation_status": state.get("status"),
        "steps": steps,
        "submission_authority": False,
        "automatic_submission_executed": False,
    }


def run_once(limit: int = 20) -> dict[str, Any]:
    operation_ids = running_operation_ids(limit=limit)
    return {
        "version": RUNTIME_VERSION,
        "operations_scanned": len(operation_ids),
        "operations": [process_operation(op) for op in operation_ids],
        "v1a_enabled": v1a.workers_enabled(),
        "v1b_enabled": v1b.workers_enabled(),
        "v1c_enabled": v1c.workers_enabled(),
        "ats_runtime_enabled": v1c.ats_runtime_enabled(),
        "submission_authority": False,
        "automatic_submission_executed": False,
    }


def main() -> int:
    print(json.dumps(run_once(), ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
