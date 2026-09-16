"""Quota-safe autonomous admission into the durable Prepare lifecycle.

The legacy n8n planner intentionally truncates its auto candidate list to the
remaining legacy queue quota. During the migration to durable Prepare that
truncation must happen *after* jobs with an existing Prepare operation are
removed; otherwise historical prepared jobs can occupy the planner window and
starve newer eligible jobs.

This module reuses the canonical eligibility predicates, targeting rules, lane
filters, score thresholds, and product quota metadata from ``n8n_dispatch``.
It changes only ordering: existing durable jobs are filtered before the final
remaining-slot slice.
"""
from __future__ import annotations

from typing import Any

from app import autonomous_prepare_v1 as runtime
from app import prepare_orchestrator_v1 as orchestrator
from app import product_application_flow_v2 as product_flow
from app.database import get_connection
from app.n8n_dispatch import (
    enabled_lanes,
    ensure_schema as ensure_n8n_schema,
    is_auto_eligible,
    lane_matches_job,
    load_rules,
    load_unqueued_jobs,
    plan_candidates,
)

SUBMISSION_AUTHORITY = False


def _eligible_unprepared_pool(
    connection: Any,
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    """Return the canonical auto-eligible pool before quota truncation."""
    jobs = load_unqueued_jobs(connection)
    existing = runtime._existing_prepare_job_ids(connection)
    active_lanes = enabled_lanes(connection)
    targeting_rules = load_rules()
    result: list[dict[str, Any]] = []

    for job in jobs:
        if int(job["id"]) in existing:
            continue
        if not is_auto_eligible(
            job,
            float(threshold),
            targeting_rules=targeting_rules,
        ):
            continue

        matching = [
            lane for lane in active_lanes if lane_matches_job(lane, job)
        ]
        if active_lanes and not matching:
            continue
        lane_thresholds = [
            float(lane["min_score"])
            for lane in matching
            if lane.get("min_score") is not None
        ]
        required_score = max([float(threshold), *lane_thresholds])
        if float(job.get("hunter_score") or 0) < required_score:
            continue
        result.append(job)

    return result


def admit_auto_candidates(*, dry_run: bool) -> dict[str, Any]:
    """Admit eligible jobs without allowing old Prepare rows to starve quota."""
    connection = get_connection()
    try:
        ensure_n8n_schema(connection)
        orchestrator.ensure_schema()

        # plan_candidates remains the single source for policy-derived threshold,
        # daily limit, and legacy auto usage. Its already-truncated candidate list
        # is deliberately ignored here because durable rows must be filtered first.
        plan = plan_candidates(connection)
        engine = runtime._configured_engine(connection)
        durable_used = runtime._admissions_today(connection)
        pool = _eligible_unprepared_pool(
            connection,
            threshold=float(plan["threshold"]),
        )

        auto_limit = plan["auto_limit"]
        if auto_limit is None:
            candidates = pool
            remaining = None
        else:
            remaining = max(
                0,
                int(auto_limit)
                - int(plan["auto_used_today"])
                - int(durable_used),
            )
            candidates = pool[:remaining]

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "engine": engine,
                "legacy_auto_used_today": int(plan["auto_used_today"]),
                "durable_auto_used_today": int(durable_used),
                "remaining_slots": remaining,
                "eligible_unprepared_count": len(pool),
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
                "submission_authority": False,
            }

        admitted: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for job in candidates:
            job_id = int(job["id"])
            try:
                result = product_flow.start_prepare_for_job(
                    job_id,
                    engine=engine,
                )
                if not bool(result.get("replayed")):
                    runtime._record_event(
                        connection,
                        job_id=job_id,
                        status="admitted",
                        payload={
                            "engine": engine,
                            "prepare_state": result.get("state"),
                            "product_stage": runtime.product_stage_for_state(
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
                        "product_stage": runtime.product_stage_for_state(
                            str(result.get("state") or "")
                        ),
                    }
                )
            except Exception as error:
                runtime._record_event(
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
            "durable_auto_used_today": int(durable_used),
            "remaining_slots": remaining,
            "eligible_unprepared_count": len(pool),
            "candidate_count": len(candidates),
            "admitted": admitted,
            "errors": errors,
            "submission_authority": False,
        }
    finally:
        connection.close()
