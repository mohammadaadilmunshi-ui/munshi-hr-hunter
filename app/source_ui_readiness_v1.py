"""Truthful status and result funnel for the Source Health page (no writes)."""
from __future__ import annotations

from typing import Any, Mapping
from app.source_readiness import readiness_with_boards


def displayed_readiness(connection, source: Mapping[str, Any], registry: Mapping[str, Any]) -> tuple[str, str]:
    if not bool(source.get("enabled")):
        return "Disabled", "source_not_enabled"
    status = readiness_with_boards(connection, source, registry)
    if not status["ready"]:
        reason = status["reason"]
        return ({
            "worker_missing": "Not implemented",
            "needs_company_board": "Needs employer board",
            "disabled_by_policy": "Blocked by cost policy",
            "retired_source": "Retired",
        }.get(reason, "Setup required"), reason)
    legacy = str(source.get("health_status") or "").lower()
    error = str(source.get("last_error") or "").lower()
    if "429" in error or "rate_limit" in error or "rate limit" in error:
        return "Rate limited", "respect_provider_cooldown"
    if legacy in ("failed", "degraded", "blocked", "unhealthy"):
        return "Degraded / failing", "investigate_latest_run"
    if not source.get("last_success_at"):
        return "Awaiting first run", "worker_ready_unverified"
    if legacy in ("healthy", "completed", "success"):
        return "Working", "last_run_completed"
    return "Awaiting verification", "uncertain_run_status"


def source_funnel(source: Mapping[str, Any]) -> dict[str, int | float]:
    def n(key: str) -> int:
        try: return max(0, int(source.get(key) or 0))
        except (TypeError, ValueError): return 0
    raw, normalized, eligible, inserted = (
        n("raw_jobs_last_run"), n("normalized_jobs_last_run"),
        n("eligible_jobs_last_run"), n("inserted_jobs_last_run"))
    return {"raw": raw, "normalized": normalized, "eligible": eligible,
            "inserted": inserted,
            "eligible_rate_pct": round((100.0 * eligible / raw), 3) if raw else 0.0,
            "rejected_role": n("reject_role_last_run"),
            "rejected_location": n("reject_location_last_run"),
            "rejected_hard_requirement": n("reject_hard_requirement_last_run")}
