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
    candidate_facts, create_lane, fetch_jobs, job_filters, lanes, save_candidate_fact,
    save_volume_policy, set_job_state, tracker_rows, volume_policy,
)
from app.product_ui import esc, page_intro, pastel_for, safe_link, score_ring


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


def _job_card(row: dict[str, Any], *, key_prefix: str, compact: bool = False) -> None:
    job_id = int(row["id"])
    tone = pastel_for(job_id)
    tags = [row.get("remote_type"), row.get("employment_type"), row.get("salary_raw")]
    tags_html = "".join(f'<span class="tag">{esc(tag)}</span>' for tag in tags if str(tag or "").strip())
    st.markdown(
        f'''<div class="job-card"><div class="job-card-main" style="--card-bg:{tone}">
        <div class="job-top"><span>{esc(row.get("location_raw"), "Location unknown")}<br><span class="quiet">{esc(_relative_time(row.get("first_seen_at")))}</span></span>{score_ring(row.get("hunter_score"), tone)}</div>
        <div class="job-title">{esc(row.get("title"), "Untitled role")}</div>
        <div>{tags_html}</div><div class="job-company">{esc(row.get("company_name"), "Company not recorded")}</div>
        <div class="job-meta">{esc(row.get("source"), "Source unknown")}</div></div><div class="job-card-foot"></div></div>''',
        unsafe_allow_html=True,
    )
    if compact:
        actions = st.columns((.9, 1.05, 1.1, .85))
    else:
        actions = st.columns((.8, .95, 1.18, 1.1, .85))
    with actions[0]:
        saved = bool(row.get("saved"))
        if st.button("Saved" if saved else "Save", key=f"{key_prefix}_save_{job_id}", use_container_width=True):
            set_job_state(job_id, saved=not saved)
            st.rerun()
    with actions[1]:
        if st.button("Details", key=f"{key_prefix}_details_{job_id}", use_container_width=True):
            st.session_state["product_job_detail"] = job_id
            st.rerun()
    if not compact:
        with actions[2]:
            if safe_link(row.get("apply_url")):
                st.link_button("Apply externally", safe_link(row["apply_url"]), use_container_width=True)
            else:
                st.button("Apply link unavailable", key=f"{key_prefix}_apply_{job_id}", disabled=True, use_container_width=True)
    with actions[-2]:
        if st.button("Prepare", key=f"{key_prefix}_prepare_{job_id}", type="primary", use_container_width=True):
            # This is the existing guarded path. It enforces canonical targeting,
            # authorization, dedupe, and n8n worker safety before it can queue work.
            try:
                from app.stored_job_n8n_worker import start_stored_job_run
                result = start_stored_job_run(job_id, actor="dashboard_product_ui")
                if result.get("success"):
                    st.success(str(result.get("message") or "Preparation request processed."))
                else:
                    st.warning(str(result.get("message") or "Preparation was not started."))
            except Exception:
                st.error("The guarded preparation request could not be started. No submission was claimed.")
    with actions[-1]:
        if st.button("Restore" if row.get("skipped") else "Pass", key=f"{key_prefix}_skip_{job_id}", use_container_width=True):
            set_job_state(job_id, skipped=not bool(row.get("skipped")))
            st.rerun()


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
            st.write(row.get("description_raw") or "A full job description is not stored for this role.")
        with right:
            st.metric("Hunter match", f"{float(row['hunter_score']):.0f}%" if row.get("hunter_score") is not None else "Not available")
            st.caption(f"Work authorization: {row.get('work_authorization') or 'Not available'}")
            st.caption(f"Employment: {row.get('employment_type') or 'Not available'}")
            if safe_link(row.get("apply_url")):
                st.link_button("Open application page", safe_link(row["apply_url"]), type="primary", use_container_width=True)
        if st.button("Close details", key="product_close_details"):
            st.session_state.pop("product_job_detail", None)
            st.rerun()


def _search_filters(namespace: str) -> dict[str, Any]:
    values = job_filters()
    defaults = st.session_state.setdefault(f"{namespace}_filters", {"query": "", "exclude": "", "location": "", "source": "", "workplace": "", "employment_type": "", "minimum_score": 0.0, "saved_only": False})
    with st.form(f"{namespace}_search_form"):
        st.markdown('<div class="search-shell">', unsafe_allow_html=True)
        search_left, search_right = st.columns((.95, 3.5))
        with search_left:
            st.selectbox("Search scope", ["Title + description", "Title + company"], key=f"{namespace}_scope", label_visibility="collapsed")
        with search_right:
            query = st.text_input("Search jobs", value=defaults["query"], placeholder="Search by title, company, or keyword…", label_visibility="collapsed")
        st.markdown("</div>", unsafe_allow_html=True)
        exclude = st.text_input("Exclude", value=defaults["exclude"], placeholder="Hide jobs mentioning…", key=f"{namespace}_exclude", label_visibility="collapsed")
        controls = st.columns(5)
        with controls[0]: location = st.selectbox("Location", [""] + values["locations"], index=([""] + values["locations"]).index(defaults["location"]) if defaults["location"] in values["locations"] else 0, format_func=lambda x: x or "Location")
        with controls[1]: workplace = st.selectbox("Workplace", [""] + values["remote"], index=([""] + values["remote"]).index(defaults["workplace"]) if defaults["workplace"] in values["remote"] else 0, format_func=lambda x: x or "Workplace")
        with controls[2]: source = st.selectbox("Source", [""] + values["sources"], index=([""] + values["sources"]).index(defaults["source"]) if defaults["source"] in values["sources"] else 0, format_func=lambda x: x or "Source")
        with controls[3]: employment = st.selectbox("Employment", [""] + values["employment"], index=([""] + values["employment"]).index(defaults["employment_type"]) if defaults["employment_type"] in values["employment"] else 0, format_func=lambda x: x or "Employment type")
        with controls[4]: minimum = st.selectbox("Match score", [0.0, 50.0, 60.0, 70.0, 80.0], index=[0.0, 50.0, 60.0, 70.0, 80.0].index(float(defaults["minimum_score"])) if float(defaults["minimum_score"]) in [0.0, 50.0, 60.0, 70.0, 80.0] else 0, format_func=lambda x: "Match score" if x == 0 else f"{x:.0f}%+")
        saved = st.checkbox("Saved only", value=bool(defaults["saved_only"]))
        submitted = st.form_submit_button("Update results", type="primary")
    if submitted:
        defaults.update({"query": query, "exclude": exclude, "location": location, "source": source, "workplace": workplace, "employment_type": employment, "minimum_score": minimum, "saved_only": saved})
    return defaults


def dashboard() -> None:
    page_intro("MUNSHI APPLY", "Your job search, with evidence.", "Browse transparent matches, prepare application packages deliberately, and keep every meaningful state tied to a source of truth.")
    policy = volume_policy()
    label = {"unlimited": "Unlimited", "custom_limit": f"Custom target: {policy.get('daily_limit')}", "paused": "Paused", "pause_after_batch": "Pausing after current batch"}[policy["mode"]]
    st.markdown(f'<div class="product-callout"><div><strong>Automation: {esc(label)}</strong><span>{"Review before action is enabled." if policy["review_first"] else "Actions still use canonical targeting and work-authorization gates."}</span></div></div>', unsafe_allow_html=True)
    filters = _search_filters("dashboard")
    jobs, count = fetch_jobs(**filters, page_size=4)
    st.markdown('<div class="section-row"><h2>Top matches</h2><span class="quiet">Ranked by canonical Hunter score</span></div>', unsafe_allow_html=True)
    if jobs:
        columns = st.columns(min(4, len(jobs)), gap="medium")
        for index, row in enumerate(jobs):
            with columns[index % len(columns)]: _job_card(row, key_prefix="dashboard", compact=True)
    else:
        st.markdown('<div class="empty-product">No jobs match the current filters. Change a filter or add a job with its complete description.</div>', unsafe_allow_html=True)
    tracker = tracker_rows()
    st.markdown('<div class="section-row"><h2>Application activity</h2><span class="quiet">Packages and dispatch evidence — not assumed submissions</span></div>', unsafe_allow_html=True)
    if tracker:
        st.dataframe([{ "Company": x["company_name"], "Role": x["title"], "Status": x["display_status"], "ATS score": x["final_ats_score"], "Updated": x.get("completed_at") or x.get("updated_at") } for x in tracker[:8]], hide_index=True, use_container_width=True)
    else:
        st.markdown('<div class="empty-product">No application-package or queue evidence yet. Preparing a job creates a traceable package record here.</div>', unsafe_allow_html=True)
    _job_detail()


def browse_jobs() -> None:
    page_intro("DISCOVER", "Browse jobs", "Search stored opportunities without losing the targeting, authorization, source, or score evidence behind each match.")
    filters = _search_filters("jobs")
    page = int(st.session_state.get("product_jobs_page", 1))
    jobs, count = fetch_jobs(**filters, page=page, page_size=16)
    header, add = st.columns((3, 1))
    with header: st.caption(f"{count:,} matching opportunities · Page {page}")
    with add:
        if st.button("+ Add your own", key="product_add_own", use_container_width=True): st.session_state["show_manual_job"] = True
    if st.session_state.get("show_manual_job"):
        _manual_add()
    if jobs:
        for start in range(0, len(jobs), 4):
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
    from app.manual_input import missing_required_fields, parse_manual_job_text
    with st.expander("Add your own job", expanded=True):
        st.caption("Paste a URL and the full job description. MUNSHI will identify missing structured fields; it will not fabricate them.")
        with st.form("product_manual_analyze"):
            url = st.text_input("Job URL", placeholder="https://careers.example.com/jobs/…")
            description = st.text_area("Full job description", height=180)
            analyze = st.form_submit_button("Analyze job", type="primary")
        if analyze:
            raw = f"Application: {url}\nJob Description: {description}"
            parsed = parse_manual_job_text(raw)
            missing = missing_required_fields(parsed)
            st.session_state["manual_analysis"] = {"missing": missing, "fields": parsed.get("fields", {})}
        result = st.session_state.get("manual_analysis")
        if result:
            if result["missing"]:
                st.warning("Missing required fields: " + ", ".join(result["missing"]) + ". Add labeled title, company, location, URL, and full job description to continue through the existing canonical manual path.")
            else:
                st.success("The pasted job contains the required fields. Use the existing monitored manual-input workflow to persist and prepare it; this screen does not start background work automatically.")


def auto_prepare() -> None:
    page_intro("AUTOMATION", "Prepare automatically, stay in control.", "Lanes describe saved target policies. They begin disabled, and every candidate still passes canonical targeting, work-authorization, dedupe, and ATS gates.")
    policy = volume_policy()
    left, right = st.columns((1.7, 1), gap="large")
    with left:
        st.markdown("### How it works")
        st.markdown("<div class='muted-panel'><b>1. Find eligible matches</b><br>Only canonical targeting and authorization evidence can qualify a role.<br><br><b>2. Prepare a package</b><br>Preparation is not an external ATS submission.<br><br><b>3. Review or act deliberately</b><br>Nothing starts merely because this page was opened.</div>", unsafe_allow_html=True)
        st.markdown("### Target lanes")
        current_lanes = lanes()
        if current_lanes:
            for lane in current_lanes:
                st.markdown(f"<div class='lane-card'><strong>{esc(lane['name'])}</strong><br><span class='quiet'>{'Enabled' if lane['enabled'] else 'Disabled by default'} · {esc(lane['volume_mode']).replace('_',' ').title()} · minimum score {lane.get('min_score') or 'not set'}</span></div>", unsafe_allow_html=True)
        else:
            st.info("No saved lanes yet. Create one below; it will remain disabled until an explicit future enable action through canonical automation policy.")
        with st.expander("Add a lane", expanded=False):
            with st.form("product_lane_form"):
                name = st.text_input("Lane name", placeholder="HR operations · Northeast")
                keywords = st.text_input("Role keywords", placeholder="HR coordinator, people operations")
                minimum = st.number_input("Minimum Hunter score", min_value=0.0, max_value=100.0, value=70.0)
                lane_mode = st.selectbox("Lane volume", ["unlimited", "custom_limit", "paused"], format_func=lambda v: {"unlimited":"Unlimited", "custom_limit":"Custom daily limit", "paused":"Paused"}[v])
                lane_limit = st.number_input("Daily limit", min_value=1, value=10, disabled=lane_mode != "custom_limit")
                if st.form_submit_button("Save disabled lane", type="primary"):
                    create_lane(name, {"keywords": keywords}, minimum, lane_mode, lane_limit)
                    st.success("Lane saved disabled. Rendering this page has not queued any work."); st.rerun()
    with right:
        st.markdown("<div class='split-panel'><div class='page-kicker-product'>APPLICATION VOLUME</div><h3>User-owned controls</h3></div>", unsafe_allow_html=True)
        with st.form("product_volume_form"):
            mode = st.radio("Mode", ["unlimited", "custom_limit", "pause_after_batch", "paused"], index=["unlimited", "custom_limit", "pause_after_batch", "paused"].index(policy["mode"]), format_func=lambda x: {"unlimited":"Unlimited", "custom_limit":"Custom daily limit", "pause_after_batch":"Pause after current batch", "paused":"Paused"}[x])
            limit = st.number_input("Custom daily limit", min_value=1, value=max(1, int(policy.get("daily_limit") or 25)), disabled=mode != "custom_limit")
            review = st.checkbox("Review before action", value=policy["review_first"])
            if st.form_submit_button("Save volume preference", type="primary"):
                save_volume_policy(mode, limit, review); st.success("Preference saved. Provider rate limits and canonical safety gates remain separate.")
        st.caption("Unlimited is a valid MUNSHI mode. It is not a promise to bypass provider throttles, targeting, authorization, or risk controls.")


def tracker() -> None:
    page_intro("TRACKER", "Pipeline and inbox", "Follow actual package, queue, and submission evidence. Gmail stays disconnected until OAuth and a server-side vault key are provisioned.")
    tab = st.radio("Tracker view", ["Pipeline", "Inbox"], horizontal=True, label_visibility="collapsed", key="product_tracker_tab")
    if tab == "Inbox":
        _inbox(); return
    records = tracker_rows()
    statuses = ["All", "Prepared", "In progress", "Needs you", "Submitted", "Failed", "Skipped", "Other"]
    selected = st.radio("Status", statuses, horizontal=True, label_visibility="collapsed")
    visible = [record for record in records if selected == "All" or record["display_status"] == selected]
    if not visible:
        st.markdown('<div class="empty-product"><h3>No pipeline items match this filter.</h3><p>When the guarded workflow creates a package or queue record, it will appear here with its actual state.</p></div>', unsafe_allow_html=True); return
    st.markdown('<div class="table-shell">', unsafe_allow_html=True)
    st.dataframe([{ "Company": x["company_name"], "Role": x["title"], "Status": x["display_status"], "Hunter match": x.get("hunter_score"), "ATS score": x.get("final_ats_score"), "Prepared / updated": x.get("completed_at") or x.get("updated_at") or x.get("queued_at") } for x in visible], hide_index=True, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _inbox() -> None:
    st.markdown('<div class="split-panel"><h3>Gmail is not configured</h3><p class="quiet">The inbox architecture is present but does not connect, sync, send, or invent messages until the owner provides OAuth client credentials and <code>MUNSHI_VAULT_KEY</code> server-side.</p></div>', unsafe_allow_html=True)
    from app.gmail_integration import gmail_configuration_status
    status = gmail_configuration_status()
    st.caption(f"OAuth client: {status['oauth_client']} · Vault: {status['vault']} · Scope: Gmail read-only")
    if status["ready"]:
        st.info("OAuth client provisioning is available. Connection initiation is intentionally a separate explicit authorization action.")
    else:
        st.button("Connect Gmail", disabled=True, help="Provision OAuth client credentials and MUNSHI_VAULT_KEY on the server first.")


def profile() -> None:
    page_intro("PROFILE", "Candidate profile and artifacts", "Structured facts remain visible and editable. Resume and cover-letter records are linked to their generated evidence instead of a free-form document editor.")
    tab = st.radio("Profile section", ["Resume", "Cover letters", "Profile details"], horizontal=True, label_visibility="collapsed", key="product_profile_tab")
    if tab == "Profile details":
        with st.form("candidate_fact_form"):
            key = st.text_input("Fact label", placeholder="Preferred roles")
            value = st.text_area("Value", placeholder="HR operations, people analytics")
            if st.form_submit_button("Save profile fact", type="primary"):
                save_candidate_fact(key, value); st.success("Candidate fact saved with source ‘Candidate’."); st.rerun()
        facts = candidate_facts()
        if facts:
            st.dataframe([{ "Fact": x["fact_key"], "Value": json.loads(x["value_json"]), "Source": x["source_label"], "Updated": x["updated_at"] } for x in facts], hide_index=True, use_container_width=True)
        else: st.info("No profile facts stored yet.")
        return
    records = tracker_rows()
    field = "cover_letter_doc_url" if tab == "Cover letters" else "resume_pdf_url"
    label = "Cover letter" if tab == "Cover letters" else "Resume"
    artifacts = [x for x in records if safe_link(x.get(field))]
    if artifacts:
        for artifact in artifacts:
            st.markdown(f"<div class='surface'><b>{esc(artifact['company_name'])} · {esc(artifact['title'])}</b><br><span class='quiet'>ATS score: {esc(artifact.get('final_ats_score'))} · Prepared: {esc(artifact.get('completed_at') or artifact.get('sent_at'))}</span></div>", unsafe_allow_html=True)
            st.link_button(f"Open {label}", safe_link(artifact[field]))
    else:
        st.markdown(f"<div class='artifact-page'><h2>{label} library</h2><p class='quiet'>No generated {label.lower()} artifact is available yet.</p><div class='artifact-line'></div><div class='artifact-line short'></div><div class='artifact-line'></div><div class='artifact-line short'></div></div>", unsafe_allow_html=True)


def research() -> None:
    page_intro("INTELLIGENCE", "Research", "MUNSHI’s operational evidence is summarized for decisions; raw diagnostics remain under Settings → System / Advanced.")
    from app.database import get_connection
    connection = get_connection()
    try:
        metrics = connection.execute("SELECT COUNT(*) AS jobs, ROUND(AVG(hunter_score),1) AS average_score, SUM(CASE WHEN hard_rejection_reason IS NOT NULL AND trim(hard_rejection_reason) != '' THEN 1 ELSE 0 END) AS blocked FROM jobs").fetchone()
        sources = [dict(row) for row in connection.execute("SELECT source_name,health_status,last_success_at,jobs_found_last_run FROM source_health ORDER BY source_name LIMIT 30").fetchall()]
    finally: connection.close()
    a,b,c = st.columns(3)
    a.metric("Stored opportunities", int(metrics["jobs"] or 0)); b.metric("Average Hunter score", f"{metrics['average_score'] or 'Not available'}"); c.metric("Explicitly blocked", int(metrics["blocked"] or 0))
    st.markdown("### Source and provider health")
    if sources: st.dataframe([{ "Source": x["source_name"], "Health": x["health_status"], "Last success": x["last_success_at"], "Last run yield": x["jobs_found_last_run"] } for x in sources], hide_index=True, use_container_width=True)
    else: st.info("No source-health records are available.")


def settings() -> None:
    page_intro("PREFERENCES", "Settings", "Change personal preferences explicitly. System and provider operations are retained under Advanced, not removed.")
    options = ["Application preferences", "What MUNSHI remembers", "Targeting", "Job boards", "ATS credentials", "Email integrations", "Usage & limits", "Appearance", "Account", "System / Advanced"]
    left, content, art = st.columns((1.05, 2.4, 1.45), gap="large")
    with left:
        section = st.radio("Settings section", options, label_visibility="collapsed", key="product_settings_section")
    with content:
        if section in {"Application preferences", "Usage & limits"}:
            policy = volume_policy(); st.markdown("### Application preferences")
            st.caption("Optimization modes are descriptive settings; they never authorize unsupported resume claims.")
            optimization = st.radio("Resume optimization", ["Conservative", "Balanced", "JD-Adaptive"], horizontal=True)
            st.caption(f"Current automation mode: {policy['mode'].replace('_',' ').title()} · use Auto Prepare to change the user-owned volume preference.")
        elif section == "What MUNSHI remembers":
            st.markdown("### Transparent remembered facts")
            facts = candidate_facts()
            st.write("Saved facts are candidate-provided structured fields, not opaque model memory.")
            st.dataframe([{ "Fact": x["fact_key"], "Source": x["source_label"], "Updated": x["updated_at"] } for x in facts], hide_index=True, use_container_width=True) if facts else st.info("No facts stored.")
        elif section == "Email integrations":
            st.markdown("### Gmail integration")
            _inbox()
        elif section == "ATS credentials":
            st.markdown("### ATS credentials")
            st.info("No Workday, iCIMS, or Oracle credential is stored by this product UI. Secret persistence requires the cross-platform encrypted vault and a server-provisioned key.")
        elif section == "System / Advanced":
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
