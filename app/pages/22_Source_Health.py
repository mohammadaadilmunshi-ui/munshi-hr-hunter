from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app.database import get_connection, get_setting
from app.source_ui_readiness_v1 import displayed_readiness, source_funnel


st.set_page_config(
    page_title="Source Health",
    page_icon="🩺",
    layout="wide",
)

st.markdown(
    """
    <style>
    div[data-testid="stMetric"] {
        min-height: 7.5rem;
        padding: 0.9rem 1rem;
    }

    div[data-testid="stMetricLabel"],
    div[data-testid="stMetricLabel"] p,
    div[data-testid="stMetricDelta"],
    div[data-testid="stMetricDelta"] div {
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: unset !important;
        line-height: 1.2 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🩺 Source Health")
st.caption(
    "Runtime failures, setup requirements, pending first runs, "
    "raw yield, eligible yield, new jobs, duplicates, providers, "
    "and randomized schedules."
)

if st.button(
    "Refresh source health",
    use_container_width=True,
):
    st.cache_data.clear()
    st.rerun()


def classify(
    status: str,
    failures: int,
    error: str,
) -> str:
    normalized = (
        status or "not_tested"
    ).strip().lower()
    combined = (
        f"{normalized} {error or ''}"
    ).lower()

    if normalized in {
        "configuration_required",
        "needs_credentials",
        "source_not_configured",
        "board_configuration_required",
    }:
        return "Setup required"

    if normalized in {
        "enabled_pending_first_run",
        "pending_first_run",
        "not_tested",
        "not_tested_direct",
        "scheduled_not_live_tested",
        "installed_pending_first_run",
        "ready",
    }:
        return "Awaiting first run"

    if failures > 0 or any(
        term in combined
        for term in (
            "failed",
            "unhealthy",
            "degraded",
            "blocked",
            "rate_limited",
            "rate limited",
            "worker_missing",
            "exception",
            "traceback",
            "timeout",
            "timed out",
            "captcha",
        )
    ):
        return "Runtime failure"

    if (
        normalized.startswith("healthy")
        or normalized.startswith("completed")
        or normalized.startswith("success")
        or normalized
        in {
            "cooldown",
            "zero_yield",
            "zero_eligible",
            "duplicate_only",
            "enabled",
            "idle",
        }
    ):
        return "Healthy"

    return "Other"


connection = get_connection()

try:
    health_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(source_health)"
        ).fetchall()
    }
    desired = [
        "source_name",
        "source_tier",
        "enabled",
        "cadence_minutes",
        "health_status",
        "cost_mode",
        "normalized_jobs_last_run",
        "reject_role_last_run",
        "reject_location_last_run",
        "reject_hard_requirement_last_run",
        "consecutive_failures",
        "last_http_status",
        "raw_jobs_last_run",
        "jobs_found_last_run",
        "eligible_jobs_last_run",
        "inserted_jobs_last_run",
        "duplicate_jobs_last_run",
        "rejected_jobs_last_run",
        "provider_used_last_run",
        "filter_summary_json",
        "last_run_at",
        "last_success_at",
        "last_failure_at",
        "last_error",
    ]
    selected = [
        name for name in desired
        if name in health_columns
    ]
    order_expression = (
        "source_tier, source_name"
        if "source_tier" in health_columns
        else "source_name"
    )
    health_rows = [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT {", ".join(selected)}
            FROM source_health
            ORDER BY {order_expression}
            """
        ).fetchall()
    ]

    tables = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    }
    registry = get_setting("source_worker_registry", {}) or {}
    for source in health_rows:
        presentation, reason = displayed_readiness(connection, source, registry)
        source["readiness_status"] = presentation
        source["readiness_reason"] = reason
        source.update({"funnel_" + k: v for k, v in source_funnel(source).items()})
    schedule_map = {}

    if "source_random_schedule" in tables:
        schedule_columns = {
            str(row[1])
            for row in connection.execute(
                """
                PRAGMA table_info(
                    source_random_schedule
                )
                """
            ).fetchall()
        }
        schedule_desired = [
            "source_name",
            "next_run_at",
            "schedule_state",
            "schedule_reason",
            "last_worker_status",
            "last_worker_returncode",
        ]
        schedule_selected = [
            name
            for name in schedule_desired
            if name in schedule_columns
        ]

        if (
            "source_name" in schedule_selected
            and len(schedule_selected) > 1
        ):
            schedule_map = {
                str(row["source_name"]): dict(row)
                for row in connection.execute(
                    f"""
                    SELECT
                        {", ".join(schedule_selected)}
                    FROM source_random_schedule
                    """
                ).fetchall()
            }

finally:
    connection.close()

records = []

for row in health_rows:
    schedule = schedule_map.get(
        str(row.get("source_name")),
        {},
    )
    record = dict(row)
    try:
        filter_summary = json.loads(
            str(row.get("filter_summary_json") or "{}")
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        filter_summary = {}
    if "linkedin" in str(row.get("source_name") or "").casefold():
        record["linkedin_working_link_pct"] = filter_summary.get(
            "linkedin_working_link_pct"
        )
        record["linkedin_quarantined_linkless_count"] = filter_summary.get(
            "linkedin_quarantined_linkless_count"
        )
    else:
        record["linkedin_working_link_pct"] = None
        record["linkedin_quarantined_linkless_count"] = None
    record["category"] = str(row.get("readiness_status") or "Other")
    record.update(
        {
            "next_run_at": schedule.get("next_run_at"),
            "schedule_state": schedule.get(
                "schedule_state"
            ),
            "schedule_reason": schedule.get(
                "schedule_reason"
            ),
            "last_worker_status": schedule.get(
                "last_worker_status"
            ),
            "last_worker_returncode": schedule.get(
                "last_worker_returncode"
            ),
        }
    )
    records.append(record)

frame = pd.DataFrame(records)

if frame.empty:
    st.warning("No source-health rows were found.")
    st.stop()

enabled = frame[
    frame["enabled"].fillna(0).astype(int) == 1
].copy()

counts = enabled["category"].value_counts().to_dict()
metric_columns = st.columns(5)

metric_columns[0].metric(
    "Enabled adapters",
    len(enabled),
)
metric_columns[1].metric(
    "Healthy",
    int(counts.get("Working", 0)),
)
metric_columns[2].metric(
    "Awaiting first run",
    int(counts.get("Awaiting first run", 0)),
)
metric_columns[3].metric(
    "Needs implementation / board",
    int(counts.get("Not implemented", 0))
    + int(counts.get("Needs employer board", 0)),
)
metric_columns[4].metric(
    "Degraded / rate limited",
    int(counts.get("Degraded / failing", 0))
    + int(counts.get("Rate limited", 0)),
)

for category in (
    "Not implemented",
    "Needs employer board",
    "Blocked by cost policy",
    "Rate limited",
    "Degraded / failing",
    "Setup required",
    "Awaiting first run",
    "Working",
    "Other",
):
    subset = enabled[
        enabled["category"] == category
    ]

    if subset.empty:
        continue

    with st.expander(
        f"{category} · {len(subset)}",
        expanded=category
        in (
            "Not implemented",
            "Needs employer board",
            "Rate limited",
            "Degraded / failing",
            "Setup required",
        ),
    ):
        preferred = [
            "source_name",
            "health_status",
            "consecutive_failures",
            "last_http_status",
            "raw_jobs_last_run",
            "funnel_normalized",
            "funnel_eligible",
            "funnel_inserted",
            "funnel_eligible_rate_pct",
            "funnel_rejected_role",
            "funnel_rejected_location",
            "funnel_rejected_hard_requirement",
            "readiness_reason",
            "jobs_found_last_run",
            "eligible_jobs_last_run",
            "inserted_jobs_last_run",
            "duplicate_jobs_last_run",
            "provider_used_last_run",
            "linkedin_working_link_pct",
            "linkedin_quarantined_linkless_count",
            "last_run_at",
            "next_run_at",
            "schedule_state",
            "last_error",
        ]
        visible = [
            name
            for name in preferred
            if name in subset.columns
        ]

        st.dataframe(
            subset[visible],
            use_container_width=True,
            hide_index=True,
        )

with st.expander(
    f"Disabled adapters · {len(frame) - len(enabled)}",
    expanded=False,
):
    disabled = frame[
        frame["enabled"].fillna(0).astype(int) != 1
    ]
    visible = [
        name
        for name in (
            "source_name",
            "health_status",
            "cadence_minutes",
            "last_error",
        )
        if name in disabled.columns
    ]
    st.dataframe(
        disabled[visible],
        use_container_width=True,
        hide_index=True,
    )

# AADIL_SOURCE_RUNTIME_PANEL_V1
from app.source_runtime_state_v1 import render_compact_streamlit_panel as _aadil_render_runtime_panel_v1
_aadil_render_runtime_panel_v1()
