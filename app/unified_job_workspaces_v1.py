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


def _requested_job_id() -> int | None:
    try:
        raw = str(st.query_params.get("job") or "").strip()
        value = int(raw)
    except (TypeError, ValueError, Exception):
        return None
    return value if value > 0 else None


_APPLICATION_FILTERS = {
    "Needs you": "needs_you",
    "Preparing": "preparing",
    "Applying": "applying",
    "Needs attention": "needs_attention",
    "Submitted": "submitted",
    "Archived": "archived",
    "All": "all",
}


def _requested_application_id() -> str | None:
    try:
        value = str(st.query_params.get("application") or "").strip()
    except Exception:
        return None
    return value or None


def _select_application(job_id: int, application_id: str | None = None) -> None:
    st.query_params["view"] = "prepare-application"
    st.query_params["job"] = str(int(job_id))
    if application_id:
        st.query_params["application"] = str(application_id)


def _clear_application_selection() -> None:
    for key in ("job", "application"):
        try:
            del st.query_params[key]
        except (KeyError, TypeError):
            pass


def _archive_application(application_id: str, job_id: int) -> None:
    from app.application_archive_v1 import archive_application

    try:
        archive_application(application_id=application_id, job_id=int(job_id))
    except Exception as error:  # noqa: BLE001 - report durable-action failure in place
        st.session_state["application_visibility_error"] = str(error)
        return
    st.session_state["application_visibility_success"] = "Application archived. Its lifecycle, resume, history, and receipts were preserved."
    _clear_application_selection()


def _restore_application(application_id: str, job_id: int) -> None:
    from app.application_archive_v1 import restore_application

    try:
        restore_application(application_id=application_id, job_id=int(job_id))
    except Exception as error:  # noqa: BLE001
        st.session_state["application_visibility_error"] = str(error)
        return
    st.session_state["application_visibility_success"] = "Application restored to active views."


def _updated_label(value: str | None) -> str:
    if not value:
        return "Update time unavailable"
    return str(value).replace("T", " ").replace("+00:00", " UTC")


def _filter_application_views(
    views: list[Any], *, group: str, query: str = ""
) -> list[Any]:
    needle = str(query or "").strip().casefold()
    return [
        item
        for item in views
        if (
            (group == "all" and not item.archived)
            or (group == "archived" and item.archived)
            or (not item.archived and item.state_group == group)
        )
        and (group == "all" or item.archived == (group == "archived"))
        and (
            not needle
            or needle
            in f"{item.company} {item.title} {item.human_state} {item.next_action}".casefold()
        )
    ]


from app.product_experience_vnext import (
    application_action_copy,
    application_stage_markup,
)


def _render_application_rail(views: list[Any], selected_id: int | None) -> None:
    st.markdown("### Application Center")
    # The default shows every durable application, already priority-sorted by
    # the shared read model. Filters narrow the same identities; they never hide
    # the existence of other current work on first open.
    default_choice = "All"
    choice = st.selectbox(
        "Filter applications",
        list(_APPLICATION_FILTERS),
        index=list(_APPLICATION_FILTERS).index(default_choice),
        key="application_center_filter",
    )
    query = st.text_input(
        "Search applications",
        placeholder="Optional company or role search…",
        key="application_center_search",
    )
    group = _APPLICATION_FILTERS[choice]
    visible = _filter_application_views(views, group=group, query=query)
    st.caption(f"{len(visible)} application{'s' if len(visible) != 1 else ''}")
    if not visible:
        st.info("No applications match this view.")
        return
    page_size = 25
    page_count = max(1, (len(visible) + page_size - 1) // page_size)
    page = 1
    if page_count > 1:
        page = st.selectbox(
            "Application page",
            list(range(1, page_count + 1)),
            format_func=lambda value: f"Page {value} of {page_count}",
            key="application_center_page",
        )
    start = (int(page) - 1) * page_size
    for item in visible[start : start + page_size]:
        with st.container(border=True):
            st.markdown(
                f"**{esc(item.company, 'Company')}**  \n{esc(item.title, 'Untitled role')}",
                unsafe_allow_html=True,
            )
            score = "—" if item.match is None else f"{item.match:.0f}% match"
            archived = " · Archived" if item.archived else ""
            st.caption(f"{score} · {item.human_state}{archived}")
            destination = " · ".join(
                value for value in (item.apply_via, item.source_platform) if value
            )
            if destination:
                st.caption(destination)
            st.caption(f"Updated {_updated_label(item.last_update)}")
            st.caption(f"Next: {application_action_copy(item.canonical_state, item.next_action)}")
            st.button(
                "Selected" if selected_id == item.job_id else "Open application",
                key=f"application_rail_{item.job_id}",
                type="primary" if selected_id == item.job_id else "secondary",
                disabled=selected_id == item.job_id,
                use_container_width=True,
                on_click=_select_application,
                args=(item.job_id, item.application_id),
            )


def _render_lifecycle(model: Any) -> None:
    receipt = dict(model.receipt_summary or {})
    confirmed = (
        bool(receipt.get("receipt_id"))
        and receipt.get("verification_status") == "VERIFIED"
    )
    st.markdown(
        application_stage_markup(model.stage_index, receipt_verified=confirmed),
        unsafe_allow_html=True,
    )



def _render_application_header(model: Any) -> None:
    match = "Match not scored" if model.match is None else f"{model.match:.0f}% match"
    st.markdown(
        f"""<div class="product-callout"><div>
        <strong>{esc(model.company, 'Company')} · {esc(model.title, 'Untitled role')}</strong>
        <span>{esc(model.location, 'Location not recorded')} · {esc(model.work_arrangement)} · {match}</span>
        <span>Apply via: {esc(model.apply_via, 'Destination not recorded')} · Source: {esc(model.source_platform, 'Not recorded')}</span>
        <span>Updated {_updated_label(model.last_update)} · Next: {esc(application_action_copy(model.canonical_state, model.next_action))}</span>
        </div><span class="status-chip">{esc(model.human_state)}</span></div>""",
        unsafe_allow_html=True,
    )
    _render_lifecycle(model)


def _render_application_visibility_feedback() -> None:
    feedback_error = st.session_state.pop("application_visibility_error", None)
    feedback_success = st.session_state.pop("application_visibility_success", None)
    if feedback_error:
        st.error(str(feedback_error))
    if feedback_success:
        st.success(str(feedback_success))


def _render_application_visibility_actions(model: Any) -> None:
    actions = st.columns((1, 1, 2.4), gap="small")
    actions[0].button(
        "Close",
        key=f"application_close_{model.job_id}",
        use_container_width=True,
        on_click=_clear_application_selection,
        help="Close this workspace selection. The application stays in your list.",
    )
    if model.application_id:
        if model.archived:
            actions[1].button(
                "Restore",
                key=f"application_restore_{model.job_id}",
                type="primary",
                use_container_width=True,
                on_click=_restore_application,
                args=(model.application_id, model.job_id),
            )
        else:
            actions[1].button(
                "Archive",
                key=f"application_archive_{model.job_id}",
                use_container_width=True,
                on_click=_archive_application,
                args=(model.application_id, model.job_id),
                help="Remove from active views without deleting any application evidence.",
            )
    actions[2].caption(
        "Archive changes visibility only. Job, resume, review, submission, and receipt history remain intact."
    )


def _run_recovery(action: str, job_id: int) -> None:
    from app import application_recovery_v1

    try:
        application_recovery_v1.execute(action, job_id=int(job_id))
    except Exception as error:
        st.session_state["application_recovery_error"] = str(error)
    else:
        st.session_state["application_recovery_success"] = action


def _render_recovery_center(model: Any) -> None:
    labels = {
        "recover_expired_lease": "Recover expired task lease",
        "retry_preparation": "Retry preparation",
        "cancel_preparation": "Cancel preparation",
        "restart_stale_preparation": "Restart stale preparation",
        "quarantine_application": "Quarantine this application",
    }
    with st.expander("Recovery Center", expanded=model.stale or model.state_group == "needs_attention"):
        st.caption(
            "Recovery is state-aware and audited. Submitted, ambiguous-submission, and consumed-authority records cannot be reset or re-submitted here."
        )
        feedback_error = st.session_state.pop("application_recovery_error", None)
        feedback_success = st.session_state.pop("application_recovery_success", None)
        if feedback_error:
            st.error(str(feedback_error))
        if feedback_success:
            st.success("Recovery action completed. Refreshing durable state.")
        st.button(
            "Refresh state",
            key=f"application_refresh_{model.job_id}",
            use_container_width=True,
        )
        durable = [
            action
            for action in model.allowed_recovery_actions
            if action not in {"refresh_state", "clear_local_selection"}
        ]
        if not durable:
            st.info("No durable recovery action is safe for this application state.")
            return
        st.markdown("**Safe actions for this state**")
        for action in durable:
            destructive = action in {
                "cancel_preparation",
                "restart_stale_preparation",
                "quarantine_application",
            }
            if destructive:
                confirmed = st.checkbox(
                    f"Confirm {labels[action].casefold()}",
                    key=f"application_confirm_{action}_{model.job_id}",
                )
            else:
                confirmed = True
            st.button(
                labels[action],
                key=f"application_recovery_{action}_{model.job_id}",
                disabled=not confirmed,
                use_container_width=True,
                on_click=_run_recovery,
                args=(action, model.job_id),
            )


def _render_advanced_diagnostics(model: Any) -> None:
    with st.expander("Advanced diagnostics", expanded=False):
        st.caption("Technical identifiers and task state are intentionally kept out of the normal application flow.")
        st.write(f"Canonical state: `{model.canonical_state or 'NOT_STARTED'}`")
        st.write(f"Application ID: `{model.application_id or 'not created'}`")
        heartbeat = model.worker_heartbeat
        st.write(
            f"Prepare worker: {heartbeat.get('label') or 'Unavailable'} · "
            f"last heartbeat {_updated_label(heartbeat.get('recorded_at'))}"
        )
        if model.projection_error:
            st.write(f"Projection error type: `{model.projection_error}`")


def _render_application_state(app_workspace: Any, model: Any) -> None:
    from app import product_application_flow_v2 as complete_loop

    state = model.canonical_state
    st.markdown(f"### {model.human_state}")
    if model.degraded:
        st.error(model.issue_summary)
    elif not state:
        st.write("MUNSHI will tailor a truthful resume and run package checks in the background. Nothing is submitted until you review the resume and choose Approve & Submit.")
        if st.button(
            "Prepare application",
            type="primary",
            key=f"application_prepare_{model.job_id}",
            use_container_width=True,
        ):
            try:
                complete_loop.start_prepare_for_job(model.job_id, engine="NATIVE")
            except Exception as error:
                st.error(str(error))
            else:
                st.rerun()
    elif model.state_group == "preparing":
        st.info(f"MUNSHI is working in the background: **{model.safe_progress_stage}**.")
        st.caption(f"Last durable update: {_updated_label(model.last_update)}")
        if model.stale:
            st.warning("This application has not updated recently. Other applications remain independent; safe recovery is available below.")
    elif model.resume_review_available:
        app_workspace._render_complete_loop(model.job_id, compact=True)
    elif model.state_group == "applying":
        st.info("Your approval is recorded. MUNSHI is applying now and will continue without another review or submit prompt.")
        st.caption("Current safe stage: opening the employer application, completing the form, uploading the approved resume, or verifying submission.")
    elif model.protected_checkpoint:
        st.warning("Action required: the employer requires a protected human checkpoint such as CAPTCHA, MFA, passkey, biometrics, or identity verification.")
        st.caption("Complete the checkpoint in this same application session. Normal employer questions remain automated from truthful stored data.")
    elif state == "SUBMISSION_UNVERIFIED":
        st.info(
            "The submission provider reports the application was sent. "
            "MUNSHI is checking for a matching employer acknowledgment; "
            "do not approve or submit it again."
        )
        st.caption("Use Check provider & refresh statuses to read the managed inbox now.")
    elif model.state_group == "needs_attention":
        st.warning(model.issue_summary or "MUNSHI stopped safely before claiming success.")
        st.write(f"Stopped at: **{model.stopped_at or model.safe_progress_stage}**")
        st.write(f"Employer submission attempted: **{'Possibly — verify first' if model.submission_attempted else 'No evidence of an attempt'}**")
    elif model.state_group == "submitted":
        receipt = model.receipt_summary or {}
        st.success("Submitted · independently verified evidence is recorded.")
        details = st.columns(3, gap="small")
        details[0].metric("Provider", str(receipt.get("provider") or "Recorded"))
        details[1].metric("Provider application ID", str(receipt.get("provider_application_id") or "Recorded"))
        details[2].metric("Verification", str(receipt.get("verification_status") or "Verified"))
        st.caption(
            f"Submitted {receipt.get('submitted_at') or 'time recorded'} · "
            f"Employer receipt checked {receipt.get('verified_at') or 'time recorded'} · "
            f"Resume {receipt.get('resume_version_id') or 'binding recorded'}"
        )
    else:
        st.info("This application is recorded. Open Tracker for later hiring-stage updates.")
    _render_recovery_center(model)
    _render_advanced_diagnostics(model)


def render_applications(product_pages: Any, app_workspace: Any) -> None:
    from app.application_view_model_v1 import application_views, view_for_job

    page_intro(
        "APPLICATIONS",
        "Applications",
        "Every active application, its exact next action, and what MUNSHI is doing now.",
    )
    if hasattr(product_pages, "_show_action_feedback"):
        product_pages._show_action_feedback()
    _render_application_visibility_feedback()
    if st.button(
        "↻ Check provider & refresh statuses",
        key="application_center_refresh",
        help="Read existing external-provider statuses and managed inbox; never retries an application.",
    ):
        from app.relay_application_dispatch_v1 import reconcile_owner_pending

        try:
            with st.spinner("Checking existing submissions and employer inbox…"):
                result=reconcile_owner_pending(limit=10)
            st.session_state["_relay_refresh_feedback"]=(
                f"Checked {result['checked']} existing applications · "
                f"{result['employer_confirmed']} employer confirmations found · "
                f"{result['errors']} read errors · no applications sent."
            )
        except (RuntimeError,LookupError,ValueError,PermissionError,
                TypeError,OSError):
            st.session_state["_relay_refresh_feedback"]=(
                "Provider lookup is temporarily unavailable. No application "
                "was retried. Local records are displayed below."
            )
        st.rerun()
    if st.session_state.get("_relay_refresh_feedback"):
        st.info(st.session_state.pop("_relay_refresh_feedback"))
    st.caption(
        "Application statuses update from durable records when you open or refresh this page. "
        "Submission requires a separately verified employer receipt."
    )

    try:
        # Pagination bounds the rendered rail; the read projection includes every
        # durable application so older items never require name recall or search.
        views = application_views(limit=None)
    except Exception:
        views = list(st.session_state.get("_application_center_last_good") or [])
        st.warning(
            "Live application state is temporarily busy. Showing the last successful read; no application action was changed."
        )
    else:
        st.session_state["_application_center_last_good"] = views
    requested = _requested_job_id()
    requested_application = _requested_application_id()
    selected = next(
        (
            item
            for item in views
            if requested_application and item.application_id == requested_application
        ),
        None,
    )
    if selected is None:
        selected = next((item for item in views if item.job_id == requested), None)
    if requested is not None and selected is None:
        try:
            selected = view_for_job(requested)
            views = [selected, *views]
        except (LookupError, ValueError):
            st.warning("The selected application is no longer available.")

    if not views and selected is None:
        st.markdown(
            '<div class="empty-product"><h3>No applications yet</h3><p>Prepare a job from Dashboard or Browse Jobs and it will stay connected here.</p></div>',
            unsafe_allow_html=True,
        )
        st.link_button("Browse jobs", "?view=jobs", type="primary")
        st.link_button("Add job", "?view=jobs&add=1")
        return

    rail, workspace = st.columns((0.9, 2.1), gap="large")
    with rail:
        _render_application_rail(views, selected.job_id if selected else None)
    with workspace:
        if selected is None:
            st.markdown("### Select an application")
            st.info("Action-required applications are listed first. Select one to open its exact durable context; search is optional.")
            counts = Counter(item.state_group for item in views if not item.archived)
            metrics = st.columns(4, gap="small")
            metrics[0].metric("Needs you", counts.get("needs_you", 0))
            metrics[1].metric("Preparing", counts.get("preparing", 0))
            metrics[2].metric("Applying", counts.get("applying", 0))
            metrics[3].metric("Needs attention", counts.get("needs_attention", 0))
        else:
            _render_application_header(selected)
            _render_application_visibility_actions(selected)
            _render_application_state(app_workspace, selected)
            if not selected.archived:
                from app.application_provider_ui_v1 import render_workspace

                render_workspace(selected.job_id)


def install_unified_job_workspaces(product_pages: Any, application_workspace_page: Any) -> None:
    """Install the unified presentation layer after older page wrappers."""
    product_pages.tracker = lambda: render_tracker(product_pages)
    product_pages.research = lambda: render_research(product_pages)
    application_workspace_page.render = lambda: render_applications(
        product_pages,
        application_workspace_page,
    )
