"""Customer-safe Application Center projection.

This module is a read model only.  Durable Prepare, lifecycle, mandate, and
receipt tables remain authoritative in ``product_application_flow_v2``.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import product_application_flow_v2 as application_flow
from app.database import get_connection

HEARTBEAT_PATH = Path(
    os.getenv("MUNSHI_PREPARE_RUNTIME_HEARTBEAT_PATH")
    or Path(__file__).resolve().parents[1] / "data" / "prepare_runtime_heartbeat.json"
)

ACTIVE_STATES = frozenset({"QUEUED", "RUNNING", "PREPARING", "PREPARING_ATS"})
REVIEW_STATES = frozenset({"READY_FOR_APPROVAL", "READY_FOR_REVIEW"})
APPLYING_STATES = frozenset({"READY_TO_SUBMIT", "SUBMITTING"})
SUBMITTED_STATES = frozenset(
    {
        "SUBMITTED",
        "VERIFIED",
        "AWAITING_RESPONSE",
        "ASSESSMENT",
        "INTERVIEW",
        "OFFER",
        "REJECTED",
        "WITHDRAWN",
    }
)
ATTENTION_STATES = frozenset(
    {"NEEDS_INPUT", "FAILED_SAFELY", "BLOCKED", "CANCELLED", "SUBMISSION_UNVERIFIED"}
)
IMMUTABLE_SUBMISSION_STATES = frozenset(
    {"SUBMITTED", "VERIFIED", "AWAITING_RESPONSE", "SUBMISSION_UNVERIFIED"}
)

_STAGE_LABELS = {
    "RESUME_PREPARE": "Resume tailoring",
    "RECRUITER_DISCOVERY": "Job intelligence",
    "ANSWER_RESOLUTION": "Package checks",
    "COVER_LETTER_DECISION": "Package checks",
    "APPLICATION_PREFLIGHT": "Package checks",
    "ATS_FORM_PREPARATION": "Package checks",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any) -> int | None:
    parsed = _timestamp(value)
    if parsed is None:
        return None
    return max(0, int((_utc_now() - parsed).total_seconds()))


def _job_row(job_id: int) -> dict[str, Any]:
    connection = get_connection()
    try:
        row = connection.execute(
            """SELECT id,company_name,title,location_raw,remote_type,
                      hunter_score,apply_url
                 FROM jobs WHERE id=?""",
            (int(job_id),),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise LookupError("The selected job is no longer available.")
    return dict(row)


def worker_health_snapshot(*, stale_after_seconds: int = 45) -> dict[str, Any]:
    """Read the value-free file heartbeat published by the supervised worker."""
    try:
        payload = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "state": "unavailable",
            "label": "Worker status unavailable",
            "recorded_at": None,
            "age_seconds": None,
        }
    if not isinstance(payload, dict):
        return {
            "state": "unavailable",
            "label": "Worker status unavailable",
            "recorded_at": None,
            "age_seconds": None,
        }
    age = _age_seconds(payload.get("recorded_at"))
    success = bool(payload.get("success"))
    if age is None or age > max(5, int(stale_after_seconds)):
        state, label = "stale", "Worker heartbeat stale"
    elif success:
        state, label = "healthy", "Worker healthy"
    else:
        state, label = "degraded", "Worker needs attention"
    return {
        "state": state,
        "label": label,
        "recorded_at": payload.get("recorded_at"),
        "age_seconds": age,
        "status": payload.get("status"),
        "reason": payload.get("reason"),
        "operation_count": int(payload.get("operation_count") or 0),
        "error_count": int(payload.get("error_count") or 0),
        "lease_recovery": dict(payload.get("lease_recovery") or {}),
    }


@dataclass(frozen=True)
class ApplicationViewModel:
    job_id: int
    application_id: str | None
    company: str
    title: str
    location: str
    work_arrangement: str
    match: float | None
    canonical_state: str
    human_state: str
    state_group: str
    last_update: str | None
    next_action: str
    safe_progress_stage: str
    stage_index: int
    stage_count: int
    resume_review_available: bool
    protected_checkpoint: bool
    issue_summary: str | None
    stopped_at: str | None
    submission_attempted: bool
    receipt_summary: dict[str, Any] | None
    worker_heartbeat: dict[str, Any]
    stale: bool
    allowed_recovery_actions: tuple[str, ...] = field(default_factory=tuple)
    degraded: bool = False
    projection_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _state_group(state: str, *, protected: bool) -> str:
    if state in REVIEW_STATES or protected:
        return "needs_you"
    if state in ACTIVE_STATES:
        return "preparing"
    if state in APPLYING_STATES:
        return "applying"
    if state in SUBMITTED_STATES:
        return "submitted"
    if state in ATTENTION_STATES:
        return "needs_attention"
    return "all"


def _next_action(state: str, *, protected: bool) -> str:
    if not state:
        return "Prepare application"
    if state in ACTIVE_STATES:
        return "No action needed"
    if state in REVIEW_STATES:
        return "Review resume"
    if protected:
        return "Complete protected checkpoint"
    if state == "SUBMISSION_UNVERIFIED":
        return "Verify before any further action"
    if state in {"FAILED_SAFELY", "BLOCKED", "CANCELLED", "NEEDS_INPUT"}:
        return "Review the issue"
    if state in APPLYING_STATES:
        return "No action needed"
    if state in SUBMITTED_STATES:
        return "View receipt"
    return "Open application"


def _human_state(state: str, *, protected: bool) -> str:
    if protected:
        return "Action required"
    if state in REVIEW_STATES:
        return "Needs review"
    if state in ACTIVE_STATES:
        return "Preparing"
    if state in APPLYING_STATES:
        return "Applying now"
    if state in SUBMITTED_STATES:
        return "Submitted"
    if state == "SUBMISSION_UNVERIFIED":
        return "Needs attention"
    return application_flow.STATE_LABELS.get(
        state,
        state.replace("_", " ").title() if state else "Not started",
    )


def _safe_stage(state: str, tasks: list[Mapping[str, Any]]) -> tuple[str, int]:
    if state in REVIEW_STATES:
        return "Needs review", 4
    if state in APPLYING_STATES:
        return "Applying now", 5
    if state in SUBMITTED_STATES:
        return "Submitted", 6
    for task in tasks:
        status = str(task.get("status") or "")
        if status in {"RUNNING", "NEEDS_INPUT", "FAILED"}:
            label = _STAGE_LABELS.get(str(task.get("task_kind") or ""), "Package checks")
            return label, {"Job intelligence": 1, "Resume tailoring": 2, "Package checks": 3}[label]
    for task in tasks:
        if str(task.get("status") or "") == "PENDING":
            label = _STAGE_LABELS.get(str(task.get("task_kind") or ""), "Package checks")
            return label, {"Job intelligence": 1, "Resume tailoring": 2, "Package checks": 3}[label]
    return ("Ready to prepare", 0) if not state else ("Application update", 1)


def _receipt(flow: Mapping[str, Any]) -> dict[str, Any] | None:
    receipt = dict(flow.get("production_receipt") or {})
    lifecycle = dict(flow.get("lifecycle") or {})
    if not receipt:
        receipt = dict(lifecycle.get("receipt") or {})
    if not receipt:
        return None
    return {
        "receipt_id": receipt.get("receipt_id"),
        "provider": receipt.get("provider"),
        "provider_application_id": receipt.get("provider_application_id"),
        "verification_status": receipt.get("verification_status") or "VERIFIED",
        "submitted_at": receipt.get("submitted_at"),
        "verified_at": receipt.get("verified_at"),
        "resume_version_id": receipt.get("resume_version_id"),
    }


def _issue(state: str, flow: Mapping[str, Any], *, protected: bool) -> tuple[str | None, str | None]:
    if protected:
        return (
            "The employer requires a protected identity or security checkpoint.",
            "Protected checkpoint",
        )
    if state == "SUBMISSION_UNVERIFIED":
        return (
            "Submission may have occurred, but independent verification is incomplete. MUNSHI will not submit again automatically.",
            "Submission verification",
        )
    tasks = list(flow.get("tasks") or [])
    stopped = next(
        (
            str(task.get("label") or "Application preparation")
            for task in tasks
            if str(task.get("status") or "") in {"FAILED", "NEEDS_INPUT"}
        ),
        None,
    )
    if state in {"FAILED_SAFELY", "BLOCKED"}:
        return (
            f"MUNSHI stopped safely during {stopped or 'application preparation'}. No submission success was assumed.",
            stopped,
        )
    if state == "CANCELLED":
        return ("Preparation was cancelled before approval.", stopped or "Preparation")
    if state == "NEEDS_INPUT":
        return (
            "A genuine unknown needs to be resolved before preparation can continue.",
            stopped,
        )
    return None, None


def _recovery_actions(
    state: str,
    flow: Mapping[str, Any],
    *,
    stale: bool,
    expired_lease: bool,
) -> tuple[str, ...]:
    actions = ["refresh_state", "clear_local_selection"]
    if state in IMMUTABLE_SUBMISSION_STATES or flow.get("execution_mandate"):
        return tuple(actions)
    if expired_lease:
        actions.append("recover_expired_lease")
    if state in {"FAILED_SAFELY", "BLOCKED", "CANCELLED"}:
        actions.append("retry_preparation")
    if state in {"QUEUED", "RUNNING", "NEEDS_INPUT"}:
        actions.append("cancel_preparation")
    if stale and state in ACTIVE_STATES:
        actions.append("restart_stale_preparation")
    if state in ATTENTION_STATES | ACTIVE_STATES:
        actions.append("quarantine_application")
    return tuple(dict.fromkeys(actions))


def view_for_job(
    job_id: int,
    *,
    seed: Mapping[str, Any] | None = None,
    heartbeat: Mapping[str, Any] | None = None,
) -> ApplicationViewModel:
    job = dict(seed or {}) or _job_row(int(job_id))
    if job.get("projection_error"):
        return degraded_view(job, str(job.get("projection_error")), heartbeat=heartbeat)

    flow = application_flow.flow_for_job(int(job_id))
    state = str(flow.get("state") or "")
    authority = str(flow.get("authority") or "")
    protected = authority == "protected_human_checkpoint"
    prepare = dict(flow.get("prepare") or {})
    lifecycle = dict(flow.get("lifecycle") or {})
    tasks = list(flow.get("tasks") or [])
    updated = lifecycle.get("updated_at") or prepare.get("updated_at")
    age = _age_seconds(updated)
    stale = state in ACTIVE_STATES and age is not None and age > 600
    now = _utc_now()
    expired_lease = any(
        str(task.get("status") or "") == "RUNNING"
        and (expires := _timestamp(task.get("lease_expires_at"))) is not None
        and expires < now
        for task in tasks
    )
    stage, stage_index = _safe_stage(state, tasks)
    issue, stopped_at = _issue(state, flow, protected=protected)
    receipt = _receipt(flow)
    application_id = str(
        lifecycle.get("application_id") or prepare.get("application_id") or ""
    ) or None
    return ApplicationViewModel(
        job_id=int(job_id),
        application_id=application_id,
        company=str(job.get("company_name") or "Company"),
        title=str(job.get("title") or "Untitled role"),
        location=str(job.get("location_raw") or "Location not recorded"),
        work_arrangement=str(job.get("remote_type") or "Work arrangement not recorded"),
        match=(float(job["hunter_score"]) if job.get("hunter_score") is not None else None),
        canonical_state=state,
        human_state=_human_state(state, protected=protected),
        state_group=_state_group(state, protected=protected),
        last_update=str(updated) if updated else None,
        next_action=_next_action(state, protected=protected),
        safe_progress_stage=stage,
        stage_index=stage_index,
        stage_count=6,
        resume_review_available=state in REVIEW_STATES,
        protected_checkpoint=protected,
        issue_summary=issue,
        stopped_at=stopped_at,
        submission_attempted=state in IMMUTABLE_SUBMISSION_STATES | SUBMITTED_STATES,
        receipt_summary=receipt,
        worker_heartbeat=dict(heartbeat or worker_health_snapshot()),
        stale=stale,
        allowed_recovery_actions=_recovery_actions(
            state,
            flow,
            stale=stale,
            expired_lease=expired_lease,
        ),
    )


def degraded_view(
    seed: Mapping[str, Any],
    error_type: str,
    *,
    heartbeat: Mapping[str, Any] | None = None,
) -> ApplicationViewModel:
    return ApplicationViewModel(
        job_id=int(seed.get("job_id") or seed.get("id")),
        application_id=None,
        company=str(seed.get("company_name") or "Company"),
        title=str(seed.get("title") or "Untitled role"),
        location=str(seed.get("location_raw") or "Location not recorded"),
        work_arrangement=str(seed.get("remote_type") or "Work arrangement not recorded"),
        match=(float(seed["hunter_score"]) if seed.get("hunter_score") is not None else None),
        canonical_state="PROJECTION_ERROR",
        human_state="Needs attention",
        state_group="needs_attention",
        last_update=str(seed.get("updated_at") or "") or None,
        next_action="Open diagnostics",
        safe_progress_stage="State unavailable",
        stage_index=0,
        stage_count=6,
        resume_review_available=False,
        protected_checkpoint=False,
        issue_summary="This application could not be read safely. Other applications are still available.",
        stopped_at="Application state projection",
        submission_attempted=False,
        receipt_summary=None,
        worker_heartbeat=dict(heartbeat or worker_health_snapshot()),
        stale=False,
        allowed_recovery_actions=("refresh_state", "clear_local_selection"),
        degraded=True,
        projection_error=error_type,
    )


def application_views(*, limit: int | None = 250) -> list[ApplicationViewModel]:
    heartbeat = worker_health_snapshot()
    rows = application_flow.pipeline_rows(
        limit=None if limit is None else int(limit)
    )
    views: list[ApplicationViewModel] = []
    for row in rows:
        try:
            views.append(
                view_for_job(
                    int(row["job_id"]),
                    seed=row,
                    heartbeat=heartbeat,
                )
            )
        except Exception as error:  # noqa: BLE001 - isolate one malformed application
            views.append(degraded_view(row, type(error).__name__, heartbeat=heartbeat))
    priority = {
        "needs_you": 0,
        "needs_attention": 1,
        "applying": 2,
        "preparing": 3,
        "submitted": 4,
        "all": 5,
    }
    views.sort(
        key=lambda item: (
            priority.get(item.state_group, 9),
            -(int(_timestamp(item.last_update).timestamp()) if _timestamp(item.last_update) else 0),
            item.company.casefold(),
            item.title.casefold(),
        )
    )
    return views


def control_center_snapshot(*, limit: int = 250) -> dict[str, Any]:
    views = application_views(limit=limit)
    counts = {
        "preparing": sum(item.state_group == "preparing" for item in views),
        "needs_review": sum(
            item.state_group == "needs_you" and item.resume_review_available
            for item in views
        ),
        "applying": sum(item.state_group == "applying" for item in views),
        "needs_attention": sum(item.state_group == "needs_attention" for item in views),
        "submitted_today": sum(
            item.state_group == "submitted"
            and (_timestamp(item.last_update) or datetime.min.replace(tzinfo=timezone.utc)).date()
            == _utc_now().date()
            for item in views
        ),
    }
    return {
        "applications": views,
        "counts": counts,
        "worker": worker_health_snapshot(),
    }
