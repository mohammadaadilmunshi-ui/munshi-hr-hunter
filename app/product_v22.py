
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
from urllib.parse import urlparse

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

/* V2.3: job details use a context-preserving right-side glass drawer only. */
@keyframes munshiJobDrawerIn{
    from{transform:translateX(34px);opacity:.78}
    to{transform:translateX(0);opacity:1}
}
.job-detail-drawer-marker{display:none!important}

/*
 * V2.4 liquid glass:
 * Streamlit's stDialog wrapper spans the whole viewport. V2.3 blurred the
 * modal layer but the opaque stDialog wrapper still hid the job grid.
 * Make only the Opportunity drawer wrapper transparent, then glass the true
 * modal backdrop from body:has(...), which works across the portal boundary.
 */
body:has(.job-detail-drawer-marker) div[data-baseweb="modal"]{
    background:
        linear-gradient(128deg,rgba(255,255,255,.10),rgba(255,255,255,.03)),
        rgba(232,240,236,.20)!important;
    backdrop-filter:blur(6px) saturate(1.12) brightness(1.01)!important;
    -webkit-backdrop-filter:blur(6px) saturate(1.12) brightness(1.01)!important;
}
body:has(.job-detail-drawer-marker) div[data-baseweb="modal"]::before{
    content:"";
    position:fixed;
    inset:0;
    pointer-events:none;
    background:
        linear-gradient(115deg,rgba(255,255,255,.12),transparent 32%,rgba(255,255,255,.04) 62%,transparent 82%);
    mix-blend-mode:screen;
}
[data-testid="stDialog"]:has(.job-detail-drawer-marker),
[data-testid="stDialog"]:has(.job-detail-drawer-marker)>div{
    background:transparent!important;
}
[data-testid="stDialog"]:has(.job-detail-drawer-marker) [role="dialog"]{
    position:fixed!important;
    top:0!important;
    right:0!important;
    bottom:0!important;
    left:auto!important;
    width:min(640px,94vw)!important;
    max-width:min(640px,94vw)!important;
    height:100dvh!important;
    max-height:100dvh!important;
    margin:0!important;
    border-radius:26px 0 0 26px!important;
    border-right:0!important;
    border-color:rgba(206,215,208,.92)!important;
    background:
        linear-gradient(145deg,rgba(255,255,255,.92),rgba(246,250,247,.84))!important;
    box-shadow:
        -28px 0 78px rgba(20,33,41,.18),
        inset 1px 0 0 rgba(255,255,255,.82)!important;
    backdrop-filter:blur(26px) saturate(1.14)!important;
    -webkit-backdrop-filter:blur(26px) saturate(1.14)!important;
    overflow-y:auto!important;
    overscroll-behavior:contain;
    animation:munshiJobDrawerIn .22s cubic-bezier(.2,.72,.24,1) both;
}
[data-testid="stDialog"]:has(.job-detail-drawer-marker) [role="dialog"] h2{
    font-size:clamp(1.55rem,2.3vw,2rem)!important;
    line-height:1.16!important;
    font-weight:700!important;
    letter-spacing:-.035em!important;
}
[data-testid="stDialog"]:has(.job-detail-drawer-marker) [role="dialog"] h3{
    margin-top:1.15rem!important;
}
[data-testid="stDialog"]:has(.job-detail-drawer-marker) [data-testid="stDialogContent"]{
    padding-bottom:1rem!important;
}
[data-testid="stDialog"]:has(.job-detail-drawer-marker) .product-dialog-card{
    background:rgba(255,255,255,.76)!important;
    box-shadow:0 10px 30px rgba(20,33,41,.045)!important;
}
[class*="st-key-job_drawer_actions_"]{
    position:sticky!important;
    bottom:0!important;
    z-index:30!important;
    margin:1rem -.2rem -.15rem!important;
    padding:.85rem .2rem .35rem!important;
    background:linear-gradient(180deg,rgba(250,251,249,.58),rgba(250,251,249,.96) 28%)!important;
    backdrop-filter:blur(16px)!important;
    -webkit-backdrop-filter:blur(16px)!important;
    border-top:1px solid rgba(223,229,224,.88)!important;
}
@media (max-width:700px){
    [data-testid="stDialog"]:has(.job-detail-drawer-marker) [role="dialog"]{
        width:100vw!important;
        max-width:100vw!important;
        border-radius:0!important;
        border-left:0!important;
    }
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
        return status.replace("_", " ").title()

    return "Queued"


def _status_description(raw_status: Any, queue_status: Any, *, artifact: bool = False) -> str:
    display = _friendly_status(raw_status, queue_status, artifact=artifact)
    descriptions = {
        "Submitted": "The employer submission has been confirmed by external evidence.",
        "Prepared": "The application package was prepared successfully.",
        "Completed": "The recorded preparation run completed.",
        "Needs review": "Preparation stopped for a review decision or truth check.",
        "No resume generated": "The run completed without a usable resume artifact.",
        "Blocked": "A targeting, eligibility, or safety gate stopped this opportunity.",
        "Failed": "The latest recorded run failed before completion.",
        "In progress": "Preparation is currently running or waiting in the queue.",
        "Skipped": "The queue intentionally skipped this item.",
        "Queued": "This item is waiting for preparation.",
    }
    return descriptions.get(display, "Recorded workflow state: " + display + ".")


def _tracker_rows_v22() -> list[dict[str, Any]]:
    rows = _PAGES._product_v22_original_tracker_rows()
    result: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        display = _friendly_status(
            copy.get("status"),
            copy.get("queue_status"),
            artifact=bool(copy.get("artifact")),
        )
        copy["display_status"] = display
        copy["status_description"] = _status_description(
            copy.get("status"),
            copy.get("queue_status"),
            artifact=bool(copy.get("artifact")),
        )
        result.append(copy)
    return result


def _provider_name(url: Any) -> str:
    value = str(url or "").casefold()
    if "greenhouse" in value:
        return "Greenhouse"
    if "lever.co" in value or "lever.co/" in value:
        return "Lever"
    if "ashbyhq" in value:
        return "Ashby"
    if "smartrecruiters" in value:
        return "SmartRecruiters"
    if "workday" in value or "myworkdayjobs" in value:
        return "Workday"
    return "Employer site"


def _compact_reasons(detail: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    reasons = [
        str(item).strip()
        for item in detail.get("fit_reasons") or candidate.get("fit_reasons") or []
        if str(item).strip()
    ]
    if reasons:
        return reasons[:3]

    fallback: list[str] = []
    for key in ("targeting_reason", "eligibility_reason"):
        value = str(candidate.get(key) or "").strip()
        if value:
            fallback.append(value.replace("_", " ").capitalize())
    if not fallback:
        fallback.append("No deterministic fit rationale was recorded yet.")
    return fallback[:3]


def _score_summary(candidate: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "Match": _PAGES.percent_value(detail.get("score") or candidate.get("score")),
        "Targeting": str(candidate.get("targeting_status") or "Not scored").replace("_", " ").title(),
        "Eligibility": str(candidate.get("eligibility_status") or "Not scored").replace("_", " ").title(),
    }


def _safe_lines(value: Any) -> list[str]:
    if isinstance(value, str):
        return [line.strip(" •\t") for line in value.splitlines() if line.strip(" •\t")]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _fact_strip(items: list[tuple[str, Any]]) -> None:
    blocks = []
    for label, value in items:
        blocks.append(
            f'<div class="package-fact"><b>{_PAGES.esc(label)}</b>'
            f'<span>{_PAGES.esc(value, "Not available")}</span></div>'
        )
    st.markdown(
        '<div class="package-strip">' + "".join(blocks) + "</div>",
        unsafe_allow_html=True,
    )


def _job_detail_v22(job_id: int) -> None:
    candidate = _PAGES.job_by_id(job_id)
    if candidate is None:
        st.warning("This opportunity is no longer available in the current result set.")
        return

    detail = _PAGES.job_detail_payload(candidate)
    st.markdown('<div class="job-detail-drawer-marker"></div>', unsafe_allow_html=True)
    company = detail.get("company_name") or candidate.get("company_name")
    title = detail.get("job_title") or candidate.get("job_title")
    provider = _provider_name(detail.get("application_link") or candidate.get("application_link"))

    st.caption(f"{company or 'Employer'} · {provider}")
    st.markdown(f"## {_PAGES.esc(title, 'Opportunity')}")
    st.caption(
        " · ".join(
            item for item in (
                str(detail.get("location") or candidate.get("location") or "").strip(),
                str(detail.get("work_arrangement") or candidate.get("work_arrangement") or "").strip(),
                str(detail.get("employment_type") or candidate.get("employment_type") or "").strip(),
            ) if item
        )
        or "Job details captured by MUNSHI."
    )

    score = _score_summary(candidate, detail)
    _fact_strip([
        ("Match", score["Match"]),
        ("Targeting", score["Targeting"]),
        ("Eligibility", score["Eligibility"]),
    ])

    with st.expander("Why MUNSHI ranked this", expanded=True):
        reasons = _compact_reasons(detail, candidate)
        for reason in reasons:
            st.write(f"• {reason}")

    description = _safe_lines(detail.get("job_description") or candidate.get("job_description"))
    if description:
        st.markdown("### Role summary")
        st.write("\n".join(description[:5]))

    for heading, key in (
        ("Responsibilities", "responsibilities"),
        ("Qualifications", "qualifications"),
        ("Preferred", "preferred_skills"),
    ):
        values = _safe_lines(detail.get(key))
        if not values:
            continue
        with st.expander(heading, expanded=False):
            for item in values[:12]:
                st.write(f"• {item}")

    work_auth = str(detail.get("work_authorization") or "").strip()
    if work_auth:
        st.markdown("### Work authorization")
        st.write(work_auth)

    with st.expander("Captured fields", expanded=False):
        _fact_strip([
            ("Compensation", detail.get("pay") or candidate.get("salary")),
            ("Posted", detail.get("posted_date") or candidate.get("posted_date")),
            ("Deadline", detail.get("apply_deadline")),
        ])
        if detail.get("employer_description"):
            st.write(detail["employer_description"])

    with st.container(
        key=f"job_drawer_actions_{job_id}",
    ):
        columns = st.columns((1, 1.1))
        with columns[0]:
            st.link_button(
                "Open employer site",
                _PAGES.safe_link(
                    detail.get("application_link") or candidate.get("application_link")
                ),
                use_container_width=True,
            )
        with columns[1]:
            if st.button(
                "Prepare this job",
                key=f"detail_prepare_v22_{job_id}",
                type="primary",
                use_container_width=True,
            ):
                st.session_state["product_prepare_job_id"] = job_id
                st.session_state["product_view"] = "auto-prepare"
                st.query_params["view"] = "auto-prepare"
                st.rerun()


def _pipeline_list_v22(rows: list[dict[str, Any]]) -> None:
    if not rows:
        st.info("No preparation history matches the current filters.")
        return

    for row in rows[:120]:
        href = _PAGES.product_url(
            "tracker",
            tab="pipeline",
            application=str(row["id"]),
        )
        updated = _PAGES.short_time(row.get("updated_at"))
        subline = " · ".join(
            item for item in (
                updated,
                str(row.get("location") or "").strip(),
            ) if item
        ) or "Recorded by MUNSHI"
        st.markdown(
            f"""
            <a class="pipeline-row-link" href="{href}" target="_self">
                <div class="pipeline-row">
                    <div class="pipeline-primary"><b>{_PAGES.esc(row["company_name"] or "Company")} · {_PAGES.esc(row["title"] or "Untitled role")}</b><span>{_PAGES.esc(subline)}</span></div>
                    <div><span class="pipeline-status">{_PAGES.esc(row["display_status"])}</span><span class="pipeline-status-copy">{_PAGES.esc(row["status_description"])}</span></div>
                    <div><span class="fit-chip">{_PAGES.esc(row.get("score") or "—")} fit</span></div>
                    <div class="pipeline-open">Open →</div>
                </div>
            </a>
            """,
            unsafe_allow_html=True,
        )


def _job_ids_from_runs(runs: list[dict[str, Any]]) -> list[int]:
    values: list[int] = []
    for run in runs:
        try:
            value = int(run.get("job_id"))
        except (TypeError, ValueError):
            continue
        if value not in values:
            values.append(value)
    return values


def _latest_run(runs: list[dict[str, Any]], job_id: int) -> dict[str, Any] | None:
    relevant = [run for run in runs if run.get("job_id") == job_id]
    return relevant[0] if relevant else None


def _package_payload(job_id: int) -> dict[str, Any]:
    rows = _PAGES.application_rows(limit=500)
    runs = _PAGES.preparation_runs(limit=700)
    row = next((item for item in rows if item["id"] == job_id), None)
    run = _latest_run(runs, job_id)
    return {
        "row": row,
        "run": run,
    }


def _package_artifacts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    row = payload.get("row") or {}
    run = payload.get("run") or {}
    artifacts: list[dict[str, Any]] = []
    if row.get("resume_doc_url"):
        artifacts.append({"label": "Resume", "url": row["resume_doc_url"]})
    if row.get("cover_letter_doc_url"):
        artifacts.append({"label": "Cover letter", "url": row["cover_letter_doc_url"]})
    raw = run.get("artifacts_json")
    if raw:
        try:
            values = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            values = []
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value.strip():
                    artifacts.append({"label": "Artifact", "url": value.strip()})
                elif isinstance(value, dict):
                    url = value.get("url") or value.get("path")
                    if url:
                        artifacts.append({
                            "label": value.get("label") or value.get("type") or "Artifact",
                            "url": url,
                        })
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for artifact in artifacts:
        url = str(artifact["url"])
        if url in seen:
            continue
        seen.add(url)
        deduped.append(artifact)
    return deduped


def _package_dialog(job_id: int) -> None:
    payload = _package_payload(job_id)
    row = payload.get("row") or {}
    run = payload.get("run") or {}
    if not row and not run:
        st.warning("No recorded package is available for this item.")
        return

    company = row.get("company_name") or "Company"
    title = row.get("title") or "Untitled role"
    display = _friendly_status(
        row.get("status") or run.get("status"),
        run.get("queue_status"),
        artifact=bool(row.get("resume_doc_url")),
    )
    description = _status_description(
        row.get("status") or run.get("status"),
        run.get("queue_status"),
        artifact=bool(row.get("resume_doc_url")),
    )

    st.caption(_PAGES.esc(company))
    st.markdown(f"## {_PAGES.esc(title)}")
    _fact_strip([
        ("Status", display),
        ("Fit", row.get("score") or "—"),
        ("Updated", _PAGES.short_time(row.get("updated_at") or run.get("updated_at"))),
    ])
    st.write(description)

    artifacts = _package_artifacts(payload)
    st.markdown("### Files")
    if not artifacts:
        st.info("No resume or cover-letter artifact was recorded for this run.")
    else:
        for artifact in artifacts[:8]:
            st.link_button(
                f"Open {artifact['label']}",
                _PAGES.safe_link(artifact["url"]),
                use_container_width=True,
            )

    messages: list[str] = []
    for source in (run, row):
        for key in ("truth_reason", "error_message"):
            value = str(source.get(key) or "").strip()
            if value and value not in messages:
                messages.append(value)
    if messages:
        st.markdown("### Why it stopped")
        for message in messages:
            st.warning(message)

    with st.expander("Technical run metadata", expanded=False):
        st.json({
            "application_id": row.get("id"),
            "run_id": run.get("id"),
            "queue_status": run.get("queue_status"),
            "raw_status": run.get("status") or row.get("status"),
            "raw_stage": run.get("stage"),
        })


def tracker_v22() -> None:
    rows = _tracker_rows_v22()
    apps = _PAGES.tracker_rows()
    app_rows = _PAGES.application_rows(limit=500)

    _PAGES.page_intro(
        "TRACKER",
        "Application tracker",
        "Open a prepared application to review the exact status, files, and next step.",
    )

    stats = {
        "Preparing": sum(row["display_status"] == "In progress" for row in rows),
        "Needs review": sum(row["display_status"] == "Needs review" for row in rows),
        "Prepared": sum(row["display_status"] in {"Prepared", "Completed"} for row in rows),
        "Submitted": sum(row["display_status"] == "Submitted" for row in rows),
    }
    columns = st.columns(4)
    for column, (label, value) in zip(columns, stats.items()):
        with column:
            st.metric(label, value)

    view = st.segmented_control(
        "Tracker view",
        ["Pipeline", "Inbox"],
        default=st.session_state.get("product_tracker_tab", "Pipeline"),
        key="product_tracker_tab",
        label_visibility="collapsed",
        on_change=_PAGES._sync_subroute,
        args=(
            "tab",
            "product_tracker_tab",
            {"Pipeline": "pipeline", "Inbox": "inbox"},
        ),
    )

    if view == "Inbox":
        _PAGES._inbox()
        return

    if not apps and not app_rows:
        _PAGES.empty_state(
            "No activity yet",
            "Your preparation history will appear here as soon as you prepare a job.",
            href="?view=jobs",
            action="Browse jobs",
        )
        return

    search, status = st.columns((2.4, 1))
    with search:
        text = st.text_input(
            "Search tracker",
            placeholder="Search company or role",
            label_visibility="collapsed",
            key="product_v22_tracker_search",
        )
    with status:
        status_value = st.selectbox(
            "Status filter",
            ["All", "In progress", "Needs review", "Prepared", "Completed", "Submitted", "Blocked", "Failed", "Skipped"],
            label_visibility="collapsed",
            key="product_v22_tracker_status",
        )

    needle = str(text or "").strip().casefold()
    filtered = [
        row for row in rows
        if (
            status_value == "All"
            or row["display_status"] == status_value
        )
        and (
            not needle
            or needle in str(row.get("company_name") or "").casefold()
            or needle in str(row.get("title") or "").casefold()
        )
    ]
    _pipeline_list_v22(filtered)

    selected_application = _consume_query_id(
        "application",
        "product_v22_tracker_application_once",
    )
    if selected_application is not None:
        @st.dialog("Application details", width="large")
        def _open_package() -> None:
            _package_dialog(selected_application)

        _open_package()


def _extension_profile_record() -> dict[str, Any] | None:
    try:
        from app import extension_profile_sync_v1
    except (ImportError, ModuleNotFoundError):
        return None
    try:
        return extension_profile_sync_v1.latest_master_profile()
    except Exception:
        return None


def _extension_profile_facts(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not record:
        return []
    payload = record.get("profile") or record.get("payload") or {}
    facts = payload.get("facts") if isinstance(payload, dict) else None
    if not isinstance(facts, list):
        return []
    return [fact for fact in facts if isinstance(fact, dict)]


def _render_profile_facts(facts: list[dict[str, Any]]) -> None:
    if not facts:
        st.info("No structured profile facts have been saved yet.")
        return
    for fact in facts[:40]:
        value = fact.get("value")
        if isinstance(value, list):
            value = " · ".join(str(item) for item in value)
        st.markdown(
            f"""<div class="profile-fact-card">
                <b>{_PAGES.esc(fact.get("fact_key"), "Profile fact")}</b>
                <span>{_PAGES.esc(value, "Not available")}</span>
            </div>""",
            unsafe_allow_html=True,
        )


def profile_v22() -> None:
    tab = st.session_state.get("product_profile_tab", "Profile details")
    options = ["Resume", "Cover letters", "Profile details"]
    if tab not in options:
        tab = "Profile details"
        st.session_state["product_profile_tab"] = tab

    rows = _PAGES.application_rows(limit=500)
    resume_artifacts = [row for row in rows if row.get("resume_doc_url")]
    cover_artifacts = [row for row in rows if row.get("cover_letter_doc_url")]
    designated = _PAGES.master_resume()
    facts = _PAGES.candidate_facts()

    st.markdown(
        f"""
        <div class="profile-topline">
            <div class="profile-selector">Default <span>⌄</span></div>
            <div class="profile-favorite">☆</div>
            <div class="profile-favorite">⌫</div>
            <div class="profile-add">＋ Add Profile</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    tabs = st.segmented_control(
        "Profile tab",
        options,
        default=tab,
        key="product_profile_tab",
        label_visibility="collapsed",
        on_change=_PAGES._sync_subroute,
        args=(
            "tab",
            "product_profile_tab",
            {"Resume": "resume", "Cover letters": "cover-letter", "Profile details": "details"},
        ),
    )
    if tabs != tab:
        tab = tabs

    if tab == "Resume":
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
                    "Use this master resume for new jobs",
                    value=True,
                    disabled=True,
                    key="product_v22_master_toggle",
                ):
                    pass
        else:
            st.info(
                "No master resume is designated yet. Create or prepare a resume "
                "in Resume Studio, then set that artifact as your master."
            )

        st.markdown("### Prepared resumes")
        if not resume_artifacts:
            st.info("No recorded resume artifacts are available yet.")
            return
        for artifact in resume_artifacts:
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
                    st.caption(
                        f"{artifact.get('display_status')} · "
                        f"{artifact.get('status_description')}"
                    )
                with action:
                    st.link_button(
                        "Open resume",
                        _PAGES.safe_link(artifact["resume_doc_url"]),
                        use_container_width=True,
                    )
        return

    if tab == "Profile details":
        extension = _extension_profile_record()
        extension_facts = _extension_profile_facts(extension)
        if extension:
            st.markdown("### MUNSHI Apply profile")
            profile_meta = extension.get("profile") or extension.get("payload") or {}
            with st.container(key="product_v22_extension_profile", border=True):
                st.markdown(
                    f"**{_PAGES.esc(profile_meta.get('fullName') or profile_meta.get('name') or 'Candidate profile')}**"
                )
                st.caption(
                    "Extension-synced MasterProfile · "
                    + str(extension.get("updated_at") or extension.get("created_at") or "date not recorded")
                )
            safe_facts: list[dict[str, Any]] = []
            for fact in extension_facts:
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
                "Use this master resume for new jobs",
                value=True,
                disabled=True,
                key="product_v22_master_toggle",
            ):
                pass
    else:
        st.info(
            "No master resume is designated yet. Create or prepare a resume "
            "in Resume Studio, then set that artifact as your master."
        )

    st.markdown("### Prepared resumes")
    if not resume_artifacts:
        st.info("No recorded resume artifacts are available yet.")
        return
    for artifact in resume_artifacts:
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
                st.caption(
                    f"{artifact.get('display_status')} · "
                    f"{artifact.get('status_description')}"
                )
            with action:
                st.link_button(
                    "Open resume",
                    _PAGES.safe_link(artifact["resume_doc_url"]),
                    use_container_width=True,
                )


def advanced_v22() -> None:
    snapshot = _PAGES.system_snapshot()
    st.markdown("### Advanced / System")
    st.caption(
        "Operator detail for maintenance. Normal product use should not require this page."
    )

    stats = [
        ("Hunter", "Healthy" if snapshot["healthy"] else "Needs attention", "Local app service"),
        ("n8n", "Configured" if snapshot["n8n_configured"] else "Not configured", "Workflow bridge"),
        ("Telegram", "Enabled" if snapshot["telegram_enabled"] else "Off", "Operator alerts"),
        ("Sources", str(snapshot["source_count"]), "Configured discovery sources"),
        ("Stored jobs", str(snapshot["jobs_stored"]), "Jobs currently retained in Hunter"),
        ("Automation", _PAGES.volume_policy()["mode"].replace("_", " ").title(), "Volume policy"),
    ]
    columns = st.columns(3)
    for index, (label, value, help_text) in enumerate(stats):
        with columns[index % 3]:
            st.markdown(
                f"""<div class="system-stat"><div class="label">{_PAGES.esc(label)}</div><div class="value">{_PAGES.esc(value)}</div><div class="help">{_PAGES.esc(help_text)}</div></div>""",
                unsafe_allow_html=True,
            )

    policy = _PAGES.volume_policy()
    if policy["mode"] == "unlimited":
        st.success(
            "Unlimited means MUNSHI does not impose a business-volume quota. "
            "Provider, targeting, eligibility, dedupe, and safety controls still apply."
        )
    elif policy["mode"] == "custom_limit":
        st.info(
            f"Custom volume target: {policy.get('daily_limit')} jobs per day."
        )
    else:
        st.warning(f"Automation mode: {policy['mode'].replace('_', ' ')}")

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
            from app import candidate_profile_authority_v1 as profile_authority

            authority = profile_authority.authority_snapshot()
            master = dict(authority.get("master_resume") or {})
            profile = dict(authority.get("profile") or {})
            confirmed_count = int(profile.get("structured_fact_count") or 0)
            draft_count = int(profile.get("draft_fact_count") or 0)
            confirmed = bool(profile.get("confirmed"))

            if confirmed:
                headline = f"{confirmed_count} confirmed Candidate Truth fact(s)"
                detail = "Candidate Truth is confirmed against the active Master Resume."
            elif draft_count:
                headline = f"{draft_count} extracted profile value(s) ready for review"
                detail = (
                    "Candidate Truth is not confirmed yet. Open Profile details to review "
                    "the extracted values once, then MUNSHI can prepare reusable answers automatically."
                )
            elif master.get("configured") and profile.get("extraction_id"):
                headline = "Profile extraction is available but has no usable values yet"
                detail = "Open Profile details to inspect the current extraction before AutoApply testing."
            elif master.get("configured"):
                headline = "Master Resume ready · profile extraction still needed"
                detail = (
                    "The active Master Resume is configured, but Candidate Truth has not been built for it yet."
                )
            else:
                headline = "Candidate profile setup needed"
                detail = "Add a Master Resume first, then build and confirm Candidate Truth once."

            st.markdown("### Profile & defaults")
            st.markdown(
                f"""<div class="settings-v22-card">
                    <b>{_PAGES.esc(headline)}</b>
                    <span>Master Resume: {"configured" if master.get("configured") else "not configured"}. {_PAGES.esc(detail)}</span>
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