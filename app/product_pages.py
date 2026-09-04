"""MUNSHI's user-facing product views.

All labels here are evidence-bound.  In particular, a completed n8n package is
shown as Prepared, never as an externally submitted application.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import streamlit as st

from app.product_state import (
    activity_summary, candidate_facts, create_lane, delete_lane, fetch_jobs,
    job_filters, lanes, research_snapshot, save_candidate_fact,
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


def _job_card(row: dict[str, Any], *, key_prefix: str) -> None:
    job_id = int(row["id"])
    tone = pastel_for(job_id)
    tags = [row.get("remote_type"), row.get("employment_type"), row.get("salary_raw")]
    tags_html = "".join(f'<span class="tag">{esc(tag)}</span>' for tag in tags if str(tag or "").strip())
    with st.container(key=f"product_card_{key_prefix}_{job_id}"):
        st.markdown(
            f'''<div class="job-card"><div class="job-card-main" style="--card-bg:{tone}">
            <div class="job-top"><span>{esc(row.get("location_raw"), "Location unknown")}<br><span class="quiet">{esc(_relative_time(row.get("first_seen_at")))}</span></span>{score_ring(row.get("hunter_score"), tone)}</div>
            <div class="job-title">{esc(row.get("title"), "Untitled role")}</div>
            <div>{tags_html}</div><div class="job-company">{esc(row.get("company_name"), "Company not recorded")}</div>
            <div class="job-meta">{esc(row.get("source"), "Source unknown")}</div></div></div>''',
            unsafe_allow_html=True,
        )
        actions = st.columns(4, gap="small")
        with actions[0]:
            saved = bool(row.get("saved"))
            if st.button("Unsave" if saved else "Save", key=f"{key_prefix}_save_{job_id}", use_container_width=True):
                set_job_state(job_id, saved=not saved)
                st.rerun()
        with actions[1]:
            if st.button("Restore" if row.get("skipped") else "Pass", key=f"{key_prefix}_skip_{job_id}", use_container_width=True):
                set_job_state(job_id, skipped=not bool(row.get("skipped")))
                st.rerun()
        with actions[2]:
            if st.button("Details", key=f"{key_prefix}_details_{job_id}", use_container_width=True):
                st.session_state["product_job_detail"] = job_id
                st.rerun()
        with actions[3]:
            if st.button("Prepare", key=f"{key_prefix}_prepare_{job_id}", type="primary", use_container_width=True):
                # The existing guarded authority enforces targeting, authorization,
                # dedupe, and worker safety. This view never claims submission.
                try:
                    from app.stored_job_n8n_worker import start_stored_job_run
                    result = start_stored_job_run(job_id, actor="dashboard_product_ui")
                    if result.get("success"):
                        st.success(str(result.get("message") or "Preparation request processed."))
                    else:
                        st.warning(str(result.get("message") or "Preparation was not started."))
                except Exception:
                    st.error("The guarded preparation request could not be started. No submission was claimed.")


def _job_detail() -> None:
    job_id = st.session_state.get("product_job_detail")
    if not job_id:
        return
    # Query by ID independently; this preserves details for skipped/filtered jobs.
    from app.database import get_connection
    connection = get_connection()
    try:
        record = connection.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
    finally:
        connection.close()
    if not record:
        st.session_state.pop("product_job_detail", None)
        return
    row = dict(record)
    with st.expander(f"Job details · {row.get('title') or 'Untitled role'}", expanded=True):
        left, right = st.columns((1.3, 1))
        with left:
            st.subheader(str(row.get("company_name") or "Company not recorded"))
            st.caption(f"{str(row.get('location_raw') or 'Location unknown')} · {str(row.get('source') or 'Source unknown')}")
            st.markdown("#### Role summary")
            st.write(row.get("description_raw") or "A full job description is not stored for this role.")
        with right:
            st.metric("Hunter match", f"{float(row['hunter_score']):.0f}%" if row.get("hunter_score") is not None else "Not available")
            st.markdown(
                f'''<div class="evidence-list">
                    <div class="evidence-item"><b>Work authorization</b>{esc(row.get("work_authorization"))}</div>
                    <div class="evidence-item"><b>Employment</b>{esc(row.get("employment_type"))}</div>
                    <div class="evidence-item"><b>Target track</b>{esc(row.get("target_track"))}</div>
                    <div class="evidence-item"><b>Compensation</b>{esc(row.get("salary_raw"))}</div>
                </div>''',
                unsafe_allow_html=True,
            )
            if row.get("hard_rejection_reason"):
                st.warning(f"Eligibility evidence: {row['hard_rejection_reason']}")
            if safe_link(row.get("apply_url")):
                st.link_button("Open application page", safe_link(row["apply_url"]), type="primary", use_container_width=True)
        with st.expander("Advanced decision evidence", expanded=False):
            st.caption("These are stored evidence fields, not a claim that missing information is known.")
            st.dataframe([{
                "Source": row.get("source"), "Remote type": row.get("remote_type"),
                "Date posted": row.get("date_posted"), "Target track": row.get("target_track"),
                "Hard rejection reason": row.get("hard_rejection_reason"),
            }], hide_index=True, use_container_width=True)
        with st.expander("Raw machine evidence", expanded=False):
            raw = row.get("detail_extraction_json")
            if raw:
                try:
                    st.json(json.loads(str(raw)))
                except (TypeError, ValueError, json.JSONDecodeError):
                    st.code(str(raw), language="text")
            else:
                st.caption("No raw extraction evidence is stored for this role.")
        if st.button("Close details", key="product_close_details"):
            st.session_state.pop("product_job_detail", None)
            st.rerun()


def _search_filters(namespace: str) -> dict[str, Any]:
    values = job_filters()
    defaults = st.session_state.setdefault(f"{namespace}_filters", {"query": "", "exclude": "", "location": "", "source": "", "workplace": "", "employment_type": "", "minimum_score": 0.0, "saved_only": False, "result_set": "all", "search_scope": "title_description"})
    with st.container(key=f"product_{namespace}_search"):
        with st.form(f"product_{namespace}_search_form"):
            search_left, search_right = st.columns((1, 3.8), gap="small")
            with search_left:
                scope = st.selectbox("Search scope", ["title_description", "title_company"], index=["title_description", "title_company"].index(defaults.get("search_scope", "title_description")), format_func=lambda value: {"title_description": "Title + description", "title_company": "Title + company"}[value], key=f"{namespace}_scope", label_visibility="collapsed")
            with search_right:
                query = st.text_input("Search jobs", value=defaults["query"], placeholder="Search by title, company, or keyword…", label_visibility="collapsed")
            exclude = st.text_input("Exclude terms", value=defaults["exclude"], placeholder="Exclude jobs mentioning…", key=f"{namespace}_exclude", label_visibility="collapsed")
            controls = st.columns((1.15, 1, 1, 1.1, .9, .8))
            with controls[0]: location = st.selectbox("Location", [""] + values["locations"], index=([""] + values["locations"]).index(defaults["location"]) if defaults["location"] in values["locations"] else 0, format_func=lambda x: x or "All locations")
            with controls[1]: workplace = st.selectbox("Workplace", [""] + values["remote"], index=([""] + values["remote"]).index(defaults["workplace"]) if defaults["workplace"] in values["remote"] else 0, format_func=lambda x: x or "Any workplace")
            with controls[2]: source = st.selectbox("Source", [""] + values["sources"], index=([""] + values["sources"]).index(defaults["source"]) if defaults["source"] in values["sources"] else 0, format_func=lambda x: x or "All sources")
            with controls[3]: employment = st.selectbox("Employment", [""] + values["employment"], index=([""] + values["employment"]).index(defaults["employment_type"]) if defaults["employment_type"] in values["employment"] else 0, format_func=lambda x: x or "Any employment")
            with controls[4]: minimum = st.selectbox("Match score", [0.0, 50.0, 60.0, 70.0, 80.0], index=[0.0, 50.0, 60.0, 70.0, 80.0].index(float(defaults["minimum_score"])) if float(defaults["minimum_score"]) in [0.0, 50.0, 60.0, 70.0, 80.0] else 0, format_func=lambda x: "Any score" if x == 0 else f"{x:.0f}%+")
            with controls[5]: result_set = st.selectbox("Result set", ["all", "saved", "passed"], index=["all", "saved", "passed"].index(str(defaults.get("result_set") or "all")), format_func=lambda x: {"all": "All jobs", "saved": "Saved", "passed": "Passed"}[x])
            submitted = st.form_submit_button("Search jobs", type="primary", use_container_width=True)
    if submitted:
        defaults.update({"query": query, "exclude": exclude, "location": location, "source": source, "workplace": workplace, "employment_type": employment, "minimum_score": minimum, "saved_only": result_set == "saved", "result_set": result_set, "search_scope": scope})
        if namespace == "jobs":
            st.session_state["product_jobs_page"] = 1
    return defaults


def dashboard() -> None:
    page_intro("MUNSHI APPLY", "The right roles. The evidence to act.", "Search current opportunities, inspect why they match, and prepare application packages through the guarded workflow.")
    policy = volume_policy()
    activity = activity_summary()
    label = {"unlimited": "Unlimited", "custom_limit": f"Custom target: {policy.get('daily_limit')}", "paused": "Paused", "pause_after_batch": "Pausing after current batch"}[policy["mode"]]
    filters = _search_filters("dashboard")
    with st.container(key="product_dashboard_metrics"):
        summary = st.columns(5, gap="small")
        summary[0].metric("Prepared today", activity["prepared_today"])
        summary[1].metric("Submitted today", activity["submitted_today"], help="Only external submission evidence is counted.")
        summary[2].metric("Needs you", activity["needs_you"])
        summary[3].metric("In progress", activity["in_progress"])
        summary[4].metric("Automation", label, help="Canonical safety and provider controls remain authoritative.")
    jobs, count = fetch_jobs(**filters, page_size=4)
    st.markdown(f'<div class="section-row"><div><h2>Top matches</h2><span class="quiet">{count:,} opportunities · ranked by canonical Hunter score</span></div><a class="product-nav-link" href="?view=jobs">Browse all jobs →</a></div>', unsafe_allow_html=True)
    if jobs:
        with st.container(key="product_dashboard_grid"):
            columns = st.columns(min(4, len(jobs)), gap="medium")
            for index, row in enumerate(jobs):
                with columns[index % len(columns)]: _job_card(row, key_prefix="dashboard")
    else:
        st.markdown('<div class="empty-product">No jobs match the current filters. Change a filter or add a job with its complete description.</div>', unsafe_allow_html=True)
    tracker = tracker_rows()
    st.markdown('<div class="section-row"><div><h2>Application activity</h2><span class="quiet">Packages and dispatch evidence — never assumed submissions</span></div><a class="product-nav-link" href="?view=tracker">Open tracker →</a></div>', unsafe_allow_html=True)
    if tracker:
        st.dataframe([{ "Company": x["company_name"], "Role": x["title"], "Status": x["display_status"], "ATS score": x["final_ats_score"], "Updated": x.get("completed_at") or x.get("updated_at") } for x in tracker[:8]], hide_index=True, use_container_width=True)
    else:
        st.markdown('<div class="empty-product">No application-package or queue evidence yet. Preparing a job creates a traceable package record here.</div>', unsafe_allow_html=True)
    _job_detail()


def browse_jobs() -> None:
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
        counters[2].metric("Needs you", activity["needs_you"])
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
        rows.append(
            f'''<div class="pipeline-row">
                <div><strong>{esc(record.get("company_name"), "Company not recorded")}</strong><span>{esc(record.get("title"), "Untitled role")}</span></div>
                <div><span class="status-chip">{esc(record["display_status"])}</span><span class="pipeline-meta">{esc(updated)}</span></div>
                <div><strong>{esc(hunter)}</strong><span class="pipeline-meta">{esc(ats)}</span></div>
            </div>'''
        )
    st.markdown(f'<div class="table-shell">{"".join(rows)}</div>', unsafe_allow_html=True)


def tracker() -> None:
    page_intro("TRACKER", "Your application workspace", "Track prepared packages, proven submissions, items that need you, and synchronized read-only Gmail evidence.")
    tab = st.radio(
        "Tracker view", ["Pipeline", "Inbox"], horizontal=True,
        label_visibility="collapsed", key="product_tracker_tab",
        on_change=_sync_subroute,
        args=("tab", "product_tracker_tab", {"Pipeline": "pipeline", "Inbox": "inbox"}),
    )
    if tab == "Inbox":
        _inbox(); return
    records = tracker_rows(limit=75)
    statuses = ["All", "Prepared", "In progress", "Needs you", "Submitted", "Failed", "Skipped", "Other"]
    with st.container(key="product_tracker_filters"):
        filters, search = st.columns((2.4, 1.6))
    with filters:
        selected = st.radio("Pipeline status", statuses, horizontal=True, label_visibility="collapsed")
    with search:
        query = st.text_input("Search pipeline", placeholder="Search company or role", label_visibility="collapsed")
    needle = query.strip().casefold()
    visible = [record for record in records if (selected == "All" or record["display_status"] == selected) and (not needle or needle in f"{record.get('company_name') or ''} {record.get('title') or ''}".casefold())]
    if not visible:
        st.markdown('<div class="empty-product"><h3>No pipeline items match this filter.</h3><p>When the guarded workflow creates a package or queue record, it will appear here with its actual state.</p></div>', unsafe_allow_html=True); return
    _pipeline_list(visible)
    choices = {f"{index + 1}. {record.get('company_name') or 'Company not recorded'} — {record.get('title') or 'Untitled role'}": record for index, record in enumerate(visible)}
    chosen = st.selectbox("Inspect pipeline evidence", list(choices), key="product_tracker_evidence")
    record = choices[chosen]
    with st.expander("Package and dispatch evidence", expanded=True):
        facts = st.columns(3)
        facts[0].metric("Lifecycle", record["display_status"])
        facts[1].metric("Hunter match", f"{float(record['hunter_score']):.0f}%" if record.get("hunter_score") is not None else "Not available")
        facts[2].metric("ATS score", record.get("final_ats_score") if record.get("final_ats_score") is not None else "Not available")
        st.caption(f"Queue state: {record.get('queue_status') or 'Not recorded'} · Result state: {record.get('n8n_status') or 'Not recorded'}")
        artifacts = st.columns(3)
        with artifacts[0]:
            if safe_link(record.get("resume_pdf_url")):
                st.link_button("Open resume", safe_link(record["resume_pdf_url"]), use_container_width=True)
        with artifacts[1]:
            if safe_link(record.get("cover_letter_doc_url")):
                st.link_button("Open cover letter", safe_link(record["cover_letter_doc_url"]), use_container_width=True)
        with artifacts[2]:
            if safe_link(record.get("apply_url")):
                st.link_button("Open application", safe_link(record["apply_url"]), use_container_width=True)
        if not any(safe_link(record.get(field)) for field in ("resume_pdf_url", "cover_letter_doc_url", "apply_url")):
            st.caption("No external artifact or application link is recorded for this item.")


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
    page_intro("PROFILE", "Your candidate workspace", "Keep factual application answers organized and open only the resume and cover-letter artifacts the workflow has actually produced.")
    tab = st.radio(
        "Profile section", ["Resume", "Cover letters", "Profile details"], horizontal=True,
        label_visibility="collapsed", key="product_profile_tab",
        on_change=_sync_subroute,
        args=("tab", "product_profile_tab", {"Resume": "resume", "Cover letters": "cover-letter", "Profile details": "details"}),
    )
    if tab == "Profile details":
        facts = candidate_facts()
        existing = {str(fact["fact_key"]): fact for fact in facts}
        st.markdown(f'<div class="product-callout"><div><strong>{len(facts)} saved profile facts</strong><span>Every fact is candidate-provided and editable.</span></div><span class="status-chip">Sensitive facts are never inferred</span></div>', unsafe_allow_html=True)
        selected_label = st.selectbox("Edit a saved fact", ["Add a new fact", *existing], key="product_profile_fact_select")
        selected = existing.get(selected_label)
        selected_value = ""
        if selected:
            try:
                selected_value = str(json.loads(str(selected.get("value_json") or '""')))
            except (TypeError, ValueError, json.JSONDecodeError):
                selected_value = ""
        with st.form("candidate_fact_form"):
            key = st.text_input("Fact label", value=selected_label if selected else "", placeholder="Preferred roles")
            value = st.text_area("Value", value=selected_value, placeholder="HR operations, people analytics")
            if st.form_submit_button("Save profile fact", type="primary"):
                try:
                    save_candidate_fact(key, value)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success("Candidate fact saved with source ‘Candidate’."); st.rerun()
        if facts:
            st.caption("Only add information you intend MUNSHI to use. Sensitive or voluntary facts are never inferred.")
            st.dataframe([{ "Fact": x["fact_key"], "Value": json.loads(x["value_json"]), "Source": x["source_label"], "Updated": x["updated_at"] } for x in facts], hide_index=True, use_container_width=True)
        else: st.info("No profile facts stored yet.")
        return
    records = tracker_rows()
    field = "cover_letter_doc_url" if tab == "Cover letters" else "resume_pdf_url"
    label = "Cover letter" if tab == "Cover letters" else "Resume"
    artifacts = [x for x in records if safe_link(x.get(field))]
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
        st.markdown(f'<div class="empty-product"><h3>No {esc(label.lower())} artifacts yet</h3><p>A real generated artifact will appear here after the guarded workflow records its URL. MUNSHI does not create a placeholder preview.</p></div>', unsafe_allow_html=True)


def research() -> None:
    page_intro("INTELLIGENCE", "Research", "See why roles match, where explicit blockers appear, and which searches and sources are producing useful evidence.")
    snapshot = research_snapshot()
    metrics = snapshot["headline"]
    a, b, c, d = st.columns(4)
    a.metric("Stored opportunities", int(metrics["jobs"] or 0))
    b.metric("Average Hunter score", metrics["average_score"] if metrics["average_score"] is not None else "Not available")
    c.metric("Explicitly blocked", int(metrics["blocked"] or 0))
    d.metric("ATS-scored packages", snapshot["ats"]["scored_packages"] or 0)
    st.markdown('<div class="section-row"><div><h2>Match intelligence</h2><span class="quiet">Scores and blockers are shown together; a high score does not override eligibility evidence.</span></div></div>', unsafe_allow_html=True)
    if snapshot["top_matches"]:
        labels = {
            int(item["id"]): f"{item.get('company_name') or 'Company not recorded'} — {item.get('title') or 'Untitled role'}"
            for item in snapshot["top_matches"]
        }
        selected_id = st.selectbox("Inspect a scored opportunity", list(labels), format_func=labels.get, key="product_research_match")
        match = next(item for item in snapshot["top_matches"] if int(item["id"]) == selected_id)
        tone = pastel_for(match["id"])
        st.markdown(
            f'''<div class="intelligence-card" style="background:{tone}">
                <div class="page-kicker-product">MATCH + ELIGIBILITY EVIDENCE</div>
                <div class="evidence-list">
                    <div class="evidence-item"><b>Hunter match</b>{esc(match.get("hunter_score"))}%</div>
                    <div class="evidence-item"><b>Target track</b>{esc(match.get("target_track"))}</div>
                    <div class="evidence-item"><b>Source</b>{esc(match.get("source"))}</div>
                    <div class="evidence-item"><b>Location</b>{esc(match.get("location_raw"))}</div>
                    <div class="evidence-item"><b>Workplace</b>{esc(match.get("remote_type"))}</div>
                    <div class="evidence-item"><b>Work authorization</b>{esc(match.get("work_authorization"))}</div>
                    <div class="evidence-item"><b>Employment</b>{esc(match.get("employment_type"))}</div>
                    <div class="evidence-item"><b>Eligibility blocker</b>{esc(match.get("hard_rejection_reason"), "None explicitly recorded")}</div>
                </div>
            </div>''',
            unsafe_allow_html=True,
        )
    else:
        st.markdown('<div class="empty-product"><h3>No scored matches yet</h3><p>Match reasoning appears after canonical job records contain a Hunter score.</p></div>', unsafe_allow_html=True)
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("### Source quality")
        if snapshot["source_quality"]:
            st.dataframe([{ "Source": item["source"], "Opportunities": item["opportunities"], "Average match": item["average_match"], "No explicit blocker": item["eligible_records"] } for item in snapshot["source_quality"]], hide_index=True, use_container_width=True)
        else: st.info("No stored source evidence is available yet.")
        st.markdown("### Recent discovery trend")
        if snapshot["trends"]:
            st.dataframe([{ "Date": item["date"], "Opportunities": item["opportunities"], "Average match": item["average_match"] } for item in snapshot["trends"]], hide_index=True, use_container_width=True)
        else: st.info("No dated discovery evidence is available yet.")
    with right:
        st.markdown("### Eligibility evidence")
        if snapshot["blockers"]:
            st.dataframe([{ "Recorded blocker": item["reason"], "Opportunities": item["count"] } for item in snapshot["blockers"]], hide_index=True, use_container_width=True)
        else: st.info("No explicit rejection evidence is recorded.")
        st.markdown("### Work authorization records")
        if snapshot["authorization"]:
            st.dataframe([{ "Recorded status": item["status"], "Opportunities": item["count"] } for item in snapshot["authorization"]], hide_index=True, use_container_width=True)
        else: st.info("No work-authorization records are available.")
    st.markdown("### Query performance")
    if snapshot["query_performance"]:
        st.dataframe([{ "Query": x["query_name"], "Runs": x["runs"], "Raw records": x["raw_records"], "Eligible records": x["eligible_records"], "Last run": x["last_run"] } for x in snapshot["query_performance"]], hide_index=True, use_container_width=True)
    else: st.info("No query-run evidence is available yet.")
    st.markdown("### Source and provider health")
    if snapshot["health"]: st.dataframe([{ "Source": x["source_name"], "Health": x["health_status"], "Last success": x["last_success_at"], "Last run yield": x["jobs_found_last_run"] } for x in snapshot["health"]], hide_index=True, use_container_width=True)
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
            st.markdown('<a class="product-nav-link" href="?view=auto-prepare">Open Auto Prepare →</a>', unsafe_allow_html=True)
        elif section == "Profile & defaults":
            st.markdown("### Candidate-provided facts")
            facts = candidate_facts()
            st.write("Saved facts are candidate-provided structured fields, not opaque model memory. Manage them in Profile → Profile details.")
            st.dataframe([{ "Fact": x["fact_key"], "Source": x["source_label"], "Updated": x["updated_at"] } for x in facts], hide_index=True, use_container_width=True) if facts else st.info("No facts stored.")
            st.markdown('<a class="product-nav-link" href="?view=profile&amp;tab=details">Open profile details →</a>', unsafe_allow_html=True)
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
    from app import operations_dashboard as legacy
    page = st.selectbox("Advanced page", ["System / Diagnostics", "Source Health", "Adapter Coverage", "Targeting", "Query Performance", "Queue / Actions", "Storage", "Backups", "Credentials"])
    renderers = {"System / Diagnostics": legacy._system_diagnostics, "Source Health": legacy._source_health, "Adapter Coverage": legacy._adapter_coverage, "Targeting": legacy._targeting, "Query Performance": legacy._query_performance, "Queue / Actions": legacy._queue_actions, "Storage": legacy._storage, "Backups": legacy._backups, "Credentials": legacy._credentials}
    renderers[page]()
