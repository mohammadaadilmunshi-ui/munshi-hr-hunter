"""Owner-scoped, explicit continuation of an already accepted Apply checkpoint.

This deliberately DOES NOT start a Hunter preparation operation, create a new
Apply session, use local browser automation, or authorize employer submission.
All writes occur through the existing immutable-plan revision + signed handoff
path, after the owner explicitly requests continuation of the same job.
"""
from __future__ import annotations

import hashlib
from typing import Any

from app import application_plan_v2
from app import application_recovery_v1 as recovery
from app import prepare_worker_adapters_v1c as workers
from app.database import get_connection
from app.tenant_foundation import current_owner_readonly


def _required_identity(value: str, prefix: str) -> str:
    text = str(value or "").strip()
    if not text.startswith(prefix) or len(text) > 240 or any(c.isspace() for c in text):
        raise ValueError(f"Expected exact {prefix} identity.")
    return text


def _latest_accepted_plan(application_id: str) -> str:
    connection = get_connection()
    try:
        owner = current_owner_readonly(connection)
        row = connection.execute(
            """SELECT plan_id FROM apply_plan_handoffs_v2
               WHERE tenant_id=? AND user_id=? AND application_id=?
                 AND state='PLAN_ACCEPTED' AND accepted_at IS NOT NULL
               ORDER BY datetime(accepted_at) DESC, rowid DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id, application_id),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("No accepted Hunter-to-Apply plan for this application.")
    return str(row["plan_id"])


def inspect_existing_checkpoint(
    *, job_id: int, plan_id: str, session_id: str,
    prepare_job_id: str, checkpoint_id: str,
) -> dict[str, Any]:
    """Read all bindings afresh; fail closed if any part has moved."""
    plan_id = _required_identity(plan_id, "application-plan-")
    session_id = _required_identity(session_id, "apply-session-")
    prepare_job_id = _required_identity(prepare_job_id, "prepare-job-")
    checkpoint_id = _required_identity(checkpoint_id, "checkpoint-")
    flow, prepare = recovery._pre_submit(int(job_id))
    application_id = str(flow.get("application_id") or "")
    if not application_id or not flow.get("execution_mandate"):
        raise ValueError("An existing approved execution mandate is required.")
    if str(prepare.get("status") or "") not in {"NEEDS_INPUT", "FAILED_SAFELY"}:
        raise ValueError("Hunter preparation is not paused at a recoverable checkpoint.")
    if _latest_accepted_plan(application_id) != plan_id:
        raise ValueError("Accepted Application Plan has changed; refresh the recovery view.")

    plan = application_plan_v2.get_application_plan(plan_id)
    if (
        str(plan.get("application_id")) != application_id
        or int(plan.get("job_id") or 0) != int(job_id)
        or plan.get("executable") is not True
        or plan.get("submission_authority") is not False
        or application_plan_v2.runtime_plan_is_superseded(plan_id)
        or application_plan_v2.application_plan_freshness(plan_id).get("fresh") is not True
    ):
        raise ValueError("Accepted plan or candidate binding is no longer executable.")

    tenant_id = str(plan["tenant_id"])
    user_id = str(plan["user_id"])
    queued = workers._read_apply_prepare_job(
        job_id=prepare_job_id, tenant_id=tenant_id, user_id=user_id,
    )
    if any((
        str(queued.get("job_id")) != prepare_job_id,
        str(queued.get("session_id")) != session_id,
        str(queued.get("application_id")) != application_id,
        str(queued.get("plan_id")) != plan_id,
        str(queued.get("state")) != "WAITING_INPUT",
        bool(queued.get("cancel_requested_at")),
        queued.get("lease_owner") not in (None, ""),
    )):
        raise ValueError("Apply's original preparation job is not in WAITING_INPUT.")
    if int(queued.get("attempt_count") or 0) >= int(queued.get("max_attempts") or 0):
        raise ValueError("Apply preparation attempt budget is exhausted.")

    payload = workers._read_apply_resolution_tasks(
        job_id=prepare_job_id, tenant_id=tenant_id, user_id=user_id,
    )
    runtime_tasks, _keys = workers._validate_runtime_resolution_sync(
        payload,
        prepare_job_id=prepare_job_id,
        session_id=session_id,
        application_id=application_id,
        plan_id=plan_id,
    )
    if str(payload.get("checkpoint_id") or "") != checkpoint_id:
        raise ValueError("Workday browser checkpoint changed; refresh before continuing.")
    if not runtime_tasks:
        raise ValueError("No outstanding browser questions at this checkpoint.")
    if any(
        str(task.get("checkpoint_id") or "") != checkpoint_id
        or str(task.get("sensitivity_class") or "").upper() != "NORMAL"
        or task.get("requires_user") is True
        for task in runtime_tasks
    ):
        raise ValueError("A protected, human-only or mismatched checkpoint is unresolved.")

    # Only IDs and field classifications leave this preview; candidate values do not.
    return {
        "job_id": int(job_id),
        "application_id": application_id,
        "plan_id": plan_id,
        "plan_digest": str(plan["plan_digest"]),
        "session_id": session_id,
        "prepare_job_id": prepare_job_id,
        "checkpoint_id": checkpoint_id,
        "browser_form_digest": str(payload["browser_form_digest"]),
        "plan": plan,
        "runtime_tasks": runtime_tasks,
        "question_types": [str(task.get("semantic_type") or "") for task in runtime_tasks],
        "submission_authority": False,
    }


def continue_existing_checkpoint(
    *, job_id: int, plan_id: str, session_id: str,
    prepare_job_id: str, checkpoint_id: str,
) -> dict[str, Any]:
    """User-invoked normal-answer recovery via MUNSHI's signed plan APIs."""
    context = inspect_existing_checkpoint(
        job_id=job_id, plan_id=plan_id, session_id=session_id,
        prepare_job_id=prepare_job_id, checkpoint_id=checkpoint_id,
    )
    # Stable retry identity. Duplicate clicks may only replay this same digest
    # and the underlying immutable revision; they cannot start a new session.
    key_material = "\x1f".join(
        [context["application_id"], plan_id, session_id,
         checkpoint_id, context["browser_form_digest"]]
    )
    operation_id = "resume-existing-" + hashlib.sha256(
        key_material.encode("utf-8")
    ).hexdigest()[:32]
    resolved = workers._universal_runtime_auto_resolution(
        operation_id=operation_id,
        plan=context["plan"],
        session_id=session_id,
        resolution_payload={
            "checkpoint_id": checkpoint_id,
            "browser_form_digest": context["browser_form_digest"],
        },
        runtime_tasks=context["runtime_tasks"],
    )
    if resolved is None:
        raise ValueError(
            "Existing checkpoint cannot be fully resolved from approved NORMAL answers."
        )
    replacement = dict(resolved.get("replacement") or {})
    if (
        replacement.get("success") is not True
        or replacement.get("state") != "PLAN_ACCEPTED"
        or replacement.get("submission_authority") is not False
        or str(replacement.get("prior_plan_id") or "") != plan_id
    ):
        raise RuntimeError("Exact signed replacement-plan acknowledgement is missing.")
    return {
        "application_id": context["application_id"],
        "session_id": session_id,
        "prepare_job_id": prepare_job_id,
        "prior_plan_id": plan_id,
        "replacement_plan_id": str(replacement["replacement_plan_id"]),
        "replacement_state": "PLAN_ACCEPTED",
        "resolved_count": int(resolved["resolved_count"]),
        "submission_authority": False,
        "new_application_created": False,
        "resume_regenerated": False,
    }
