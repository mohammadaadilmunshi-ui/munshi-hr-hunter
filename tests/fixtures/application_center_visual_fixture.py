"""Synthetic, value-free visual fixture for Application Center evidence."""
from __future__ import annotations

from types import SimpleNamespace

import streamlit as st

from app import application_view_model_v1 as view_model
from app import auto_prepare_readiness_ui_v1, product_pages
from app import unified_job_workspaces_v1 as workspaces
from app.product_ui import inject_css


def _model(
    job_id: int,
    company: str,
    title: str,
    state: str,
    group: str,
    human: str,
    action: str,
    *,
    stage: str,
    stage_index: int,
    stale: bool = False,
    review: bool = False,
    protected: bool = False,
    receipt: dict | None = None,
    recovery: tuple[str, ...] = ("refresh_state", "clear_local_selection"),
) -> view_model.ApplicationViewModel:
    return view_model.ApplicationViewModel(
        job_id=job_id,
        application_id=f"application-fixture-{job_id}",
        company=company,
        title=title,
        location="New York, NY",
        work_arrangement="Hybrid",
        match=float(96 - job_id),
        canonical_state=state,
        human_state=human,
        state_group=group,
        last_update="2026-09-22T10:14:00+00:00",
        next_action=action,
        safe_progress_stage=stage,
        stage_index=stage_index,
        stage_count=6,
        resume_review_available=review,
        protected_checkpoint=protected,
        issue_summary=(
            "MUNSHI stopped safely during package checks. No submission success was assumed."
            if group == "needs_attention"
            else None
        ),
        stopped_at="Package checks" if group == "needs_attention" else None,
        submission_attempted=state == "SUBMISSION_UNVERIFIED",
        receipt_summary=receipt,
        worker_heartbeat={
            "state": "healthy",
            "label": "Worker healthy",
            "recorded_at": "2026-09-22T10:14:08+00:00",
        },
        stale=stale,
        allowed_recovery_actions=recovery,
    )


VIEWS = [
    _model(
        2,
        "Northstar Health",
        "People Operations Analyst",
        "READY_FOR_APPROVAL",
        "needs_you",
        "Needs review",
        "Review resume",
        stage="Needs review",
        stage_index=4,
        review=True,
    ),
    _model(
        6,
        "Lumen Works",
        "HR Coordinator",
        "NEEDS_INPUT",
        "needs_you",
        "Action required",
        "Complete protected checkpoint",
        stage="Protected checkpoint",
        stage_index=5,
        protected=True,
    ),
    _model(
        4,
        "Harbor & Co.",
        "Recruiting Operations Partner",
        "FAILED_SAFELY",
        "needs_attention",
        "Needs attention",
        "Review the issue",
        stage="Package checks",
        stage_index=3,
        stale=True,
        recovery=(
            "refresh_state",
            "clear_local_selection",
            "recover_expired_lease",
            "retry_preparation",
            "restart_stale_preparation",
            "quarantine_application",
        ),
    ),
    _model(
        3,
        "Atlas Labs",
        "Talent Programs Associate",
        "SUBMITTING",
        "applying",
        "Applying now",
        "No action needed",
        stage="Applying now",
        stage_index=5,
    ),
    _model(
        1,
        "Mosaic Systems",
        "People Analytics Specialist",
        "RUNNING",
        "preparing",
        "Preparing",
        "No action needed",
        stage="Resume tailoring",
        stage_index=2,
    ),
    _model(
        5,
        "Evergreen Group",
        "HR Operations Associate",
        "VERIFIED",
        "submitted",
        "Submitted",
        "View receipt",
        stage="Submitted",
        stage_index=6,
        receipt={
            "provider": "Greenhouse",
            "provider_application_id": "GH-20481",
            "verification_status": "VERIFIED",
            "submitted_at": "2026-09-22 09:42 UTC",
            "resume_version_id": "Tailored resume v3",
        },
    ),
]


def _resume_review(job_id: int, *, compact: bool = False) -> None:
    st.markdown("### Review tailored resume")
    st.caption(
        "This is the one customer review. Check the truthful, job-specific resume; Approve & Submit then starts unattended application execution."
    )
    metrics = st.columns(3)
    metrics[0].metric("JD match", "92% Strong")
    metrics[1].metric("Supported requirements", "11 of 12")
    metrics[2].metric("One-page fit", "Verified")
    st.markdown("**Required skills**")
    st.write("✓ HR operations  ·  ✓ Workday  ·  ✓ People analytics  ·  ○ Benefits administration")
    st.markdown("**What changed**")
    changes = st.columns(2)
    changes[0].metric("Bullets improved", 5)
    changes[1].metric("Job-relevant skills", 4)
    st.markdown("**Professional summary**")
    st.write(
        "People operations analyst with evidence-backed experience improving HR workflows, reporting, and employee support."
    )
    with st.expander("Resume preview", expanded=True):
        st.markdown(
            "**AADIL MUNSHI**  \nPeople Operations Analyst  \n\n"
            "**EXPERIENCE**  \n• Improved HR operations reporting and cross-functional service delivery.  \n"
            "• Built reliable people-data workflows using truthful source evidence."
        )
    actions = st.columns((1, 2))
    actions[0].button("Skip", use_container_width=True)
    actions[1].button("Approve & Submit", type="primary", use_container_width=True)
    st.caption("No second review follows. Protected checkpoints remain protected.")


def _render_applications() -> None:
    view_model.application_views = lambda limit=250: VIEWS
    view_model.view_for_job = lambda job_id: next(
        item for item in VIEWS if item.job_id == int(job_id)
    )
    pages = SimpleNamespace(_show_action_feedback=lambda: None)
    workspace = SimpleNamespace(_render_complete_loop=_resume_review)
    workspaces.render_applications(pages, workspace)


def _render_auto_prepare() -> None:
    counts = {
        "preparing": 1,
        "needs_review": 1,
        "applying": 1,
        "needs_attention": 1,
        "submitted_today": 1,
    }
    snapshot = {
        "applications": VIEWS,
        "counts": counts,
        "worker": {
            "state": "healthy",
            "label": "Worker healthy",
            "recorded_at": "2026-09-22T10:14:08+00:00",
        },
    }
    view_model.control_center_snapshot = lambda limit=250: snapshot
    auto_prepare_readiness_ui_v1.readiness_snapshot = lambda: {
        "ready": True,
        "missing": [],
        "values": {},
    }
    product_pages.volume_policy = lambda: {
        "mode": "unlimited",
        "daily_limit": None,
        "review_first": False,
    }
    product_pages.auto_prepare()


st.set_page_config(page_title="MUNSHI Application Center V2", layout="wide")
inject_css()
if str(st.query_params.get("view") or "applications") == "auto":
    _render_auto_prepare()
else:
    _render_applications()
