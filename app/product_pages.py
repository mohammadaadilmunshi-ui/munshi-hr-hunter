"""MUNSHI's user-facing product views.

All labels here are evidence-bound.  In particular, a completed n8n package is
shown as Prepared, never as an externally submitted application.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from app.product_state import (
    activity_summary, candidate_facts, clear_master_resume, create_lane, delete_lane, fetch_jobs,
    job_filters, lanes, master_resume, research_snapshot, save_candidate_fact, save_master_resume,
    save_review_preference, save_volume_policy, set_job_state, set_lane_enabled, tracker_rows,
    update_lane, volume_policy,
)
from app.product_ui import esc, page_intro, pastel_for, safe_link, score_ring


def _sync_subroute(parameter: str, state_key: str, values: dict[str, str]) -> None:
    """Keep user-selected product tabs shareable without doing external work."""
    value = values.get(str(st.session_state.get(state_key)))
    if not value:
        return
    try:
        st.query_params[parameter] = value
    except Exception:
        pass



def _relative_time(value: Any) -> str:
    if not value:
        return "Date not recorded"
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        delta = datetime.now(date.tzinfo) - date
        hours = int(delta.total_seconds() // 3600)
        if hours < 1:
            return "Seen recently"
        if hours < 24:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"
    except (ValueError, TypeError):
        return "Date recorded"


def _set_action_feedback(tone: str, message: str) -> None:
    st.session_state["product_action_feedback"] = {"tone": str(tone), "message": str(message)}


def _show_action_feedback() -> None:
    feedback = st.session_state.pop("product_action_feedback", None)
    if not isinstance(feedback, dict):
        return
    message = str(feedback.get("message") or "").strip()
    if not message:
        return
    icon = {"success": "✅", "warning": "⚠️", "error": "❌", "info": "ℹ️"}.get(str(feedback.get("tone") or "info"), "ℹ️")
    st.toast(message, icon=icon)


def _toggle_job_saved(job_id: int, currently_saved: bool) -> None:
    set_job_state(int(job_id), saved=not bool(currently_saved))
    _set_action_feedback("success", "Removed from Saved jobs." if currently_saved else "Saved job.")


def _toggle_job_skipped(job_id: int, currently_skipped: bool) -> None:
    set_job_state(int(job_id), skipped=not bool(currently_skipped))
    _set_action_feedback("success", "Restored job to results." if currently_skipped else "Passed on job.")


def _prepare_job(job_id: int) -> None:
    try:
        from app.stored_job_n8n_worker import start_stored_job_run
        result = start_stored_job_run(int(job_id), actor="dashboard_product_ui")
        if result.get("success"):
            _set_action_feedback("success", str(result.get("message") or "Preparation request processed."))
        else:
            _set_action_feedback("warning", str(result.get("message") or "Preparation was not started."))
    except Exception:
        _set_action_feedback("error", "The guarded preparation request could not be started. No submission was claimed.")


def _job_card(row: dict[str, Any], *, key_prefix: str) -> None:
    job_id = int(row["id"])
    tone = pastel_for(job_id)
    view = "jobs" if key_prefix == "jobs" else "dashboard"
    tags = [row.get("remote_type"), row.get("employment_type"), row.get("salary_raw")]
    if row.get("saved"):
        tags.append("Saved")
    tags_html = "".join(f'<span class="tag">{esc(tag)}</span>' for tag in tags if str(tag or "").strip())
    with st.container(key=f"product_card_{key_prefix}_{job_id}"):
        st.markdown(
            f"""<a class="job-card-click" href="?view={view}&amp;job={job_id}" target="_self" aria-label="Open {esc(row.get("title"), "job")} details">
                <div class="job-card"><div class="job-card-main" style="--card-bg:{tone}">
                <div class="job-top"><span>{esc(row.get("location_raw"), "Location unknown")}<br><span class="quiet">{esc(_relative_time(row.get("first_seen_at")))}</span></span>{score_ring(row.get("hunter_score"), tone)}</div>
                <div class="job-title">{esc(row.get("title"), "Untitled role")}</div>
                <div>{tags_html}</div><div class="job-company">{esc(row.get("company_name"), "Company not recorded")}</div>
                <div class="job-meta">{esc(row.get("source"), "Source unknown")}</div></div></div></a>""",
            unsafe_allow_html=True,
        )
        actions = st.columns(3, gap="small")
        saved = bool(row.get("saved"))
        skipped = bool(row.get("skipped"))
        with actions[0]:
            st.button("Saved" if saved else "Save", key=f"{key_prefix}_save_{job_id}", use_container_width=True, on_click=_toggle_job_saved, args=(job_id, saved))
        with actions[1]:
            st.button("Restore" if skipped else "Pass", key=f"{key_prefix}_skip_{job_id}", use_container_width=True, on_click=_toggle_job_skipped, args=(job_id, skipped))
        with actions[2]:
            st.button("Prepare", key=f"{key_prefix}_prepare_{job_id}", type="primary", use_container_width=True, on_click=_prepare_job, args=(job_id,))

def _clear_job_query() -> None:
    try:
        if "job" in st.query_params:
            del st.query_params["job"]
    except Exception:
        pass


@st.dialog("Job details", width="large")
def _job_detail_dialog(job_id: int) -> None:
    from app.database import get_connection
    connection = get_connection()
    try:
        record = connection.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        state_record = connection.execute("SELECT saved,skipped FROM product_job_state WHERE job_id=?", (int(job_id),)).fetchone()
    finally:
        connection.close()
    if not record:
        st.warning("This stored job is no longer available.")
        st.button("Close", on_click=_clear_job_query)
        return
    row = dict(record)
    row["saved"] = int(state_record["saved"]) if state_record else 0
    row["skipped"] = int(state_record["skipped"]) if state_record else 0
    st.markdown(f"## {esc(row.get('title'), 'Untitled role')}", unsafe_allow_html=True)
    st.caption(f"{row.get('company_name') or 'Company not recorded'} · {row.get('location_raw') or 'Location unknown'} · {row.get('source') or 'Source unknown'}")
    left, right = st.columns((1.55, 1), gap="large")
    with left:
        st.markdown("### Role summary")
        st.write(row.get("description_raw") or "A full job description is not stored for this role.")
    with right:
        st.metric("Hunter match", f"{float(row['hunter_score']):.0f}%" if row.get("hunter_score") is not None else "Not available")
        st.markdown(f"""<div class="evidence-list">
            <div class="evidence-item"><b>Employment</b>{esc(row.get("employment_type"))}</div>
            <div class="evidence-item"><b>Workplace</b>{esc(row.get("remote_type"))}</div>
            <div class="evidence-item"><b>Target track</b>{esc(row.get("target_track"))}</div>
            <div class="evidence-item"><b>Compensation</b>{esc(row.get("salary_raw"))}</div>
        </div>""", unsafe_allow_html=True)
        authorization = str(row.get("work_authorization") or "").strip()
        if authorization:
            st.caption("Work-authorization evidence is recorded.")
            with st.expander("Review work-authorization evidence", expanded=False):
                st.write(authorization)
        else:
            st.caption("Work authorization: not recorded.")
        if row.get("hard_rejection_reason"):
            st.warning("Eligibility evidence: " + str(row["hard_rejection_reason"]))
    action_columns = st.columns((1, 1, 1, 1.2))
    with action_columns[0]:
        st.button("Saved" if row["saved"] else "Save", key=f"dialog_save_{job_id}", use_container_width=True, on_click=_toggle_job_saved, args=(job_id, bool(row["saved"])))
    with action_columns[1]:
        st.button("Restore" if row["skipped"] else "Pass", key=f"dialog_skip_{job_id}", use_container_width=True, on_click=_toggle_job_skipped, args=(job_id, bool(row["skipped"])))
    with action_columns[2]:
        st.button("Prepare", key=f"dialog_prepare_{job_id}", type="primary", use_container_width=True, on_click=_prepare_job, args=(job_id,))
    with action_columns[3]:
        if safe_link(row.get("apply_url")):
            st.link_button("Open application", safe_link(row["apply_url"]), use_container_width=True)
        else:
            st.button("No application URL", disabled=True, use_container_width=True)
    with st.expander("Advanced decision evidence", expanded=False):
        st.caption("Stored evidence only; missing values are not inferred.")
        st.dataframe([{"Source": row.get("source"), "Remote type": row.get("remote_type"), "Date posted": row.get("date_posted"), "Target track": row.get("target_track"), "Hard rejection reason": row.get("hard_rejection_reason")}], hide_index=True, use_container_width=True)
    with st.expander("Raw machine evidence", expanded=False):
        raw = row.get("detail_extraction_json")
        if raw:
            try:
                st.json(json.loads(str(raw)))
            except (TypeError, ValueError, json.JSONDecodeError):
                st.code(str(raw), language="text")
        else:
            st.caption("No raw extraction evidence is stored for this role.")
    st.button("Close", key=f"dialog_close_{job_id}", on_click=_clear_job_query, use_container_width=True)


def _job_detail() -> None:
    try:
        raw_job = str(st.query_params.get("job") or "").strip()
    except Exception:
        raw_job = ""
    if not raw_job:
        return
    try:
        job_id = int(raw_job)
    except (TypeError, ValueError):
        _clear_job_query()
        return
    _job_detail_dialog(job_id)

def _filter_defaults() -> dict[str, Any]:
    return {
        "query": "", "exclude": "", "location": "", "source": "",
        "workplace": "", "employment_type": "", "target_track": "",
        "score_range": (0, 100), "eligibility": "all", "freshness_days": 0,
        "ats_only": False, "result_set": "all", "search_scope": "all_fields",
        "sort_by": "match_desc",
    }


def _reset_search_filters(namespace: str) -> None:
    defaults = _filter_defaults()
    for key, value in defaults.items():
        st.session_state[f"{namespace}_filter_{key}"] = value
    st.session_state[f"{namespace}_filters"] = defaults.copy()
    if namespace == "jobs":
        st.session_state["product_jobs_page"] = 1


def _search_filters(namespace: str) -> dict[str, Any]:
    values = job_filters()
    defaults = _filter_defaults()
    stored = st.session_state.setdefault(f"{namespace}_filters", defaults.copy())
    for key, default in defaults.items():
        st.session_state.setdefault(f"{namespace}_filter_{key}", stored.get(key, default))
    with st.container(key=f"product_{namespace}_search"):
        search_left, search_right = st.columns((1, 3.8), gap="small")
        with search_left:
            scope = st.selectbox("Search scope", ["all_fields", "title_description", "title_company"], format_func=lambda value: {"all_fields": "Title + company + JD", "title_description": "Title + description", "title_company": "Title + company"}[value], key=f"{namespace}_filter_search_scope", label_visibility="collapsed")
        with search_right:
            query = st.text_input("Search jobs", placeholder="Search title, company, or job-description keyword…", key=f"{namespace}_filter_query", label_visibility="collapsed")
        exclude = st.text_input("Exclude terms", placeholder="Exclude terms — separate multiple terms with commas…", key=f"{namespace}_filter_exclude", label_visibility="collapsed")
        controls = st.columns((1.15, 1, 1, 1.1), gap="small")
        with controls[0]:
            location = st.selectbox("Location", [""] + values["locations"], format_func=lambda x: x or "All locations", key=f"{namespace}_filter_location")
        with controls[1]:
            workplace = st.selectbox("Workplace", [""] + values["remote"], format_func=lambda x: x or "Any workplace", key=f"{namespace}_filter_workplace")
        with controls[2]:
            source = st.selectbox("Source", [""] + values["sources"], format_func=lambda x: x or "All sources", key=f"{namespace}_filter_source")
        with controls[3]:
            employment = st.selectbox("Employment", [""] + values["employment"], format_func=lambda x: x or "Any employment", key=f"{namespace}_filter_employment_type")
        with st.expander("Advanced filters", expanded=False):
            advanced_one = st.columns((1.25, 1, 1), gap="small")
            with advanced_one[0]:
                target_track = st.selectbox("Target track", [""] + values["target_tracks"], format_func=lambda x: x or "Any target track", key=f"{namespace}_filter_target_track")
            with advanced_one[1]:
                eligibility = st.selectbox("Eligibility evidence", ["all", "unblocked", "blocked"], format_func=lambda x: {"all": "All evidence states", "unblocked": "No explicit blocker", "blocked": "Explicit blocker recorded"}[x], key=f"{namespace}_filter_eligibility")
            with advanced_one[2]:
                freshness_days = st.selectbox("First seen", [0, 1, 3, 7, 14, 30, 90], format_func=lambda x: "Any time" if x == 0 else "Past 24 hours" if x == 1 else f"Past {x} days", key=f"{namespace}_filter_freshness_days")
            advanced_two = st.columns((1.4, 1, 1), gap="small")
            with advanced_two[0]:
                score_range = st.slider("Hunter match range", min_value=0, max_value=100, key=f"{namespace}_filter_score_range")
            with advanced_two[1]:
                sort_by = st.selectbox("Sort", ["match_desc", "newest", "oldest", "ats_desc", "company"], format_func=lambda x: {"match_desc": "Best match", "newest": "Newest first", "oldest": "Oldest first", "ats_desc": "Highest ATS score", "company": "Company A–Z"}[x], key=f"{namespace}_filter_sort_by")
            with advanced_two[2]:
                result_set = st.selectbox("Result set", ["all", "saved", "passed"], format_func=lambda x: {"all": "All jobs", "saved": "Saved", "passed": "Passed"}[x], key=f"{namespace}_filter_result_set")
            ats_only = st.toggle("Only jobs with an ATS-scored package", key=f"{namespace}_filter_ats_only")
        reset_area, explanation = st.columns((1, 4), gap="small")
        with reset_area:
            st.button("Reset filters", key=f"{namespace}_reset_filters", use_container_width=True, on_click=_reset_search_filters, args=(namespace,))
        with explanation:
            st.caption("Filters update live. Every filter uses stored job, score, ATS, and eligibility evidence.")
    current = {
        "query": query, "exclude": exclude, "location": location, "source": source,
        "workplace": workplace, "employment_type": employment, "target_track": target_track,
        "score_range": tuple(score_range), "minimum_score": float(score_range[0]),
        "maximum_score": float(score_range[1]), "eligibility": eligibility,
        "freshness_days": int(freshness_days), "ats_only": bool(ats_only),
        "saved_only": result_set == "saved", "result_set": result_set,
        "search_scope": scope, "sort_by": sort_by,
    }
    st.session_state[f"{namespace}_filters"] = {key: current.get(key, defaults.get(key)) for key in defaults}
    query_args = dict(current)
    query_args.pop("score_range", None)
    return query_args

def dashboard() -> None:
    _show_action_feedback()
    page_intro("MUNSHI APPLY", "The right roles. The evidence to act.", "Search current opportunities, inspect why they match, and prepare application packages through the guarded workflow.")
    policy = volume_policy()
    activity = activity_summary()
    label = {"unlimited": "Unlimited", "custom_limit": f"Custom target: {policy.get('daily_limit')}", "paused": "Paused", "pause_after_batch": "Pausing after current batch"}[policy["mode"]]
    filters = _search_filters("dashboard")
    with st.container(key="product_dashboard_metrics"):
        summary = st.columns(5, gap="small")
        summary[0].metric("Prepared today", activity["prepared_today"])
        summary[1].metric("Submitted today", activity["submitted_today"], help="Only external submission evidence is counted.")
        summary[2].metric("Needs review", activity["needs_you"])
        summary[3].metric("In progress", activity["in_progress"])
        summary[4].metric("Automation", label, help="Canonical safety and provider controls remain authoritative.")
    jobs, count = fetch_jobs(**filters, page_size=4)
    st.markdown(f'<div class="section-row"><div><h2>Top matches</h2><span class="quiet">{count:,} opportunities · ranked by canonical Hunter score</span></div><a class="product-nav-link" href="?view=jobs" target="_self">Browse all jobs →</a></div>', unsafe_allow_html=True)
    if jobs:
        with st.container(key="product_dashboard_grid"):
            columns = st.columns(min(4, len(jobs)), gap="medium")
            for index, row in enumerate(jobs):
                with columns[index % len(columns)]: _job_card(row, key_prefix="dashboard")
    else:
        st.markdown('<div class="empty-product">No jobs match the current filters. Change a filter or add a job with its complete description.</div>', unsafe_allow_html=True)
    tracker = tracker_rows()
    st.markdown('<div class="section-row"><div><h2>Application activity</h2><span class="quiet">Packages and dispatch evidence — never assumed submissions</span></div><a class="product-nav-link" href="?view=tracker" target="_self">Open tracker →</a></div>', unsafe_allow_html=True)
    if tracker:
        st.dataframe([{ "Company": x["company_name"], "Role": x["title"], "Status": x["display_status"], "ATS score": x["final_ats_score"], "Updated": x.get("completed_at") or x.get("updated_at") } for x in tracker[:8]], hide_index=True, use_container_width=True)
    else:
        st.markdown('<div class="empty-product">No application-package or queue evidence yet. Preparing a job creates a traceable package record here.</div>', unsafe_allow_html=True)
    _job_detail()


def browse_jobs() -> None:
    _show_action_feedback()
    page_intro("DISCOVER", "Browse jobs", "Search every stored opportunity without losing the targeting, authorization, source, or score evidence behind the match.")
    filters = _search_filters("jobs")
    page = int(st.session_state.get("product_jobs_page", 1))
    jobs, count = fetch_jobs(**filters, page=page, page_size=16)
    header, add = st.columns((3.2, 1))
    with header: st.markdown(f"**{count:,} matching opportunities**  ·  Page {page}")
    with add:
        if st.button("+ Add your own", key="product_add_own", use_container_width=True): st.session_state["show_manual_job"] = True
    if st.session_state.get("show_manual_job"):
        _manual_add()
    if jobs:
        with st.container(key="product_job_grid"):
            for start in range(0, len(jobs), 4):
                with st.container(key=f"product_job_grid_row_{start}"):
                    cols = st.columns(4, gap="medium")
                    for index, row in enumerate(jobs[start:start + 4]):
                        with cols[index]: _job_card(row, key_prefix="jobs")
        prev, _, next_ = st.columns((1, 3, 1))
        with prev:
            if st.button("← Previous", disabled=page <= 1, key="jobs_previous"):
                st.session_state["product_jobs_page"] = page - 1; st.rerun()
        with next_:
            if st.button("Next →", disabled=page * 16 >= count, key="jobs_next"):
                st.session_state["product_jobs_page"] = page + 1; st.rerun()
    else:
        st.markdown('<div class="empty-product">No stored jobs match this view.</div>', unsafe_allow_html=True)
    _job_detail()


def _manual_add() -> None:
    from app.manual_input import missing_required_fields, parse_manual_job_text, persist_manual_job
    with st.expander("Add your own job", expanded=True):
        st.caption("Paste an application URL and full labeled job text. MUNSHI will show parsed data and missing fields; it will not fabricate them.")
        with st.form("product_manual_analyze"):
            url = st.text_input("Job URL", placeholder="https://careers.example.com/jobs/…")
            description = st.text_area("Full labeled job text", height=220, placeholder="Job Title: ...\nCompany Name: ...\nLocation: ...\nJob Description: ...")
            analyze = st.form_submit_button("Analyze job", type="primary")
        if analyze:
            raw = f"Application: {url}\n{description}".strip()
            parsed = parse_manual_job_text(raw)
            missing = missing_required_fields(parsed)
            st.session_state["manual_analysis"] = {"missing": missing, "fields": parsed.get("fields", {}), "raw": raw}
        result = st.session_state.get("manual_analysis")
        if result:
            if result["missing"]:
                st.warning("Missing required fields: " + ", ".join(result["missing"]) + ". Add labeled title, company, location, URL, and full job description to continue through the existing canonical manual path.")
            else:
                st.success("The pasted job contains required fields. Review the parsed values, then explicitly confirm persistence through the canonical manual-job store.")
            st.json(result["fields"])
            if not result["missing"]:
                confirmed = st.checkbox("I confirm these parsed fields are accurate and should be persisted.", key="product_manual_confirm")
                if st.button("Persist job", key="product_manual_persist", type="primary", disabled=not confirmed):
                    saved = persist_manual_job(result["raw"])
                    st.session_state["product_manual_saved_job_id"] = saved["job_id"]
                    st.success(f"Job {saved['job_id']} was persisted through the canonical manual path.")
            job_id = st.session_state.get("product_manual_saved_job_id")
            if job_id and st.button("Prepare persisted job", key="product_manual_prepare"):
                from app.stored_job_n8n_worker import start_stored_job_run
                prepared = start_stored_job_run(int(job_id), actor="dashboard_product_manual_input")
                if prepared.get("success"):
                    st.success(str(prepared.get("message") or "Preparation request processed."))
                else:
                    st.warning(str(prepared.get("message") or "Preparation was not started."))


def auto_prepare() -> None:
    page_intro("AUTOMATION", "Auto Prepare, under your control.", "Choose the global preparation volume and use lanes to narrow role types. Opening this page never creates work.")
    policy = volume_policy()
    activity = activity_summary()
    current_lanes = lanes()
    enabled_count = sum(bool(lane["enabled"]) for lane in current_lanes)
    mode_label = {"unlimited": "Unlimited", "custom_limit": f"Custom daily target · {policy.get('daily_limit')}", "pause_after_batch": "Pause after current batch", "paused": "Paused"}[policy["mode"]]
    st.markdown(
        f'<div class="product-callout"><div><strong>{esc(mode_label)}</strong><span>The global volume preference is active. Lanes only narrow eligible roles.</span></div><span class="status-chip">{enabled_count} of {len(current_lanes)} lanes enabled</span></div>',
        unsafe_allow_html=True,
    )
    with st.container(key="product_auto_metrics"):
        counters = st.columns(4, gap="small")
        counters[0].metric("Prepared today", activity["prepared_today"])
        counters[1].metric("Submitted today", activity["submitted_today"], help="Requires recorded external submission evidence.")
        counters[2].metric("Needs review", activity["needs_you"])
        counters[3].metric("In progress", activity["in_progress"])
    with st.container(key="product_auto_split"):
        left, right = st.columns((1.6, 1), gap="large")
    with left:
        st.markdown("### Target lanes")
        st.caption("Saved role filters are evaluated only after canonical targeting and work-authorization rules.")
        if current_lanes:
            for lane in current_lanes:
                try:
                    keywords = json.loads(str(lane.get("filter_json") or "{}")).get("keywords", "")
                except (TypeError, ValueError, json.JSONDecodeError):
                    keywords = ""
                st.markdown(
                    f"<div class='lane-card'><strong>{esc(lane['name'])}</strong><br>"
                    f"<span class='status-chip'>{'Enabled' if lane['enabled'] else 'Disabled'}</span> "
                    f"<span class='quiet'>Minimum match {esc(lane.get('min_score'))} · Keywords: {esc(keywords)}</span></div>",
                    unsafe_allow_html=True,
                )
                controls = st.columns(3)
                with controls[0]:
                    if st.button("Disable" if lane["enabled"] else "Enable", key=f"lane_enable_{lane['id']}", use_container_width=True):
                        set_lane_enabled(int(lane["id"]), not bool(lane["enabled"])); st.rerun()
                with controls[1]:
                    if st.button("Edit", key=f"lane_edit_{lane['id']}", use_container_width=True):
                        st.session_state["product_edit_lane"] = int(lane["id"]); st.rerun()
                with controls[2]:
                    pending = st.session_state.get("product_confirm_delete_lane") == int(lane["id"])
                    if st.button("Confirm remove" if pending else "Remove", key=f"lane_delete_{lane['id']}", use_container_width=True):
                        if pending:
                            delete_lane(int(lane["id"]))
                            st.session_state.pop("product_confirm_delete_lane", None)
                            st.rerun()
                        st.session_state["product_confirm_delete_lane"] = int(lane["id"])
                        st.warning("Select Confirm remove to delete this disabled or enabled lane. This does not change canonical targeting.")
        else:
            st.markdown('<div class="empty-product"><h3>No lanes yet</h3><p>Create a focused role filter when you need one. New lanes remain disabled until you enable them.</p></div>', unsafe_allow_html=True)
        with st.expander("Create a lane", expanded=not current_lanes):
            with st.form("product_lane_form"):
                name = st.text_input("Lane name", placeholder="HR operations · Northeast")
                keywords = st.text_input("Role keywords", placeholder="HR coordinator, people operations")
                minimum = st.number_input("Minimum Hunter score", min_value=0.0, max_value=100.0, value=70.0)
                if st.form_submit_button("Save disabled lane", type="primary"):
                    try:
                        create_lane(name, {"keywords": keywords}, minimum, "unlimited", None)
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.success("Lane saved disabled. No work was queued."); st.rerun()
        editable = next((lane for lane in current_lanes if int(lane["id"]) == st.session_state.get("product_edit_lane")), None)
        if editable:
            with st.form("product_lane_edit_form"):
                edit_name = st.text_input("Lane name", value=str(editable["name"]))
                try: edit_keywords = json.loads(str(editable["filter_json"])).get("keywords", "")
                except (TypeError, ValueError, json.JSONDecodeError): edit_keywords = ""
                edit_keywords = st.text_input("Role keywords", value=str(edit_keywords))
                edit_minimum = st.number_input("Minimum Hunter score", min_value=0.0, max_value=100.0, value=float(editable["min_score"] or 0))
                if st.form_submit_button("Save lane changes", type="primary"):
                    try:
                        update_lane(int(editable["id"]), name=edit_name, filters={"keywords": edit_keywords}, min_score=edit_minimum)
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.session_state.pop("product_edit_lane", None); st.rerun()
    with right:
        with st.container(key="product_volume_panel", border=True):
            st.markdown("### Preparation volume")
            st.caption("The only product-level volume control.")
            with st.form("product_volume_form"):
                mode = st.radio("Mode", ["unlimited", "custom_limit", "pause_after_batch", "paused"], index=["unlimited", "custom_limit", "pause_after_batch", "paused"].index(policy["mode"]), format_func=lambda x: {"unlimited":"Unlimited", "custom_limit":"Custom daily limit", "pause_after_batch":"Pause after current batch", "paused":"Paused"}[x])
                limit = st.number_input("Custom daily limit", min_value=1, value=max(1, int(policy.get("daily_limit") or 25)), disabled=mode != "custom_limit")
                review = st.checkbox("Review before action", value=policy["review_first"])
                if st.form_submit_button("Save preference", type="primary", use_container_width=True):
                    save_volume_policy(mode, limit, review)
                    st.success("Preference saved. Canonical safeguards remain separate.")
        st.markdown("<div class='muted-panel'><b>What Unlimited means</b><br>No MUNSHI business quota. Provider throttles, targeting, authorization, dedupe, ATS, and risk controls still apply.<br><br><b>Pause after current batch</b><br>The active batch can finish; a later batch must not start.</div>", unsafe_allow_html=True)


def _pipeline_list(records: list[dict[str, Any]]) -> None:
    rows = []
    for record in records:
        hunter = f"{float(record['hunter_score']):.0f}% match" if record.get("hunter_score") is not None else "Match not scored"
        ats = f"ATS {float(record['final_ats_score']):.0f}" if record.get("final_ats_score") is not None else "ATS not scored"
        updated = record.get("completed_at") or record.get("updated_at") or record.get("queued_at") or "Date not recorded"
        rows.append(f"""<div class="pipeline-row">
            <div><strong>{esc(record.get("company_name"), "Company not recorded")}</strong><span>{esc(record.get("title"), "Untitled role")}</span></div>
            <div><span class="status-chip">{esc(record["display_status"])}</span><span class="pipeline-meta">{esc(updated)}</span><span class="pipeline-evidence">{esc(record.get("status_evidence"), "No lifecycle evidence")}</span></div>
            <div><strong>{esc(hunter)}</strong><span class="pipeline-meta">{esc(ats)}</span></div>
        </div>""")
    st.markdown(f'<div class="table-shell">{"".join(rows)}</div>', unsafe_allow_html=True)

def tracker() -> None:
    page_intro("TRACKER", "Your application workspace", "Track prepared packages, review-required items, proven submissions, and synchronized read-only Gmail evidence.")
    tab = st.radio("Tracker view", ["Pipeline", "Inbox"], horizontal=True, label_visibility="collapsed", key="product_tracker_tab", on_change=_sync_subroute, args=("tab", "product_tracker_tab", {"Pipeline": "pipeline", "Inbox": "inbox"}))
    if tab == "Inbox":
        _inbox(); return
    records = tracker_rows(limit=250)
    preferred = ["Prepared", "In progress", "Needs review", "Blocked", "Submitted", "Failed", "Skipped", "Queue completed", "Status not recorded"]
    represented = list(dict.fromkeys(str(record.get("display_status") or "Status not recorded") for record in records))
    statuses = ["All"] + [status for status in preferred if status in represented]
    statuses += [status for status in represented if status not in statuses]
    with st.container(key="product_tracker_filters"):
        filters, search = st.columns((2.4, 1.6))
    with filters:
        selected = st.radio("Pipeline status", statuses, horizontal=True, label_visibility="collapsed")
    with search:
        query = st.text_input("Search pipeline", placeholder="Search company or role", label_visibility="collapsed")
    needle = query.strip().casefold()
    visible = [record for record in records if (selected == "All" or record["display_status"] == selected) and (not needle or needle in f"{record.get('company_name') or ''} {record.get('title') or ''}".casefold())]
    if not visible:
        st.markdown('<div class="empty-product"><h3>No pipeline items match this filter.</h3><p>Lifecycle labels are generated only from recorded workflow and queue evidence.</p></div>', unsafe_allow_html=True)
        return
    _pipeline_list(visible)
    choices = {f"{index + 1}. {record.get('company_name') or 'Company not recorded'} — {record.get('title') or 'Untitled role'}": record for index, record in enumerate(visible)}
    chosen = st.selectbox("Inspect pipeline evidence", list(choices), key="product_tracker_evidence")
    record = choices[chosen]
    with st.expander("Package and dispatch evidence", expanded=True):
        facts = st.columns(3)
        facts[0].metric("Lifecycle", record["display_status"])
        facts[1].metric("Hunter match", f"{float(record['hunter_score']):.0f}%" if record.get("hunter_score") is not None else "Not available")
        facts[2].metric("ATS score", record.get("final_ats_score") if record.get("final_ats_score") is not None else "Not available")
        st.caption("Recorded lifecycle evidence · " + str(record.get("status_evidence") or "none"))
        artifacts = st.columns(3)
        with artifacts[0]:
            if safe_link(record.get("resume_pdf_url")): st.link_button("Open resume", safe_link(record["resume_pdf_url"]), use_container_width=True)
        with artifacts[1]:
            if safe_link(record.get("cover_letter_doc_url")): st.link_button("Open cover letter", safe_link(record["cover_letter_doc_url"]), use_container_width=True)
        with artifacts[2]:
            if safe_link(record.get("apply_url")): st.link_button("Open application", safe_link(record["apply_url"]), use_container_width=True)
        if not any(safe_link(record.get(field)) for field in ("resume_pdf_url", "cover_letter_doc_url", "apply_url")):
            st.caption("No external artifact or application link is recorded.")

def _inbox() -> None:
    from app.gmail_integration import begin_authorization, connection_status, disconnect, gmail_configuration_status, stored_messages, sync_messages
    status = gmail_configuration_status()
    st.caption(f"Gmail read-only · OAuth client: {status['oauth_client']} · Secure vault: {status['vault']}")
    if not status["ready"]:
        st.markdown('<div class="empty-product"><h3>Gmail is not configured</h3><p>An administrator must provision the OAuth client and encrypted server-side vault before connection is available. MUNSHI never sends email from this integration.</p></div>', unsafe_allow_html=True)
        st.button("Connect Gmail", disabled=True, help="Gmail must be configured on the server before connection is available.")
        return
    connection = connection_status()
    if not connection["connected"]:
        if st.button("Connect Gmail", key="gmail_connect", type="primary"):
            st.session_state["gmail_authorization_url"] = begin_authorization()
        if st.session_state.get("gmail_authorization_url"):
            st.link_button("Continue to Google", st.session_state["gmail_authorization_url"], type="primary")
        return
    st.success(f"Gmail connected: {connection['account']}. Last sync: {connection['last_sync_at'] or 'never'}")
    actions = st.columns((1, 1, 3))
    with actions[0]:
        if st.button("Sync now", key="gmail_sync"):
            try: st.success(f"Synchronized {sync_messages()} new message(s).")
            except RuntimeError as error: st.warning(str(error))
    with actions[1]:
        if st.button("Disconnect", key="gmail_disconnect"):
            disconnect(); st.session_state.pop("gmail_authorization_url", None); st.rerun()
    filters = st.columns((1, 2.5))
    with filters[0]:
        category = st.selectbox("Category", ["All", "interview", "assessment", "offer", "rejection", "verification", "reminder", "applied", "unclassified"], key="gmail_category")
    with filters[1]:
        query = st.text_input("Search synchronized messages", key="gmail_query", placeholder="Search subject, sender, or preview")
    messages = stored_messages(category=category, query=query)
    if messages:
        message_ids = [str(item["gmail_message_id"]) for item in messages]
        if st.session_state.get("product_inbox_selected") not in message_ids:
            st.session_state["product_inbox_selected"] = message_ids[0]
        with st.container(key="product_inbox_split"):
            listing, reader = st.columns((1, 1.45), gap="medium")
        with listing:
            st.markdown("### Messages")
            for item in messages[:25]:
                message_id = str(item["gmail_message_id"])
                if st.button(
                    f"{item.get('subject') or '(No subject)'}\n\n{item.get('sender') or 'Sender not recorded'}",
                    key=f"gmail_message_{message_id}",
                    type="primary" if message_id == st.session_state["product_inbox_selected"] else "secondary",
                    use_container_width=True,
                ):
                    st.session_state["product_inbox_selected"] = message_id
                    st.rerun()
                st.caption(str(item.get("received_at") or "Date not recorded"))
        selected = next(item for item in messages if str(item["gmail_message_id"]) == st.session_state["product_inbox_selected"])
        with reader:
            with st.container(key="product_message_reader", border=True):
                st.markdown(f"### {esc(selected.get('subject'), '(No subject)')}", unsafe_allow_html=True)
                st.caption(f"From {selected.get('sender') or 'Sender not recorded'} · {selected.get('received_at') or 'Date not recorded'} · {selected.get('category') or 'unclassified'}")
                body = selected.get("body_text") or selected.get("snippet") or "No stored message preview is available."
                st.markdown(f'<div class="message-reader-body">{esc(body)}</div>', unsafe_allow_html=True)
                with st.expander("Classification evidence", expanded=False):
                    st.caption(selected.get("classification_evidence") or "No classification evidence is stored.")
    else:
        st.markdown('<div class="empty-product"><h3>No synchronized messages</h3><p>No stored Gmail messages match this filter. Sync runs only when you select Sync now.</p></div>', unsafe_allow_html=True)


def profile() -> None:
    page_intro("PROFILE", "Your candidate workspace", "Keep one explicitly designated master resume, real tailored artifacts, cover letters, and candidate-provided facts in one place.")
    tab = st.radio("Profile section", ["Resume", "Cover letters", "Profile details"], horizontal=True, label_visibility="collapsed", key="product_profile_tab", on_change=_sync_subroute, args=("tab", "product_profile_tab", {"Resume": "resume", "Cover letters": "cover-letter", "Profile details": "details"}))
    if tab == "Profile details":
        facts = candidate_facts()
        existing = {str(fact["fact_key"]): fact for fact in facts}
        st.markdown(f'<div class="product-callout"><div><strong>{len(facts)} saved profile facts</strong><span>Every fact is candidate-provided and editable.</span></div><span class="status-chip">Sensitive facts are never inferred</span></div>', unsafe_allow_html=True)
        selected_label = st.selectbox("Edit a saved fact", ["Add a new fact", *existing], key="product_profile_fact_select")
        selected = existing.get(selected_label)
        selected_value = ""
        if selected:
            try: selected_value = str(json.loads(str(selected.get("value_json") or '""')))
            except (TypeError, ValueError, json.JSONDecodeError): selected_value = ""
        with st.form("candidate_fact_form"):
            key = st.text_input("Fact label", value=selected_label if selected else "", placeholder="Preferred roles")
            value = st.text_area("Value", value=selected_value, placeholder="HR operations, people analytics")
            if st.form_submit_button("Save profile fact", type="primary"):
                try: save_candidate_fact(key, value)
                except ValueError as error: st.error(str(error))
                else: st.success("Candidate fact saved with source ‘Candidate’."); st.rerun()
        if facts:
            st.caption("Only add information you intend MUNSHI to use. Sensitive or voluntary facts are never inferred.")
            st.dataframe([{"Fact": x["fact_key"], "Value": json.loads(x["value_json"]), "Source": x["source_label"], "Updated": x["updated_at"]} for x in facts], hide_index=True, use_container_width=True)
        else: st.info("No profile facts stored yet.")
        return
    records = tracker_rows(limit=250)
    field = "cover_letter_doc_url" if tab == "Cover letters" else "resume_pdf_url"
    label = "Cover letter" if tab == "Cover letters" else "Resume"
    artifacts = [x for x in records if safe_link(x.get(field))]
    if tab == "Resume":
        designated = master_resume()
        st.markdown("### Master resume")
        if designated:
            with st.container(key="product_master_resume", border=True):
                st.markdown(f"**{esc(designated.get('label') or 'Master resume')}**", unsafe_allow_html=True)
                st.caption("Explicitly designated master artifact · " + str(designated.get("designated_at") or "date not recorded"))
                master_actions = st.columns((1, 1, 2.4))
                with master_actions[0]:
                    st.link_button("Open master resume", safe_link(designated["url"]), type="primary", use_container_width=True)
                with master_actions[1]:
                    if st.button("Clear designation", key="clear_master_resume", use_container_width=True): clear_master_resume(); st.rerun()
                with master_actions[2]:
                    st.caption("This designation does not rewrite the document or silently replace it with a tailored version.")
                if st.toggle("Preview master resume", key="product_master_resume_preview"):
                    components.iframe(designated["url"], height=760, scrolling=True)
        else:
            st.markdown('<div class="empty-product"><h3>No master resume designated yet</h3><p>MUNSHI will not guess which tailored resume should become your master. Choose one of the real generated artifacts below only if you want to designate it.</p></div>', unsafe_allow_html=True)
            if artifacts:
                options = {f"{artifact.get('company_name') or 'Company not recorded'} — {artifact.get('title') or 'Untitled role'} · ATS {artifact.get('final_ats_score') if artifact.get('final_ats_score') is not None else 'N/A'}": artifact for artifact in artifacts}
                selected_label = st.selectbox("Choose an existing resume artifact", list(options), key="master_resume_candidate")
                selected = options[selected_label]
                if st.button("Set selected artifact as master", type="primary", key="set_master_resume"):
                    save_master_resume(int(selected["job_id"]), selected["resume_pdf_url"], selected_label); st.rerun()
        st.markdown("### Tailored resume history")
    if artifacts:
        for artifact in artifacts:
            with st.container(key=f"product_profile_artifact_{field}_{artifact['job_id']}", border=True):
                description, action = st.columns((3, 1))
                with description:
                    st.markdown(f"**{esc(artifact['company_name'])} · {esc(artifact['title'])}**", unsafe_allow_html=True)
                    ats = artifact.get("final_ats_score") if artifact.get("final_ats_score") is not None else "Not available"
                    prepared = artifact.get("completed_at") or artifact.get("sent_at") or "Date not recorded"
                    st.caption(f"ATS score: {ats} · Prepared: {prepared}")
                with action:
                    st.link_button(f"Open {label}", safe_link(artifact[field]), use_container_width=True)
    else:
        st.markdown(f'<div class="empty-product"><h3>No {esc(label.lower())} artifacts yet</h3><p>A real generated artifact will appear here after the guarded workflow records its URL.</p></div>', unsafe_allow_html=True)

def _research_blocker_label(reason: Any) -> str:
    from app.presentation_analytics import humanize_machine_value
    raw = str(reason or "").strip()
    if not raw:
        return "Not recorded"
    leaf = raw.rsplit(":", 1)[-1]
    aliases = {
        "dashboard_hard_reject_keyword": "Hard requirement or exclusion keyword",
        "hard_reject_keyword": "Hard requirement or exclusion keyword",
        "country_unknown_fail_closed": "U.S. location not confirmed",
        "title_not_in_configured_role_families": "Role outside targeting",
    }
    return aliases.get(leaf, humanize_machine_value(leaf))


def _authorization_summary(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.casefold() in {"not recorded", "unknown"}:
        return "Not recorded"
    return "Recorded — review evidence"


def research() -> None:
    page_intro("INTELLIGENCE", "Research", "Understand discovery scale, source yield, match quality, blockers, and application-package evidence without mixing telemetry horizons.")
    snapshot = research_snapshot()
    metrics = snapshot["headline"]
    lifetime = snapshot.get("lifetime") or {}
    st.markdown("### Lifetime opportunity funnel")
    funnel = st.columns(4, gap="small")
    funnel[0].metric("Opportunities scanned", f"{int(lifetime.get('scanned') or 0):,}", help="Provider records measured by source-run telemetry.")
    funnel[1].metric("Normalized records", f"{int(lifetime.get('normalized') or 0):,}")
    funnel[2].metric("Eligible telemetry", f"{int(lifetime.get('eligible') or 0):,}", help="Eligible count only for the source-run telemetry horizon.")
    funnel[3].metric("Stored opportunities", f"{int(metrics.get('jobs') or 0):,}", help="Current canonical job inventory; not the same horizon as scanned records.")
    funnel_two = st.columns(4, gap="small")
    funnel_two[0].metric("Targeting decisions", f"{int(lifetime.get('decisions') or 0):,}")
    funnel_two[1].metric("Telegram-delivered jobs", f"{int(lifetime.get('jobs_delivered') or 0):,}")
    funnel_two[2].metric("ATS-scored packages", f"{int(snapshot['ats'].get('scored_packages') or 0):,}")
    funnel_two[3].metric("Explicitly blocked", f"{int(metrics.get('blocked') or 0):,}")
    st.caption("Evidence horizon note: “Opportunities scanned” is cumulative provider telemetry from source runs. “Stored opportunities” is the current canonical job inventory. They are intentionally not the same number.")
    telemetry = snapshot.get("source_telemetry") or []
    if telemetry:
        st.markdown("### Source discovery performance")
        frame = pd.DataFrame(telemetry)
        st.bar_chart(frame[["source", "scanned", "eligible"]].set_index("source").head(12), height=330)
        st.dataframe(frame.rename(columns={"source": "Source", "runs": "Runs", "scanned": "Scanned", "normalized": "Normalized", "eligible": "Eligible", "new_eligible": "New eligible", "last_run": "Last run"}), hide_index=True, use_container_width=True)
    else: st.info("No source-run telemetry is available yet.")
    st.markdown('<div class="section-row"><div><h2>Match intelligence</h2><span class="quiet">Score, target-track, and blocker evidence are shown together. A high match never overrides an explicit blocker.</span></div></div>', unsafe_allow_html=True)
    if snapshot["top_matches"]:
        labels = {int(item["id"]): f"{item.get('company_name') or 'Company not recorded'} — {item.get('title') or 'Untitled role'}" for item in snapshot["top_matches"]}
        selected_id = st.selectbox("Inspect a scored opportunity", list(labels), format_func=labels.get, key="product_research_match")
        match = next(item for item in snapshot["top_matches"] if int(item["id"]) == selected_id)
        tone = pastel_for(match["id"])
        st.markdown(f"""<div class="intelligence-card" style="background:{tone}"><div class="page-kicker-product">MATCH + ELIGIBILITY EVIDENCE</div><div class="evidence-list">
            <div class="evidence-item"><b>Hunter match</b>{esc(match.get("hunter_score"))}%</div><div class="evidence-item"><b>Target track</b>{esc(match.get("target_track"))}</div>
            <div class="evidence-item"><b>Source</b>{esc(match.get("source"))}</div><div class="evidence-item"><b>Location</b>{esc(match.get("location_raw"))}</div>
            <div class="evidence-item"><b>Workplace</b>{esc(match.get("remote_type"))}</div><div class="evidence-item"><b>Work authorization</b>{esc(_authorization_summary(match.get("work_authorization")))}</div>
            <div class="evidence-item"><b>Employment</b>{esc(match.get("employment_type"))}</div><div class="evidence-item"><b>Eligibility blocker</b>{esc(_research_blocker_label(match.get("hard_rejection_reason")), "None explicitly recorded")}</div>
        </div></div>""", unsafe_allow_html=True)
        authorization = str(match.get("work_authorization") or "").strip()
        if authorization:
            with st.expander("Full work-authorization evidence", expanded=False): st.write(authorization)
    else:
        st.markdown('<div class="empty-product"><h3>No scored matches yet</h3><p>Match reasoning appears after canonical job records contain a Hunter score.</p></div>', unsafe_allow_html=True)
    trend = snapshot.get("trends") or []
    if trend:
        st.markdown("### Discovery trend")
        trend_frame = pd.DataFrame(trend)
        st.line_chart(trend_frame.set_index("date")[["opportunities", "average_match"]], height=280)
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("### Stored source quality")
        if snapshot["source_quality"]:
            st.dataframe([{"Source": item["source"], "Stored opportunities": item["opportunities"], "Average match": item["average_match"], "No explicit blocker": item["eligible_records"]} for item in snapshot["source_quality"]], hide_index=True, use_container_width=True)
        else: st.info("No stored source evidence is available.")
    with right:
        st.markdown("### Eligibility blockers")
        if snapshot["blockers"]:
            st.dataframe([{"Blocker": _research_blocker_label(item["reason"]), "Opportunities": item["count"]} for item in snapshot["blockers"]], hide_index=True, use_container_width=True)
            with st.expander("Raw blocker evidence", expanded=False):
                st.dataframe([{"Raw blocker": item["reason"], "Opportunities": item["count"]} for item in snapshot["blockers"]], hide_index=True, use_container_width=True)
        else: st.info("No explicit rejection evidence is recorded.")
    st.markdown("### Query performance")
    if snapshot["query_performance"]:
        st.dataframe([{"Query": x["query_name"], "Runs": x["runs"], "Raw records": x["raw_records"], "Eligible records": x["eligible_records"], "Last run": x["last_run"]} for x in snapshot["query_performance"]], hide_index=True, use_container_width=True)
    else: st.info("No query-run evidence is available.")
    st.markdown("### Source and provider health")
    if snapshot["health"]:
        st.dataframe([{"Source": x["source_name"], "Health": x["health_status"], "Last success": x["last_success_at"], "Last run yield": x["jobs_found_last_run"]} for x in snapshot["health"]], hide_index=True, use_container_width=True)
    else: st.info("No source-health records are available.")

def settings() -> None:
    page_intro("PREFERENCES", "Settings", "Change personal preferences explicitly. System and provider operations are retained under Advanced, not removed.")
    options = ["Apply", "Automation", "Integrations", "Profile & defaults", "Credentials", "Advanced / System"]
    with st.container(key="product_settings_split"):
        left, content, art = st.columns((1.05, 2.4, 1.45), gap="large")
    with left:
        section = st.radio(
            "Settings section", options, label_visibility="collapsed", key="product_settings_section",
            on_change=_sync_subroute,
            args=("section", "product_settings_section", {
                "Apply": "apply", "Automation": "automation", "Integrations": "integrations",
                "Profile & defaults": "profile", "Credentials": "credentials", "Advanced / System": "advanced",
            }),
        )
    with content:
        if section == "Apply":
            policy = volume_policy(); st.markdown("### Review and preparation")
            st.caption("This saves your product-level review preference for the current workspace. The guarded workflow and all canonical targeting, authorization, dedupe, and ATS gates remain authoritative.")
            with st.form("product_review_preference_form"):
                review = st.checkbox("Require review before action", value=policy["review_first"])
                if st.form_submit_button("Save review preference", type="primary", use_container_width=True):
                    save_review_preference(review)
                    st.success("Review preference saved.")
            st.caption("Resume tailoring remains constrained to evidence already stored in the candidate profile and job record; MUNSHI does not add unsupported claims or make external submissions from this screen.")
        elif section == "Automation":
            policy = volume_policy(); st.markdown("### Automation volume")
            st.metric("Current mode", policy["mode"].replace("_", " ").title())
            st.caption("Use Auto Prepare to change volume, manage lanes, and review current activity. Lanes narrow candidates and never replace canonical targeting.")
            st.markdown('<a class="product-nav-link" href="?view=auto-prepare" target="_self">Open Auto Prepare →</a>', unsafe_allow_html=True)
        elif section == "Profile & defaults":
            st.markdown("### Candidate-provided facts")
            facts = candidate_facts()
            st.write("Saved facts are candidate-provided structured fields, not opaque model memory. Manage them in Profile → Profile details.")
            st.dataframe([{ "Fact": x["fact_key"], "Source": x["source_label"], "Updated": x["updated_at"] } for x in facts], hide_index=True, use_container_width=True) if facts else st.info("No facts stored.")
            st.markdown('<a class="product-nav-link" href="?view=profile&amp;tab=details" target="_self">Open profile details →</a>', unsafe_allow_html=True)
        elif section == "Integrations":
            st.markdown("### Gmail integration")
            _inbox()
        elif section == "Credentials":
            from app.secure_vault import vault_available
            st.markdown("### ATS credentials")
            if vault_available():
                st.success("Encrypted credential storage is available.")
            else:
                st.warning("Encrypted credential storage is not configured on this server.")
            st.info("No Workday, iCIMS, or Oracle credential is exposed by this product UI. Credential entry remains unavailable unless it can use the encrypted vault; passwords are never revealed here.")
        elif section == "Advanced / System":
            _advanced()
        else:
            st.markdown(f"### {section}")
            st.info("This section is intentionally read-only until it can use an existing canonical authority without changing runtime state during rendering.")
    with art:
        st.markdown("<div class='settings-illustration'><div><b>Truth before automation</b><br><br>Every consequential product label is backed by a canonical record, explicit policy, or visible ‘not configured’ state.</div></div>", unsafe_allow_html=True)


def _advanced() -> None:
    snapshot = research_snapshot()
    lifetime = snapshot.get("lifetime") or {}
    headline = snapshot.get("headline") or {}
    st.markdown("### System intelligence")
    st.caption("Clean operational evidence first. Legacy engineering tools remain available below only when you explicitly open them.")
    primary = st.columns(4, gap="small")
    primary[0].metric("Opportunities scanned", f"{int(lifetime.get('scanned') or 0):,}")
    primary[1].metric("Normalized records", f"{int(lifetime.get('normalized') or 0):,}")
    primary[2].metric("Jobs stored", f"{int(headline.get('jobs') or 0):,}")
    primary[3].metric("Eligible telemetry", f"{int(lifetime.get('eligible') or 0):,}")
    secondary = st.columns(4, gap="small")
    secondary[0].metric("Recorded source runs", f"{int(lifetime.get('runs') or 0):,}")
    secondary[1].metric("Targeting decisions", f"{int(lifetime.get('decisions') or 0):,}")
    secondary[2].metric("Telegram-delivered jobs", f"{int(lifetime.get('jobs_delivered') or 0):,}")
    secondary[3].metric("ATS-scored packages", f"{int(snapshot['ats'].get('scored_packages') or 0):,}")
    st.caption("The large scanned count is cumulative source-run telemetry. Jobs stored is the canonical inventory. Different horizons are kept separate so the dashboard never inflates one metric with another.")
    telemetry = snapshot.get("source_telemetry") or []
    if telemetry:
        st.markdown("### Source telemetry")
        st.dataframe([{"Source": row["source"], "Runs": row["runs"], "Scanned": row["scanned"], "Normalized": row["normalized"], "Eligible": row["eligible"], "New eligible": row["new_eligible"], "Last run": row["last_run"]} for row in telemetry], hide_index=True, use_container_width=True)
    if snapshot.get("health"):
        st.markdown("### Source health")
        st.dataframe([{"Source": row["source_name"], "Health": row["health_status"], "Last success": row["last_success_at"], "Last run yield": row["jobs_found_last_run"]} for row in snapshot["health"]], hide_index=True, use_container_width=True)
    with st.expander("Legacy engineering console", expanded=False):
        st.caption("Low-level diagnostics are intentionally separated from the product overview. They may include localhost probes and raw engineering labels useful for maintenance, but those should not be mistaken for customer-facing product status.")
        tool = st.selectbox("Engineering tool", ["None", "System / Diagnostics", "Source Health", "Adapter Coverage", "Targeting", "Query Performance", "Queue / Actions", "Storage", "Backups", "Credentials"], key="product_legacy_engineering_tool")
        if tool != "None":
            from app import operations_dashboard as legacy
            renderers = {"System / Diagnostics": legacy._system_diagnostics, "Source Health": legacy._source_health, "Adapter Coverage": legacy._adapter_coverage, "Targeting": legacy._targeting, "Query Performance": legacy._query_performance, "Queue / Actions": legacy._queue_actions, "Storage": legacy._storage, "Backups": legacy._backups, "Credentials": legacy._credentials}
            renderers[tool]()
