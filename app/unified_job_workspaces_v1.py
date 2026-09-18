"""Unified job-facing workspaces for Tracker, Research, and Applications.

The normal product UI should teach one interaction model: a stored opportunity is
represented by the same Dashboard/Browse Jobs card everywhere.  This layer is
presentation-only.  It reuses the established product_pages._job_card renderer,
keeps all preparation/submission authorities unchanged, and moves raw provider
telemetry back to Settings -> Advanced/System.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import streamlit as st

from app.product_state import research_snapshot, tracker_rows
from app.product_ui import esc, page_intro


_APPLICATION_SECTIONS = (
    "Overview",
    "Resume",
    "Answers",
    "Intelligence",
    "Readiness",
)
_RESEARCH_SECTIONS = (
    "Opportunities",
    "Companies",
    "Role signals",
    "Risks",
)


def _fetch_job_rows(job_ids: list[int]) -> list[dict[str, Any]]:
    """Load canonical job rows in caller order without inventing missing state."""
    ordered = [int(value) for value in dict.fromkeys(job_ids) if int(value) > 0]
    if not ordered:
        return []

    from app.database import get_connection

    connection = get_connection()
    try:
        placeholders = ",".join("?" for _ in ordered)
        rows = connection.execute(
            f"SELECT * FROM jobs WHERE id IN ({placeholders})",
            tuple(ordered),
        ).fetchall()
        by_id = {int(row["id"]): dict(row) for row in rows}
        try:
            states = connection.execute(
                f"SELECT job_id, saved, skipped FROM product_job_state "
                f"WHERE job_id IN ({placeholders})",
                tuple(ordered),
            ).fetchall()
        except Exception:
            states = []
    finally:
        connection.close()

    for state in states:
        job_id = int(state["job_id"])
        if job_id not in by_id:
            continue
        by_id[job_id]["saved"] = int(state["saved"] or 0)
        by_id[job_id]["skipped"] = int(state["skipped"] or 0)

    for row in by_id.values():
        row.setdefault("saved", 0)
        row.setdefault("skipped", 0)
    return [by_id[job_id] for job_id in ordered if job_id in by_id]


def _card_grid(
    product_pages: Any,
    rows: list[dict[str, Any]],
    *,
    key_prefix: str,
    columns: int = 3,
    limit: int = 18,
    footer_by_id: dict[int, str] | None = None,
    application_links: bool = False,
) -> None:
    visible = rows[: max(1, int(limit))]
    if not visible:
        return
    count = min(max(1, int(columns)), len(visible))
    grid = st.columns(count, gap="medium")
    for index, row in enumerate(visible):
        job_id = int(row["id"])
        with grid[index % count]:
            product_pages._job_card(row, key_prefix=key_prefix)
            footer = str((footer_by_id or {}).get(job_id) or "").strip()
            if footer:
                st.caption(footer)
            if application_links:
                st.link_button(
                    "Work on this application",
                    f"?view=prepare-application&job={job_id}",
                    use_container_width=True,
                    key=f"{key_prefix}_workspace_{job_id}",
                )


def _pipeline_records() -> list[dict[str, Any]]:
    legacy = tracker_rows(limit=250)
    try:
        from app import product_application_flow_v2 as complete_loop

        modern = complete_loop.pipeline_rows(limit=250)
    except Exception:
        modern = []

    merged: dict[int, dict[str, Any]] = {
        int(row["job_id"]): dict(row)
        for row in legacy
        if row.get("job_id") is not None
    }
    for row in modern:
        if row.get("job_id") is None:
            continue
        job_id = int(row["job_id"])
        combined = dict(merged.get(job_id) or {})
        combined.update(dict(row))
        merged[job_id] = combined

    records = list(merged.values())
    records.sort(
        key=lambda row: str(
            row.get("updated_at")
            or row.get("completed_at")
            or row.get("queued_at")
            or ""
        ),
        reverse=True,
    )
    return records


def _pipeline_footer(record: dict[str, Any]) -> str:
    status = str(record.get("display_status") or "Status not recorded")
    ats = (
        f"ATS {float(record['final_ats_score']):.0f}"
        if record.get("final_ats_score") is not None
        else "ATS not scored"
    )
    progress = record.get("progress_percent")
    progress_text = f" · {int(progress)}% prepared" if progress is not None else ""
    questions = int(record.get("runtime_question_count") or 0)
    question_text = f" · {questions} question(s) need attention" if questions else ""
    return f"{status} · {ats}{progress_text}{question_text}"


def render_tracker(product_pages: Any) -> None:
    page_intro(
        "TRACKER",
        "Your application workspace",
        "Follow every application through the same job cards you already use on Dashboard and Browse jobs.",
    )
    if hasattr(product_pages, "_show_action_feedback"):
        product_pages._show_action_feedback()

    tab = st.radio(
        "Tracker view",
        ["Pipeline", "Inbox"],
        horizontal=True,
        label_visibility="collapsed",
        key="product_tracker_tab",
    )
    if tab == "Inbox":
        product_pages._inbox()
        return

    records = _pipeline_records()
    statuses = ["All"] + list(
        dict.fromkeys(
            str(record.get("display_status") or "Status not recorded")
            for record in records
        )
    )
    controls = st.columns((1.3, 2.7), gap="small")
    with controls[0]:
        selected = st.selectbox(
            "Status",
            statuses,
            key="unified_tracker_status",
            label_visibility="collapsed",
        )
    with controls[1]:
        query = st.text_input(
            "Search applications",
            placeholder="Search company or role…",
            key="unified_tracker_search",
            label_visibility="collapsed",
        )

    needle = query.strip().casefold()
    visible = [
        record
        for record in records
        if (selected == "All" or str(record.get("display_status")) == selected)
        and (
            not needle
            or needle
            in f"{record.get('company_name') or ''} {record.get('title') or ''}".casefold()
        )
    ]
    if not visible:
        st.info("No applications match the current status and search filters.")
        return

    jobs = _fetch_job_rows([int(row["job_id"]) for row in visible])
    record_by_id = {int(row["job_id"]): row for row in visible}
    footers = {
        int(job["id"]): _pipeline_footer(record_by_id[int(job["id"])])
        for job in jobs
        if int(job["id"]) in record_by_id
    }
    st.caption(
        "Open any card for the same job-details drawer used elsewhere, or continue into its application workspace."
    )
    _card_grid(
        product_pages,
        jobs,
        key_prefix="tracker_unified",
        columns=3,
        limit=60,
        footer_by_id=footers,
        application_links=True,
    )
    if len(jobs) > 60:
        st.caption(f"Showing the 60 most recently updated of {len(jobs)} matching applications.")
    product_pages._job_detail()


def _research_jobs(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    matches = list(snapshot.get("top_matches") or [])
    return _fetch_job_rows(
        [int(row["id"]) for row in matches if row.get("id") is not None]
    )


def _decision_brief(snapshot: dict[str, Any]) -> None:
    matches = list(snapshot.get("top_matches") or [])
    blockers = list(snapshot.get("blockers") or [])
    if not matches and not blockers:
        st.info("Research will become useful as stored, scored opportunities accumulate.")
        return

    tracks = Counter(
        str(row.get("target_track") or "").strip()
        for row in matches
        if str(row.get("target_track") or "").strip()
    )
    workplaces = Counter(
        str(row.get("remote_type") or "").strip()
        for row in matches
        if str(row.get("remote_type") or "").strip()
    )
    locations = Counter(
        str(row.get("location_raw") or "").strip()
        for row in matches
        if str(row.get("location_raw") or "").strip()
    )

    notes: list[tuple[str, str]] = []
    if tracks:
        notes.append(("Role direction", f"Your strongest recurring stored opportunity family is {tracks.most_common(1)[0][0]}."))
    if workplaces:
        notes.append(("Work arrangement", f"The most common workplace pattern among current top matches is {workplaces.most_common(1)[0][0]}."))
    if locations:
        notes.append(("Location signal", f"The most recurring location in current top-match evidence is {locations.most_common(1)[0][0]}."))
    if blockers:
        top = blockers[0]
        notes.append(("Risk to watch", f"The most frequent recorded blocker is {str(top.get('reason') or 'an unresolved eligibility condition').replace('_', ' ')}."))

    if not notes:
        return
    st.markdown("### Decision brief")
    cols = st.columns(min(4, len(notes)), gap="small")
    for index, (label, copy) in enumerate(notes):
        with cols[index % len(cols)]:
            with st.container(border=True):
                st.markdown(f"**{esc(label)}**", unsafe_allow_html=True)
                st.write(copy)


def _research_opportunities(product_pages: Any, snapshot: dict[str, Any]) -> None:
    matches = list(snapshot.get("top_matches") or [])
    jobs = _research_jobs(snapshot)
    if not jobs:
        st.info("No scored stored opportunities are available yet.")
        return

    by_match = {int(row["id"]): row for row in matches if row.get("id") is not None}
    eligible = [
        job
        for job in jobs
        if not str(by_match.get(int(job["id"]), {}).get("hard_rejection_reason") or "").strip()
    ]
    blocked = [job for job in jobs if job not in eligible]
    st.markdown("### Best current opportunities")
    st.caption(
        "These are stored opportunities ranked by existing MUNSHI evidence. A card never overrides an explicit blocker."
    )
    _card_grid(
        product_pages,
        eligible or jobs,
        key_prefix="research_opportunity",
        columns=3,
        limit=12,
    )
    if blocked:
        with st.expander(f"Review {len(blocked)} high-match opportunity/ies with recorded blockers", expanded=False):
            _card_grid(
                product_pages,
                blocked,
                key_prefix="research_blocked",
                columns=3,
                limit=12,
            )


def _research_companies(product_pages: Any, snapshot: dict[str, Any]) -> None:
    jobs = _research_jobs(snapshot)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        grouped[str(job.get("company_name") or "Company not recorded")].append(job)
    if not grouped:
        st.info("No company-level opportunity evidence is available yet.")
        return

    st.markdown("### Companies worth a closer look")
    st.caption(
        "Company groups come only from your stored scored opportunities. Open a role card to inspect the underlying job evidence."
    )
    ranked = sorted(
        grouped.items(),
        key=lambda item: max(float(row.get("hunter_score") or 0) for row in item[1]),
        reverse=True,
    )
    for company, company_jobs in ranked[:8]:
        best = sorted(
            company_jobs,
            key=lambda row: float(row.get("hunter_score") or 0),
            reverse=True,
        )
        st.markdown(f"#### {esc(company)}", unsafe_allow_html=True)
        _card_grid(
            product_pages,
            best,
            key_prefix=f"research_company_{abs(hash(company))}",
            columns=3,
            limit=3,
        )


def _research_role_signals(product_pages: Any, snapshot: dict[str, Any]) -> None:
    matches = list(snapshot.get("top_matches") or [])
    jobs = _research_jobs(snapshot)
    match_by_id = {int(row["id"]): row for row in matches if row.get("id") is not None}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        evidence = match_by_id.get(int(job["id"]), {})
        track = str(evidence.get("target_track") or job.get("target_track") or "Other relevant roles")
        grouped[track].append(job)
    if not grouped:
        st.info("Role-pattern evidence is not available yet.")
        return

    st.markdown("### Role signals from your strongest matches")
    st.caption(
        "Use these clusters to see where your current Candidate Truth is repeatedly matching, rather than reading provider throughput statistics."
    )
    ranked = sorted(
        grouped.items(),
        key=lambda item: max(float(row.get("hunter_score") or 0) for row in item[1]),
        reverse=True,
    )
    for track, track_jobs in ranked[:6]:
        st.markdown(f"#### {esc(track)}", unsafe_allow_html=True)
        _card_grid(
            product_pages,
            sorted(track_jobs, key=lambda row: float(row.get("hunter_score") or 0), reverse=True),
            key_prefix=f"research_track_{abs(hash(track))}",
            columns=3,
            limit=3,
        )


def _research_risks(product_pages: Any, snapshot: dict[str, Any]) -> None:
    matches = list(snapshot.get("top_matches") or [])
    jobs = _research_jobs(snapshot)
    evidence = {int(row["id"]): row for row in matches if row.get("id") is not None}
    risky = [
        job
        for job in jobs
        if str(evidence.get(int(job["id"]), {}).get("hard_rejection_reason") or "").strip()
    ]
    blockers = list(snapshot.get("blockers") or [])

    st.markdown("### Risks and eligibility evidence")
    st.caption(
        "Research highlights recorded blockers so a strong match score never hides an eligibility problem."
    )
    if blockers:
        for item in blockers[:6]:
            reason = str(item.get("reason") or "Unresolved blocker").replace("_", " ")
            st.warning(reason)
    else:
        st.success("No aggregate eligibility blocker is currently recorded in the research snapshot.")
    if risky:
        st.markdown("#### Opportunities requiring caution")
        _card_grid(
            product_pages,
            risky,
            key_prefix="research_risk",
            columns=3,
            limit=12,
        )


def render_research(product_pages: Any) -> None:
    page_intro(
        "RESEARCH",
        "Research your next move",
        "Turn stored job evidence into decisions: which opportunities to pursue, which companies repeat, what role patterns fit, and what risks need attention.",
    )
    if hasattr(product_pages, "_show_action_feedback"):
        product_pages._show_action_feedback()
    snapshot = research_snapshot()
    _decision_brief(snapshot)
    st.caption(
        "Raw provider throughput, source health, query performance, and cumulative telemetry live in Settings → Advanced / System."
    )
    st.link_button(
        "Open system diagnostics",
        "?view=settings&section=advanced",
        use_container_width=False,
    )

    section = st.radio(
        "Research section",
        _RESEARCH_SECTIONS,
        horizontal=True,
        label_visibility="collapsed",
        key="unified_research_section",
    )
    if section == "Opportunities":
        _research_opportunities(product_pages, snapshot)
    elif section == "Companies":
        _research_companies(product_pages, snapshot)
    elif section == "Role signals":
        _research_role_signals(product_pages, snapshot)
    else:
        _research_risks(product_pages, snapshot)
    product_pages._job_detail()


def _application_job_choices(app_workspace: Any) -> list[dict[str, Any]]:
    options = list(app_workspace._job_options(limit=250))
    return _fetch_job_rows([int(row["id"]) for row in options])


def _requested_job_id() -> int | None:
    try:
        raw = str(st.query_params.get("job") or "").strip()
        value = int(raw)
    except (TypeError, ValueError, Exception):
        return None
    return value if value > 0 else None


def _application_picker(product_pages: Any, jobs: list[dict[str, Any]], selected_id: int) -> None:
    with st.expander("Choose another job", expanded=False):
        query = st.text_input(
            "Find a job",
            placeholder="Search company or role…",
            key="unified_application_search",
            label_visibility="collapsed",
        )
        needle = query.strip().casefold()
        matches = [
            row
            for row in jobs
            if not needle
            or needle in f"{row.get('company_name') or ''} {row.get('title') or ''}".casefold()
        ]
        _card_grid(
            product_pages,
            matches,
            key_prefix="applications_picker",
            columns=3,
            limit=9,
            application_links=True,
        )
        if len(matches) > 9:
            st.caption(f"Showing 9 of {len(matches)} matches. Refine the search to find another job.")


def _render_selected_job(product_pages: Any, job: dict[str, Any], flow: dict[str, Any]) -> None:
    st.markdown("### Current application")
    columns = st.columns((1.25, 1), gap="large")
    with columns[0]:
        product_pages._job_card(job, key_prefix="application_current")
    with columns[1]:
        with st.container(border=True):
            st.markdown(f"**{esc(job.get('company_name'), 'Company')}**", unsafe_allow_html=True)
            st.markdown(f"### {esc(job.get('title'), 'Untitled role')}", unsafe_allow_html=True)
            st.caption(str(job.get("location_raw") or "Location not recorded"))
            st.metric("Application status", str(flow.get("display_status") or "Not started"))
            progress = int(flow.get("progress_percent") or 0)
            st.progress(progress / 100)
            st.caption(f"{progress}% of recorded preparation complete")
            if int(flow.get("runtime_question_count") or 0):
                st.warning(f"{int(flow['runtime_question_count'])} employer question(s) need your attention.")


def _render_resume_panel(app_workspace: Any, job_id: int) -> None:
    versions = app_workspace._resume_versions(job_id)
    st.markdown("### Resume for this application")
    st.caption("Review the truth-bound resume version associated with this job.")
    if not versions:
        st.info("No strengthened resume is stored for this job yet.")
        st.link_button("Open Resume Studio", "?view=resume-studio", use_container_width=False)
        return
    labels = {
        f"Version {row.get('version_number', '?')} · {str(row.get('version_id'))[:12]}…": row
        for row in versions
    }
    chosen_label = st.selectbox(
        "Resume version",
        list(labels),
        key=f"unified_application_resume_{job_id}",
    )
    chosen = labels[chosen_label]
    cols = st.columns(3)
    cols[0].metric("Candidate Truth", "Bound" if chosen.get("candidate_truth_bound") is True else "Review")
    cols[1].metric("Job evidence", "Bound" if chosen.get("job_snapshot_bound") is True else "Review")
    cols[2].metric("Version", f"v{chosen.get('version_number', '?')}")
    if not (chosen.get("candidate_truth_bound") is True and chosen.get("job_snapshot_bound") is True):
        st.warning("Regenerate this resume before treating it as current application evidence.")
    st.link_button("Open Resume Studio", "?view=resume-studio", use_container_width=False)


def _render_answers_panel(app_workspace: Any) -> None:
    planning = app_workspace._safe_planning_input()
    answers = list(planning.get("answers") or [])
    excluded = list(planning.get("excluded_answers") or [])
    ready = [row for row in answers if row.get("planning_use") == "autofill_ready"]
    context = [row for row in answers if row.get("planning_use") == "context_only"]
    st.markdown("### Application answers")
    st.caption("Only planner-safe reusable answers are shown here. Protected values stay on their secure resolver path.")
    cols = st.columns(3)
    cols[0].metric("Autofill ready", len(ready))
    cols[1].metric("Context only", len(context))
    cols[2].metric("Stale excluded", len(excluded))
    if not answers and not excluded:
        st.info("No reusable Answer Brain memories are stored yet. Missing evidence remains unresolved rather than guessed.")
    if answers:
        with st.expander("Review reusable answer inventory", expanded=False):
            st.dataframe(
                [
                    {
                        "Meaning": row.get("question_key") or row.get("question_family") or "—",
                        "Use": row.get("planning_use"),
                        "Source": row.get("source"),
                        "Confidence": row.get("confidence"),
                    }
                    for row in answers
                ],
                hide_index=True,
                use_container_width=True,
            )
    st.link_button("Manage reusable answers", "?view=profile&tab=details", use_container_width=False)


def _render_intelligence_panel(app_workspace: Any, job_id: int) -> None:
    st.markdown("### Opportunity intelligence")
    st.caption("Evidence-backed fit, policy gates, unresolved facts, and relationship evidence for this job.")
    current = app_workspace._latest_opportunity(job_id)
    try:
        from app import opportunity_intelligence_v3 as opportunity

        enabled = opportunity.opportunity_intelligence_enabled()
    except Exception:
        enabled = False
        opportunity = None
    if not enabled:
        st.info("Opportunity Intelligence is not enabled in this environment.")
    else:
        if st.button("Refresh opportunity evaluation", type="primary", key=f"unified_evaluate_{job_id}"):
            try:
                current = opportunity.evaluate_job(job_id)
                st.success("Opportunity evaluation refreshed from current evidence.")
            except Exception as error:
                st.error(str(error))
        if current:
            cols = st.columns(3)
            cols[0].metric("Decision", str(current.get("status") or "Review"))
            score = current.get("opportunity_score")
            cols[1].metric("Evidence score", "Unknown" if score is None else f"{float(score):.0f}%")
            pursuit = current.get("pursuit_strategy") or {}
            cols[2].metric("Pursuit", str(pursuit.get("pursuit_state") or "Review"))
            failures = list(current.get("hard_failures") or [])
            unknowns = list(current.get("unknowns") or [])
            if failures:
                st.error("Recorded blockers: " + ", ".join(map(str, failures)))
            if unknowns:
                st.warning("Still unresolved: " + ", ".join(map(str, unknowns)))
            if not failures and not unknowns:
                st.success("No unresolved opportunity-policy items are recorded.")
        else:
            st.info("No saved opportunity evaluation exists for this job yet.")

    relationship = app_workspace._latest_relationship(job_id)
    with st.expander("People and relationship evidence", expanded=False):
        if relationship:
            strategy = relationship.get("combined_strategy") or relationship.get("strategy") or {}
            st.write(f"Recommended relationship approach: **{strategy.get('pursuit_state') or relationship.get('pursuit_state') or 'Review'}**")
            score = relationship.get("relationship_score")
            if score is not None:
                st.metric("Relationship score", f"{float(score):.0f}%")
        else:
            st.caption("No saved relationship strategy is attached to this application yet.")


def _render_readiness_panel(app_workspace: Any, job_id: int) -> None:
    opportunity = app_workspace._latest_opportunity(job_id)
    relationship = app_workspace._latest_relationship(job_id)
    versions = app_workspace._resume_versions(job_id)
    resume_version_id = str(versions[0].get("version_id") or "") if versions else None
    result = app_workspace._readiness(
        job_id=job_id,
        resume_version_id=resume_version_id,
        opportunity_evaluation_id=str((opportunity or {}).get("evaluation_id") or "") or None,
        relationship_strategy_id=str((relationship or {}).get("strategy_id") or "") or None,
    )
    st.markdown("### Application readiness")
    status = str(result.get("status") or "HOLD")
    blockers = list(result.get("blockers") or result.get("reasons") or [])
    if status == "READY":
        st.success("Ready for the next preparation/review step based on the currently bound evidence.")
    else:
        st.warning("This application still has evidence or preparation items to resolve.")
    for blocker in blockers:
        st.write(f"• {blocker}")
    st.info("Preparation only · Submission authority remains OFF in this staging tranche.")


def render_applications(product_pages: Any, app_workspace: Any) -> None:
    page_intro(
        "APPLICATIONS",
        "Applications",
        "Choose a job card, prepare its package, and move through one clear workspace instead of a long technical page.",
    )
    if hasattr(product_pages, "_show_action_feedback"):
        product_pages._show_action_feedback()
    jobs = _application_job_choices(app_workspace)
    if not jobs:
        st.info("No stored jobs are available yet. Discover or add a job first.")
        return

    by_id = {int(row["id"]): row for row in jobs}
    requested = _requested_job_id()
    selected_id = requested if requested in by_id else int(jobs[0]["id"])
    selected_job = by_id[selected_id]

    _application_picker(product_pages, jobs, selected_id)
    flow = app_workspace._render_complete_loop.__globals__.get("complete_loop").flow_for_job(selected_id)
    _render_selected_job(product_pages, selected_job, flow)

    section = st.radio(
        "Application workspace section",
        _APPLICATION_SECTIONS,
        horizontal=True,
        label_visibility="collapsed",
        key="unified_application_section",
    )
    if section == "Overview":
        app_workspace._render_complete_loop(selected_id)
    elif section == "Resume":
        _render_resume_panel(app_workspace, selected_id)
    elif section == "Answers":
        _render_answers_panel(app_workspace)
    elif section == "Intelligence":
        _render_intelligence_panel(app_workspace, selected_id)
    else:
        _render_readiness_panel(app_workspace, selected_id)
    product_pages._job_detail()


def install_unified_job_workspaces(product_pages: Any, application_workspace_page: Any) -> None:
    """Install the unified presentation layer after older page wrappers."""
    product_pages.tracker = lambda: render_tracker(product_pages)
    product_pages.research = lambda: render_research(product_pages)
    application_workspace_page.render = lambda: render_applications(
        product_pages,
        application_workspace_page,
    )
