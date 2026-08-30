from __future__ import annotations

import html
import json
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import streamlit as st

from app.database import (
    DB_PATH,
    get_connection,
    get_setting,
    save_board_policy,
    save_setting,
    save_source_policy,
)
from app.dashboard_theme import premium_dashboard_css
from app.presentation_analytics import (
    decision_view_model,
    daily_history,
    explainable_evidence_model,
    humanize_machine_value,
    lifetime_metrics,
    n8n_execution_metrics,
    operational_summary_metrics,
    provider_intelligence,
    rejection_intelligence,
)
from app.runtime_recovery import RuntimeRecovery
from app.runtime_config import service_endpoint
from app.ui_time import (
    format_local,
    format_local_clock,
    format_local_short,
    local_date,
    local_day_bounds_utc,
    sqlite_utc,
    system_timezone,
    timezone_label,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
HEALTHY_STATES = {"healthy", "live_verified", "fixture_tested"}
DEGRADED_STATES = {"degraded", "cooldown", "stale", "needs_credentials"}
FAILED_STATES = {"failed", "error", "blocked"}


def _query(sql: str, parameters: Iterable[Any] = ()) -> pd.DataFrame:
    connection = get_connection()
    try:
        return pd.read_sql_query(sql, connection, params=tuple(parameters))
    finally:
        connection.close()


def _scalar(sql: str, parameters: Iterable[Any] = (), default: Any = 0) -> Any:
    connection = get_connection()
    try:
        row = connection.execute(sql, tuple(parameters)).fetchone()
        if row is None or row[0] is None:
            return default
        return row[0]
    finally:
        connection.close()


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _lines(value: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for line in value.splitlines():
        item = line.strip()
        key = item.casefold()
        if item and key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _format_bytes(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount /= 1024
        index += 1
    return f"{amount:,.1f} {units[index]}"


def _format_when(value: Any, *, empty: str = "Never run") -> str:
    return format_local(value, empty=empty)


def _localize_columns(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    localized = frame.copy()
    for column in columns:
        if column in localized.columns:
            localized[column] = localized[column].map(
                lambda value: format_local_short(value, empty="Not available")
            )
    return localized


def _time_caption() -> None:
    st.caption(f"Times shown in system local time · {timezone_label()}")


def _service_up(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


@st.cache_data(ttl=15, show_spinner=False)
def _runtime_recovery_snapshot() -> dict[str, Any]:
    """Read the same canonical health model used by the owner recovery command."""
    return RuntimeRecovery().status_snapshot()


def _status_pill(label: str, state: str) -> None:
    normalized = state.casefold()
    tone = "good" if normalized in HEALTHY_STATES else "warn"
    if normalized in FAILED_STATES:
        tone = "bad"
    st.markdown(
        f'<span class="status-pill {tone}">{label}: {state}</span>',
        unsafe_allow_html=True,
    )


def _select_dashboard_page(label: str) -> None:
    st.session_state["active_dashboard_page"] = label


def _metric_row(items: list[tuple[str, Any, str | None]]) -> None:
    if not items:
        return
    per_row = 3 if len(items) in {5, 6} else min(4, len(items))
    for offset in range(0, len(items), per_row):
        chunk = items[offset : offset + per_row]
        columns = st.columns(len(chunk))
        for column, (label, value, help_text) in zip(columns, chunk):
            column.metric(label, value, help=help_text)


def _empty(message: str) -> None:
    st.info(message, icon="ℹ️")


def _apply_styles() -> None:
    st.markdown(premium_dashboard_css(), unsafe_allow_html=True)


def _page_intro(kicker: str, title: str, copy: str) -> None:
    st.markdown(
        f"""
        <div class="page-intro">
          <div>
            <div class="page-kicker">{html.escape(kicker)}</div>
            <h2>{html.escape(title)}</h2>
            <div class="page-copy">{html.escape(copy)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _section_heading(title: str, copy: str) -> None:
    st.markdown(
        f'<div class="section-heading"><strong>{html.escape(title)}</strong>'
        f'<span>{html.escape(copy)}</span></div>',
        unsafe_allow_html=True,
    )


def _source_state(row: Any) -> str:
    enabled = bool(row.get("enabled") if hasattr(row, "get") else row["enabled"])
    health = str(row.get("health_status") or "not_tested").casefold()
    last_run = str(row.get("last_run_at") or "").strip()
    if not enabled and health in {"needs_credentials", "configuration_required"}:
        return "Setup required"
    if not enabled:
        return "Disabled"
    schedule = str(row.get("schedule_state") or "").casefold()
    if schedule == "running":
        return "Running"
    if health in FAILED_STATES or health in {"disabled_after_timeout"}:
        return "Failed"
    if health in DEGRADED_STATES or schedule == "failure_backoff":
        return "Degraded"
    if not last_run:
        return "Never run"
    return "Healthy"


def _header(*, compact: bool = False) -> None:
    orchestration = get_setting("orchestration", {}) or {}
    targeting = get_setting("targeting", {}) or {}
    eligibility = targeting.get("eligibility") if isinstance(targeting.get("eligibility"), dict) else {}
    mode = html.escape(str(targeting.get("mode") or "Unconfigured"))
    geography = html.escape(str(eligibility.get("label") or "Unconfigured geography"))
    maintenance = bool(orchestration.get("maintenance_mode", True))
    if compact:
        st.markdown(
            f"""
            <div class="masthead">
              <div class="masthead-brand">MUNSHI APPLY</div>
              <div class="masthead-context">Executive Intelligence · {mode} · {geography}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div class="hero">
          <div class="brand-row"><span class="monogram">M</span><div class="eyebrow">Executive Intelligence System</div></div>
          <h1>MUNSHI APPLY</h1>
          <div class="hero-copy">Autonomous Job Discovery &amp; Application Intelligence</div>
          <div class="hero-subcopy">Continuous multi-source discovery, canonical targeting, deduplication,
          automation orchestration, and application intelligence from one control plane.</div>
          <div class="hero-meta">
            <span class="hero-chip live">Control center online</span>
            <span class="hero-chip">{geography}</span>
            <span class="hero-chip">{mode}-focused targeting</span>
            <span class="hero-chip">Multi-source discovery</span>
            <span class="hero-chip">Canonical targeting</span>
            <span class="hero-chip">Automated scheduling</span>
            <span class="hero-chip">Telegram delivery</span>
            <span class="hero-chip">n8n orchestration</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
        )
    if maintenance:
        st.warning(
            "Controlled maintenance is active. Source orchestration is paused while acceptance checks run; "
            "dashboard, Telegram listener, FastAPI, and n8n change-control monitoring remain independent.",
            icon="🛡️",
        )


def _overview() -> None:
    _page_intro(
        "Executive brief",
        "MUNSHI Apply at a glance",
        "Lifetime scale, today's autonomous work, current runtime health, and the intelligence behind every decision.",
    )
    connection = get_connection()
    try:
        lifetime = lifetime_metrics(connection)
        history = daily_history(connection)
        rejections = rejection_intelligence(connection)
        providers = provider_intelligence(connection)
        operational_summaries = operational_summary_metrics(connection)
    finally:
        connection.close()
    source_summary = _query(
        """
        SELECT
          COUNT(*) configured,
          SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) enabled,
          SUM(CASE WHEN enabled=1 AND health_status='healthy' THEN 1 ELSE 0 END) healthy,
          SUM(CASE WHEN enabled=1 AND health_status IN ('degraded','failed','error','disabled_after_timeout') THEN 1 ELSE 0 END) attention,
          SUM(CASE WHEN enabled=1 AND schedule_state='running' THEN 1 ELSE 0 END) running,
          SUM(CASE WHEN enabled=1 AND schedule_state IN ('cooldown','deferred') THEN 1 ELSE 0 END) scheduled,
          SUM(CASE WHEN enabled=1 AND schedule_state='failure_backoff' THEN 1 ELSE 0 END) backoff,
          SUM(CASE WHEN enabled=0 THEN 1 ELSE 0 END) disabled,
          SUM(CASE WHEN enabled=0 AND health_status IN ('needs_credentials','configuration_required') THEN 1 ELSE 0 END) setup
        FROM source_runtime_truth_v1
        """
    ).iloc[0]
    local_start, local_end = local_day_bounds_utc()
    bounds = (sqlite_utc(local_start), sqlite_utc(local_end))
    today = _query(
        """
        SELECT COUNT(*) runs, COALESCE(SUM(request_count),0) requests,
               COALESCE(SUM(raw_count),0) raw, COALESCE(SUM(normalized_count),0) normalized,
               COALESCE(SUM(duplicate_count),0) duplicate,
               COALESCE(SUM(eligible_count),0) eligible,
               COALESCE(SUM(new_eligible_count),0) new_eligible,
               COALESCE(SUM(reject_role_count),0) reject_role,
               COALESCE(SUM(reject_location_count),0) reject_location,
               COALESCE(SUM(reject_hard_requirement_count),0) reject_hard,
               COALESCE(SUM(reject_company_count),0) reject_company,
               COALESCE(SUM(reject_other_targeting_count),0) reject_other,
               COALESCE(SUM(telegram_count),0) telegram,
               COALESCE(SUM(downstream_success_count),0) downstream,
               COALESCE(SUM(accounting_delta),0) accounting_delta
        FROM source_runs
        WHERE datetime(started_at)>=datetime(?) AND datetime(started_at)<datetime(?)
        """,
        bounds,
    ).iloc[0]
    jobs_today = _scalar(
        "SELECT COUNT(*) FROM jobs WHERE datetime(first_seen_at)>=datetime(?) AND datetime(first_seen_at)<datetime(?)",
        bounds,
    )
    integration = get_setting("integration_health", {}) or {}
    n8n_host, n8n_port = service_endpoint("n8n")
    n8n_up = _service_up(n8n_host, n8n_port)
    workflow_id = str((integration.get("n8n_read_only_snapshot") or {}).get("workflow_id") or "")
    n8n_history, _ = n8n_execution_metrics(workflow_id)
    orchestration = get_setting("orchestration", {}) or {}
    maintenance = bool(orchestration.get("maintenance_mode", True))
    overall = "Maintenance" if maintenance else ("Needs attention" if int(source_summary.get("attention") or 0) else "Operational")

    _section_heading("Lifetime intelligence", "Persisted evidence from each subsystem's earliest trustworthy record; telemetry horizons are labeled honestly.")
    _metric_row(
        [
            ("System active since", format_local(lifetime.get("first_job"), empty="Not available", include_zone=False, date_style="%b %-d, %Y"), "Earliest persisted job record."),
            ("Recorded source runs", f"{int(lifetime.get('runs') or 0):,}", "Canonical telemetry introduced " + format_local(lifetime.get("first_run"), empty="not yet", include_zone=False, date_style="%b %-d, %Y")),
            ("Opportunities scanned", f"{int(lifetime.get('scanned') or 0):,}", "Provider records measured by canonical source-run telemetry."),
            ("Jobs stored", f"{int(lifetime.get('jobs_stored') or 0):,}", "Current persisted job inventory."),
            ("Targeting decisions", f"{int(lifetime.get('decisions') or 0):,}", "Recorded canonical decisions."),
            ("Eligible opportunities (telemetry)", f"{int(lifetime.get('eligible') or 0):,}", "Canonical eligible count measured only since source-run telemetry began; this is not the lifetime stored-job horizon."),
            ("Telegram-delivered jobs (stored history)", f"{int(lifetime.get('jobs_delivered') or 0):,}", "Persisted delivered-job history. This horizon can begin earlier than canonical source-run telemetry, so it is not directly comparable to Eligible opportunities (telemetry)."),
            ("Canonical n8n executions", f"{int(n8n_history.get('executions') or 0):,}" if n8n_history.get("available") else "Not available", "Read-only execution history for the verified canonical workflow only."),
        ]
    )
    st.caption(
        "Evidence horizons · stored jobs since "
        f"{format_local(lifetime.get('first_job'), empty='not available', include_zone=False, date_style='%b %-d, %Y')} · "
        f"canonical source telemetry since {format_local(lifetime.get('first_run'), empty='not available', include_zone=False, date_style='%b %-d, %Y')} · "
        "Telegram-delivered jobs use persisted stored-job history and are not a subset of the newer source-telemetry Eligible count · "
        f"canonical n8n history since {format_local(n8n_history.get('first_execution'), empty='not available', include_zone=False, date_style='%b %-d, %Y')}."
    )

    new_jobs_sent_today = _scalar(
        """
        SELECT COUNT(DISTINCT claim.job_id)
        FROM telegram_delivery_claims claim
        JOIN jobs j ON j.id=claim.job_id
        WHERE claim.delivery_state='sent'
          AND datetime(COALESCE(claim.sent_at, claim.reserved_at))
              >= datetime('now','localtime','start of day','utc')
          AND datetime(j.added_at)
              >= datetime('now','localtime','start of day','utc')
        """
    )
    backlog_jobs_sent_today = max(int(today["telegram"] or 0) - int(new_jobs_sent_today or 0), 0)

    _section_heading(
        "Today",
        "Autonomous discovery since the Mac's local midnight. The Telegram card below counts only newly stored jobs so it is directly comparable with today's Eligible and New jobs stored totals.",
    )
    rejected_today = int(today["reject_role"] + today["reject_location"] + today["reject_hard"] + today["reject_company"] + today["reject_other"])
    _metric_row(
        [
            ("Source runs", f"{int(today['runs']):,}", None),
            ("Opportunities scanned", f"{int(today['raw']):,}", "Provider records evaluated today."),
            ("Normalized", f"{int(today['normalized']):,}", None),
            ("Duplicates", f"{int(today['duplicate']):,}", None),
            ("Rejected by targeting", f"{rejected_today:,}", None),
            ("Eligible", f"{int(today['eligible']):,}", None),
            ("New jobs stored", f"{int(jobs_today):,}", "Jobs first stored since local midnight. This is a new-storage count, not every job that can be delivered today."),
            ("New jobs sent to Telegram", f"{int(new_jobs_sent_today):,}", "Unique jobs first stored today that were successfully delivered to Telegram today. Previously stored backlog deliveries are shown separately below."),
        ]
    )
    st.caption(
        f"Telegram activity today · {int(today['telegram']):,} total unique job-card deliveries = "
        f"{int(new_jobs_sent_today):,} newly stored today + "
        f"{int(backlog_jobs_sent_today):,} previously stored backlog jobs."
    )
    if int(today["normalized"]) == 0:
        _empty("Waiting for the first canonical source run today." if not maintenance else "Source runs are paused by controlled maintenance.")
    elif int(today["accounting_delta"]) == 0:
        st.success("Discovery pipeline accounting is balanced. Telegram delivery is shown separately as new-today delivery plus backlog activity.")
    else:
        st.error(f"Pipeline accounting requires review: delta {int(today['accounting_delta']):,}.")

    targeting = dict(get_setting("targeting", {}) or {})
    eligibility = dict(targeting.get("eligibility") or {})
    last_adapter = _query(
        "SELECT source_name,completed_at,run_status FROM source_runs ORDER BY datetime(COALESCE(completed_at,started_at)) DESC LIMIT 1"
    )
    next_adapter = _query(
        """
        SELECT source_name,next_run_at FROM source_runtime_truth_v1
        WHERE enabled=1 AND schedule_state NOT IN ('running','disabled') AND next_run_at IS NOT NULL
        ORDER BY datetime(next_run_at) LIMIT 1
        """
    )
    runtime_times = _query(
        """
        SELECT MAX(updated_at) last_tick FROM source_random_schedule
        """
    ).iloc[0]
    coordinator_at = _scalar("SELECT MAX(created_at) FROM events WHERE event_type='unified_hourly_coordinator_run'", default=None)
    heartbeat = ROOT_DIR / str(integration.get("telegram_heartbeat_path") or "")
    telegram_state = "Listening" if heartbeat.is_file() and datetime.now(timezone.utc).timestamp() - heartbeat.stat().st_mtime < 180 else "Not available"
    remote = "Allowed" if eligibility.get("remote_allowed") else "Blocked"
    hybrid = "Allowed" if eligibility.get("hybrid_allowed") else "Blocked"
    onsite = "Allowed" if eligibility.get("onsite_allowed") else "Blocked"
    last_adapter_text = "No run yet" if last_adapter.empty else f"{last_adapter.iloc[0]['source_name']} · {format_local_short(last_adapter.iloc[0]['completed_at'], empty='time unavailable')}"
    next_adapter_text = "Waiting for schedule" if next_adapter.empty else f"{next_adapter.iloc[0]['source_name']} · {format_local_short(next_adapter.iloc[0]['next_run_at'])}"

    _section_heading("Live operations", "Current source truth and autonomous service evidence; normal cadence waiting is not treated as a failure.")
    _metric_row(
        [
            ("System", overall, "Canonical runtime health."),
            ("Configured", f"{int(source_summary.get('configured') or 0):,}", None),
            ("Enabled", f"{int(source_summary.get('enabled') or 0):,}", None),
            ("Healthy", f"{int(source_summary.get('healthy') or 0):,}", None),
            ("Needs attention", f"{int(source_summary.get('attention') or 0):,}", "Enabled providers reporting degraded or failed health."),
            ("Running now", f"{int(source_summary.get('running') or 0):,}", None),
            ("Scheduled normally", f"{int(source_summary.get('scheduled') or 0):,}", "Healthy cadence or short active-work deferral."),
            ("Failure backoff", f"{int(source_summary.get('backoff') or 0):,}", None),
        ]
    )
    st.markdown(
        f"""
        <div class="snapshot-grid">
          <div class="snapshot-card"><div class="snapshot-label">Targeting model</div><div class="snapshot-value">{html.escape(str(targeting.get('mode') or 'Not configured'))}-focused · {html.escape(str(eligibility.get('label') or 'Not configured'))}</div><div class="snapshot-detail">Remote {remote} · Hybrid {hybrid} · Onsite {onsite} · unknown country {html.escape(str(eligibility.get('unknown_country_policy') or 'not configured'))}</div></div>
          <div class="snapshot-card"><div class="snapshot-label">Autonomous scheduling</div><div class="snapshot-value">{'Paused for maintenance' if maintenance else 'Randomized runner active'}</div><div class="snapshot-detail">Last adapter: {html.escape(last_adapter_text)}<br>Next due: {html.escape(next_adapter_text)}</div></div>
          <div class="snapshot-card"><div class="snapshot-label">Automation services</div><div class="snapshot-value">Telegram {telegram_state.lower()} · n8n {'reachable' if n8n_up else 'not available'}</div><div class="snapshot-detail">Scheduler tick {html.escape(format_local_short(runtime_times.get('last_tick')))} · hourly coordinator {html.escape(format_local_short(coordinator_at))}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _time_caption()

    _section_heading("Historical activity", "A restrained seven-day view; deeper Today, 7-day, 30-day, and Lifetime analysis is available in Historical Intelligence.")
    if history.empty:
        _empty("No historical activity is recorded yet.")
    else:
        cutoff = datetime.now().astimezone(system_timezone()).date() - timedelta(days=6)
        recent_history = history.loc[history.index >= cutoff]
        chart_left, chart_right = st.columns(2)
        chart_left.markdown("**Daily discovery activity**")
        chart_left.bar_chart(recent_history[[column for column in ("Source runs", "Opportunities scanned") if column in recent_history]], height=245)
        chart_right.markdown("**Daily targeting outcomes**")
        chart_right.bar_chart(recent_history[[column for column in ("Duplicates", "Rejected", "Eligible", "Jobs stored") if column in recent_history]], height=245)

    _section_heading("Source intelligence", "Mature provider highlights require at least three measured runs and 100 normalized records.")
    mature = providers.loc[providers["Mature"] == True] if not providers.empty else providers  # noqa: E712
    if mature.empty:
        _empty("Waiting for enough repeated provider samples to produce mature comparisons.")
    else:
        coverage = mature.sort_values(["Scanned", "Runs"], ascending=False).iloc[0]
        eligible_volume = mature.sort_values(["Eligible", "Runs"], ascending=False).iloc[0]
        eligible_mature = mature.loc[mature["Eligible"] > 0].sort_values(["Eligible yield %", "Eligible"], ascending=False)
        best_yield = eligible_mature.iloc[0] if not eligible_mature.empty else None
        _metric_row(
            [
                ("Highest measured coverage", str(coverage["Provider"]), f"{int(coverage['Scanned']):,} records across {int(coverage['Runs'])} runs"),
                ("Highest eligible volume", str(eligible_volume["Provider"]), f"{int(eligible_volume['Eligible']):,} eligible across mature samples"),
                ("Best mature eligible yield", str(best_yield["Provider"]) if best_yield is not None else "Not available", f"{float(best_yield['Eligible yield %']):.2f}% across {int(best_yield['Runs'])} runs" if best_yield is not None else "No mature provider has eligible results yet"),
            ]
        )

    _section_heading("Targeting intelligence", "MUNSHI Apply evaluates opportunities against canonical role, location, requirement, and company policy—it does not merely collect listings.")
    if rejections.empty:
        _empty("No historical targeting rejections are recorded.")
    else:
        reject_left, reject_right = st.columns([1.15, 1])
        reject_left.bar_chart(rejections.set_index("Reason")[["Count"]], horizontal=True, height=250)
        reject_right.dataframe(rejections.rename(columns={"Share": "Share %"}), hide_index=True, width="stretch", height=250)

    _section_heading("The autonomous system", "A persistent control plane turns provider records into governed application intelligence.")
    st.markdown(
        """
        <div class="system-story">
          <div><strong>01</strong><span>Multi-source discovery</span><small>Scheduled provider adapters</small></div>
          <b>→</b><div><strong>02</strong><span>Normalization</span><small>One canonical record model</small></div>
          <b>→</b><div><strong>03</strong><span>Deduplication</span><small>Cross-source identity control</small></div>
          <b>→</b><div><strong>04</strong><span>Canonical targeting</span><small>Role, location, and eligibility</small></div>
          <b>→</b><div><strong>05</strong><span>Delivery &amp; orchestration</span><small>Telegram, queue, and n8n</small></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "Runtime assurance · canonical macOS service ownership, serialized source work, persistent schedules, and state-preserving recovery after normal workstation interruptions."
    )

    _section_heading("Attention queue", "Only enabled sources whose canonical runtime truth needs review.")
    attention = _query(
        """
        SELECT source_name AS Provider, health_status AS State,
               consecutive_failures AS Failures, last_run_at AS "Last run",
               next_run_at AS "Next attempt", substr(COALESCE(last_error,''),1,140) AS "Latest evidence"
        FROM source_runtime_truth_v1
        WHERE enabled=1 AND health_status NOT IN ('healthy','maintenance')
        ORDER BY CASE health_status WHEN 'failed' THEN 0 WHEN 'error' THEN 0 ELSE 1 END,
                 consecutive_failures DESC, source_name
        """
    )
    if attention.empty:
        st.success("No enabled source currently reports a degraded or failed state.")
    else:
        attention = _localize_columns(attention, ["Last run", "Next attempt"])
        st.dataframe(attention, hide_index=True, width="stretch", height=min(310, 42 + 36 * len(attention)))


def _historical_intelligence() -> None:
    _page_intro(
        "Recorded evidence",
        "Historical intelligence",
        "Explore system activity from the earliest trustworthy record through today, grouped by the Mac's current local calendar.",
    )
    connection = get_connection()
    try:
        lifetime = lifetime_metrics(connection)
        history = daily_history(connection)
        rejections = rejection_intelligence(connection)
        providers = provider_intelligence(connection)
        operational_summaries = operational_summary_metrics(connection)
    finally:
        connection.close()
    integration = get_setting("integration_health", {}) or {}
    workflow_id = str((integration.get("n8n_read_only_snapshot") or {}).get("workflow_id") or "")
    n8n_summary, n8n_daily = n8n_execution_metrics(workflow_id)

    _section_heading("Evidence horizons", "Each historical series begins when its underlying persisted evidence becomes trustworthy.")
    _metric_row(
        [
            ("Stored-job history", format_local(lifetime.get("first_job"), empty="Not available", include_zone=False, date_style="%b %-d, %Y"), None),
            ("Source-run telemetry", format_local(lifetime.get("first_run"), empty="Not available", include_zone=False, date_style="%b %-d, %Y"), None),
            ("Targeting decisions", format_local(lifetime.get("first_decision"), empty="Not available", include_zone=False, date_style="%b %-d, %Y"), None),
            ("Canonical n8n history", format_local(n8n_summary.get("first_execution"), empty="Not available", include_zone=False, date_style="%b %-d, %Y"), None),
        ]
    )
    _time_caption()

    period = st.selectbox("Historical range", ["Today", "7 days", "30 days", "Lifetime"], index=3)
    filtered = history.copy()
    today_local = datetime.now().astimezone(system_timezone()).date()
    if not filtered.empty and period != "Lifetime":
        days = {"Today": 1, "7 days": 7, "30 days": 30}[period]
        filtered = filtered.loc[filtered.index >= today_local - timedelta(days=days - 1)]

    _section_heading(f"{period} activity", "Daily buckets use system-local midnight boundaries; provider timestamps remain stored in UTC.")
    if filtered.empty:
        _empty("No activity is recorded for this range.")
    else:
        c1, c2 = st.columns(2)
        discovery_columns = [column for column in ("Source runs", "Provider requests", "Opportunities scanned") if column in filtered]
        outcome_columns = [column for column in ("Duplicates", "Rejected", "Eligible", "Jobs stored") if column in filtered]
        c1.markdown("**Discovery activity**")
        c1.bar_chart(filtered[discovery_columns], height=290)
        c2.markdown("**Decision outcomes**")
        c2.bar_chart(filtered[outcome_columns], height=290)
        automation_columns = [column for column in ("Telegram deliveries",) if column in filtered]
        if automation_columns:
            st.markdown("**Recorded Telegram delivery activity**")
            st.bar_chart(filtered[automation_columns], height=220)

    _section_heading("Historical measured totals", "These stages share a telemetry horizon but should not be interpreted as a forced strict funnel.")
    _metric_row(
        [
            ("Provider requests", f"{int(lifetime.get('requests') or 0):,}", None),
            ("Opportunities scanned", f"{int(lifetime.get('scanned') or 0):,}", None),
            ("Normalized", f"{int(lifetime.get('normalized') or 0):,}", None),
            ("Duplicates", f"{int(lifetime.get('duplicates') or 0):,}", None),
            ("Targeting rejects", f"{int(lifetime.get('rejected') or 0):,}", None),
            ("Eligible", f"{int(lifetime.get('eligible') or 0):,}", None),
            ("New eligible", f"{int(lifetime.get('new_eligible') or 0):,}", None),
            ("Jobs stored", f"{int(lifetime.get('jobs_stored') or 0):,}", "Stored-job history predates canonical source-run telemetry."),
        ]
    )

    _section_heading("Rejection intelligence", "Count and share of recorded canonical targeting rejections.")
    if rejections.empty:
        _empty("No canonical targeting rejections are recorded.")
    else:
        left, right = st.columns([1.2, 1])
        left.bar_chart(rejections.set_index("Reason")[["Count"]], horizontal=True, height=280)
        right.dataframe(rejections.rename(columns={"Share": "Share %"}), hide_index=True, width="stretch", height=280)

    _section_heading("Historical provider intelligence", "Yield comparisons are labeled mature only after at least three measured runs and 100 normalized records.")
    if providers.empty:
        _empty("No provider history is recorded.")
    else:
        visible = providers[["Provider", "Runs", "Requests", "Scanned", "Normalized", "Eligible", "New eligible", "Eligible yield %", "Errors", "Mature", "Last completed"]]
        visible = _localize_columns(visible, ["Last completed"])
        st.dataframe(visible, hide_index=True, width="stretch", height=510)

    _section_heading("Canonical n8n execution history", "Read-only aggregation for the verified workflow identity; tests and manual runs remain labeled as recorded executions, not job applications.")
    if not n8n_summary.get("available"):
        _empty("Canonical n8n execution history is not available.")
    else:
        _metric_row(
            [
                ("Recorded executions", f"{int(n8n_summary['executions']):,}", None),
                ("Successful", f"{int(n8n_summary['successful']):,}", None),
                ("Failed", f"{int(n8n_summary['failed']):,}", None),
                ("Running", f"{int(n8n_summary['running']):,}", None),
                ("First recorded", format_local_short(n8n_summary.get("first_execution")), None),
                ("Latest", format_local_short(n8n_summary.get("latest_execution")), None),
                ("Last successful", format_local_short(n8n_summary.get("last_success")), None),
            ]
        )
        if not n8n_daily.empty:
            st.bar_chart(n8n_daily[["Executions", "Successful", "Failed"]], height=240)
        st.caption("n8n database access is read-only. Workflow configuration, activation, credentials, and executions are never mutated by this dashboard.")

    _section_heading(
        "Telegram adapter visibility",
        "Adapter operation cards are counted separately from individual job-opportunity cards.",
    )
    if not operational_summaries.get("available"):
        _empty("Durable adapter-summary history is not available yet.")
    else:
        _metric_row(
            [
                ("Summary cards generated", f"{int(operational_summaries.get('generated') or 0):,}", "Recorded since the durable notification contract was activated."),
                ("Delivered", f"{int(operational_summaries.get('delivered') or 0):,}", None),
                ("Pending", f"{int(operational_summaries.get('pending') or 0):,}", None),
                ("Retrying", f"{int(operational_summaries.get('retrying') or 0):,}", None),
                ("Uncertain", f"{int(operational_summaries.get('uncertain') or 0):,}", "Ambiguous sends are withheld from blind replay to prevent duplicate cards."),
                ("Earliest recorded", format_local_short(operational_summaries.get("earliest")), None),
                ("Latest recorded", format_local_short(operational_summaries.get("latest")), None),
            ]
        )


def _source_health() -> None:
    _page_intro("Provider intelligence", "Source health", "Canonical readiness, current runtime state, cadence, yield, and safe source control.")
    all_data = _query(
        """
        SELECT source_name, enabled, cost_mode, health_status, schedule_state,
               cadence_minutes, last_run_at, last_success_at, next_run_at,
               jobs_found_last_run, eligible_jobs_last_run, inserted_jobs_last_run,
               CASE WHEN normalized_jobs_last_run>0
                    THEN ROUND(100.0*eligible_jobs_last_run/normalized_jobs_last_run,1) END yield_pct,
               ROUND(COALESCE(last_duration_ms,average_response_ms),0) latency_ms,
               error_count_last_run, consecutive_failures, last_http_status,
               substr(COALESCE(last_error,''),1,220) last_error
        FROM source_runtime_truth_v1 ORDER BY lower(source_name)
        """
    )
    if all_data.empty:
        _empty("No canonical source-runtime records exist.")
        return
    all_data["display_state"] = all_data.apply(_source_state, axis=1)
    enabled_count = int(all_data["enabled"].fillna(0).astype(int).sum())
    needs_attention = int(
        ((all_data["enabled"] == 1) & all_data["display_state"].isin(["Degraded", "Failed"])).sum()
    )
    _metric_row(
        [
            ("Configured", len(all_data), None),
            ("Enabled", enabled_count, None),
            ("Healthy", int(((all_data["enabled"] == 1) & (all_data["health_status"] == "healthy")).sum()), None),
            ("Needs attention", needs_attention, "Enabled providers reporting degraded or failed health."),
            ("Scheduled normally", int(((all_data["enabled"] == 1) & all_data["schedule_state"].isin(["cooldown", "deferred"])).sum()), "Cadence waiting and short active-work deferrals are normal."),
            ("Running", int(((all_data["enabled"] == 1) & (all_data["schedule_state"] == "running")).sum()), None),
            ("Failure backoff", int(((all_data["enabled"] == 1) & (all_data["schedule_state"] == "failure_backoff")).sum()), None),
            ("Disabled", int((all_data["display_state"].isin(["Disabled", "Setup required"])).sum()), None),
            ("Setup required", int((all_data["display_state"] == "Setup required").sum()), None),
        ]
    )

    _section_heading("Provider status", "Filterable operational view; the default table keeps only decision-useful columns.")
    health_filter = st.selectbox(
        "Filter providers",
        ["All", "Healthy", "Running", "Degraded", "Failed", "Disabled", "Setup / new"],
        index=0,
        key="source_health_filter",
    )
    filtered = all_data
    if health_filter in {"Healthy", "Running", "Degraded", "Failed"}:
        filtered = filtered.loc[filtered["display_state"] == health_filter]
    elif health_filter == "Disabled":
        filtered = filtered.loc[filtered["enabled"] == 0]
    elif health_filter == "Setup / new":
        filtered = filtered.loc[filtered["display_state"].isin(["Setup required", "Never run"])]
    table = filtered.rename(
        columns={
            "source_name": "Provider", "display_state": "State", "enabled": "Enabled",
            "last_success_at": "Last success", "next_run_at": "Next run",
            "jobs_found_last_run": "Jobs found", "eligible_jobs_last_run": "Eligible",
            "yield_pct": "Yield %", "latency_ms": "Latency ms", "error_count_last_run": "Errors",
        }
    )[["Provider", "State", "Enabled", "Last success", "Next run", "Jobs found", "Eligible", "Yield %", "Latency ms", "Errors"]]
    table = _localize_columns(table, ["Last success", "Next run"])
    st.dataframe(table, hide_index=True, width="stretch", height=min(520, 44 + 35 * max(1, len(table))))
    _time_caption()

    _section_heading("Source control", "One canonical write path for enablement and cadence. Saving a policy never launches a source run.")
    all_sources = all_data[["source_name", "enabled", "cadence_minutes", "cost_mode", "health_status", "last_error"]].copy()
    if all_sources.empty:
        _empty("No source registry records exist.")
        return
    options = all_sources["source_name"].tolist()
    selected = st.selectbox("Provider", options, index=0, key="source_control_provider")
    current = all_sources.loc[all_sources["source_name"] == selected].iloc[0]
    paid = str(current.get("cost_mode") or "").casefold() == "paid"
    if selected == "USAJobs":
        selected_runtime = all_data.loc[all_data["source_name"] == selected].iloc[0]
        try:
            from app.secure_credentials import credential_status

            credential = credential_status()
            credential_configured = bool(credential.get("api_key_present") and credential.get("email_present"))
        except Exception:
            credential_configured = False
        connection_verified = bool(
            credential_configured
            and (
                int(selected_runtime.get("last_http_status") or 0) == 200
                or bool(selected_runtime.get("last_success_at"))
            )
        )
        st.markdown(
            f"""
            <div class="adapter-truth-panel">
              <div><div class="integration-name">USAJobs</div><div class="integration-sub">Official U.S. Government API · one adapter in the discovery portfolio</div></div>
              <div class="status-grid">
                <div class="status-cell"><span>Credentials</span><strong>{'Configured' if credential_configured else 'Not configured'}</strong></div>
                <div class="status-cell"><span>API connection</span><strong>{'Verified' if connection_verified else 'Not verified'}</strong></div>
                <div class="status-cell"><span>Runtime</span><strong>{'Enabled' if bool(selected_runtime['enabled']) else 'Disabled'}</strong></div>
                <div class="status-cell"><span>Scheduler</span><strong>{html.escape(humanize_machine_value(selected_runtime.get('schedule_state')))}</strong></div>
                <div class="status-cell"><span>Cadence</span><strong>{int(selected_runtime['cadence_minutes'])} minutes</strong></div>
                <div class="status-cell"><span>Last run</span><strong>{html.escape(_format_when(selected_runtime.get('last_run_at')))}</strong></div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("Credential readiness and runtime enablement are independent. Only this canonical source policy controls scheduled discovery.")
    with st.form("source_control_form"):
        left, right = st.columns(2)
        enabled = left.toggle("Enabled for scheduled discovery", value=bool(current["enabled"]) and not paid, disabled=paid, help="This changes canonical runtime eligibility. It does not run the source now.")
        cadence = right.number_input("Cadence (minutes)", 5, 10080, int(current["cadence_minutes"]), step=5, help="The randomized scheduler will honor this interval after the policy is saved.")
        submitted = st.form_submit_button("Save source policy", type="primary")
    if paid:
        st.warning(f"{selected} is classified as paid and remains disabled by the free-only source policy.")
    elif selected == "USAJobs":
        st.info("This adapter remains under manual owner control. Saving Enabled changes scheduler eligibility but never launches an immediate run.", icon="🔐")
    if submitted:
        changed = save_source_policy(
            selected,
            enabled=bool(enabled),
            cadence_minutes=int(cadence),
            changed_by="streamlit:owner",
        )
        st.success(
            f"{selected} policy saved with versioned history."
            if changed else f"{selected} policy was already current."
        )

    with st.expander("Provider detail and latest error evidence", expanded=False):
        detail = all_data.loc[all_data["source_name"] == selected].rename(
            columns={"source_name": "Provider", "health_status": "Canonical health", "schedule_state": "Schedule state", "last_run_at": "Last run", "last_success_at": "Last success", "next_run_at": "Next run", "last_error": "Latest evidence"}
        )
        detail = _localize_columns(detail, ["Last run", "Last success", "Next run"])
        st.dataframe(detail[["Provider", "Canonical health", "Schedule state", "Last run", "Last success", "Next run", "Latest evidence"]], hide_index=True, width="stretch")


def _adapter_coverage() -> None:
    _page_intro("Coverage portfolio", "Adapter coverage", "Verified provider capabilities and representative employer boards, with unsupported coverage labeled honestly.")
    st.caption("Coverage is marked only when implementation and test evidence exist. Blocked rows are not presented as working adapters.")
    data = _query(
        """
        SELECT provider AS Provider, implemented AS Implemented,
               fixture_tested AS "Fixture tested", live_tested AS "Live tested",
               enabled AS Enabled, health_status AS "Coverage evidence",
               support_level AS "Support level", us_board_count AS "U.S. boards",
               blocked_reason AS "Blocked reason", last_verified_at AS "Last verified",
               implementation_module AS Module
        FROM adapter_coverage ORDER BY Implemented DESC, Enabled DESC, Provider
        """
    )
    mapping = dict(get_setting("adapter_source_mapping", {}) or {})
    provider_sources = {
        str(item.get("provider") or ""): str(item.get("source_name") or "")
        for item in list(mapping.get("providers") or [])
        if item.get("provider") and item.get("source_name")
    }
    runtime_health = _query(
        "SELECT source_name, health_status FROM source_health"
    )
    health_by_source = (
        dict(zip(runtime_health["source_name"], runtime_health["health_status"]))
        if not runtime_health.empty
        else {}
    )
    data.insert(
        5,
        "Runtime health",
        [
            health_by_source.get(provider_sources.get(str(provider), ""), "not enabled")
            for provider in data["Provider"]
        ],
    )
    implemented = int(data["Implemented"].sum()) if not data.empty else 0
    live = int(data["Live tested"].sum()) if not data.empty else 0
    blocked = int((data["Support level"] == "blocked").sum()) if not data.empty else 0
    _metric_row(
        [("Implemented", implemented, None), ("Live tested", live, None), ("Explicitly blocked", blocked, "Blocked coverage is honest, not fake." )]
    )
    visible_coverage = data[["Provider", "Implemented", "Fixture tested", "Live tested", "Enabled", "Runtime health", "Support level", "U.S. boards", "Last verified"]]
    visible_coverage = _localize_columns(visible_coverage, ["Last verified"])
    st.dataframe(visible_coverage, hide_index=True, width="stretch", height=500)
    with st.expander("Coverage implementation detail", expanded=False):
        st.dataframe(_localize_columns(data, ["Last verified"]), hide_index=True, width="stretch")

    st.markdown("#### Employer board registry")
    boards = _query(
        """
        SELECT id, company_name AS Company, provider AS Provider, tenant AS Tenant,
               site_name AS Site, board_url AS "Board URL", us_relevance AS "U.S. relevance",
               enabled AS Enabled, priority_weight AS Priority, health_status AS Health,
               last_job_count AS Jobs, last_verified_at AS "Last verified", notes AS Notes
        FROM provider_board_registry ORDER BY Enabled DESC, Priority DESC, Provider, Company
        """
    )
    if boards.empty:
        _empty("No employer boards are registered yet.")
        return
    board_table = _localize_columns(boards[["Company", "Provider", "U.S. relevance", "Enabled", "Priority", "Health", "Jobs", "Last verified"]], ["Last verified"])
    st.dataframe(board_table, hide_index=True, width="stretch")
    board_id = st.selectbox(
        "Board to configure",
        boards["id"].tolist(),
        format_func=lambda value: f"{boards.loc[boards['id']==value, 'Company'].iloc[0]} · {boards.loc[boards['id']==value, 'Provider'].iloc[0]}",
    )
    board = boards.loc[boards["id"] == board_id].iloc[0]
    with st.form("board_control_form"):
        left, right = st.columns(2)
        board_enabled = left.toggle("Enabled for controlled source runs", value=bool(board["Enabled"]))
        priority = right.number_input("Priority weight", -100, 100, int(board["Priority"]))
        notes = st.text_area("Operational notes", value=str(board["Notes"] or ""), height=90)
        board_submit = st.form_submit_button("Save board policy", type="primary")
    if board_submit:
        changed = save_board_policy(
            int(board_id),
            enabled=bool(board_enabled),
            priority_weight=int(priority),
            notes=notes,
            changed_by="streamlit:owner",
        )
        st.success(
            "Board policy saved to the canonical registry with versioned history."
            if changed else "Board policy was already current."
        )


def _targeting() -> None:
    _page_intro("Canonical policy", "Targeting", "Understand the active eligibility model first, then make deliberate versioned changes to its rules.")
    targeting = dict(get_setting("targeting", {}) or {})
    eligibility = dict(targeting.get("eligibility") or {})
    families = list(targeting.get("role_families") or [])
    locations = _query(
        """
        SELECT location_name AS Location, rule_purpose AS Purpose,
               remote_allowed AS Remote, hybrid_allowed AS Hybrid, onsite_allowed AS Onsite,
               priority_weight AS "Preference weight", is_active AS Active
        FROM location_rules ORDER BY CASE rule_purpose WHEN 'eligibility' THEN 0 ELSE 1 END,
                                          priority_weight DESC, location_name
        """
    )
    left, middle, right = st.columns(3)
    active_mode = html.escape(str(targeting.get("mode") or "Not configured"))
    geography_label = html.escape(str(eligibility.get("label") or "Not configured"))
    arrangements = " · ".join(
        label for key, label in (("remote_allowed", "Remote"), ("hybrid_allowed", "Hybrid"), ("onsite_allowed", "Onsite"))
        if bool(eligibility.get(key))
    ) or "None enabled"
    left.markdown(f'<div class="policy-card"><strong>ACTIVE MODE</strong><br><h2>{active_mode}</h2><span class="quiet">Historical authorization fields are not current eligibility rules.</span></div>', unsafe_allow_html=True)
    middle.markdown(f'<div class="policy-card"><strong>ELIGIBILITY</strong><br><h3>{geography_label}</h3><span class="quiet">Unknown and worldwide-only locations follow the configured fail-closed policy.</span></div>', unsafe_allow_html=True)
    right.markdown(f'<div class="policy-card"><strong>WORK ARRANGEMENTS</strong><br><h3>{html.escape(arrangements)}</h3><span class="quiet">Allowed only when configured country eligibility is evidenced.</span></div>', unsafe_allow_html=True)
    st.info(
        f"Current policy: {active_mode}-focused · {geography_label} · Remote {'allowed' if eligibility.get('remote_allowed') else 'blocked'} · Hybrid {'allowed' if eligibility.get('hybrid_allowed') else 'blocked'} · Onsite {'allowed' if eligibility.get('onsite_allowed') else 'blocked'} · Unknown country {eligibility.get('unknown_country_policy', 'reject')}.",
        icon="🎯",
    )
    _section_heading("Eligibility and high-impact rules", "Changes are saved to canonical SQLite configuration with versioned history. No policy is changed until you confirm and save.")
    with st.form("targeting_policy_form"):
        st.markdown("**Location eligibility**")
        st.caption("Work arrangement is allowed only after United States eligibility is evidenced; foreign-only roles remain rejected.")
        c1, c2, c3 = st.columns(3)
        remote = c1.toggle("Remote U.S. allowed", value=bool(eligibility.get("remote_allowed", True)), help="Allow remote roles whose eligible country is the United States.")
        hybrid = c2.toggle("Hybrid U.S. allowed", value=bool(eligibility.get("hybrid_allowed", True)), help="Allow hybrid roles whose eligible country is the United States.")
        onsite = c3.toggle("Onsite U.S. allowed", value=bool(eligibility.get("onsite_allowed", True)), help="Allow onsite roles located in the United States.")
        unknown_policy = st.selectbox(
            "Unknown country policy",
            ["reject", "review"],
            index=0 if eligibility.get("unknown_country_policy", "reject") == "reject" else 1,
            help="Fail-closed rejection is recommended for reliable U.S. eligibility.",
        )
        experience = dict(targeting.get("experience_policy") or {})
        required_years = st.number_input(
            "Reject when explicitly required minimum experience reaches (years)",
            0, 20, int(experience.get("reject_required_min_years", 2)),
            help="Preferred, optional, unknown, benefits, and boilerplate mentions do not trigger this reject.",
        )
        st.markdown("**Role, seniority, and company hard rejects**")
        st.caption("Enter one rule per line. These lists are high impact: matching evidence can reject a job before delivery.")
        c1, c2, c3 = st.columns(3)
        hard_rejects = c1.text_area(
            "Title-only seniority rejects",
            "\n".join(targeting.get("title_only_hard_rejects") or []), height=220,
            help="Senior or leadership phrases that are disqualifying when they occur in the job title.",
        )
        negative_contexts = c2.text_area(
            "Non-HR role contexts",
            "\n".join(targeting.get("role_negative_contexts") or []), height=220,
            help="Contexts that look HR-adjacent but are outside the intended role families.",
        )
        company_exclusions = c3.text_area(
            "Company exclusions",
            "\n".join(targeting.get("company_exclusions") or []), height=220,
            help="Company names that canonical targeting should exclude.",
        )
        confirm_policy = st.checkbox("I reviewed these high-impact targeting changes")
        save_policy = st.form_submit_button("Save canonical targeting policy", type="primary", disabled=not confirm_policy)
    if save_policy:
        eligibility.update(
            remote_allowed=bool(remote), hybrid_allowed=bool(hybrid), onsite_allowed=bool(onsite),
            unknown_country_policy=unknown_policy,
        )
        experience["reject_required_min_years"] = int(required_years)
        targeting.update(
            eligibility=eligibility, experience_policy=experience,
            title_only_hard_rejects=_lines(hard_rejects),
            role_negative_contexts=_lines(negative_contexts),
            company_exclusions=_lines(company_exclusions),
        )
        save_setting("targeting", targeting, changed_by="streamlit:owner")
        st.success("Canonical targeting policy saved with versioned history.")

    _section_heading("Location preferences", "Preference boosts affect ranking; they do not narrow nationwide eligibility.")
    st.dataframe(locations, hide_index=True, width="stretch")
    st.caption("NJ, NYC, and Philadelphia are preference boosts. They are not geographic eligibility boundaries.")

    _section_heading("Role families and query rotation", "Eligible title evidence and provider queries remain explicit and independently editable.")
    if not families:
        _empty("No role families are configured.")
        return
    family_names = [str(item.get("name") or "Unnamed") for item in families]
    selected_name = st.selectbox("Role family", family_names)
    family_index = family_names.index(selected_name)
    family = dict(families[family_index])
    with st.form("role_family_form"):
        left_rules, right_rules = st.columns(2)
        title_phrases = left_rules.text_area("Eligible title phrases", "\n".join(family.get("title_phrases") or []), height=190, help="One canonical title phrase per line.")
        queries = right_rules.text_area("Provider queries", "\n".join(family.get("queries") or []), height=190, help="One discovery query per line; final eligibility still uses the canonical gate.")
        confirm_family = st.checkbox("I reviewed this role-family change")
        save_family = st.form_submit_button("Save role family", type="primary", disabled=not confirm_family)
    if save_family:
        family["title_phrases"] = _lines(title_phrases)
        family["queries"] = _lines(queries)
        families[family_index] = family
        targeting["role_families"] = families
        save_setting("targeting", targeting, changed_by="streamlit:owner")
        st.success(f"{selected_name} saved to the canonical targeting policy.")


def _render_decision_intelligence(
    detail: Any,
    *,
    rejected_record: bool,
    technical_evidence: dict[str, Any],
) -> None:
    view = decision_view_model(detail, rejected_record=rejected_record)
    explanation = explainable_evidence_model(detail, rejected_record=rejected_record)
    decision_tone = "decision-eligible" if view["decision"] == "Eligible" else "decision-rejected"
    st.markdown(
        f"""
        <div class="decision-hero {decision_tone}">
          <div><span>Decision</span><strong>{html.escape(str(view['decision']))}</strong></div>
          <div><span>Primary reason</span><strong>{html.escape(str(view['primary_reason']))}</strong></div>
        </div>
        <div class="decision-grid">
          <div class="decision-card"><span>Role match</span><strong>{'✓' if view['role_matched'] else '—'} {html.escape(str(view['role_summary']))}</strong><p>{html.escape(str(view['matched_phrase']))}</p><small>Target family · {html.escape(str(view['target_family']))}<br>Matched from · {html.escape(str(view['matched_from']))}</small></div>
          <div class="decision-card"><span>Location</span><strong>{'✓' if view['location_confirmed'] else '—'} {html.escape(str(view['location_summary']))}</strong><p>{html.escape(str(view['location']))}</p><small>Arrangement · {html.escape(str(view['arrangement']))}</small></div>
          <div class="decision-card"><span>Experience</span><strong>{'✓' if view['experience_clear'] else '—'} {html.escape(str(view['experience_summary']))}</strong><p>{html.escape(str(view['experience_impact']))}</p></div>
          <div class="decision-card"><span>Delivery</span><strong>{html.escape(str(view['delivery_state']))}</strong><p>{html.escape(str(view['delivery_reason']))}</p><small>{html.escape(str(view['downstream_state']))}</small></div>
          <div class="decision-card"><span>Source</span><strong>{html.escape(str(view['source']))}</strong><p>Original provider recorded for this opportunity.</p><small>Full source history is available in decision evidence.</small></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if view["application_url"]:
        st.link_button("Open application page", view["application_url"])

    def render_facts(title: str, subtitle: str, facts: list[tuple[str, str]]) -> None:
        fact_html = "".join(
            f'<div class="evidence-fact"><span>{html.escape(str(label))}</span><strong>{html.escape(str(value))}</strong></div>'
            for label, value in facts
        )
        st.markdown(
            f'<section class="evidence-section"><div class="evidence-title"><span>{html.escape(title)}</span><p>{html.escape(subtitle)}</p></div><div class="evidence-grid">{fact_html}</div></section>',
            unsafe_allow_html=True,
        )

    with st.expander("Advanced decision evidence", expanded=False):
        st.caption("Explainable proof for reviewers and technical stakeholders. Internal codes, hashes, identifiers, and raw payloads remain one level deeper.")
        render_facts(
            "Targeting evidence",
            "What the canonical policy evaluated and why the role decision was reached.",
            explanation["targeting"],
        )
        render_facts(
            "Location evidence",
            "How reported geography and work arrangement were interpreted under nationwide U.S. policy.",
            explanation["location"],
        )
        render_facts(
            "Experience evidence",
            "Detected requirements and whether they affected canonical eligibility.",
            explanation["experience"],
        )
        if explanation["experience_rows"]:
            st.dataframe(
                pd.DataFrame(explanation["experience_rows"]),
                hide_index=True,
                width="stretch",
                column_config={
                    "Requirement": st.column_config.TextColumn("Requirement", width="large"),
                    "Minimum": st.column_config.TextColumn("Minimum", width="small"),
                    "Maximum": st.column_config.TextColumn("Maximum", width="small"),
                    "Classification": st.column_config.TextColumn("Classification", width="small"),
                    "Impact": st.column_config.TextColumn("Impact", width="medium"),
                    "Source": st.column_config.TextColumn("Source", width="small"),
                },
            )
        else:
            st.caption("No experience requirement evidence was recorded for this opportunity.")
        if explanation["hard_rule_rows"]:
            st.markdown("##### Configured hard-rule matches")
            st.dataframe(
                pd.DataFrame(explanation["hard_rule_rows"]),
                hide_index=True,
                width="stretch",
            )
        render_facts(
            "Deduplication & provenance",
            "Where the record came from and whether multiple source observations were consolidated.",
            explanation["provenance"],
        )
        if explanation["source_names"]:
            st.markdown("**Observed sources**")
            st.markdown("\n".join(f"- {html.escape(str(source_name))}" for source_name in explanation["source_names"]))
        for link in explanation["source_links"]:
            st.link_button(str(link["label"]), str(link["url"]))
        render_facts(
            "Delivery & automation",
            "What reached Telegram and downstream orchestration after the targeting decision.",
            explanation["delivery"],
        )

        with st.expander("Raw machine evidence", expanded=False):
            st.caption("Forensic payloads for audit and engineering review. This layer intentionally preserves internal fields exactly as stored.")
            for label, payload in technical_evidence.items():
                st.markdown(f"**{label}**")
                st.json(payload)


def _job_explorer() -> None:
    _page_intro("Decision intelligence", "Job explorer", "Search the opportunity record, then understand the targeting decision in clear human language.")
    record_type = st.selectbox("Record view", ["Eligible / stored jobs", "Rejected / duplicate decisions"], index=0)
    if record_type == "Rejected / duplicate decisions":
        rejected_sources = _query("SELECT DISTINCT COALESCE(source_name,'Unknown') AS source FROM targeting_decisions ORDER BY source")
        category_options = {
            "All reasons": None,
            "Already discovered": "DUPLICATE",
            "Role outside targeting": "REJECT_ROLE",
            "Location not eligible": "REJECT_LOCATION",
            "Hard requirement": "REJECT_HARD_REQUIREMENT",
            "Company exclusion": "REJECT_COMPANY",
            "Other targeting rule": "REJECT_OTHER_TARGETING",
        }
        c1, c2, c3 = st.columns([2, 1, 1])
        search_rejected = c1.text_input("Search rejected title, company, or location")
        source_rejected = c2.selectbox("Decision source", ["All"] + rejected_sources["source"].tolist())
        category_label = c3.selectbox("Primary reason", list(category_options))
        rejected_clauses = ["primary_category!='ELIGIBLE'"]
        rejected_params: list[Any] = []
        if search_rejected.strip():
            rejected_clauses.append("(title LIKE ? OR company_name LIKE ? OR location_raw LIKE ?)")
            needle = f"%{search_rejected.strip()}%"
            rejected_params.extend([needle, needle, needle])
        if source_rejected != "All":
            rejected_clauses.append("COALESCE(source_name,'Unknown')=?")
            rejected_params.append(source_rejected)
        if category_options[category_label]:
            rejected_clauses.append("primary_category=?")
            rejected_params.append(category_options[category_label])
        rejected = _query(
            f"""
            SELECT id, title AS Title, company_name AS Company, location_raw AS Location,
                   COALESCE(source_name,'Unknown') AS Source, primary_category AS "Primary reason",
                   decided_at AS "Decided at"
            FROM targeting_decisions WHERE {' AND '.join(rejected_clauses)}
            ORDER BY datetime(decided_at) DESC, id DESC LIMIT 500
            """,
            rejected_params,
        )
        if rejected.empty:
            _empty("No rejected or duplicate decisions match these filters.")
            return
        rejected["Primary reason"] = rejected["Primary reason"].map(humanize_machine_value)
        rejected = _localize_columns(rejected, ["Decided at"])
        st.dataframe(rejected.drop(columns=["id"]), hide_index=True, width="stretch", height=410)
        decision_id = st.selectbox(
            "Open decision intelligence",
            rejected["id"].tolist(),
            format_func=lambda value: f"{rejected.loc[rejected['id']==value, 'Title'].iloc[0]} · {rejected.loc[rejected['id']==value, 'Primary reason'].iloc[0]}",
        )
        detail = _query("SELECT * FROM targeting_decisions WHERE id=?", [decision_id]).iloc[0]
        st.markdown(f"### {html.escape(str(detail.get('title') or 'Untitled opportunity'))} · {html.escape(str(detail.get('company_name') or 'Company not recorded'))}")
        _render_decision_intelligence(
            detail,
            rejected_record=True,
            technical_evidence={
                "Targeting evidence JSON": _json(detail.get("evidence_json"), {}),
                "Secondary diagnostic reasons": _json(detail.get("secondary_reasons_json"), []),
                "Record identifiers and rule evidence": {
                    "decision_id": int(detail.get("id")),
                    "run_id": detail.get("run_id"),
                    "job_identity": detail.get("job_identity"),
                    "rules_version": detail.get("rules_version"),
                    "rules_hash": detail.get("rules_hash"),
                },
            },
        )
        return

    filters = _query("SELECT DISTINCT source FROM jobs WHERE source IS NOT NULL ORDER BY source")
    companies = _query("SELECT DISTINCT company_name FROM jobs WHERE trim(COALESCE(company_name,''))!='' ORDER BY company_name")
    modes = _query("SELECT DISTINCT remote_type FROM jobs WHERE trim(COALESCE(remote_type,''))!='' ORDER BY remote_type")
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    search = c1.text_input("Search role, company, or location")
    company = c2.selectbox("Company", ["All"] + companies["company_name"].tolist())
    source = c3.selectbox("Source", ["All"] + filters["source"].tolist())
    work_mode = c4.selectbox("Work mode", ["All"] + modes["remote_type"].tolist())
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    location = c1.text_input("Location contains")
    decision = c2.selectbox("Targeting state", ["All", "Eligible", "Historical / unavailable"])
    telegram = c3.selectbox("Telegram", ["All", "Sent", "Pending"])
    recency = c4.selectbox("Discovered", ["Any time", "Today", "Past 7 days", "Past 30 days"])
    clauses = ["1=1"]
    params: list[Any] = []
    if search.strip():
        clauses.append("(title LIKE ? OR company_name LIKE ? OR location_raw LIKE ?)")
        needle = f"%{search.strip()}%"
        params.extend([needle, needle, needle])
    if source != "All":
        clauses.append("source=?")
        params.append(source)
    if company != "All":
        clauses.append("company_name=?")
        params.append(company)
    if work_mode != "All":
        clauses.append("remote_type=?")
        params.append(work_mode)
    if location.strip():
        clauses.append("location_raw LIKE ?")
        params.append(f"%{location.strip()}%")
    if decision == "Eligible":
        clauses.append("primary_decision='ELIGIBLE'")
    elif decision == "Historical / unavailable":
        clauses.append("COALESCE(primary_decision,'')='' ")
    if telegram == "Sent":
        clauses.append("telegram_sent=1")
    elif telegram == "Pending":
        clauses.append("telegram_sent=0")
    if recency in {"Today", "Past 7 days", "Past 30 days"}:
        end_utc = datetime.now(timezone.utc)
        if recency == "Today":
            start_utc, end_utc = local_day_bounds_utc()
        else:
            days = 7 if recency == "Past 7 days" else 30
            local_now = datetime.now().astimezone(system_timezone())
            local_start = (local_now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
            start_utc = local_start.astimezone(timezone.utc)
        clauses.append("datetime(first_seen_at)>=datetime(?) AND datetime(first_seen_at)<datetime(?)")
        params.extend([sqlite_utc(start_utc), sqlite_utc(end_utc)])
    jobs = _query(
        f"""
        SELECT id, title AS Title, company_name AS Company, location_raw AS Location,
               remote_type AS "Work mode", source AS Source,
               COALESCE(primary_decision,'LEGACY') AS Decision,
               ROUND(COALESCE(hunter_score,0),1) AS Score,
               first_seen_at AS Discovered,
               COALESCE(NULLIF(apply_url,''),job_url) AS Application
        FROM jobs WHERE {' AND '.join(clauses)} ORDER BY datetime(first_seen_at) DESC LIMIT 500
        """,
        params,
    )
    if jobs.empty:
        _empty("No jobs match these filters.")
        return
    jobs["Decision"] = jobs["Decision"].map(humanize_machine_value)
    jobs = _localize_columns(jobs, ["Discovered"])
    st.caption(f"Showing {len(jobs):,} matching records, newest first. Decision intelligence is summarized below; technical proof stays collapsed.")
    st.dataframe(
        jobs.drop(columns=["id"]), hide_index=True, width="stretch", height=410,
        column_config={"Application": st.column_config.LinkColumn("Application", display_text="Open")},
    )
    selected_id = st.selectbox(
        "Open decision intelligence",
        jobs["id"].tolist(),
        format_func=lambda value: f"{jobs.loc[jobs['id']==value, 'Title'].iloc[0]} · {jobs.loc[jobs['id']==value, 'Company'].iloc[0]}",
    )
    detail = _query("SELECT * FROM jobs WHERE id=?", [selected_id]).iloc[0]
    st.markdown(f"### {html.escape(str(detail.get('title') or 'Untitled opportunity'))} · {html.escape(str(detail.get('company_name') or 'Company not recorded'))}")
    _render_decision_intelligence(
        detail,
        rejected_record=False,
        technical_evidence={
            "Targeting evidence JSON": _json(detail.get("decision_evidence_json"), {"note": "Historical record; canonical evidence unavailable."}),
            "Role evidence JSON": _json(detail.get("role_evidence_json"), {}),
            "Location evidence JSON": _json(detail.get("location_evidence_json"), {}),
            "Experience evidence JSON": _json(detail.get("experience_evidence_json"), []),
            "Deduplication and provenance": {
                "duplicate_group": detail.get("duplicate_group"),
                "source_provenance": _json(detail.get("source_provenance_json"), []),
                "job_fingerprint": detail.get("job_fingerprint"),
            },
            "Delivery and quarantine evidence": {
                "telegram_sent": bool(detail.get("telegram_sent")),
                "sent_to_n8n": bool(detail.get("sent_to_n8n")),
                "secondary_reasons": _json(detail.get("secondary_reasons_json"), []),
                "database_id": int(detail.get("id")),
                "rules_version": detail.get("targeting_rules_version"),
                "rules_hash": detail.get("targeting_rules_hash"),
            },
        },
    )


# AADIL_DASHBOARD_JOB_RANKINGS_VISUAL_CARDS_V1_5
# V1.3: non-technical card browser + application-package/resume/contact visibility.
def _job_rankings() -> None:
    _page_intro(
        "DISCOVERY INTELLIGENCE",
        "Job Rankings",
        "Browse your ranked opportunities without spreadsheet-style sorting. Start with a simple view, search normally, and open any job for its full details, resume package, contacts, and outreach evidence.",
    )

    def _clean(value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return "" if text.casefold() in {"none", "nan", "null"} else text

    def _is_url(value: Any) -> bool:
        return _clean(value).startswith(("http://", "https://"))

    def _parsed(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        text = _clean(value)
        if not text or text[0:1] not in {"{", "["}:
            return value
        try:
            return json.loads(text)
        except Exception:
            return value

    def _walk(value: Any) -> Iterable[tuple[str, Any]]:
        value = _parsed(value)
        if isinstance(value, dict):
            for key, child in value.items():
                yield str(key), child
                yield from _walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from _walk(child)

    def _find_scalar(sources: list[Any], aliases: tuple[str, ...]) -> Any:
        wanted = {item.casefold() for item in aliases}
        for source in sources:
            source = _parsed(source)
            if isinstance(source, dict):
                for key in aliases:
                    if key in source and _clean(source.get(key)):
                        return source.get(key)
            for key, value in _walk(source):
                if key.casefold() in wanted and not isinstance(_parsed(value), (dict, list)) and _clean(value):
                    return value
        return None

    def _find_list(sources: list[Any], aliases: tuple[str, ...]) -> list[Any]:
        wanted = {item.casefold() for item in aliases}
        for source in sources:
            source = _parsed(source)
            if isinstance(source, dict):
                for key in aliases:
                    value = _parsed(source.get(key))
                    if isinstance(value, list) and value:
                        return value
            for key, value in _walk(source):
                if key.casefold() in wanted:
                    parsed = _parsed(value)
                    if isinstance(parsed, list) and parsed:
                        return parsed
        return []

    def _docx_from_google_doc(url: str) -> str:
        url = _clean(url)
        if "docs.google.com/document/d/" not in url:
            return ""
        try:
            document_id = url.split("/document/d/", 1)[1].split("/", 1)[0].split("?", 1)[0]
        except Exception:
            return ""
        return f"https://docs.google.com/document/d/{document_id}/export?format=docx" if document_id else ""

    def _package_for_job(job_id: int) -> dict[str, Any]:
        sources: list[Any] = []
        outcome: dict[str, Any] = {}

        table_exists = int(
            _scalar(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='n8n_results'",
                default=0,
            )
            or 0
        )
        if table_exists:
            info = _query("PRAGMA table_info(n8n_results)")
            columns = set(info["name"].astype(str).tolist()) if not info.empty and "name" in info.columns else set()
            id_column = next((name for name in ("job_id", "row_id", "hunter_row_id") if name in columns), None)
            order_column = "id" if "id" in columns else next(
                (name for name in ("completed_at", "sent_at", "updated_at", "created_at") if name in columns),
                None,
            )
            if id_column:
                order_sql = f' ORDER BY "{order_column}" DESC' if order_column else ""
                frame = _query(
                    f'SELECT * FROM n8n_results WHERE "{id_column}"=?{order_sql} LIMIT 1',
                    [int(job_id)],
                )
                if not frame.empty:
                    outcome = frame.iloc[0].to_dict()
                    sources.append(outcome)
                    for value in outcome.values():
                        parsed = _parsed(value)
                        if isinstance(parsed, (dict, list)):
                            sources.append(parsed)

        callback = _query(
            """
            SELECT payload_json
            FROM events
            WHERE job_id=? AND event_type='n8n_status_callback'
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 1
            """,
            [int(job_id)],
        )
        if not callback.empty:
            callback_payload = _parsed(callback.iloc[0].get("payload_json"))
            if isinstance(callback_payload, (dict, list)):
                sources.insert(0, callback_payload)

        resume_doc = _clean(_find_scalar(sources, (
            "resume_doc_url", "google_doc_url", "resume_document_url", "resume_google_doc_url"
        )))
        resume_pdf = _clean(_find_scalar(sources, (
            "resume_pdf_url", "google_pdf_url", "pdf_url", "resume_export_url"
        )))
        resume_word = _clean(_find_scalar(sources, (
            "resume_word_url", "resume_docx_url", "word_url"
        ))) or _docx_from_google_doc(resume_doc)
        cover_doc = _clean(_find_scalar(sources, (
            "cover_letter_doc_url", "cover_letter_document_url", "cover_letter_url"
        )))
        cover_pdf = _clean(_find_scalar(sources, (
            "cover_letter_pdf_url", "cover_pdf_url"
        )))
        cover_word = _clean(_find_scalar(sources, (
            "cover_letter_word_url", "cover_letter_docx_url"
        ))) or _docx_from_google_doc(cover_doc)
        sheet_url = _clean(_find_scalar(sources, (
            "google_sheet_url", "google_sheet_row_url", "spreadsheet_url", "sheet_url"
        )))
        contacts_sheet = _clean(_find_scalar(sources, ("contacts_sheet_url",)))
        outreach_sheet = _clean(_find_scalar(sources, ("outreach_sheet_url",)))
        ats_score = _find_scalar(sources, ("final_ats_score", "ats_resume_score", "ats_score"))
        n8n_status = _clean(_find_scalar(sources, ("n8n_status", "workflow_status", "run_status")))
        error_message = _clean(_find_scalar(sources, ("error_message", "writer_error")))

        contacts_raw = _find_list(sources, (
            "recruiter_contacts", "contacts", "contact_results", "recruiters", "recruiter_contacts_json"
        ))
        contacts: list[dict[str, Any]] = []
        for raw in contacts_raw[:20]:
            if not isinstance(raw, dict):
                continue
            contacts.append({
                "name": _clean(raw.get("name") or raw.get("contact_name") or raw.get("recruiter_name")),
                "title": _clean(raw.get("title") or raw.get("contact_title") or raw.get("recruiter_title")),
                "email": _clean(raw.get("email") or raw.get("contact_email")),
                "linkedin": _clean(raw.get("linkedin_url") or raw.get("linkedin") or raw.get("profile_url")),
                "why": _clean(raw.get("why_this_contact") or raw.get("relevance_reason")),
                "action": _clean(raw.get("suggested_action") or raw.get("next_action")),
            })

        if not contacts:
            names = _find_list(sources, ("recruiter_names", "recruiter_names_json"))
            linkedins = _find_list(sources, ("recruiter_linkedin_urls", "recruiter_linkedin_urls_json", "linkedin_urls"))
            for index, name in enumerate(names[:20]):
                contacts.append({
                    "name": _clean(name),
                    "title": "",
                    "email": "",
                    "linkedin": _clean(linkedins[index]) if index < len(linkedins) else "",
                    "why": "",
                    "action": "",
                })

        outreach_raw = _find_list(sources, (
            "outreach_drafts", "outreach", "drafts", "outreach_json"
        ))
        outreach: list[dict[str, Any]] = []
        for raw in outreach_raw[:20]:
            if not isinstance(raw, dict):
                continue
            outreach.append({
                "name": _clean(raw.get("contact_name") or raw.get("recipient_name") or raw.get("name")),
                "title": _clean(raw.get("contact_title") or raw.get("recipient_title")),
                "subject": _clean(raw.get("subject")),
                "message": _clean(raw.get("message") or raw.get("message_draft") or raw.get("draft")),
                "follow_up": _clean(raw.get("follow_up_message")),
                "linkedin": _clean(raw.get("linkedin_url")),
            })

        recruiter_found = str(_find_scalar(sources, ("recruiter_found", "contacts_found")) or "").casefold() in {"1", "true", "yes"}
        outreach_found = str(_find_scalar(sources, ("outreach_draft_created",)) or "").casefold() in {"1", "true", "yes"}

        return {
            "available": bool(sources),
            "resume_doc": resume_doc,
            "resume_pdf": resume_pdf,
            "resume_word": resume_word,
            "cover_doc": cover_doc,
            "cover_pdf": cover_pdf,
            "cover_word": cover_word,
            "sheet": sheet_url,
            "contacts_sheet": contacts_sheet,
            "outreach_sheet": outreach_sheet,
            "ats_score": ats_score,
            "n8n_status": n8n_status,
            "error": error_message,
            "contacts": contacts,
            "outreach": outreach,
            "recruiter_found": recruiter_found,
            "outreach_found": outreach_found,
        }

    favorite_setting_key = "dashboard_job_favorites_v1"
    favorite_raw = get_setting(favorite_setting_key, []) or []
    favorite_ids: set[int] = set()
    if isinstance(favorite_raw, list):
        for value in favorite_raw:
            try:
                favorite_ids.add(int(value))
            except Exception:
                continue

    def _persist_favorites(values: set[int]) -> None:
        save_setting(favorite_setting_key, sorted(int(value) for value in values))

    total_ranked = int(_scalar("SELECT COUNT(*) FROM jobs WHERE hunter_score IS NOT NULL", default=0) or 0)
    high_matches = int(_scalar("SELECT COUNT(*) FROM jobs WHERE hunter_score>=80", default=0) or 0)
    excellent_matches = int(_scalar("SELECT COUNT(*) FROM jobs WHERE hunter_score>=95", default=0) or 0)

    metrics = st.columns(4)
    metrics[0].metric("All ranked jobs", f"{total_ranked:,}")
    metrics[1].metric("Strong matches · 80+", f"{high_matches:,}")
    metrics[2].metric("Excellent matches · 95+", f"{excellent_matches:,}")
    metrics[3].metric("★ Favorites", f"{len(favorite_ids):,}")

    _section_heading(
        "Find a job",
        "No spreadsheet sorting required. Pick a view, search in plain language, and use the simple order menu.",
    )

    view_choice = st.radio(
        "Show me",
        ["Best matches (80+)", "★ Favorites", "Newest jobs", "Excellent matches (95+)", "Needs review", "All ranked jobs"],
        horizontal=True,
        key="rankings_easy_view_v13",
    )

    search_col, sort_col = st.columns([2.2, 1.0])
    with search_col:
        search_text = st.text_input(
            "Search",
            key="rankings_easy_search_v13",
            placeholder="Type a role, company, location, or industry",
        )
    with sort_col:
        sort_label = st.selectbox(
            "Order jobs by",
            ["Best match first", "Newest first", "Oldest first"],
            index=0 if view_choice != "Newest jobs" else 1,
            key="rankings_easy_sort_v13",
        )

    source_df = _query(
        "SELECT DISTINCT source AS value FROM jobs WHERE hunter_score IS NOT NULL AND trim(COALESCE(source,''))!='' ORDER BY lower(source)"
    )
    track_df = _query(
        "SELECT DISTINCT target_track AS value FROM jobs WHERE hunter_score IS NOT NULL AND trim(COALESCE(target_track,''))!='' ORDER BY lower(target_track)"
    )
    state_df = _query(
        "SELECT DISTINCT state AS value FROM jobs WHERE hunter_score IS NOT NULL AND trim(COALESCE(state,''))!='' ORDER BY state"
    )
    status_df = _query(
        "SELECT DISTINCT status AS value FROM jobs WHERE hunter_score IS NOT NULL AND trim(COALESCE(status,''))!='' ORDER BY lower(status)"
    )

    sources = source_df["value"].astype(str).tolist() if not source_df.empty else []
    tracks = track_df["value"].astype(str).tolist() if not track_df.empty else []
    states = state_df["value"].astype(str).tolist() if not state_df.empty else []
    statuses = status_df["value"].astype(str).tolist() if not status_df.empty else []

    with st.expander("More filters · optional", expanded=False):
        filter_a, filter_b = st.columns(2)
        with filter_a:
            role_tracks = st.multiselect("Role family", tracks, key="rankings_easy_tracks_v13", placeholder="All role families")
            selected_states = st.multiselect("State", states, key="rankings_easy_states_v13", placeholder="All states")
            work_modes = st.multiselect(
                "Work arrangement",
                ["Remote", "Hybrid", "On-site", "Not specified"],
                key="rankings_easy_work_v13",
                placeholder="All arrangements",
            )
        with filter_b:
            source_values = st.multiselect("Source", sources, key="rankings_easy_sources_v13", placeholder="All sources")
            selected_statuses = st.multiselect("Pipeline status", statuses, key="rankings_easy_status_v13", placeholder="All statuses")
            added_when = st.selectbox(
                "Added",
                ["Any time", "Today", "Last 7 days", "Last 30 days", "Custom range"],
                key="rankings_easy_added_v13",
            )
        custom_dates: Any = None
        if added_when == "Custom range":
            bounds = _query(
                "SELECT MIN(date(COALESCE(NULLIF(added_at,''),created_at))) AS min_date, MAX(date(COALESCE(NULLIF(added_at,''),created_at))) AS max_date FROM jobs WHERE hunter_score IS NOT NULL"
            )
            today = datetime.now().astimezone().date()
            try:
                minimum = datetime.fromisoformat(str(bounds.iloc[0].get("min_date") or today)).date()
            except Exception:
                minimum = today
            try:
                maximum = datetime.fromisoformat(str(bounds.iloc[0].get("max_date") or today)).date()
            except Exception:
                maximum = today
            custom_dates = st.date_input(
                "Custom added date range",
                value=(minimum, maximum),
                min_value=minimum,
                max_value=maximum,
                key="rankings_easy_custom_dates_v13",
            )
        page_size = st.selectbox("Jobs per page", [10, 20, 30], index=0, key="rankings_easy_page_size_v13")

    class_work_mode = (
        "CASE "
        "WHEN lower(COALESCE(remote_type,'')) LIKE '%hybrid%' THEN 'Hybrid' "
        "WHEN lower(COALESCE(remote_type,'')) LIKE '%remote%' OR lower(trim(COALESCE(remote_type,'')))='true' THEN 'Remote' "
        "WHEN lower(COALESCE(remote_type,'')) LIKE '%on-site%' OR lower(COALESCE(remote_type,'')) LIKE '%onsite%' THEN 'On-site' "
        "ELSE 'Not specified' END"
    )

    clauses = ["hunter_score IS NOT NULL"]
    params: list[Any] = []

    if view_choice == "Best matches (80+)":
        clauses.append("hunter_score>=80")
    elif view_choice == "★ Favorites":
        if favorite_ids:
            placeholders = ",".join("?" for _ in favorite_ids)
            clauses.append(f"id IN ({placeholders})")
            params.extend(sorted(favorite_ids))
        else:
            clauses.append("1=0")
    elif view_choice == "Excellent matches (95+)":
        clauses.append("hunter_score>=95")
    elif view_choice == "Needs review":
        clauses.append("(lower(status) LIKE '%review%' OR lower(status) IN ('held','failed','truth_review_required','ats_review_required'))")

    if search_text.strip():
        term = f"%{search_text.strip()}%"
        clauses.append(
            "(lower(title) LIKE lower(?) OR lower(company_name) LIKE lower(?) OR lower(COALESCE(location_raw,'')) LIKE lower(?) OR lower(COALESCE(industry,'')) LIKE lower(?))"
        )
        params.extend([term, term, term, term])

    def _add_in(column_sql: str, values: list[str]) -> None:
        if not values:
            return
        placeholders = ",".join("?" for _ in values)
        clauses.append(f"{column_sql} IN ({placeholders})")
        params.extend(values)

    _add_in("target_track", role_tracks)
    _add_in("state", selected_states)
    _add_in("source", source_values)
    _add_in("status", selected_statuses)
    _add_in(class_work_mode, work_modes)

    now_local = datetime.now().astimezone()
    if added_when == "Today":
        clauses.append("date(COALESCE(NULLIF(added_at,''),created_at))=date(?)")
        params.append(str(now_local.date()))
    elif added_when == "Last 7 days":
        clauses.append("date(COALESCE(NULLIF(added_at,''),created_at))>=date(?)")
        params.append(str((now_local - timedelta(days=6)).date()))
    elif added_when == "Last 30 days":
        clauses.append("date(COALESCE(NULLIF(added_at,''),created_at))>=date(?)")
        params.append(str((now_local - timedelta(days=29)).date()))
    elif added_when == "Custom range" and isinstance(custom_dates, (tuple, list)) and len(custom_dates) == 2:
        clauses.append("date(COALESCE(NULLIF(added_at,''),created_at)) BETWEEN date(?) AND date(?)")
        params.extend([str(custom_dates[0]), str(custom_dates[1])])

    where_sql = " AND ".join(clauses)
    sort_sql = {
        "Best match first": "hunter_score DESC, datetime(COALESCE(NULLIF(added_at,''),created_at)) DESC, id DESC",
        "Newest first": "datetime(COALESCE(NULLIF(added_at,''),created_at)) DESC, id DESC",
        "Oldest first": "datetime(COALESCE(NULLIF(added_at,''),created_at)) ASC, id ASC",
    }[sort_label]

    matched = int(_scalar(f"SELECT COUNT(*) FROM jobs WHERE {where_sql}", params, default=0) or 0)
    st.caption(f"{matched:,} jobs match what you selected.")
    if matched <= 0:
        _empty("No jobs match these filters. Try a broader search or choose All ranked jobs.")
        return

    page_count = max(1, (matched + int(page_size) - 1) // int(page_size))
    page_key = "rankings_easy_page_v13"
    current_page = int(st.session_state.get(page_key, 1) or 1)
    current_page = min(max(current_page, 1), page_count)
    st.session_state[page_key] = current_page
    offset = (current_page - 1) * int(page_size)

    jobs = _query(
        f"""
        SELECT
          id,
          hunter_score,
          title,
          company_name,
          COALESCE(NULLIF(location_raw,''),'Location not specified') AS location,
          {class_work_mode} AS work_mode,
          COALESCE(NULLIF(employment_type,''),'Not specified') AS employment_type,
          COALESCE(NULLIF(salary_raw,''),'Not specified') AS salary,
          COALESCE(NULLIF(target_track,''),'Unclassified') AS target_track,
          source,
          status,
          date_posted,
          COALESCE(NULLIF(added_at,''),created_at) AS added,
          apply_url,
          job_url,
          industry,
          telegram_sent,
          sent_to_n8n,
          already_applied
        FROM jobs
        WHERE {where_sql}
        ORDER BY {sort_sql}
        LIMIT ? OFFSET ?
        """,
        [*params, int(page_size), int(offset)],
    )

    nav_left, nav_mid, nav_right = st.columns([1, 2, 1])
    with nav_left:
        if st.button("← Previous", disabled=current_page <= 1, key="rankings_prev_v13"):
            st.session_state[page_key] = current_page - 1
            st.rerun()
    with nav_mid:
        st.markdown(f"<div style='text-align:center;padding-top:.45rem'><b>Page {current_page} of {page_count}</b></div>", unsafe_allow_html=True)
    with nav_right:
        if st.button("Next →", disabled=current_page >= page_count, key="rankings_next_v13"):
            st.session_state[page_key] = current_page + 1
            st.rerun()

    start_row = offset + 1
    end_row = min(offset + len(jobs), matched)
    st.caption(f"Showing jobs {start_row:,}–{end_row:,} of {matched:,}.")

        # AADIL_JOB_RANKINGS_N8N_STATE_UI_V1_7
    notice_v17 = st.session_state.pop("rankings_n8n_notice_v17", None)
    if isinstance(notice_v17, dict):
        notice_level = str(notice_v17.get("level") or "info").casefold()
        notice_text = str(
            notice_v17.get("message") or "n8n status updated."
        )
        if notice_level == "success":
            st.success(notice_text)
        elif notice_level == "warning":
            st.warning(notice_text)
        elif notice_level == "error":
            st.error(notice_text)
        else:
            st.info(notice_text)

    st.markdown(
        """
        <style>
        .munshi-card-v16 {
            border: 1px solid rgba(24, 48, 65, .10);
            border-radius: 22px;
            padding: 18px 18px 15px 18px;
            min-height: 250px;
            box-shadow: 0 8px 24px rgba(24, 48, 65, .055);
            transition: transform .16s ease, box-shadow .16s ease;
            display: flex;
            flex-direction: column;
            gap: 12px;
            overflow: hidden;
        }
        .munshi-card-v16:hover {
            transform: translateY(-2px);
            box-shadow: 0 14px 34px rgba(24, 48, 65, .09);
        }
        .munshi-card-top-v16 {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 12px;
        }
        .munshi-card-meta-v16 {
            color: #607184;
            font-size: .80rem;
            line-height: 1.45;
            min-width: 0;
        }
        .munshi-card-meta-v16 strong {
            color: #163247;
            font-weight: 750;
        }
        .munshi-card-score-v16 {
            width: 66px;
            height: 66px;
            min-width: 66px;
            border-radius: 50%;
            display: grid;
            place-items: center;
            box-shadow: inset 0 0 0 1px rgba(17, 61, 49, .08);
        }
        .munshi-card-score-inner-v16 {
            width: 51px;
            height: 51px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            background: rgba(255, 255, 255, .88);
            color: #123f34;
        }
        .munshi-card-score-inner-v16 strong {
            font-size: 1.02rem;
            line-height: 1;
        }
        .munshi-card-score-inner-v16 span {
            margin-top: 3px;
            font-size: .48rem;
            font-weight: 850;
            letter-spacing: .08em;
        }
        .munshi-card-role-v16 {
            color: #0f2c40;
            font-size: 1.13rem;
            font-weight: 780;
            line-height: 1.17;
            letter-spacing: -.017em;
            min-height: 2.35em;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .munshi-card-company-v16 {
            color: #1c374c;
            font-weight: 720;
            font-size: .91rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .munshi-card-sub-v16 {
            color: #788798;
            font-size: .73rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .munshi-card-pills-v16 {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: auto;
        }
        .munshi-pill-v16 {
            display: inline-block;
            max-width: 100%;
            padding: 5px 9px;
            border-radius: 999px;
            background: rgba(255, 255, 255, .72);
            border: 1px solid rgba(26, 52, 70, .10);
            color: #43596a;
            font-size: .66rem;
            font-weight: 700;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        div[data-testid="stVerticalBlock"]:has(.munshi-card-actions-v16) button,
        div[data-testid="stVerticalBlock"]:has(.munshi-card-actions-v16) a {
            border-radius: 999px !important;
            min-height: 2.55rem;
        }
        .munshi-card-actions-v16 {
            display: block;
            width: 0;
            height: 0;
            overflow: hidden;
        }
        div[data-testid="stDialog"] {
            background: rgba(8, 22, 32, .54) !important;
            backdrop-filter: blur(5px);
        }
        div[data-testid="stDialog"] [role="dialog"] {
            border-radius: 24px !important;
            box-shadow: 0 30px 80px rgba(0, 0, 0, .28) !important;
            max-width: min(1050px, calc(100vw - 48px)) !important;
            width: min(1050px, calc(100vw - 48px)) !important;
        }
        @media (max-width: 1050px) {
            .munshi-card-role-v16 { font-size: 1rem; }
            .munshi-card-score-v16 { width: 60px; height: 60px; min-width: 60px; }
            .munshi-card-score-inner-v16 { width: 46px; height: 46px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    @st.dialog("Job details")
    def _job_detail_dialog_v16(selected_job_id: int) -> None:
        detail_frame = _query("SELECT * FROM jobs WHERE id=?", [int(selected_job_id)])
        if detail_frame.empty:
            st.warning("This job record is no longer available.")
            return

        detail = detail_frame.iloc[0].to_dict()
        package = _package_for_job(int(selected_job_id))
        detail_score = float(detail.get("hunter_score") or 0)
        detail_title = _clean(detail.get("title")) or "Untitled job"
        detail_company = _clean(detail.get("company_name")) or "Company not recorded"

        top_left, top_right = st.columns([5, 1])
        with top_left:
            st.markdown(
                f"### {html.escape(detail_title)}",
                unsafe_allow_html=True,
            )
            st.caption(detail_company)
        with top_right:
            st.metric("Match", f"{detail_score:.0f}%")
            if st.button(
                "Close",
                key=f"rankings_close_dialog_v16_{selected_job_id}",
                use_container_width=True,
            ):
                st.session_state.pop("rankings_dialog_job_v16", None)
                st.rerun()

        action_cols = st.columns(3)
        apply_url = _clean(detail.get("apply_url"))
        job_url = _clean(detail.get("job_url"))

        with action_cols[0]:
            if _is_url(apply_url):
                st.link_button("Apply to this job", apply_url)
        with action_cols[1]:
            if _is_url(job_url):
                st.link_button("Original posting", job_url)
        with action_cols[2]:
            if int(detail.get("already_applied") or 0) == 1:
                st.success("✓ Marked applied")
            elif package.get("n8n_status"):
                st.info("✓ n8n package available")

        job_tab, resume_tab, contacts_tab, outreach_tab, score_tab = st.tabs(
            ["Job details", "Resume & documents", "Contacts", "Outreach", "Why this score"]
        )

        with job_tab:
            facts = [
                ("Location", detail.get("location_raw")),
                ("Work arrangement", detail.get("remote_type")),
                ("Employment type", detail.get("employment_type")),
                ("Salary", detail.get("salary_raw")),
                ("Industry", detail.get("industry")),
                ("Date posted", detail.get("date_posted")),
                ("Apply deadline", detail.get("apply_deadline")),
                ("Hours per week", detail.get("hours_per_week")),
                ("Source", detail.get("source")),
            ]
            left, right = st.columns(2)
            for index, (label, value) in enumerate(facts):
                value_text = _clean(value)
                if not value_text or value_text in {"Not specified", "[]"}:
                    continue
                with (left if index % 2 == 0 else right):
                    st.markdown(f"**{label}**")
                    st.write(value_text)

            description = _clean(detail.get("description_raw"))
            if description:
                st.markdown("#### Full job description")
                st.write(description)

            for heading, key in (
                ("Responsibilities", "responsibilities"),
                ("Qualifications", "qualifications"),
                ("Preferred qualifications", "preferred_qualifications"),
                ("Work authorization", "work_authorization"),
            ):
                text = _clean(detail.get(key))
                if text and text not in {"[]", "Not specified"}:
                    st.markdown(f"#### {heading}")
                    st.write(text)

        with resume_tab:
            if package.get("ats_score") not in (None, ""):
                try:
                    st.metric("Final ATS score", f"{float(package['ats_score']):.0f}")
                except Exception:
                    st.metric("Final ATS score", _clean(package.get("ats_score")))

            if package.get("n8n_status"):
                st.caption(
                    "Workflow result: "
                    + _clean(package.get("n8n_status")).replace("_", " ").title()
                )

            links = [
                ("Resume Google Doc", package.get("resume_doc")),
                ("Resume PDF", package.get("resume_pdf")),
                ("Resume Word", package.get("resume_word")),
                ("Cover letter", package.get("cover_doc")),
                ("Cover letter PDF", package.get("cover_pdf")),
                ("Cover letter Word", package.get("cover_word")),
                ("Main Google Sheet", package.get("sheet")),
            ]
            usable = [(label, url) for label, url in links if _is_url(url)]

            if usable:
                for start in range(0, len(usable), 3):
                    cols = st.columns(min(3, len(usable) - start))
                    for local_index, (label, url) in enumerate(usable[start:start + 3]):
                        with cols[local_index]:
                            st.link_button(label, url)
            else:
                st.info(
                    "No tailored resume/application documents are stored for this job yet. "
                    "They appear after the n8n application-package workflow completes."
                )

            if package.get("error"):
                st.warning(f"Latest workflow warning/error: {package['error']}")

        with contacts_tab:
            contacts = list(package.get("contacts") or [])
            if contacts:
                st.success(
                    f"{len(contacts)} contact{'s' if len(contacts) != 1 else ''} available."
                )
                for number, contact in enumerate(contacts, 1):
                    with st.container(border=True):
                        name = contact.get("name") or f"Contact {number}"
                        title_text = contact.get("title") or "Title not recorded"
                        st.markdown(f"#### {name}")
                        st.caption(title_text)
                        if contact.get("email"):
                            st.markdown(f"**Email:** {contact['email']}")
                        if _is_url(contact.get("linkedin")):
                            st.link_button(
                                f"Open {name} on LinkedIn",
                                contact["linkedin"],
                            )
                        if contact.get("why"):
                            st.write(contact["why"])
                        if contact.get("action"):
                            st.caption(f"Suggested action: {contact['action']}")
            elif package.get("recruiter_found"):
                st.warning(
                    "The workflow reported recruiter/contact data, but the latest "
                    "local result does not preserve the actual contact details."
                )
            else:
                st.info("No recruiter/contact records are stored for this job yet.")

            if _is_url(package.get("contacts_sheet")):
                st.link_button("Open Contacts Sheet", package["contacts_sheet"])

        with outreach_tab:
            drafts = list(package.get("outreach") or [])
            if drafts:
                st.success(
                    f"{len(drafts)} outreach draft{'s' if len(drafts) != 1 else ''} available."
                )
                for number, draft in enumerate(drafts, 1):
                    with st.container(border=True):
                        recipient = draft.get("name") or f"Draft {number}"
                        st.markdown(f"#### Message for {recipient}")
                        if draft.get("title"):
                            st.caption(draft["title"])
                        if draft.get("subject"):
                            st.markdown(f"**Subject:** {draft['subject']}")
                        if draft.get("message"):
                            st.write(draft["message"])
                        if draft.get("follow_up"):
                            st.markdown("**Follow-up**")
                            st.write(draft["follow_up"])
                        if _is_url(draft.get("linkedin")):
                            st.link_button(
                                f"Open {recipient} on LinkedIn",
                                draft["linkedin"],
                            )
            elif package.get("outreach_found"):
                st.warning(
                    "An outreach draft was reported, but its text is not stored "
                    "in the latest local result."
                )
            else:
                st.info("No outreach draft is stored for this job yet.")

            if _is_url(package.get("outreach_sheet")):
                st.link_button("Open Outreach Sheet", package["outreach_sheet"])

        with score_tab:
            st.markdown(f"### {detail_score:.0f}% Hunter match")
            if _clean(detail.get("match_label")):
                st.markdown(f"**Match label:** {_clean(detail.get('match_label'))}")
            if _clean(detail.get("target_track")):
                st.markdown(f"**Role family:** {_clean(detail.get('target_track'))}")
            st.markdown(
                f"**Ghost-risk score:** {float(detail.get('ghost_risk_score') or 0):.0f}"
            )
            breakdown = _json(detail.get("score_breakdown_json"), {})
            if isinstance(breakdown, dict) and breakdown:
                with st.expander("Technical score breakdown", expanded=False):
                    st.json(breakdown)

    gradients_v16 = (
        "linear-gradient(145deg,#fff4bd 0%,#fffdf3 76%,#ffffff 100%)",
        "linear-gradient(145deg,#eee5ff 0%,#fbf9ff 76%,#ffffff 100%)",
        "linear-gradient(145deg,#d9f8e9 0%,#f6fcf9 76%,#ffffff 100%)",
        "linear-gradient(145deg,#ffe7c5 0%,#fff9f1 76%,#ffffff 100%)",
        "linear-gradient(145deg,#e8e6ff 0%,#faf9ff 76%,#ffffff 100%)",
    )

    visible_job_ids_v17 = [
        int(value)
        for value in jobs["id"].tolist()
        if value is not None
    ]

    result_job_ids_v17: set[int] = set()
    latest_queue_v17: dict[int, str] = {}
    global_open_job_v17: int | None = None
    global_open_label_v17 = ""

    if visible_job_ids_v17:
        placeholders_v17 = ",".join("?" for _ in visible_job_ids_v17)

        result_frame_v17 = _query(
            f"""
            SELECT DISTINCT job_id
            FROM n8n_results
            WHERE job_id IN ({placeholders_v17})
            """,
            visible_job_ids_v17,
        )
        if not result_frame_v17.empty:
            result_job_ids_v17 = {
                int(value)
                for value in result_frame_v17["job_id"].tolist()
                if value is not None
            }

        queue_frame_v17 = _query(
            f"""
            SELECT q.job_id, q.queue_status
            FROM n8n_dispatch_queue q
            JOIN (
                SELECT job_id, MAX(id) AS max_id
                FROM n8n_dispatch_queue
                WHERE job_id IN ({placeholders_v17})
                GROUP BY job_id
            ) latest ON latest.max_id=q.id
            """,
            visible_job_ids_v17,
        )
        if not queue_frame_v17.empty:
            latest_queue_v17 = {
                int(record["job_id"]): str(
                    record.get("queue_status") or ""
                ).casefold()
                for record in queue_frame_v17.to_dict("records")
                if record.get("job_id") is not None
            }

    open_states_v17 = {
        "pending",
        "queued",
        "accepted",
        "dispatching",
        "dispatched",
        "running",
        "waiting",
        "processing",
    }
    open_placeholders_v17 = ",".join("?" for _ in open_states_v17)
    global_open_frame_v17 = _query(
        f"""
        SELECT q.job_id, q.queue_status, j.title, j.company_name
        FROM n8n_dispatch_queue q
        LEFT JOIN jobs j ON j.id=q.job_id
        WHERE lower(COALESCE(q.queue_status,'')) IN ({open_placeholders_v17})
        ORDER BY q.id DESC
        LIMIT 1
        """,
        sorted(open_states_v17),
    )
    if not global_open_frame_v17.empty:
        open_record_v17 = global_open_frame_v17.iloc[0].to_dict()
        global_open_job_v17 = int(open_record_v17["job_id"])
        open_title_v17 = (
            _clean(open_record_v17.get("title"))
            or f"Job #{global_open_job_v17}"
        )
        open_company_v17 = _clean(open_record_v17.get("company_name"))
        global_open_label_v17 = (
            f"{open_title_v17} · {open_company_v17}"
            if open_company_v17
            else open_title_v17
        )
        st.info(
            "n8n is currently processing "
            + global_open_label_v17
            + ". Other n8n actions are temporarily paused until this run finishes."
        )

    card_columns = None

    for card_index, (_, row) in enumerate(jobs.iterrows()):
        if card_index % 3 == 0:
            card_columns = st.columns(3, gap="medium")

        job_id = int(row["id"])
        score = float(row.get("hunter_score") or 0)
        score_int = max(0, min(100, int(round(score))))
        score_angle = score_int * 3.6

        role = _clean(row.get("title")) or "Untitled job"
        company = _clean(row.get("company_name")) or "Company not recorded"
        location = _clean(row.get("location")) or "Location not specified"
        work_mode = _clean(row.get("work_mode")) or "Not specified"
        employment = _clean(row.get("employment_type")) or "Not specified"
        source = _clean(row.get("source")) or "Unknown source"
        salary = _clean(row.get("salary"))
        added = (
            format_local_short(row.get("added"))
            if _clean(row.get("added"))
            else "Date not recorded"
        )

        tone = card_index % len(gradients_v16)
        background = gradients_v16[tone]

        pill_values = [work_mode, employment]
        if salary and salary != "Not specified":
            pill_values.append(salary[:38])

        pills_html = "".join(
            f'<span class="munshi-pill-v16">{html.escape(value)}</span>'
            for value in pill_values
            if value
        )

        card_html = (
            f'<div class="munshi-card-v16" style="background:{background};">'
            f'<div class="munshi-card-top-v16">'
            f'<div class="munshi-card-meta-v16">'
            f'<strong>{html.escape(location)}</strong><br>{html.escape(added)}'
            f'</div>'
            f'<div class="munshi-card-score-v16" '
            f'style="background:conic-gradient(#153f34 {score_angle:.1f}deg,rgba(21,63,52,.12) 0deg);">'
            f'<div class="munshi-card-score-inner-v16">'
            f'<strong>{score_int}%</strong><span>MATCH</span>'
            f'</div></div></div>'
            f'<div class="munshi-card-role-v16">{html.escape(role)}</div>'
            f'<div class="munshi-card-company-v16">{html.escape(company)}</div>'
            f'<div class="munshi-card-sub-v16">{html.escape(source)}</div>'
            f'<div class="munshi-card-pills-v16">{pills_html}</div>'
            f'</div>'
        )

        with card_columns[card_index % 3]:
            st.markdown(card_html, unsafe_allow_html=True)
            st.markdown(
                '<span class="munshi-card-actions-v16"></span>',
                unsafe_allow_html=True,
            )

            is_favorite = job_id in favorite_ids
            apply_url = _clean(row.get("apply_url"))

            package_ready_v17 = job_id in result_job_ids_v17
            queue_state_v17 = latest_queue_v17.get(job_id, "")
            starting_job_v17 = st.session_state.get(
                "rankings_n8n_starting_job_v17"
            )

            if package_ready_v17:
                n8n_card_state_v17 = "ready"
                if starting_job_v17 == job_id:
                    st.session_state.pop("rankings_n8n_starting_job_v17", None)
            elif queue_state_v17 in open_states_v17:
                n8n_card_state_v17 = "running"
            elif starting_job_v17 == job_id:
                n8n_card_state_v17 = "starting"
            elif global_open_job_v17 is not None and global_open_job_v17 != job_id:
                n8n_card_state_v17 = "busy"
            elif queue_state_v17 == "failed":
                n8n_card_state_v17 = "failed"
                if starting_job_v17 == job_id:
                    st.session_state.pop("rankings_n8n_starting_job_v17", None)
            elif queue_state_v17 == "completed" and not package_ready_v17:
                n8n_card_state_v17 = "check"
            else:
                n8n_card_state_v17 = "available"

            actions = st.columns([0.62, 1.15, 1.0, 1.0])

            with actions[0]:
                if st.button(
                    "★" if is_favorite else "☆",
                    key=f"rankings_favorite_v16_{job_id}",
                    help="Remove from favorites" if is_favorite else "Save to favorites",
                    use_container_width=True,
                ):
                    if is_favorite:
                        favorite_ids.discard(job_id)
                    else:
                        favorite_ids.add(job_id)
                    _persist_favorites(favorite_ids)
                    st.rerun()

            with actions[1]:
                if st.button(
                    "Details",
                    key=f"rankings_details_v16_{job_id}",
                    use_container_width=True,
                ):
                    st.session_state["rankings_dialog_job_v16"] = job_id
                    st.rerun()

            with actions[2]:
                if _is_url(apply_url):
                    st.link_button("Apply", apply_url)
                else:
                    st.button(
                        "Apply",
                        key=f"rankings_apply_disabled_v16_{job_id}",
                        disabled=True,
                        use_container_width=True,
                    )

            with actions[3]:
                if n8n_card_state_v17 == "ready":
                    st.button(
                        "✓ Ready",
                        key=f"rankings_n8n_ready_v17_{job_id}",
                        disabled=True,
                        help="Completed n8n package is stored and available in Details.",
                        use_container_width=True,
                    )
                elif n8n_card_state_v17 == "running":
                    st.button(
                        "Running…",
                        key=f"rankings_n8n_running_v17_{job_id}",
                        disabled=True,
                        help="n8n is actively processing this job.",
                        use_container_width=True,
                    )
                elif n8n_card_state_v17 == "starting":
                    st.button(
                        "Starting…",
                        key=f"rankings_n8n_starting_v17_{job_id}",
                        disabled=True,
                        help="The guarded worker is starting the production run.",
                        use_container_width=True,
                    )
                elif n8n_card_state_v17 == "busy":
                    st.button(
                        "n8n busy",
                        key=f"rankings_n8n_busy_v17_{job_id}",
                        disabled=True,
                        help="Another application package is running.",
                        use_container_width=True,
                    )
                elif n8n_card_state_v17 == "check":
                    st.button(
                        "Check result",
                        key=f"rankings_n8n_check_v17_{job_id}",
                        disabled=True,
                        help="Queue completed but the local result is not visible yet.",
                        use_container_width=True,
                    )
                else:
                    run_label_v17 = (
                        "Retry n8n"
                        if n8n_card_state_v17 == "failed"
                        else "Run n8n"
                    )
                    if st.button(
                        run_label_v17,
                        key=f"rankings_run_n8n_v17_{job_id}",
                        type="primary",
                        use_container_width=True,
                    ):
                        try:
                            from app.stored_job_n8n_worker import start_stored_job_run

                            start_result = start_stored_job_run(
                                job_id,
                                actor="dashboard",
                            )
                            message = str(
                                start_result.get("message")
                                or "n8n request processed."
                            )
                            if bool(start_result.get("success")) and bool(
                                start_result.get("started")
                            ):
                                st.session_state["rankings_n8n_starting_job_v17"] = job_id
                                level_v17 = "success"
                            elif bool(start_result.get("success")):
                                level_v17 = "info"
                            else:
                                level_v17 = "warning"
                            st.session_state["rankings_n8n_notice_v17"] = {
                                "level": level_v17,
                                "message": message,
                                "job_id": job_id,
                            }
                            st.rerun()
                        except Exception as error:
                            st.session_state["rankings_n8n_notice_v17"] = {
                                "level": "error",
                                "message": "Could not start the guarded n8n worker: " + str(error),
                                "job_id": job_id,
                            }
                            st.rerun()

    selected_dialog_job = st.session_state.get("rankings_dialog_job_v16")
    if selected_dialog_job is not None:
        _job_detail_dialog_v16(int(selected_dialog_job))

    nav_left, nav_mid, nav_right = st.columns([1, 2, 1])
    with nav_left:
        if st.button("← Previous page", disabled=current_page <= 1, key="rankings_bottom_prev_v13"):
            st.session_state[page_key] = current_page - 1
            st.rerun()
    with nav_mid:
        st.markdown(f"<div style='text-align:center;padding-top:.45rem'><b>Page {current_page} of {page_count}</b></div>", unsafe_allow_html=True)
    with nav_right:
        if st.button("Next page →", disabled=current_page >= page_count, key="rankings_bottom_next_v13"):
            st.session_state[page_key] = current_page + 1
            st.rerun()

def _query_performance() -> None:
    _page_intro("Yield intelligence", "Query performance", "Turn raw request telemetry into comparable query, provider, yield, latency, and error intelligence.")
    data = _query(
        """
        SELECT query_name AS Query, COALESCE(provider,source_name) AS Provider,
               COALESCE(role_family,'') AS "Role family", SUM(request_count) AS Requests,
               SUM(raw_count) AS Raw, SUM(normalized_count) AS Normalized,
               SUM(duplicate_count) AS Duplicate, SUM(eligible_count) AS Eligible,
               CASE WHEN SUM(normalized_count)>0 THEN ROUND(100.0*SUM(eligible_count)/SUM(normalized_count),1) END AS "Eligible %",
               SUM(new_eligible_count) AS "New eligible", SUM(telegram_count) AS Telegram,
               SUM(error_count) AS Errors, ROUND(SUM(COALESCE(duration_ms,0)),0) AS "Runtime ms",
               MAX(measured_at) AS "Last measured"
        FROM query_performance GROUP BY query_name, provider, source_name, role_family
        ORDER BY "New eligible" DESC, "Eligible %" DESC, Raw DESC
        """
    )
    if data.empty:
        _empty("No canonical query-performance samples exist yet. Controlled maintenance prevents misleading historical telemetry from being relabeled as canonical yield.")
        return
    total_requests = int(data["Requests"].sum())
    total_raw = int(data["Raw"].sum())
    total_eligible = int(data["Eligible"].sum())
    total_errors = int(data["Errors"].sum())
    runtime_per_request = float(data["Runtime ms"].sum() / total_requests) if total_requests else 0
    _metric_row(
        [
            ("Queries measured", len(data), None),
            ("Requests", f"{total_requests:,}", None),
            ("Jobs returned", f"{total_raw:,}", None),
            ("Eligible", f"{total_eligible:,}", None),
            ("Average request latency", f"{runtime_per_request:,.0f} ms" if total_requests else "Not available", None),
            ("Errors", f"{total_errors:,}", None),
        ]
    )
    _section_heading("Measured query combinations", "Sorted by new eligible yield, then eligible rate and raw volume.")
    visible = data[["Query", "Provider", "Role family", "Requests", "Raw", "Eligible", "Eligible %", "New eligible", "Errors", "Runtime ms", "Last measured"]]
    visible = _localize_columns(visible, ["Last measured"])
    st.dataframe(visible, hide_index=True, width="stretch", height=560)
    _time_caption()
    st.caption("Adaptive weighting should be based on repeated representative samples, not one smoke run.")


def _queue_actions() -> None:
    _page_intro("Delivery control", "Queue / actions", "Understand what is ready, waiting, dispatched, completed, failed, or held for review.")
    telegram = _query(
        "SELECT delivery_state AS Status, COUNT(*) AS Jobs FROM telegram_delivery_claims GROUP BY delivery_state ORDER BY Jobs DESC"
    )
    queue = _query(
        "SELECT queue_status AS Status, COUNT(*) AS Jobs FROM n8n_dispatch_queue GROUP BY queue_status ORDER BY Jobs DESC"
    )
    progress = _query(
        "SELECT run_status AS Status, COUNT(*) AS Jobs FROM telegram_n8n_progress GROUP BY run_status ORDER BY Jobs DESC"
    )
    queue_states = {str(row["Status"]): int(row["Jobs"]) for _, row in queue.iterrows()}
    telegram_states = {str(row["Status"]): int(row["Jobs"]) for _, row in telegram.iterrows()}
    progress_states = {str(row["Status"]): int(row["Jobs"]) for _, row in progress.iterrows()}
    _metric_row(
        [
            ("Ready", queue_states.get("pending", 0), None),
            ("Waiting", sum(queue_states.get(key, 0) for key in ("reserved", "sending", "waiting")), None),
            ("Review required", sum(telegram_states.get(key, 0) for key in ("reserved", "uncertain")), None),
            ("Dispatched", sum(queue_states.get(key, 0) for key in ("sent", "dispatched")), None),
            ("Failed", queue_states.get("failed", 0) + progress_states.get("failed", 0), None),
            ("Completed", queue_states.get("completed", 0) + progress_states.get("completed", 0), None),
        ]
    )
    telegram_tab, downstream_tab, n8n_tab = st.tabs(["Telegram delivery", "Downstream dispatch", "n8n progress"])
    with telegram_tab:
        st.dataframe(telegram, hide_index=True, width="stretch") if not telegram.empty else _empty("No claimed Telegram deliveries.")
    with downstream_tab:
        st.dataframe(queue, hide_index=True, width="stretch") if not queue.empty else _empty("No downstream queue records.")
    with n8n_tab:
        st.dataframe(progress, hide_index=True, width="stretch") if not progress.empty else _empty("No tracked downstream executions.")
    st.info("n8n is governed by controlled engineering and change-control protections. This page reads local queue and execution evidence; it does not mutate the workflow.", icon="🛡️")
    uncertain = _query(
        """
        SELECT claim.job_id AS "Job ID", j.title AS Title, j.company_name AS Company,
               claim.delivery_state AS State, claim.error_type AS "Error type",
               claim.updated_at AS Updated
        FROM telegram_delivery_claims claim JOIN jobs j ON j.id=claim.job_id
        WHERE claim.delivery_state IN ('reserved','uncertain')
        ORDER BY claim.updated_at DESC
        """
    )
    _section_heading("Deliveries needing review", "Uncertain claims are held and protected from automatic duplicate delivery.")
    if not uncertain.empty:
        st.dataframe(_localize_columns(uncertain, ["Updated"]), hide_index=True, width="stretch")
    else:
        _empty("No uncertain Telegram deliveries. Duplicate-send guard is clear.")
    recent = _query(
        """
        SELECT q.id, j.title AS Title, j.company_name AS Company, q.dispatch_mode AS Mode,
               q.queue_status AS Status, q.attempt_count AS Attempts,
               q.queued_at AS Queued, q.completed_at AS Completed,
               substr(COALESCE(q.last_error,''),1,180) AS Error
        FROM n8n_dispatch_queue q JOIN jobs j ON j.id=q.job_id
        ORDER BY q.updated_at DESC LIMIT 200
        """
    )
    _section_heading("Recent queue activity", "Latest downstream state transitions and failure evidence.")
    if not recent.empty:
        st.dataframe(_localize_columns(recent, ["Queued", "Completed"]), hide_index=True, width="stretch")
    else:
        _empty("Queue is empty.")
    _time_caption()


def _database_health() -> tuple[str, int]:
    connection = get_connection()
    try:
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        return quick, foreign
    finally:
        connection.close()


def _credentials() -> None:
    # AADIL_USAJOBS_DASHBOARD_CREDENTIALS_POST_V18_V19
    from app.credentials_page import render_credentials_page
    render_credentials_page()


def _system_diagnostics() -> None:
    _page_intro("Runtime assurance", "System / diagnostics", "Service reachability, database integrity, scheduler policy, recent control state, and protected orchestration evidence.")
    integration = get_setting("integration_health", {}) or {}
    services = dict(integration.get("services") or {})
    quick, foreign = _database_health()
    records: list[dict[str, Any]] = []
    records.append({"Component": "Hunter SQLite", "State": "healthy" if quick == "ok" and foreign == 0 else "failed", "Evidence": f"quick_check={quick}; foreign_key_violations={foreign}"})
    for name, item in services.items():
        state = "healthy" if _service_up(str(item.get("host")), int(item.get("port", 0))) else "failed"
        service_label = {"fastapi": "FastAPI", "streamlit": "Streamlit", "n8n": "n8n"}.get(str(name).casefold(), str(name))
        label = f"{service_label} (observed endpoint)" if item.get("read_only") else service_label
        records.append({"Component": label, "State": state, "Evidence": f"{item.get('host')}:{item.get('port')}"})
    heartbeat = ROOT_DIR / str(integration.get("telegram_heartbeat_path") or "")
    if heartbeat.is_file():
        age = max(0, datetime.now(timezone.utc).timestamp() - heartbeat.stat().st_mtime)
        records.append({"Component": "Telegram listener", "State": "healthy" if age < 180 else "stale", "Evidence": f"heartbeat age {age:.0f}s"})
    else:
        records.append({"Component": "Telegram listener", "State": "stale", "Evidence": "configured heartbeat not found"})
    st.dataframe(pd.DataFrame(records), hide_index=True, width="stretch")

    _section_heading(
        "Runtime recovery",
        "One ownership-aware controller restores missing components while preserving healthy services, queues, schedules, and singleton worker safety.",
    )
    try:
        recovery = _runtime_recovery_snapshot()
    except Exception as error:
        recovery = {}
        st.warning(
            f"The recovery health model could not be read ({type(error).__name__}). No recovery action was taken."
        )
    components = dict(recovery.get("components") or {})
    last_recovery = dict(recovery.get("last_recovery") or {})
    worker = dict(recovery.get("source_worker") or {})
    healthy_core = sum(
        1 for item in components.values() if item.get("kind") == "core" and item.get("healthy")
    )
    total_core = sum(1 for item in components.values() if item.get("kind") == "core")
    recovery_state = str(last_recovery.get("result") or "No recovery recorded")
    _metric_row(
        [
            ("Core services", f"{healthy_core}/{total_core} healthy" if total_core else "Not available", "Canonical process and health ownership."),
            ("Last recovery", format_local_clock(last_recovery.get("completed_at"), empty="No recovery recorded"), str(last_recovery.get("trigger") or "Waiting for first recovery event").replace("_", " ").title()),
            ("Recovery state", recovery_state, "Recovery never resets source policy, targeting, schedules, delivery claims, or queues."),
            ("Active source worker", "None" if worker.get("state") in {None, "idle"} else str(worker.get("display_state") or "Not available"), f"PID {worker.get('pid')}" if worker.get("pid") else "Serialized worker lane is available."),
        ]
    )
    if components:
        recovery_rows = [
            {
                "Component": {
                    "n8n": "n8n",
                    "fastapi": "FastAPI",
                    "telegram": "Telegram",
                    "randomized_scheduler": "Randomized scheduler",
                    "hourly_coordinator": "Hourly coordinator",
                    "streamlit": "Streamlit",
                }.get(str(name), str(name).replace("_", " ").title()),
                "State": item.get("state") or "Not available",
                "Ownership": "Canonical LaunchAgent" if item.get("launchd_loaded") else "Not yet canonical",
                "Evidence": item.get("message") or "No additional evidence",
            }
            for name, item in components.items()
        ]
        st.dataframe(pd.DataFrame(recovery_rows), hide_index=True, width="stretch")
    st.caption(
        "Safe recovery is idempotent: healthy services continue running; only missing or stale project-owned components are repaired."
    )
    if st.button("Run safe recovery", type="primary", width="stretch"):
        with st.spinner("Inspecting canonical ownership and recovering only missing services…"):
            result = subprocess.run(
                [str(ROOT_DIR / "bin" / "munshi-safe-restart"), "recover", "--json"],
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                timeout=240,
                check=False,
            )
        _runtime_recovery_snapshot.clear()
        if result.returncode == 0:
            st.success("Safe recovery completed. Healthy services were preserved and missing ownership was repaired.")
            st.rerun()
        else:
            st.error("Recovery stopped safely because the runtime requires attention. No broad process kill or state reset was performed.")
            st.code((result.stderr or result.stdout or "No diagnostic output.")[-3000:])
    orchestration = get_setting("orchestration", {}) or {}
    scheduler_tick = _scalar("SELECT MAX(updated_at) FROM source_random_schedule", default=None)
    latest_adapter = _query(
        "SELECT source_name,COALESCE(completed_at,started_at) occurred_at,run_status FROM source_runs ORDER BY datetime(COALESCE(completed_at,started_at)) DESC LIMIT 1"
    )
    next_due = _query(
        """
        SELECT source_name,next_run_at FROM source_runtime_truth_v1
        WHERE enabled=1 AND next_run_at IS NOT NULL AND schedule_state NOT IN ('running','disabled')
        ORDER BY datetime(next_run_at) LIMIT 1
        """
    )
    coordinator_at = _scalar("SELECT MAX(created_at) FROM events WHERE event_type='unified_hourly_coordinator_run'", default=None)
    last_adapter_label = "Last adapter" if latest_adapter.empty else f"Last adapter · {latest_adapter.iloc[0]['source_name']}"
    last_adapter_value = "No run yet" if latest_adapter.empty else format_local_clock(latest_adapter.iloc[0]["occurred_at"], empty="Time unavailable")
    next_due_label = "Next adapter due" if next_due.empty else f"Next due · {next_due.iloc[0]['source_name']}"
    next_due_value = "Waiting for schedule" if next_due.empty else format_local_clock(next_due.iloc[0]["next_run_at"])
    _section_heading("Automation timeline", "Scheduler checks, provider completion, and provider due times are distinct signals.")
    _metric_row(
        [
            (
                "Source scheduling",
                "Randomized" if orchestration.get("strategy") == "randomized_source_runner" else humanize_machine_value(orchestration.get("strategy")),
                "One eligible provider per scheduler cycle." if orchestration.get("strategy") == "randomized_source_runner" else None,
            ),
            ("Maintenance", "ON" if orchestration.get("maintenance_mode") else "OFF", None),
            ("Worker lane", "Single source" if int(orchestration.get("max_parallel_source_workers") or 0) == 1 else str(orchestration.get("max_parallel_source_workers") or "Not configured"), "Singleton protection remains intact."),
            ("Overlap allowed", "Yes" if orchestration.get("allow_overlapping_source_cycles") else "No", None),
            ("Last scheduler state update", format_local_clock(scheduler_tick), None),
            (last_adapter_label, last_adapter_value, None),
            (next_due_label, next_due_value, None),
            ("Hourly coordinator", format_local_clock(coordinator_at), None),
        ]
    )
    _time_caption()
    notifications = dict(get_setting("source_run_notifications", {}) or {})
    try:
        from app.telegram_run_visibility import operational_summary_health

        summary_health = operational_summary_health()
    except Exception:
        summary_health = {
            "enabled": bool(notifications.get("enabled")),
            "generated": 0,
            "delivered": 0,
            "terminal_generated": 0,
            "terminal_delivered": 0,
            "incident_story_generated": 0,
            "pending": 0,
            "retrying": 0,
            "uncertain": 0,
            "latest": None,
            "latest_sent": None,
        }
    st.markdown("#### Telegram operational summaries")
    st.caption(
        "Every real adapter attempt receives one durable terminal summary. Job-opportunity cards remain a separate delivery stream; empty scheduler polls stay silent."
    )
    latest = dict(summary_health.get("latest") or {})
    latest_sent = dict(summary_health.get("latest_sent") or {})
    _metric_row(
        [
            ("Adapter run summaries", "Enabled" if summary_health.get("enabled") else "Configuration required", "Required operational visibility policy."),
            ("Terminal summaries", f"{int(summary_health.get('terminal_generated') or 0):,}", "One per committed adapter run; separate from job cards."),
            ("Terminal summaries delivered", f"{int(summary_health.get('terminal_delivered') or 0):,}", None),
            ("Incident / recovery cards", f"{int(summary_health.get('incident_story_generated') or 0):,}", "Delayed-run and resolved-incident visibility."),
            ("All operational cards delivered", f"{int(summary_health.get('delivered') or 0):,}", None),
            ("Pending operational cards", f"{int(summary_health.get('pending') or 0):,}", None),
            ("Failed / retrying", f"{int(summary_health.get('retrying') or 0):,}", None),
            ("Uncertain delivery", f"{int(summary_health.get('uncertain') or 0):,}", "Protected from blind replay to avoid duplicate messages."),
            (f"Last summary event · {latest.get('source_name') or 'None yet'}", format_local_clock(latest.get("created_at"), empty="Waiting for first event"), humanize_machine_value(latest.get("delivery_state"))),
            ("Last successful Telegram send", format_local_clock(latest_sent.get("sent_at"), empty="Waiting for first delivery"), None),
        ]
    )
    st.caption(
        "Persistent identity is one adapter summary per source run. Telegram outages retain pending cards with bounded retry; recovery never regenerates historical cards."
    )
    snapshot = dict(integration.get("n8n_read_only_snapshot") or {})
    _section_heading("n8n integrity baseline", "Stored workflow identity and hashes support controlled engineering under change-control protections.")
    st.markdown('<span class="status-pill warn">CONTROLLED MUTATION · explicit engineering authority required</span>', unsafe_allow_html=True)
    with st.expander("Advanced n8n integrity evidence", expanded=False):
        st.caption("Technical workflow identifiers and immutable comparison hashes.")
        st.json({
            "workflow_id": snapshot.get("workflow_id"), "active": snapshot.get("active"),
            "active_version_id": snapshot.get("active_version_id"),
            "nodes_sha256": snapshot.get("nodes_sha256"),
            "connections_sha256": snapshot.get("connections_sha256"),
            "database_quick_check": snapshot.get("database_quick_check"),
            "checked_at_system_local": format_local(snapshot.get("checked_at")),
            "change_control": "controlled engineering; explicit authorization required",
        })
    st.caption(f"Hunter database: {DB_PATH} · secrets are never displayed.")


def _storage() -> None:
    _page_intro("Storage governance", "Storage", "Project-only storage, retained recovery scope, classified cleanup, and measured reclamation without personal-file risk.")
    latest = _query("SELECT * FROM storage_metrics ORDER BY measured_at DESC LIMIT 1")
    if latest.empty:
        _empty("No storage measurement exists.")
        return
    row = latest.iloc[0]
    _metric_row(
        [
            ("Mac free space", _format_bytes(row["disk_free_bytes"]), None),
            ("MUNSHI Apply project", _format_bytes(row["project_bytes"]), None),
            ("Runtime", _format_bytes(row["runtime_bytes"]), None),
            ("Retained backups", _format_bytes(row["backup_bytes"]), None),
            ("Diagnostics", _format_bytes(row["diagnostic_bytes"]), None),
            ("Reclaimed", _format_bytes(row["reclaimed_bytes"]), None),
        ]
    )
    st.caption(f"Measured {_format_when(row['measured_at'])}. Personal content is outside the cleanup engine's allowed roots. Times shown in system local time · {timezone_label()}.")
    largest = _query(
        """
        SELECT path AS Path, size_bytes AS Bytes, backup_type AS Type,
               verified AS Verified, retained_reason AS "Retained reason"
        FROM backup_inventory ORDER BY size_bytes DESC LIMIT 20
        """
    )
    if not largest.empty:
        largest["Size"] = largest["Bytes"].map(_format_bytes)
        st.markdown("#### Largest retained recovery artifacts")
        st.dataframe(largest.drop(columns=["Bytes"]), hide_index=True, width="stretch")
    st.markdown("#### Safe cleanup engine")
    st.info("The cleanup engine is restricted to configured project backup roots and protected-path rules. It cannot target arbitrary Documents, Desktop, Downloads, photos, or credentials.", icon="🧹")
    left, right = st.columns(2)
    if left.button("Scan for verified redundancy", width="stretch"):
        result = subprocess.run(
            [sys.executable, str(ROOT_DIR / "scripts" / "cleanup_redundant_backups.py")],
            cwd=ROOT_DIR, text=True, capture_output=True, timeout=180,
        )
        st.code((result.stdout or result.stderr or "No candidates found.")[-6000:])
    confirmed = right.checkbox("I reviewed the scan and want SAFE_DELETE candidates removed")
    if right.button("Run classified cleanup", disabled=not confirmed, width="stretch"):
        result = subprocess.run(
            [sys.executable, str(ROOT_DIR / "scripts" / "cleanup_redundant_backups.py"), "--execute"],
            cwd=ROOT_DIR, text=True, capture_output=True, timeout=600,
        )
        if result.returncode == 0:
            st.success("Classified cleanup completed. Refresh storage metrics after the next audit migration.")
        else:
            st.error("Cleanup stopped without a successful result.")
        st.code((result.stdout or result.stderr or "No output.")[-6000:])


def _backups() -> None:
    _page_intro("Recovery portfolio", "Backups", "A compact verified rollback set with exact hashes, restore scope, and explicit retention reasons.")
    data = _query(
        """
        SELECT created_at AS Date, path AS Path, size_bytes AS Bytes, sha256 AS SHA256,
               backup_type AS Type, verified AS Verified, restore_scope AS "Restore scope",
               retained_reason AS "Retained reason", recorded_at AS "Indexed"
        FROM backup_inventory ORDER BY created_at DESC
        """
    )
    if data.empty:
        _empty("No retained backups are indexed.")
        return
    data["Size"] = data["Bytes"].map(_format_bytes)
    data = _localize_columns(data, ["Date", "Indexed"])
    st.dataframe(data.drop(columns=["Bytes"]), hide_index=True, width="stretch", height=580)
    verified = int(data["Verified"].sum())
    _metric_row(
        [("Indexed", len(data), None), ("Verified", verified, None), ("Total retained", _format_bytes(data["Bytes"].sum()), None)]
    )
    st.caption(f"Retention keeps current, previous known-good, and materially distinct milestone recovery scope. Times shown in system local time · {timezone_label()}.")


def render() -> None:
    _apply_styles()
    pages = {
        "Overview": _overview,
        "Historical Intelligence": _historical_intelligence,
        "Source Health": _source_health,
        "Adapter Coverage": _adapter_coverage,
        "Targeting": _targeting,
        "Job Explorer": _job_explorer,
        "Job Rankings": _job_rankings,
        "Query Performance": _query_performance,
        "Queue / Actions": _queue_actions,
        "Credentials": _credentials,
        "System / Diagnostics": _system_diagnostics,
        "Storage": _storage,
        "Backups": _backups,
    }
    groups = {
        "Overview": ["Overview", "Historical Intelligence"],
        "Discovery": ["Source Health", "Adapter Coverage", "Job Explorer", "Job Rankings", "Query Performance"],
        "Policy": ["Targeting"],
        "Automation": ["Queue / Actions"],
        "Integrations": ["Credentials"],
        "System": ["System / Diagnostics", "Storage", "Backups"],
    }
    if st.session_state.get("active_dashboard_page") not in pages:
        st.session_state["active_dashboard_page"] = "Overview"
    selected = str(st.session_state["active_dashboard_page"])
    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-brand"><div class="sidebar-brand-row">
              <span class="sidebar-mark">M</span><div><div class="sidebar-title">MUNSHI APPLY</div>
              <div class="sidebar-subtitle">Executive Intelligence</div></div>
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        for group, labels in groups.items():
            st.markdown(f'<div class="nav-group">{group}</div>', unsafe_allow_html=True)
            for label in labels:
                st.button(
                    label,
                    key=f"nav_{label.casefold().replace(' ', '_').replace('/', '_')}",
                    type="primary" if selected == label else "secondary",
                    width="stretch",
                    on_click=_select_dashboard_page,
                    args=(label,),
                )
        orchestration = get_setting("orchestration", {}) or {}
        targeting = get_setting("targeting", {}) or {}
        eligibility = targeting.get("eligibility") if isinstance(targeting.get("eligibility"), dict) else {}
        st.markdown('<div class="sidebar-foot">', unsafe_allow_html=True)
        _status_pill("Targeting", f"{targeting.get('mode') or 'Unconfigured'} · {eligibility.get('label') or 'Unconfigured geography'}")
        _status_pill("Scheduler", "maintenance" if orchestration.get("maintenance_mode") else "active")
        st.markdown('<span class="status-pill good">n8n · change controlled</span>', unsafe_allow_html=True)
        st.markdown("<p>Canonical authority: SQLite<br>Policy changes are versioned.</p></div>", unsafe_allow_html=True)
    _header(compact=selected != "Overview")
    pages[selected]()
