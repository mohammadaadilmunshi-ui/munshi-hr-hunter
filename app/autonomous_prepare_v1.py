"""Autonomous admission and execution for the durable Prepare lifecycle.

This module is the ownership boundary between legacy n8n dispatch selection and
Prepare. Automatic eligibility is still decided by ``n8n_dispatch.plan_candidates``;
auto candidates no longer need a legacy n8n queue row to become visible in the
product pipeline. Manual n8n requests remain on the existing queue.

Safety invariants:
- no submission authority is created here;
- automatic admission is idempotent per job through product_application_flow_v2;
- existing Prepare operations are never duplicated;
- daily automation limits count both legacy auto queue rows and new durable
  admissions during the migration window;
- N8N Prepare operations fail closed into NEEDS_INPUT/ISSUE until their result
  carries the immutable Candidate Truth + job/resume binding required by the
  Phase 9 preflight contract. Legacy URL-only callbacks are never promoted to a
  trusted resume artifact.
"""
from __future__ import annotations

import json
from typing import Any

from app import prepare_orchestrator_v1 as orchestrator
from app import product_application_flow_v2 as product_flow
from app.database import get_connection
from app.n8n_dispatch import (
    _begin_dispatch_transaction,
    ensure_schema as ensure_n8n_schema,
    insert_queue_item,
    plan_candidates,
)
from app.tenant_foundation import current_owner

AUTONOMOUS_EVENT = "autonomous_prepare_admitted"
AUTONOMOUS_ACTOR = "autonomous_prepare_v1"
WORKER_ID_PREFIX = "autonomous-prepare-v1"


def _table_exists(connection: Any, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (str(table),),
        ).fetchone()
        is not None
    )


def _existing_prepare_job_ids(connection: Any) -> set[int]:
    if not _table_exists(connection, "prepare_operations_v1"):
        return set()
    owner = current_owner(connection)
    rows = connection.execute(
        """SELECT DISTINCT job_id
             FROM prepare_operations_v1
            WHERE tenant_id=? AND user_id=?""",
        (owner.tenant_id, owner.user_id),
    ).fetchall()
    return {int(row[0]) for row in rows}


def _admissions_today(connection: Any) -> int:
    row = connection.execute(
        """SELECT COUNT(*)
             FROM events
            WHERE event_type=?
              AND event_status='admitted'
              AND date(created_at,'localtime')=date('now','localtime')""",
        (AUTONOMOUS_EVENT,),
    ).fetchone()
    return int(row[0] if row else 0)


def _record_event(
    connection: Any,
    *,
    job_id: int,
    status: str,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        """INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
           VALUES(?,?,?,?,?)""",
        (
            int(job_id),
            AUTONOMOUS_EVENT,
            AUTONOMOUS_ACTOR,
            str(status),
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        ),
    )
    connection.commit()


def _configured_engine(connection: Any) -> str:
    row = connection.execute(
        "SELECT value_json FROM settings WHERE setting_key='product_automation_policy_v1'"
    ).fetchone()
    policy: dict[str, Any] = {}
    if row is not None:
        try:
            raw = json.loads(str(row[0] or "{}"))
            if isinstance(raw, dict):
                policy = raw
        except json.JSONDecodeError:
            policy = {}
    engine = str(policy.get("prepare_engine") or "NATIVE").strip().upper()
    if engine not in {"NATIVE", "N8N"}:
        raise RuntimeError("Product automation policy has an invalid prepare engine.")
    return engine


def queue_manual_candidates(*, webhook_mode: str, dry_run: bool) -> dict[str, Any]:
    """Queue only explicit/manual n8n work while auto work moves to Prepare."""
    connection = get_connection()
    try:
        ensure_n8n_schema(connection)
        plan = plan_candidates(connection)
        selected = list(plan["manual_candidates"])
        queued: list[dict[str, Any]] = []
        if dry_run:
            queued = [
                {
                    "job_id": int(job["id"]),
                    "company": job["company_name"],
                    "title": job["title"],
                    "hunter_score": job["hunter_score"],
                    "dispatch_mode": "telegram_manual",
                    "queue_id": None,
                    "queue_status": "dry_run",
                }
                for job in selected
            ]
        else:
            _begin_dispatch_transaction(connection)
            for job in selected:
                row = insert_queue_item(
                    connection,
                    job,
                    "telegram_manual",
                    webhook_mode,
                )
                queued.append(
                    {
                        "job_id": int(job["id"]),
                        "company": job["company_name"],
                        "title": job["title"],
                        "hunter_score": job["hunter_score"],
                        "dispatch_mode": "telegram_manual",
                        "queue_id": row["id"],
                        "queue_status": row["queue_status"],
                    }
                )
            connection.commit()
        return {
            "success": True,
            "dry_run": bool(dry_run),
            "webhook_mode": webhook_mode,
            "threshold": plan["threshold"],
            "auto_limit": plan["auto_limit"],
            "manual_limit": plan["manual_limit"],
            "auto_used_today": plan["auto_used_today"],
            "manual_used_today": plan["manual_used_today"],
            "selected_count": len(selected),
            "queued": queued,
            "n8n_calls": 0,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def admit_auto_candidates(*, dry_run: bool) -> dict[str, Any]:
    """Admit currently eligible auto candidates into the durable Prepare graph."""
    connection = get_connection()
    try:
        ensure_n8n_schema(connection)
        orchestrator.ensure_schema()
        plan = plan_candidates(connection)
        engine = _configured_engine(connection)
        existing = _existing_prepare_job_ids(connection)
        candidates = [
            job for job in plan["auto_candidates"] if int(job["id"]) not in existing
        ]

        durable_used = _admissions_today(connection)
        auto_limit = plan["auto_limit"]
        if auto_limit is not None:
            remaining = max(
                0,
                int(auto_limit) - int(plan["auto_used_today"]) - durable_used,
            )
            candidates = candidates[:remaining]

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "engine": engine,
                "legacy_auto_used_today": int(plan["auto_used_today"]),
                "durable_auto_used_today": durable_used,
                "candidate_count": len(candidates),
                "admitted": [
                    {
                        "job_id": int(job["id"]),
                        "company": job["company_name"],
                        "title": job["title"],
                        "engine": engine,
                        "state": "OPTIMIZING",
                    }
                    for job in candidates
                ],
                "errors": [],
            }

        admitted: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for job in candidates:
            job_id = int(job["id"])
            try:
                result = product_flow.start_prepare_for_job(job_id, engine=engine)
                if not bool(result.get("replayed")):
                    _record_event(
                        connection,
                        job_id=job_id,
                        status="admitted",
                        payload={
                            "engine": engine,
                            "prepare_state": result.get("state"),
                            "product_stage": product_stage_for_state(
                                str(result.get("state") or "")
                            ),
                            "submission_authority": False,
                        },
                    )
                admitted.append(
                    {
                        "job_id": job_id,
                        "company": job["company_name"],
                        "title": job["title"],
                        "engine": engine,
                        "replayed": bool(result.get("replayed")),
                        "prepare_state": result.get("state"),
                        "product_stage": product_stage_for_state(
                            str(result.get("state") or "")
                        ),
                    }
                )
            except Exception as error:
                _record_event(
                    connection,
                    job_id=job_id,
                    status="failed",
                    payload={
                        "engine": engine,
                        "error_type": type(error).__name__,
                        "error": str(error)[:1000],
                        "submission_authority": False,
                    },
                )
                errors.append(
                    {
                        "job_id": job_id,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
        return {
            "success": not bool(errors),
            "dry_run": False,
            "engine": engine,
            "legacy_auto_used_today": int(plan["auto_used_today"]),
            "durable_auto_used_today": durable_used,
            "candidate_count": len(candidates),
            "admitted": admitted,
            "errors": errors,
        }
    finally:
        connection.close()


def product_stage_for_state(state: str) -> str:
    resolved = str(state or "").upper()
    if resolved in {"QUEUED", "RUNNING", "PREPARING", "PREPARING_ATS", "PLAN_ACCEPTED"}:
        return "OPTIMIZING"
    if resolved in {"NEEDS_INPUT", "FAILED_SAFELY", "BLOCKED", "SUBMISSION_UNVERIFIED"}:
        return "ISSUE"
    if resolved in {"READY_FOR_REVIEW", "READY_TO_SUBMIT"}:
        return "NEEDS REVIEW"
    return resolved or "NOT STARTED"


def _pending_task(operation: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for task in operation.get("tasks") or []:
        if str(task.get("task_kind") or "") == kind and str(task.get("status") or "") == "PENDING":
            return dict(task)
    return None


def _fail_closed_n8n_contract(operation_id: str) -> dict[str, Any]:
    """Put legacy URL-only n8n Prepare work into ISSUE, never trusted-ready."""
    operation = orchestrator.prepare_operation(operation_id)
    task = _pending_task(operation, orchestrator.TASK_RESUME)
    if task is None:
        return operation
    worker_id = f"{WORKER_ID_PREFIX}-n8n-contract"
    claimed = orchestrator.claim_ready_tasks(
        operation_id=operation_id,
        worker_id=worker_id,
        limit=1,
        task_kinds=[orchestrator.TASK_RESUME],
    )
    if not claimed:
        return orchestrator.prepare_operation(operation_id)
    orchestrator.task_needs_input(
        task_id=str(claimed[0]["task_id"]),
        worker_id=worker_id,
        unresolved_keys=["n8n.resume_truth_binding_contract_required"],
        output={
            "engine": "N8N",
            "reason": (
                "Legacy n8n resume callbacks do not yet carry the immutable "
                "resume hash plus Candidate Truth and job-snapshot bindings "
                "required by Phase 9 preflight."
            ),
            "submission_authority": False,
        },
    )
    return orchestrator.prepare_operation(operation_id)


def drive_operation_once(operation_id: str) -> dict[str, Any]:
    """Run each currently-ready preparation adapter once for one operation."""
    operation = orchestrator.prepare_operation(str(operation_id))
    if str(operation.get("engine") or "").upper() == "N8N":
        result = _fail_closed_n8n_contract(str(operation_id))
        return {
            "operation_id": str(operation_id),
            "status": result["status"],
            "product_stage": product_stage_for_state(result["status"]),
            "engine": "N8N",
            "submission_authority": False,
        }

    from app import prepare_worker_adapters_v1a as v1a
    from app import prepare_worker_adapters_v1b as v1b
    from app import prepare_worker_adapters_v1c as v1c

    steps = (
        (v1a.run_native_resume_once, "resume"),
        (v1a.run_recruiter_evidence_once, "recruiter"),
        (v1b.run_answer_resolution_once, "answers"),
        (v1b.run_cover_letter_decision_once, "cover"),
        (v1c.run_application_preflight_once, "preflight"),
        (v1c.run_ats_form_preparation_once, "ats"),
    )
    step_results: list[dict[str, Any]] = []
    for runner, name in steps:
        current = orchestrator.prepare_operation(str(operation_id))
        if str(current.get("status") or "") in {
            "NEEDS_INPUT",
            "READY_FOR_REVIEW",
            "FAILED_SAFELY",
            "CANCELLED",
        }:
            break
        if name == "ats":
            from app import relay_single_prepare_v1 as relay_one_click
            from app import native_standing_consent_v1 as consent

            # Explicit per-job Relay intent takes precedence over the separate
            # Native standing consent. Never let two engines claim one opening.
            current_job_id = int(current.get("job_id") or 0)
            relay_state = (
                relay_one_click.status(current_job_id) if current_job_id
                else {"status": "NONE", "operation_id": ""}
            )
            if relay_state["status"] == "UNAVAILABLE":
                raise RuntimeError(
                    "Relay intent ledger unavailable; cannot safely choose a submission engine."
                )
            if relay_state["operation_id"] == str(operation_id) and relay_state["status"] in {
                "REQUESTED", "CLAIMED",
            }:
                if relay_state["status"] == "CLAIMED":
                    relay_result = {"authorized": False, "reason": "one_use_claim_consumed"}
                else:
                    relay_result = relay_one_click.maybe_dispatch(str(operation_id))
                step_results.append({"step": "relay_one_click", "result": relay_result})
                # This operation belongs exclusively to Relay. An ambiguous
                # provider outcome must never fall through to Native ATS.
                final = orchestrator.prepare_operation(str(operation_id))
                return {
                    "operation_id": str(operation_id),
                    "status": final["status"],
                    "product_stage": product_stage_for_state(final["status"]),
                    "engine": "NATIVE",
                    "steps": step_results,
                    "submission_authority": False,
                    "relay_submission_state": relay_result.get("status") or relay_result.get("reason"),
                }

            if consent.status().get("enabled") is True:
                try:
                    policy_result = consent.maybe_authorize_fresh_operation(
                        str(operation_id)
                    )
                except (PermissionError, RuntimeError, ValueError) as error:
                    # Keep unexpected account/form issues reviewable; never
                    # promote them into authority or retry a consumed slot.
                    policy_result = {
                        "authorized": False,
                        "reason": "standing_consent_fail_closed",
                        "error_type": type(error).__name__,
                    }
                step_results.append({
                    "step": "bounded_standing_consent",
                    "result": policy_result,
                })
        try:
            result = runner(
                operation_id=str(operation_id),
                worker_id=f"{WORKER_ID_PREFIX}-{name}",
            )
            step_results.append({"step": name, "result": result})
        except RuntimeError as error:
            # Feature/runtime gates deliberately remain authoritative. Do not
            # convert disabled execution into fake customer input or bypass it.
            text = str(error)
            if "disabled" in text.casefold():
                step_results.append(
                    {"step": name, "blocked_by_runtime_gate": True, "error": text}
                )
                break
            if name == "recruiter":
                # Recruiter discovery is advisory/optional. A stale relationship
                # snapshot must never block resume, answers, or ATS preparation.
                step_results.append(
                    {"step": name, "optional_step_error": True, "error": text}
                )
                continue
            raise
        except Exception as error:
            if name == "recruiter":
                step_results.append(
                    {
                        "step": name,
                        "optional_step_error": True,
                        "error": str(error),
                        "error_type": type(error).__name__,
                    }
                )
                continue
            raise

    final = orchestrator.prepare_operation(str(operation_id))
    return {
        "operation_id": str(operation_id),
        "status": final["status"],
        "product_stage": product_stage_for_state(final["status"]),
        "engine": "NATIVE",
        "steps": step_results,
        "submission_authority": False,
    }


def drive_open_operations(
    *,
    limit: int = 25,
    newest_first: bool = False,
    min_created_at: str | None = None,
) -> dict[str, Any]:
    """Advance owner-scoped QUEUED/RUNNING Prepare operations independently.

    Existing coordinator callers keep oldest-first fairness. The low-latency
    supervised runtime may explicitly request newest-first ordering and a
    deployment watermark so stale pre-activation work cannot unexpectedly
    consume model/API resources after a runtime repair.
    """
    connection = get_connection()
    try:
        orchestrator.ensure_schema()
        owner = current_owner(connection)
        ordering = (
            "datetime(created_at) DESC, rowid DESC"
            if newest_first
            else "datetime(created_at), rowid"
        )
        minimum = str(min_created_at or "").strip()
        minimum_clause = (
            " AND datetime(created_at) >= datetime(?)"
            if minimum
            else ""
        )
        params: list[Any] = [owner.tenant_id, owner.user_id]
        if minimum:
            params.append(minimum)
        params.append(max(1, int(limit)))
        rows = connection.execute(
            f"""SELECT operation_id
                  FROM prepare_operations_v1
                 WHERE tenant_id=? AND user_id=?
                   AND status IN ('QUEUED','RUNNING')
                   {minimum_clause}
                 ORDER BY {ordering}
                 LIMIT ?""",
            tuple(params),
        ).fetchall()
        operation_ids = [str(row[0]) for row in rows]
    finally:
        connection.close()

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for operation_id in operation_ids:
        try:
            results.append(drive_operation_once(operation_id))
        except Exception as error:
            errors.append(
                {
                    "operation_id": operation_id,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
    return {
        "success": not bool(errors),
        "operation_count": len(operation_ids),
        "results": results,
        "errors": errors,
        "submission_authority": False,
    }
