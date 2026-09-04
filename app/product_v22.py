
"""Direct Product UI V2.2 polish layer.

This module is deliberately presentation-focused. It does not schedule work on
render, does not claim submissions without external evidence, and reuses the
existing guarded preparation authority for explicit Prepare actions.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import streamlit as st

_PAGES: Any = None


def _asset_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / name


def brand_logo_data_uri() -> str:
    data = _asset_path("munshi_crown.png").read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def inject_v22_css() -> None:
    st.markdown(
        """
<style>
/* V2.2: keep the existing MUNSHI light-product language inside dialogs too. */
.brand-mark{
    display:grid!important;
    place-items:center!important;
    width:38px!important;
    height:38px!important;
    background:transparent!important;
    clip-path:none!important;
    overflow:visible!important;
}
.brand-mark img{
    width:38px!important;
    height:38px!important;
    object-fit:contain!important;
    display:block!important;
}
div[data-baseweb="modal"]{
    background:rgba(20,33,41,.34)!important;
    backdrop-filter:blur(10px)!important;
    -webkit-backdrop-filter:blur(10px)!important;
}
[data-testid="stDialog"],
[data-testid="stDialog"]>div,
[data-testid="stDialog"] [role="dialog"]{
    background:#F7F9F6!important;
    color:#142129!important;
    color-scheme:light!important;
}
[data-testid="stDialog"] [role="dialog"]{
    border:1px solid #D9E1DB!important;
    border-radius:22px!important;
    box-shadow:0 28px 90px rgba(20,33,41,.24)!important;
}
[data-testid="stDialog"] [data-testid="stMarkdownContainer"],
[data-testid="stDialog"] [data-testid="stMarkdownContainer"] *{
    color:#142129!important;
}
[data-testid="stDialog"] [role="dialog"] h1,
[data-testid="stDialog"] [role="dialog"] h2,
[data-testid="stDialog"] [role="dialog"] h3,
[data-testid="stDialog"] [role="dialog"] p,
[data-testid="stDialog"] [role="dialog"] span,
[data-testid="stDialog"] [role="dialog"] label,
[data-testid="stDialog"] [role="dialog"] div{
    color:#142129;
}
[data-testid="stDialog"] [data-testid="stCaptionContainer"],
[data-testid="stDialog"] .quiet{
    color:#66737B!important;
}
[data-testid="stDialog"] [data-testid="stMetric"]{
    background:#FFF!important;
    border:1px solid #DFE5E0!important;
}
[data-testid="stDialog"] [data-testid="stExpander"]{
    background:#FFF!important;
    border:1px solid #DFE5E0!important;
    border-radius:13px!important;
}
[data-testid="stDialog"] [data-testid="stExpander"] summary{
    color:#123D31!important;
}
[data-testid="stDialog"] .stButton>button,
[data-testid="stDialog"] .stLinkButton>a{
    background:#FFF!important;
    color:#44545B!important;
    border:1px solid #CED7D0!important;
}
[data-testid="stDialog"] .stButton>button[kind="primary"],
[data-testid="stDialog"] .stLinkButton>a[kind="primary"]{
    background:#123D31!important;
    border-color:#123D31!important;
    color:#FFF!important;
}
[data-testid="stDialog"] .stButton>button[kind="primary"] *,
[data-testid="stDialog"] .stLinkButton>a[kind="primary"] *{
    color:#FFF!important;
}
.product-dialog-card{
    background:#FFF;
    border:1px solid #DFE5E0;
    border-radius:16px;
    padding:1rem;
    margin:.65rem 0;
}
.product-dialog-card strong{
    display:block;
    color:#142129;
}
.product-dialog-card span{
    color:#66737B!important;
    display:block;
    margin-top:.2rem;
}
.package-strip{
    display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));
    gap:.7rem;
    margin:.7rem 0 1rem;
}
.package-fact{
    background:#FFF;
    border:1px solid #DFE5E0;
    border-radius:14px;
    padding:.8rem .9rem;
}
.package-fact b{
    display:block;
    color:#66737B;
    font-size:.68rem;
    text-transform:uppercase;
    letter-spacing:.07em;
    margin-bottom:.2rem;
}
.package-fact span{
    color:#142129!important;
    font-weight:720;
}
.pipeline-row-link{
    display:block;
    color:inherit!important;
    text-decoration:none!important;
    border-radius:14px;
    transition:background .15s ease, transform .15s ease;
}
.pipeline-row-link:hover{
    background:#F3F7F4;
    transform:translateY(-1px);
}
.pipeline-row-link .pipeline-row{
    grid-template-columns:minmax(220px,1.8fr) minmax(230px,1.25fr) minmax(135px,.65fr) 76px;
}
.pipeline-status-copy{
    display:block!important;
    font-size:.74rem!important;
    line-height:1.35!important;
    color:#66737B!important;
    margin-top:.28rem!important;
}
.pipeline-open{
    color:#123D31!important;
    font-weight:760!important;
    text-align:right!important;
    white-space:nowrap!important;
}
.settings-v22-copy{
    color:#66737B;
    line-height:1.55;
    max-width:760px;
}
.settings-v22-card{
    background:#FFF;
    border:1px solid #DFE5E0;
    border-radius:16px;
    padding:1rem 1.05rem;
    margin:.55rem 0;
}
.settings-v22-card b{
    color:#142129;
}
.settings-v22-card span{
    display:block;
    color:#66737B;
    margin-top:.22rem;
    line-height:1.45;
}
.system-stat{
    background:#FFF;
    border:1px solid #DFE5E0;
    border-radius:16px;
    padding:1rem 1.05rem;
    min-height:132px;
}
.system-stat .label{
    color:#66737B;
    font-size:.78rem;
    font-weight:720;
}
.system-stat .value{
    color:#142129;
    font-size:clamp(1.8rem,3vw,2.55rem);
    font-weight:780;
    letter-spacing:-.04em;
    margin:.22rem 0;
    white-space:nowrap;
}
.system-stat .help{
    color:#7B878D;
    font-size:.71rem;
    line-height:1.35;
}
.profile-source-grid{
    display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));
    gap:.7rem;
    margin:.7rem 0 1rem;
}
.profile-source{
    background:#FFF;
    border:1px solid #DFE5E0;
    border-radius:15px;
    padding:.9rem 1rem;
}
.profile-source b{
    display:block;
    color:#142129;
}
.profile-source span{
    display:block;
    color:#66737B;
    margin-top:.22rem;
    font-size:.78rem;
    line-height:1.4;
}
.profile-fact-card{
    background:#FFF;
    border:1px solid #DFE5E0;
    border-radius:14px;
    padding:.85rem 1rem;
    margin:.45rem 0;
}
.profile-fact-card b{
    display:block;
    color:#142129;
}
.profile-fact-card span{
    color:#66737B;
    display:block;
    margin-top:.2rem;
    overflow-wrap:anywhere;
}
@media (max-width:900px){
    .package-strip,.profile-source-grid{grid-template-columns:1fr}
    .pipeline-row-link .pipeline-row{grid-template-columns:1fr}
    .pipeline-open{text-align:left!important}
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _consume_query_id(parameter: str, state_key: str) -> int | None:
    """Consume a one-shot query parameter so later filter reruns cannot reopen it."""
    try:
        raw = str(st.query_params.get(parameter) or "").strip()
    except Exception:
        raw = ""

    if raw:
        try:
            st.session_state[state_key] = int(raw)
        except (TypeError, ValueError):
            st.session_state.pop(state_key, None)
        try:
            del st.query_params[parameter]
        except Exception:
            pass

    value = st.session_state.pop(state_key, None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _friendly_status(raw_status: Any, queue_status: Any, *, artifact: bool = False) -> str:
    status = str(raw_status or "").strip().casefold()
    queue = str(queue_status or "").strip().casefold()

    if status in {"submitted", "submission_confirmed", "externally_submitted"}:
        return "Submitted"

    if status in {
        "truth_review_required",
        "ats_review_required",
        "review_required",
        "needs_review",
        "manual_review_required",
        "placement_or_polish_review_required",
        "completed_with_warnings",
        "completed_with_warning",
    }:
        return "Needs review"

    if status in {
        "completed_without_writer",
        "completed_without_resume",
        "writer_not_run",
        "writer_skipped",
    }:
        return "No resume generated"

    if status in {
        "rejected_by_dashboard_targeting",
        "targeting_rejected",
        "blocked",
        "work_authorization_blocked",
        "eligibility_blocked",
    }:
        return "Blocked"

    if status in {"failed", "error"} or queue == "failed":
        return "Failed"

    if queue in {
        "pending",
        "queued",
        "accepted",
        "dispatching",
        "dispatched",
        "running",
        "waiting",
        "processing",
    }:
        return "In progress"

    if queue == "skipped":
        return "Skipped"

    if artifact:
        return "Prepared"

    if status in {
        "application_ready",
        "final_ready",
        "final_ready_deterministic_95_plus",
        "completed",
        "complete",
        "package_prepared",
        "prepared",
    }:
        return "Prepared"

    if queue in {"completed", "complete"}:
        return "Completed"

    if status:
        return "Review status"

    return "Status not recorded"


def _status_description(status: str) -> str:
    descriptions = {
        "Prepared": "A resume or application package artifact is recorded and ready to review.",
        "Needs review": "The workflow finished with a truth, quality, or placement issue that needs your review.",
        "No resume generated": "The workflow completed, but no resume artifact was produced for this run.",
        "In progress": "The guarded preparation pipeline is still processing this job.",
        "Blocked": "Canonical targeting or eligibility evidence prevented normal preparation.",
        "Submitted": "External submission evidence is recorded for this application.",
        "Failed": "The latest preparation attempt ended in a recorded failure.",
        "Skipped": "You passed on this opportunity.",
        "Completed": "The queue completed, but no prepared artifact is recorded in the latest evidence.",
        "Review status": "The workflow has a recorded state that should be inspected before acting.",
        "Status not recorded": "No lifecycle evidence is recorded for this item yet.",
    }
    return descriptions.get(status, "Open the item to review its recorded evidence.")


def _tracker_rows_v22(limit: int = 100) -> list[dict[str, Any]]:
    original = getattr(_PAGES, "_product_v22_original_tracker_rows")
    rows = original(limit=limit)

    # The latest workflow result is authoritative for lifecycle status, but an
    # earlier successful result may hold the prepared resume/cover-letter URLs.
    # Recover those real artifacts without pretending they belong to the latest
    # run. This fixes jobs whose prepared document exists but was hidden by a
    # later review-only result.
    missing_ids = [
        int(row["job_id"])
        for row in rows
        if not (
            _PAGES.safe_link(row.get("resume_pdf_url"))
            or _PAGES.safe_link(row.get("resume_doc_url"))
            or _PAGES.safe_link(row.get("cover_letter_doc_url"))
        )
    ]
    artifact_by_job: dict[int, dict[str, Any]] = {}
    if missing_ids:
        from app.database import get_connection

        connection = get_connection()
        try:
            for job_id in missing_ids:
                record = connection.execute(
                    """SELECT resume_doc_url,resume_pdf_url,cover_letter_doc_url,
                              final_ats_score,completed_at,sent_at
                         FROM n8n_results
                        WHERE job_id=?
                          AND (
                              trim(COALESCE(resume_pdf_url,'')) != ''
                           OR trim(COALESCE(resume_doc_url,'')) != ''
                           OR trim(COALESCE(cover_letter_doc_url,'')) != ''
                          )
                        ORDER BY id DESC
                        LIMIT 1""",
                    (job_id,),
                ).fetchone()
                if record:
                    artifact_by_job[job_id] = dict(record)
        finally:
            connection.close()

    for row in rows:
        fallback = artifact_by_job.get(int(row["job_id"]), {})
        for field in (
            "resume_doc_url",
            "resume_pdf_url",
            "cover_letter_doc_url",
        ):
            if not _PAGES.safe_link(row.get(field)) and _PAGES.safe_link(fallback.get(field)):
                row[field] = fallback[field]
        if row.get("final_ats_score") is None and fallback.get("final_ats_score") is not None:
            row["final_ats_score"] = fallback["final_ats_score"]
        row["artifact_prepared_at"] = fallback.get("completed_at") or fallback.get("sent_at")

        artifact = bool(
            _PAGES.safe_link(row.get("resume_pdf_url"))
            or _PAGES.safe_link(row.get("resume_doc_url"))
            or _PAGES.safe_link(row.get("cover_letter_doc_url"))
        )
        status = _friendly_status(
            row.get("n8n_status"),
            row.get("queue_status"),
            artifact=artifact,
        )
        row["display_status"] = status
        row["status_description"] = _status_description(status)

    return rows


def _job_bundle(job_id: int) -> dict[str, Any] | None:
    from app.database import get_connection

    connection = get_connection()
    try:
        job = connection.execute(
            "SELECT * FROM jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()
        if not job:
            return None

        state = connection.execute(
            """SELECT saved,skipped
                 FROM product_job_state
                WHERE job_id=?""",
            (int(job_id),),
        ).fetchone()

        latest = connection.execute(
            """SELECT id,n8n_status,final_ats_score,resume_doc_url,
                      resume_pdf_url,cover_letter_doc_url,sent_at,completed_at
                 FROM n8n_results
                WHERE job_id=?
                ORDER BY id DESC
                LIMIT 1""",
            (int(job_id),),
        ).fetchone()

        artifact = connection.execute(
            """SELECT id,n8n_status,final_ats_score,resume_doc_url,
                      resume_pdf_url,cover_letter_doc_url,sent_at,completed_at
                 FROM n8n_results
                WHERE job_id=?
                  AND (
                      trim(COALESCE(resume_pdf_url,'')) != ''
                   OR trim(COALESCE(resume_doc_url,'')) != ''
                   OR trim(COALESCE(cover_letter_doc_url,'')) != ''
                  )
                ORDER BY id DESC
                LIMIT 1""",
            (int(job_id),),
        ).fetchone()

        queue = connection.execute(
            """SELECT queue_status,queued_at,updated_at
                 FROM n8n_dispatch_queue
                WHERE job_id=?
                ORDER BY id DESC
                LIMIT 1""",
            (int(job_id),),
        ).fetchone()
    finally:
        connection.close()

    row = dict(job)
    row["saved"] = int(state["saved"]) if state else 0
    row["skipped"] = int(state["skipped"]) if state else 0
    row["latest_result"] = dict(latest) if latest else {}
    row["artifact_result"] = dict(artifact) if artifact else {}
    row["queue"] = dict(queue) if queue else {}
    return row


@st.dialog("Opportunity details", width="large")
def _opportunity_dialog(job_id: int) -> None:
    row = _job_bundle(job_id)
    if not row:
        st.warning("This stored opportunity is no longer available.")
        return

    latest = row["latest_result"]
    artifact = row["artifact_result"]
    queue = row["queue"]

    resume_url = (
        _PAGES.safe_link(artifact.get("resume_pdf_url"))
        or _PAGES.safe_link(artifact.get("resume_doc_url"))
    )
    cover_url = _PAGES.safe_link(artifact.get("cover_letter_doc_url"))
    apply_url = _PAGES.safe_link(row.get("apply_url"))
    has_artifact = bool(resume_url or cover_url)

    status = _friendly_status(
        latest.get("n8n_status"),
        queue.get("queue_status"),
        artifact=has_artifact,
    )

    st.markdown(
        f"## {_PAGES.esc(row.get('title'), 'Untitled role')}",
        unsafe_allow_html=True,
    )
    st.caption(
        f"{row.get('company_name') or 'Company not recorded'} · "
        f"{row.get('location_raw') or 'Location unknown'} · "
        f"{row.get('source') or 'Source unknown'}"
    )

    status_html = (
        f'<div class="product-dialog-card">'
        f'<strong>{_PAGES.esc(status)}</strong>'
        f'<span>{_PAGES.esc(_status_description(status))}</span>'
        f"</div>"
    )
    st.markdown(status_html, unsafe_allow_html=True)

    left, right = st.columns((1.55, 1), gap="large")

    with left:
        st.markdown("### Role summary")
        st.write(
            row.get("description_raw")
            or "A full job description is not stored for this role."
        )

    with right:
        st.metric(
            "Hunter match",
            (
                f"{float(row['hunter_score']):.0f}%"
                if row.get("hunter_score") is not None
                else "Not available"
            ),
        )
        st.markdown(
            f"""<div class="evidence-list">
                <div class="evidence-item"><b>Employment</b>{_PAGES.esc(row.get("employment_type"))}</div>
                <div class="evidence-item"><b>Workplace</b>{_PAGES.esc(row.get("remote_type"))}</div>
                <div class="evidence-item"><b>Target track</b>{_PAGES.esc(row.get("target_track"))}</div>
                <div class="evidence-item"><b>Compensation</b>{_PAGES.esc(row.get("salary_raw"))}</div>
            </div>""",
            unsafe_allow_html=True,
        )

        authorization = str(row.get("work_authorization") or "").strip()
        if authorization:
            st.caption("Work-authorization evidence is recorded.")
            with st.expander(
                "Review work-authorization evidence",
                expanded=False,
            ):
                st.write(authorization)
        else:
            st.caption("Work authorization: not recorded.")

        if row.get("hard_rejection_reason"):
            labeler = getattr(_PAGES, "_research_blocker_label", None)
            blocker = (
                labeler(row["hard_rejection_reason"])
                if callable(labeler)
                else str(row["hard_rejection_reason"])
            )
            st.warning(f"Eligibility evidence: {blocker}")

    st.markdown("### Prepared package")
    if has_artifact:
        ats_value = artifact.get("final_ats_score")
        prepared_at = artifact.get("completed_at") or artifact.get("sent_at")
        st.markdown(
            f"""<div class="package-strip">
                <div class="package-fact"><b>Package state</b><span>{_PAGES.esc(status)}</span></div>
                <div class="package-fact"><b>ATS score</b><span>{_PAGES.esc(ats_value, "Not scored")}</span></div>
                <div class="package-fact"><b>Prepared</b><span>{_PAGES.esc(prepared_at, "Date not recorded")}</span></div>
            </div>""",
            unsafe_allow_html=True,
        )
        package_actions = st.columns(3, gap="small")
        with package_actions[0]:
            if resume_url:
                st.link_button(
                    "Open prepared resume",
                    resume_url,
                    type="primary",
                    use_container_width=True,
                )
            else:
                st.button(
                    "Resume not recorded",
                    disabled=True,
                    use_container_width=True,
                )
        with package_actions[1]:
            if cover_url:
                st.link_button(
                    "Open cover letter",
                    cover_url,
                    use_container_width=True,
                )
            else:
                st.button(
                    "Cover letter not recorded",
                    disabled=True,
                    use_container_width=True,
                )
        with package_actions[2]:
            if apply_url:
                st.link_button(
                    "Open application",
                    apply_url,
                    use_container_width=True,
                )
            else:
                st.button(
                    "Application URL not recorded",
                    disabled=True,
                    use_container_width=True,
                )
    else:
        st.info(
            "No prepared resume or cover-letter artifact is recorded for this "
            "job yet. A completed queue state alone is not shown as a resume."
        )

    st.markdown("### Actions")
    action_columns = st.columns((1, 1, 1, 1.25))
    with action_columns[0]:
        st.button(
            "Saved" if row["saved"] else "Save",
            key=f"v22_dialog_save_{job_id}",
            use_container_width=True,
            on_click=_PAGES._toggle_job_saved,
            args=(job_id, bool(row["saved"])),
        )
    with action_columns[1]:
        st.button(
            "Restore" if row["skipped"] else "Pass",
            key=f"v22_dialog_pass_{job_id}",
            use_container_width=True,
            on_click=_PAGES._toggle_job_skipped,
            args=(job_id, bool(row["skipped"])),
        )
    with action_columns[2]:
        st.button(
            "Prepare",
            key=f"v22_dialog_prepare_{job_id}",
            type="primary",
            use_container_width=True,
            on_click=_PAGES._prepare_job,
            args=(job_id,),
        )
    with action_columns[3]:
        if apply_url:
            st.link_button(
                "Open original job",
                apply_url,
                use_container_width=True,
            )
        else:
            st.button(
                "Original URL unavailable",
                disabled=True,
                use_container_width=True,
            )

    with st.expander("Decision evidence", expanded=False):
        st.caption(
            "Human-readable stored evidence only. Raw extractor JSON is kept out of the normal product experience."
        )
        st.dataframe(
            [{
                "Source": row.get("source"),
                "Workplace": row.get("remote_type"),
                "Date posted": row.get("date_posted"),
                "Target track": row.get("target_track"),
                "Eligibility blocker": row.get("hard_rejection_reason"),
            }],
            hide_index=True,
            use_container_width=True,
        )


def _job_detail_v22() -> None:
    job_id = _consume_query_id("job", "_product_v22_job_once")
    if job_id is not None:
        _opportunity_dialog(job_id)


def _pipeline_list_v22(records: list[dict[str, Any]]) -> None:
    rows: list[str] = []
    for record in records:
        hunter = (
            f"{float(record['hunter_score']):.0f}% match"
            if record.get("hunter_score") is not None
            else "Match not scored"
        )
        ats = (
            f"ATS {float(record['final_ats_score']):.0f}"
            if record.get("final_ats_score") is not None
            else "ATS not scored"
        )
        updated = (
            record.get("completed_at")
            or record.get("updated_at")
            or record.get("queued_at")
            or "Date not recorded"
        )
        job_id = int(record["job_id"])
        rows.append(
            f"""<a class="pipeline-row-link"
                    href="?view=tracker&amp;pipeline_job={job_id}"
                    target="_self"
                    aria-label="Open {_PAGES.esc(record.get('title'), 'application item')}">
                <div class="pipeline-row">
                    <div>
                        <strong>{_PAGES.esc(record.get("company_name"), "Company not recorded")}</strong>
                        <span>{_PAGES.esc(record.get("title"), "Untitled role")}</span>
                    </div>
                    <div>
                        <span class="status-chip">{_PAGES.esc(record.get("display_status"))}</span>
                        <span class="pipeline-status-copy">{_PAGES.esc(record.get("status_description"))}</span>
                        <span class="pipeline-meta">{_PAGES.esc(updated)}</span>
                    </div>
                    <div>
                        <strong>{_PAGES.esc(hunter)}</strong>
                        <span class="pipeline-meta">{_PAGES.esc(ats)}</span>
                    </div>
                    <div class="pipeline-open">Open →</div>
                </div>
            </a>"""
        )

    st.markdown(
        f'<div class="table-shell">{"".join(rows)}</div>',
        unsafe_allow_html=True,
    )


def _tracker_detail_v22() -> None:
    job_id = _consume_query_id(
        "pipeline_job",
        "_product_v22_pipeline_once",
    )
    if job_id is not None:
        _opportunity_dialog(job_id)


def tracker_v22() -> None:
    _PAGES.page_intro(
        "TRACKER",
        "Your application workspace",
        "Open any row to see the actual package, ATS score, application link, "
        "and the reason for its current lifecycle state.",
    )

    tab = st.radio(
        "Tracker view",
        ["Pipeline", "Inbox"],
        horizontal=True,
        label_visibility="collapsed",
        key="product_tracker_tab",
        on_change=_PAGES._sync_subroute,
        args=(
            "tab",
            "product_tracker_tab",
            {"Pipeline": "pipeline", "Inbox": "inbox"},
        ),
    )

    if tab == "Inbox":
        _PAGES._inbox()
        return

    records = _tracker_rows_v22(limit=250)
    preferred = [
        "Prepared",
        "Needs review",
        "No resume generated",
        "In progress",
        "Blocked",
        "Submitted",
        "Failed",
        "Skipped",
        "Completed",
        "Review status",
        "Status not recorded",
    ]
    represented = list(
        dict.fromkeys(
            str(record.get("display_status") or "Status not recorded")
            for record in records
        )
    )
    statuses = ["All"]
    statuses.extend(
        status
        for status in preferred
        if status in represented
    )
    statuses.extend(
        status
        for status in represented
        if status not in statuses
    )

    filters, search = st.columns((2.5, 1.5), gap="medium")
    with filters:
        selected = st.radio(
            "Pipeline status",
            statuses,
            horizontal=True,
            label_visibility="collapsed",
        )
    with search:
        query = st.text_input(
            "Search pipeline",
            placeholder="Search company or role",
            label_visibility="collapsed",
        )

    needle = query.strip().casefold()
    visible = [
        record
        for record in records
        if (
            selected == "All"
            or record["display_status"] == selected
        )
        and (
            not needle
            or needle in (
                f"{record.get('company_name') or ''} "
                f"{record.get('title') or ''}"
            ).casefold()
        )
    ]

    if visible:
        st.caption(
            "Select any row. Status labels describe the recorded product state; "
            "Submitted is used only when external submission evidence exists."
        )
        _pipeline_list_v22(visible)
    else:
        st.markdown(
            '<div class="empty-product"><h3>No pipeline items match this '
            'filter.</h3><p>Try another lifecycle state or search term.</p></div>',
            unsafe_allow_html=True,
        )

    _tracker_detail_v22()


def _extension_profile_record() -> dict[str, Any]:
    """Read a server-side MUNSHI Apply profile only if a bridge has stored one."""
    from app.database import get_setting

    for key in (
        "munshi_apply_master_profile_v1",
        "munshi_apply_profile_v1",
    ):
        value = get_setting(key, {})
        if isinstance(value, dict) and value.get("facts"):
            return value
    return {}


def _render_profile_facts(facts: list[dict[str, Any]]) -> None:
    if not facts:
        st.info(
            "No structured Hunter profile facts are synchronized yet. "
            "The dashboard will not invent values from job data."
        )
        return

    for fact in facts:
        try:
            value = json.loads(str(fact.get("value_json") or '""'))
        except (TypeError, ValueError, json.JSONDecodeError):
            value = fact.get("value_json")
        if isinstance(value, list):
            value_text = " · ".join(str(item) for item in value)
        else:
            value_text = str(value)
        st.markdown(
            f"""<div class="profile-fact-card">
                <b>{_PAGES.esc(fact.get("fact_key"), "Profile fact")}</b>
                <span>{_PAGES.esc(value_text, "Not available")}</span>
                <span>Source: {_PAGES.esc(fact.get("source_label"), "Candidate")}</span>
            </div>""",
            unsafe_allow_html=True,
        )


def profile_v22() -> None:
    _PAGES.page_intro(
        "PROFILE",
        "Your candidate workspace",
        "Master resume, tailored application artifacts, and verified profile "
        "facts are separated so nothing is silently inferred.",
    )

    tab = st.radio(
        "Profile section",
        ["Resume", "Cover letters", "Profile details"],
        horizontal=True,
        label_visibility="collapsed",
        key="product_profile_tab",
        on_change=_PAGES._sync_subroute,
        args=(
            "tab",
            "product_profile_tab",
            {
                "Resume": "resume",
                "Cover letters": "cover-letter",
                "Profile details": "details",
            },
        ),
    )

    records = _tracker_rows_v22(limit=400)
    resume_artifacts = [
        row
        for row in records
        if (
            _PAGES.safe_link(row.get("resume_pdf_url"))
            or _PAGES.safe_link(row.get("resume_doc_url"))
        )
    ]
    cover_artifacts = [
        row
        for row in records
        if _PAGES.safe_link(row.get("cover_letter_doc_url"))
    ]
    facts = _PAGES.candidate_facts()
    designated = _PAGES.master_resume()
    extension_profile = _extension_profile_record()

    if tab == "Profile details":
        st.markdown(
            f"""<div class="profile-source-grid">
                <div class="profile-source">
                    <b>Hunter profile facts</b>
                    <span>{len(facts)} structured candidate-provided fact(s) stored on this server.</span>
                </div>
                <div class="profile-source">
                    <b>Master resume</b>
                    <span>{"Designated and available." if designated else "Not designated yet."}</span>
                </div>
                <div class="profile-source">
                    <b>MUNSHI Apply profile</b>
                    <span>{"Server-side profile bridge data is available." if extension_profile else "Extension profile is browser-local; no server sync record is available yet."}</span>
                </div>
            </div>""",
            unsafe_allow_html=True,
        )

        if extension_profile:
            st.markdown("### MUNSHI Apply verified profile")
            safe_facts = []
            for fact in extension_profile.get("facts", []):
                if not isinstance(fact, dict):
                    continue
                if fact.get("protected"):
                    continue
                category = str(fact.get("category") or "")
                if category == "VOLUNTARY_DEMOGRAPHIC":
                    continue
                safe_facts.append(fact)

            if safe_facts:
                for fact in safe_facts[:40]:
                    value = fact.get("value")
                    if isinstance(value, list):
                        value = " · ".join(str(item) for item in value)
                    st.markdown(
                        f"""<div class="profile-fact-card">
                            <b>{_PAGES.esc(fact.get("key"), "Profile fact")}</b>
                            <span>{_PAGES.esc(value, "Not available")}</span>
                            <span>{_PAGES.esc(fact.get("category"), "Profile")} · {_PAGES.esc(fact.get("trustLevel"), "Unknown trust")}</span>
                        </div>""",
                        unsafe_allow_html=True,
                    )
            else:
                st.info(
                    "A MUNSHI Apply profile record exists, but no non-protected "
                    "facts are available for display."
                )

        st.markdown("### Hunter profile details")
        _render_profile_facts(facts)

        with st.expander("Add or update a candidate fact", expanded=False):
            existing = {
                str(fact["fact_key"]): fact
                for fact in facts
            }
            selected_label = st.selectbox(
                "Edit a saved fact",
                ["Add a new fact", *existing],
                key="product_v22_profile_fact_select",
            )
            selected = existing.get(selected_label)
            selected_value = ""
            if selected:
                try:
                    selected_value = str(
                        json.loads(
                            str(selected.get("value_json") or '""')
                        )
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    selected_value = ""

            with st.form("product_v22_candidate_fact_form"):
                key = st.text_input(
                    "Fact label",
                    value=selected_label if selected else "",
                    placeholder="Preferred roles",
                )
                value = st.text_area(
                    "Value",
                    value=selected_value,
                    placeholder="HR operations, people analytics",
                )
                if st.form_submit_button(
                    "Save profile fact",
                    type="primary",
                ):
                    try:
                        _PAGES.save_candidate_fact(key, value)
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.success("Candidate fact saved.")
                        st.rerun()
        return

    if tab == "Cover letters":
        st.markdown("### Prepared cover letters")
        if not cover_artifacts:
            st.info("No recorded cover-letter artifacts are available yet.")
            return
        for artifact in cover_artifacts:
            with st.container(
                key=f"product_v22_cover_{artifact['job_id']}",
                border=True,
            ):
                description, action = st.columns((3, 1))
                with description:
                    st.markdown(
                        f"**{_PAGES.esc(artifact.get('company_name'))} · "
                        f"{_PAGES.esc(artifact.get('title'))}**",
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        f"{artifact.get('display_status')} · "
                        f"{artifact.get('status_description')}"
                    )
                with action:
                    st.link_button(
                        "Open cover letter",
                        _PAGES.safe_link(
                            artifact["cover_letter_doc_url"]
                        ),
                        use_container_width=True,
                    )
        return

    st.markdown("### Master resume")
    if designated:
        with st.container(
            key="product_v22_master_resume",
            border=True,
        ):
            st.markdown(
                f"**{_PAGES.esc(designated.get('label') or 'Master resume')}**",
                unsafe_allow_html=True,
            )
            st.caption(
                "Explicitly designated master artifact · "
                + str(
                    designated.get("designated_at")
                    or "date not recorded"
                )
            )
            actions = st.columns((1, 1, 2))
            with actions[0]:
                st.link_button(
                    "Open master resume",
                    _PAGES.safe_link(designated["url"]),
                    type="primary",
                    use_container_width=True,
                )
            with actions[1]:
                if st.button(
                    "Clear designation",
                    key="product_v22_clear_master",
                    use_container_width=True,
                ):
                    _PAGES.clear_master_resume()
                    st.rerun()
            with actions[2]:
                st.caption(
                    "Master and tailored resumes remain separate. "
                    "MUNSHI never silently replaces your master resume."
                )
            if st.toggle(
                "Preview master resume",
                key="product_v22_master_preview",
            ):
                _PAGES.components.iframe(
                    designated["url"],
                    height=760,
                    scrolling=True,
                )
    else:
        st.markdown(
            '<div class="empty-product"><h3>No master resume designated</h3>'
            '<p>Choose a real prepared resume artifact below. MUNSHI will not '
            'guess which document is your master.</p></div>',
            unsafe_allow_html=True,
        )

    if resume_artifacts and not designated:
        options = {
            (
                f"{row.get('company_name') or 'Company not recorded'} — "
                f"{row.get('title') or 'Untitled role'} · ATS "
                f"{row.get('final_ats_score') if row.get('final_ats_score') is not None else 'N/A'}"
            ): row
            for row in resume_artifacts
        }
        chosen = st.selectbox(
            "Choose an existing prepared resume",
            list(options),
            key="product_v22_master_choice",
        )
        selected = options[chosen]
        if st.button(
            "Set selected resume as master",
            type="primary",
            key="product_v22_set_master",
        ):
            resume_url = (
                _PAGES.safe_link(selected.get("resume_pdf_url"))
                or _PAGES.safe_link(selected.get("resume_doc_url"))
            )
            _PAGES.save_master_resume(
                int(selected["job_id"]),
                resume_url,
                chosen,
            )
            st.rerun()

    st.markdown("### Tailored resume history")
    if not resume_artifacts:
        st.info("No recorded resume artifacts are available yet.")
        return

    for artifact in resume_artifacts:
        resume_url = (
            _PAGES.safe_link(artifact.get("resume_pdf_url"))
            or _PAGES.safe_link(artifact.get("resume_doc_url"))
        )
        with st.container(
            key=f"product_v22_resume_{artifact['job_id']}",
            border=True,
        ):
            description, action = st.columns((3, 1))
            with description:
                st.markdown(
                    f"**{_PAGES.esc(artifact.get('company_name'))} · "
                    f"{_PAGES.esc(artifact.get('title'))}**",
                    unsafe_allow_html=True,
                )
                ats = (
                    artifact.get("final_ats_score")
                    if artifact.get("final_ats_score") is not None
                    else "Not available"
                )
                st.caption(
                    f"ATS {ats} · {artifact.get('display_status')} · "
                    f"{artifact.get('status_description')}"
                )
            with action:
                st.link_button(
                    "Open resume",
                    resume_url,
                    use_container_width=True,
                )


def _stat_card(label: str, value: Any, help_text: str) -> None:
    st.markdown(
        f"""<div class="system-stat">
            <div class="label">{_PAGES.esc(label)}</div>
            <div class="value">{_PAGES.esc(value, "0")}</div>
            <div class="help">{_PAGES.esc(help_text)}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def advanced_v22() -> None:
    snapshot = _PAGES.research_snapshot()
    lifetime = snapshot.get("lifetime") or {}
    headline = snapshot.get("headline") or {}

    st.markdown("### System intelligence")
    st.markdown(
        '<div class="settings-v22-copy">These are real evidence-backed totals. '
        'Cumulative provider telemetry is kept separate from the current '
        'deduplicated job inventory so the dashboard does not inflate counts.'
        "</div>",
        unsafe_allow_html=True,
    )

    stats = [
        (
            "Opportunities scanned",
            f"{int(lifetime.get('scanned') or 0):,}",
            "Cumulative provider records observed across source runs.",
        ),
        (
            "Normalized records",
            f"{int(lifetime.get('normalized') or 0):,}",
            "Records successfully normalized by source telemetry.",
        ),
        (
            "Jobs stored",
            f"{int(headline.get('jobs') or 0):,}",
            "Current canonical deduplicated opportunity inventory.",
        ),
        (
            "Eligible telemetry",
            f"{int(lifetime.get('eligible') or 0):,}",
            "Source-run records counted eligible in telemetry.",
        ),
        (
            "Recorded source runs",
            f"{int(lifetime.get('runs') or 0):,}",
            "Durable discovery source-run records.",
        ),
        (
            "Targeting decisions",
            f"{int(lifetime.get('decisions') or 0):,}",
            "Canonical targeting decisions recorded.",
        ),
        (
            "Telegram-delivered jobs",
            f"{int(lifetime.get('jobs_delivered') or 0):,}",
            "Opportunity cards with durable delivery evidence.",
        ),
        (
            "ATS-scored packages",
            f"{int(snapshot.get('ats', {}).get('scored_packages') or 0):,}",
            "Prepared package results with an ATS score.",
        ),
        (
            "Explicitly blocked",
            f"{int(headline.get('blocked') or 0):,}",
            "Stored jobs with an explicit blocker.",
        ),
    ]

    for start in range(0, len(stats), 3):
        columns = st.columns(3, gap="medium")
        for column, stat in zip(columns, stats[start:start + 3]):
            with column:
                _stat_card(*stat)

    telemetry = snapshot.get("source_telemetry") or []
    if telemetry:
        st.markdown("### Source telemetry")
        st.dataframe(
            [{
                "Source": row["source"],
                "Runs": row["runs"],
                "Scanned": row["scanned"],
                "Normalized": row["normalized"],
                "Eligible": row["eligible"],
                "New eligible": row["new_eligible"],
                "Last run": row["last_run"],
            } for row in telemetry],
            hide_index=True,
            use_container_width=True,
        )

    health = snapshot.get("health") or []
    if health:
        st.markdown("### Provider health")
        st.dataframe(
            [{
                "Source": row["source_name"],
                "Health": row["health_status"],
                "Last success": row["last_success_at"],
                "Last run yield": row["jobs_found_last_run"],
            } for row in health],
            hide_index=True,
            use_container_width=True,
        )

    with st.expander(
        "Engineering diagnostics",
        expanded=False,
    ):
        st.caption(
            "Low-level localhost probes and raw engineering pages live here "
            "only for maintenance. They are not customer-facing product status."
        )
        from app import operations_dashboard as legacy

        tool = st.selectbox(
            "Diagnostic tool",
            [
                "None",
                "System / Diagnostics",
                "Source Health",
                "Adapter Coverage",
                "Targeting",
                "Query Performance",
                "Queue / Actions",
                "Storage",
                "Backups",
                "Credentials",
            ],
            key="product_v22_legacy_tool",
        )
        renderers = {
            "System / Diagnostics": legacy._system_diagnostics,
            "Source Health": legacy._source_health,
            "Adapter Coverage": legacy._adapter_coverage,
            "Targeting": legacy._targeting,
            "Query Performance": legacy._query_performance,
            "Queue / Actions": legacy._queue_actions,
            "Storage": legacy._storage,
            "Backups": legacy._backups,
            "Credentials": legacy._credentials,
        }
        if tool != "None":
            renderers[tool]()


def settings_v22() -> None:
    _PAGES.page_intro(
        "PREFERENCES",
        "Settings",
        "Readable product controls first. Engineering diagnostics are available "
        "only when you intentionally open them.",
    )

    options = [
        "Apply",
        "Automation",
        "Integrations",
        "Profile & defaults",
        "Credentials",
        "Advanced / System",
    ]

    left, content = st.columns((1.05, 3.95), gap="large")
    with left:
        section = st.radio(
            "Settings section",
            options,
            label_visibility="collapsed",
            key="product_settings_section",
            on_change=_PAGES._sync_subroute,
            args=(
                "section",
                "product_settings_section",
                {
                    "Apply": "apply",
                    "Automation": "automation",
                    "Integrations": "integrations",
                    "Profile & defaults": "profile",
                    "Credentials": "credentials",
                    "Advanced / System": "advanced",
                },
            ),
        )

    with content:
        if section == "Apply":
            policy = _PAGES.volume_policy()
            st.markdown("### Review before preparation")
            st.markdown(
                '<div class="settings-v22-card"><b>What this controls</b>'
                '<span>This preference changes whether you want a review step '
                'before action. Targeting, work authorization, dedupe, ATS, '
                'and provider safety gates remain authoritative.</span></div>',
                unsafe_allow_html=True,
            )
            with st.form("product_v22_review_form"):
                review = st.checkbox(
                    "Require review before action",
                    value=policy["review_first"],
                )
                if st.form_submit_button(
                    "Save review preference",
                    type="primary",
                ):
                    _PAGES.save_review_preference(review)
                    st.success("Review preference saved.")

        elif section == "Automation":
            policy = _PAGES.volume_policy()
            current_lanes = _PAGES.lanes()
            st.markdown("### Automation")
            st.markdown(
                f"""<div class="settings-v22-card">
                    <b>Current mode: {_PAGES.esc(policy["mode"].replace("_", " ").title())}</b>
                    <span>{sum(bool(lane["enabled"]) for lane in current_lanes)} of {len(current_lanes)} target lane(s) enabled. Unlimited means no MUNSHI business quota; provider and safety controls still apply.</span>
                </div>""",
                unsafe_allow_html=True,
            )
            st.markdown(
                '<a class="product-nav-link" href="?view=auto-prepare" '
                'target="_self">Open Auto Prepare →</a>',
                unsafe_allow_html=True,
            )

        elif section == "Integrations":
            st.markdown("### Integrations")
            extension = _extension_profile_record()
            st.markdown(
                f"""<div class="settings-v22-card">
                    <b>MUNSHI Apply profile bridge</b>
                    <span>{"Server-side MasterProfile data is available." if extension else "No server-side MasterProfile sync record is available. The browser extension keeps its MasterProfile locally until a secure sync bridge is enabled."}</span>
                </div>""",
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div class="settings-v22-card"><b>Gmail read-only</b>'
                '<span>Connect Gmail to synchronize application confirmations, '
                'assessments, interviews, offers, rejections, and reminders. '
                'This integration does not send email.</span></div>',
                unsafe_allow_html=True,
            )
            _PAGES._inbox()

        elif section == "Profile & defaults":
            facts = _PAGES.candidate_facts()
            designated = _PAGES.master_resume()
            st.markdown("### Profile & defaults")
            st.markdown(
                f"""<div class="settings-v22-card">
                    <b>{len(facts)} structured profile fact(s)</b>
                    <span>Master resume: {"configured" if designated else "not configured"}. Open Profile to review source-backed facts and prepared artifacts.</span>
                </div>""",
                unsafe_allow_html=True,
            )
            st.markdown(
                '<a class="product-nav-link" '
                'href="?view=profile&amp;tab=details" target="_self">'
                'Open Profile details →</a>',
                unsafe_allow_html=True,
            )

        elif section == "Credentials":
            from app.secure_vault import vault_available

            st.markdown("### Credentials")
            if vault_available():
                st.success(
                    "Encrypted credential storage is available on this server."
                )
            else:
                st.warning(
                    "Encrypted credential storage is not configured on this server."
                )
            st.markdown(
                '<div class="settings-v22-card"><b>Credential policy</b>'
                '<span>Passwords are never displayed in plaintext. ATS '
                'credentials become editable only through the encrypted vault '
                'and explicit user actions.</span></div>',
                unsafe_allow_html=True,
            )

        elif section == "Advanced / System":
            advanced_v22()


def install_product_v22(pages_module: Any) -> None:
    """Install V2.2 presentation overrides exactly once per interpreter."""
    global _PAGES

    if getattr(pages_module, "_product_v22_installed", False):
        _PAGES = pages_module
        return

    _PAGES = pages_module
    pages_module._product_v22_original_tracker_rows = pages_module.tracker_rows
    pages_module.tracker_rows = _tracker_rows_v22
    pages_module._job_detail = _job_detail_v22
    pages_module._pipeline_list = _pipeline_list_v22
    pages_module.tracker = tracker_v22
    pages_module.profile = profile_v22
    pages_module.settings = settings_v22
    pages_module._advanced = advanced_v22
    pages_module._product_v22_installed = True
