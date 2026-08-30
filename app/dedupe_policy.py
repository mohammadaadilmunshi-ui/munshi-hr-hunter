from __future__ import annotations

from typing import Any, Mapping

MARKER = "AADIL_DEDUPE_CURRENT_TARGETING_KEEPER_V1"

# A historical row can remain in the audit trail without remaining authoritative
# for future duplicate suppression. These statuses are never valid automatic
# duplicate keepers unless another explicit protection signal applies first.
NON_KEEPER_STATUSES = {
    "rejected",
    "blacklisted",
    "rejected_by_dashboard_targeting",
    "duplicate",
}

# These states represent explicit downstream/user intent. They remain duplicate
# keepers even if the dashboard policy later changes, preventing accidental
# re-application or loss of a manually held/processed application.
PROTECTED_KEEPER_STATUSES = {
    "already_applied",
    "approved_for_n8n",
    "sent_to_n8n",
    "application_ready",
    "n8n_failed",
    "hold",
    "held",
}


def _status(row: Mapping[str, Any]) -> str:
    return str(row.get("status") or "").strip().casefold().replace(" ", "_")


def _truthy_int(value: Any) -> bool:
    try:
        return int(value or 0) == 1
    except (TypeError, ValueError):
        return bool(value)


def dedupe_keeper_decision(
    candidate: Mapping[str, Any] | None,
    *,
    rules: Any = None,
) -> dict[str, Any]:
    """Return whether an existing job is allowed to suppress rediscovery.

    The job row is never modified here. The decision is deliberately biased
    toward *not suppressing* when current targeting cannot be proven. A duplicate
    is cheaper than silently losing a newly corrected, eligible job.
    """
    if not candidate:
        return {"allowed": False, "reason": "missing_candidate"}

    row = dict(candidate)
    status = _status(row)

    if status in PROTECTED_KEEPER_STATUSES:
        return {"allowed": True, "reason": f"protected_status:{status}"}

    if _truthy_int(row.get("already_applied")):
        return {"allowed": True, "reason": "already_applied_flag"}

    if _truthy_int(row.get("sent_to_n8n")):
        return {"allowed": True, "reason": "sent_to_n8n_flag"}

    # Preserve verified manual/non-discovery jobs. Import lazily so this module
    # can be used safely from job_store/job_duplicate_guard without cycles.
    try:
        from app.strict_dashboard_targeting_v2 import protected_non_discovery_row

        if protected_non_discovery_row(row):
            return {"allowed": True, "reason": "protected_non_discovery_row"}
    except Exception:
        # Do not make a historical automatic row authoritative merely because a
        # protection helper could not be imported/evaluated.
        pass

    if status in NON_KEEPER_STATUSES:
        return {"allowed": False, "reason": f"terminal_non_keeper:{status}"}

    try:
        from app.dashboard_targeting_gate import (
            evaluate_dashboard_job,
            load_dashboard_targeting_rules,
        )

        current_rules = rules if rules is not None else load_dashboard_targeting_rules()
        result = evaluate_dashboard_job(
            row,
            rules=current_rules,
            require_location=True,
        )
    except Exception as error:
        return {
            "allowed": False,
            "reason": "targeting_evaluation_failed",
            "detail": f"{type(error).__name__}: {error}",
        }

    if bool(result.get("accepted")):
        return {
            "allowed": True,
            "reason": "current_dashboard_targeting_match",
            "targeting_reason": result.get("reason"),
        }

    return {
        "allowed": False,
        "reason": "current_dashboard_targeting_reject",
        "targeting_reason": result.get("reason"),
        "targeting_detail": result.get("detail"),
        "location_rejection_reason": result.get("location_rejection_reason"),
    }


def dedupe_keeper_allowed(
    candidate: Mapping[str, Any] | None,
    *,
    rules: Any = None,
) -> bool:
    return bool(dedupe_keeper_decision(candidate, rules=rules).get("allowed"))
