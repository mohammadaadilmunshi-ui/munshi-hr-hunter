"""Compatibility routing for autonomous preparation rollout.

Native Resume can own automatic durable Prepare immediately because its immutable
Candidate Truth/job/artifact bindings are already complete. The legacy n8n auto
lane must not be silently replaced until the n8n producer emits the equivalent
resume binding required by durable Phase 9 preparation.

The historical automatic/preparation default is n8n. An account without an
explicit saved ``prepare_engine`` therefore remains on that proven path. Native
automatic durable preparation activates only after an explicit NATIVE preference.
The experimental durable N8N bridge is separately opt-in and default-off.

Compatibility routing also excludes any job that already has durable Prepare
work before applying the legacy auto quota. Changing engines must never create a
second preparation lane for the same job.
"""
from __future__ import annotations

import os
from typing import Any

from app import autonomous_prepare_v1 as runtime
from app.database import get_connection
from app.n8n_dispatch import (
    _begin_dispatch_transaction,
    ensure_schema as ensure_n8n_schema,
    insert_queue_item,
    load_setting,
    plan_candidates,
)

N8N_DURABLE_PREPARE_ENV = "MUNSHI_N8N_DURABLE_PREPARE_ENABLED"
SUBMISSION_AUTHORITY = False


def _enabled(value: Any) -> bool:
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def n8n_durable_prepare_enabled() -> bool:
    """Return the explicit opt-in for the still-incomplete n8n durable bridge."""
    return _enabled(os.getenv(N8N_DURABLE_PREPARE_ENV))


def configured_engine() -> str:
    """Resolve the saved automatic engine without silently migrating users.

    Older product policy rows predate ``prepare_engine`` entirely and the
    existing chooser historically defaults to n8n. Missing therefore means
    N8N for backward compatibility. Only an explicit NATIVE value changes
    automatic ownership to durable Native Prepare.
    """
    connection = get_connection()
    try:
        policy = load_setting(
            connection,
            "product_automation_policy_v1",
            {},
        )
    finally:
        connection.close()

    engine = str(policy.get("prepare_engine") or "").strip().upper()
    if not engine:
        return "N8N"
    if engine not in {"NATIVE", "N8N"}:
        raise RuntimeError("Product automation policy has an invalid prepare engine.")
    return engine


def routing_mode() -> dict[str, Any]:
    """Choose automatic ownership without changing eligibility or quotas."""
    engine = configured_engine()
    durable_n8n = n8n_durable_prepare_enabled()

    if engine == "N8N" and not durable_n8n:
        return {
            "engine": "N8N",
            "mode": "LEGACY_N8N_COMPAT",
            "durable_prepare_is_auto_owner": False,
            "legacy_auto_n8n_queue_enabled": True,
            "reason": "n8n_durable_resume_binding_not_enabled",
            "submission_authority": False,
        }

    return {
        "engine": engine,
        "mode": "DURABLE_PREPARE",
        "durable_prepare_is_auto_owner": True,
        "legacy_auto_n8n_queue_enabled": False,
        "reason": (
            "native_durable_prepare"
            if engine == "NATIVE"
            else "explicit_n8n_durable_prepare_opt_in"
        ),
        "submission_authority": False,
    }


def queue_legacy_n8n_candidates(
    *,
    webhook_mode: str,
    dry_run: bool,
) -> dict[str, Any]:
    """Preserve legacy n8n auto behavior without duplicating durable work.

    The canonical planner remains authoritative for policy-derived thresholds,
    manual limits and legacy usage. For automatic candidates we rebuild the
    canonical eligible pool only so existing durable Prepare jobs can be removed
    *before* quota truncation. Durable admissions from earlier in the same day
    also consume the user's shared auto volume budget.
    """
    from app import autonomous_prepare_admission_v1 as admission

    connection = get_connection()
    try:
        ensure_n8n_schema(connection)
        plan = plan_candidates(connection)
        existing_prepare = runtime._existing_prepare_job_ids(connection)
        durable_used = runtime._admissions_today(connection)

        manual_candidates = [
            job
            for job in plan["manual_candidates"]
            if int(job["id"]) not in existing_prepare
        ]
        auto_pool = admission._eligible_unprepared_pool(
            connection,
            threshold=float(plan["threshold"]),
        )

        auto_limit = plan["auto_limit"]
        if auto_limit is None:
            remaining = None
            auto_candidates = auto_pool
        else:
            remaining = max(
                0,
                int(auto_limit)
                - int(plan["auto_used_today"])
                - int(durable_used),
            )
            auto_candidates = auto_pool[:remaining]

        selected = [
            *( (job, "telegram_manual") for job in manual_candidates ),
            *( (job, "auto_top_match") for job in auto_candidates ),
        ]

        if dry_run:
            queued = [
                {
                    "job_id": int(job["id"]),
                    "company": job["company_name"],
                    "title": job["title"],
                    "hunter_score": job["hunter_score"],
                    "dispatch_mode": dispatch_mode,
                    "queue_id": None,
                    "queue_status": "dry_run",
                }
                for job, dispatch_mode in selected
            ]
        else:
            queued = []
            if selected:
                _begin_dispatch_transaction(connection)
                for job, dispatch_mode in selected:
                    row = insert_queue_item(
                        connection,
                        job,
                        dispatch_mode,
                        webhook_mode,
                    )
                    queued.append(
                        {
                            "job_id": int(job["id"]),
                            "company": job["company_name"],
                            "title": job["title"],
                            "hunter_score": job["hunter_score"],
                            "dispatch_mode": dispatch_mode,
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
            "auto_limit": auto_limit,
            "manual_limit": plan["manual_limit"],
            "legacy_auto_used_today": int(plan["auto_used_today"]),
            "durable_auto_used_today": int(durable_used),
            "remaining_slots": remaining,
            "manual_candidate_count": len(manual_candidates),
            "auto_candidate_count": len(auto_candidates),
            "selected_count": len(selected),
            "queued": queued,
            "n8n_calls": 0,
            "submission_authority": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
