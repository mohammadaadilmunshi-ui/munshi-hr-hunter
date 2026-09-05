"""Cross-phase integrity contract for strengthened Career OS Phases 4 through 7.

The contract is read-only and preparation-only. It verifies that a selected resume,
Answer Brain state, opportunity evaluation, and optional relationship strategy all
refer to the same current Candidate Truth and owned job state. It grants no browser,
ATS, n8n, Gmail, outreach, credential, or submission authority.
"""
from __future__ import annotations

from typing import Any

from app import answer_brain_v2 as answers_v2
from app import native_resume_service_v4 as resume_v4
from app import opportunity_intelligence_v3 as opportunity_v3
from app import phase45_truth_binding
from app import relationship_intelligence_v3 as relationship_v3
from app.phase67_common import safe_owned_job_snapshot

INTEGRITY_VERSION = "phase4-7-integrity-v1"


def _same_truth(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not left or not right:
        return False
    return all(
        str(left.get(field)) == str(right.get(field))
        for field in ("source_extraction_id", "profile_revision", "profile_digest")
    )


def application_preparation_readiness(
    *,
    job_id: int,
    resume_version_id: str,
    opportunity_evaluation_id: str,
    relationship_strategy_id: str | None = None,
) -> dict[str, Any]:
    """Fail closed when any Phase 4-7 binding is missing, stale, or mismatched."""
    resolved_job_id = int(job_id)
    blockers: list[str] = []
    checks: dict[str, Any] = {}

    current_job = safe_owned_job_snapshot(resolved_job_id)
    try:
        current_truth = phase45_truth_binding.current_candidate_profile_snapshot()
        current_truth_public = phase45_truth_binding.public_binding_state(current_truth)
    except (LookupError, RuntimeError, ValueError):
        current_truth_public = None
        blockers.append("candidate_truth_profile_unavailable")

    resume = resume_v4.get_version(resume_version_id)
    if int(resume["job_id"]) != resolved_job_id:
        blockers.append("resume_job_mismatch")
    if not resume.get("candidate_truth_bound"):
        blockers.append("resume_candidate_truth_unbound")
    if not resume.get("job_snapshot_bound"):
        blockers.append("resume_job_snapshot_unbound")
    resume_truth = resume.get("candidate_truth_binding") or None
    resume_job = resume.get("job_snapshot_binding") or {}
    if current_truth_public and not _same_truth(resume_truth, current_truth_public):
        blockers.append("resume_candidate_truth_stale")
    if resume_job and str(resume_job.get("job_snapshot_sha256")) != str(current_job["job_snapshot_sha256"]):
        blockers.append("resume_job_snapshot_stale")
    checks["phase4_resume"] = {
        "version_id": str(resume_version_id),
        "candidate_truth_bound": bool(resume.get("candidate_truth_bound")),
        "job_snapshot_bound": bool(resume.get("job_snapshot_bound")),
        "generation_input_sha256": resume_job.get("generation_input_sha256"),
    }

    try:
        planning = answers_v2.planning_input()
        answer_truth = planning.get("candidate_truth_binding")
        if current_truth_public and not _same_truth(answer_truth, current_truth_public):
            blockers.append("answer_brain_candidate_truth_mismatch")
        stale_exclusions = [
            item for item in planning.get("excluded_answers") or []
            if item.get("reason") == "stale_or_unbound_profile_answer"
        ]
        if stale_exclusions:
            blockers.append("answer_brain_contains_stale_profile_memory")
        checks["phase5_answers"] = {
            "available": True,
            "planner_answer_count": len(planning.get("answers") or []),
            "excluded_stale_profile_answer_count": len(stale_exclusions),
        }
    except RuntimeError:
        blockers.append("answer_brain_disabled_or_unavailable")
        checks["phase5_answers"] = {"available": False}

    opportunity = opportunity_v3.get_evaluation(opportunity_evaluation_id)
    if int(opportunity["job_id"]) != resolved_job_id:
        blockers.append("opportunity_job_mismatch")
    freshness = opportunity_v3.evaluation_freshness(opportunity_evaluation_id)
    if not freshness["fresh"]:
        blockers.append("opportunity_evaluation_stale")
    if resume_truth and not _same_truth(resume_truth, opportunity.get("candidate_truth_binding")):
        blockers.append("resume_opportunity_truth_mismatch")
    if resume_job and str(resume_job.get("job_snapshot_sha256")) != str(opportunity.get("job_snapshot_sha256")):
        blockers.append("resume_opportunity_job_snapshot_mismatch")
    if str(opportunity.get("status")) != "PASS":
        blockers.append(f"opportunity_status_{str(opportunity.get('status') or 'unknown').casefold()}")
    checks["phase6_opportunity"] = {
        "evaluation_id": str(opportunity_evaluation_id),
        "fresh": bool(freshness["fresh"]),
        "status": opportunity.get("status"),
        "pursuit_state": (opportunity.get("pursuit_strategy") or {}).get("pursuit_state"),
        "score_confidence": opportunity.get("score_confidence"),
    }

    if relationship_strategy_id is None:
        checks["phase7_relationship"] = {"state": "NOT_REQUIRED"}
    else:
        relationship = relationship_v3.get_strategy(relationship_strategy_id)
        if int(relationship["job_id"]) != resolved_job_id:
            blockers.append("relationship_job_mismatch")
        relationship_freshness = relationship_v3.strategy_freshness(relationship_strategy_id)
        if not relationship_freshness["fresh"]:
            blockers.append("relationship_strategy_stale")
        context = relationship.get("opportunity_context") or {}
        if str(context.get("evaluation_id") or "") != str(opportunity_evaluation_id):
            blockers.append("relationship_opportunity_mismatch")
        if str(context.get("result_sha256") or "") != str(opportunity.get("result_sha256") or ""):
            blockers.append("relationship_opportunity_digest_mismatch")
        checks["phase7_relationship"] = {
            "strategy_id": str(relationship_strategy_id),
            "fresh": bool(relationship_freshness["fresh"]),
            "combined_pursuit_state": (relationship.get("strategy") or {}).get("combined_pursuit_state"),
            "networking_action": (relationship.get("strategy") or {}).get("networking_action"),
        }

    blockers = sorted(set(blockers))
    return {
        "version": INTEGRITY_VERSION,
        "job_id": resolved_job_id,
        "status": "READY" if not blockers else "HOLD",
        "blockers": blockers,
        "checks": checks,
        "candidate_truth_binding": current_truth_public,
        "job_snapshot_sha256": current_job["job_snapshot_sha256"],
        "submission_authority": False,
        "automatic_actions_executed": False,
    }
