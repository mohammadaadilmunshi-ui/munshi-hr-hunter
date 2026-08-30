from __future__ import annotations

import pandas as pd
import streamlit as st

from app.database import get_connection


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
        "consecutive_failures",
        "last_http_status",
        "raw_jobs_last_run",
        "jobs_found_last_run",
        "eligible_jobs_last_run",
        "inserted_jobs_last_run",
        "duplicate_jobs_last_run",
        "rejected_jobs_last_run",
        "provider_used_last_run",
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
    record["category"] = classify(
        str(row.get("health_status") or ""),
        int(row.get("consecutive_failures") or 0),
        str(row.get("last_error") or ""),
    )
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
metric_columns = st.columns(4)

metric_columns[0].metric(
    "Enabled adapters",
    len(enabled),
)
metric_columns[1].metric(
    "Healthy",
    int(counts.get("Healthy", 0)),
)
metric_columns[2].metric(
    "Awaiting first run",
    int(counts.get("Awaiting first run", 0)),
)
metric_columns[3].metric(
    "Setup + runtime issues",
    int(counts.get("Setup required", 0))
    + int(counts.get("Runtime failure", 0)),
)

for category in (
    "Runtime failure",
    "Setup required",
    "Awaiting first run",
    "Healthy",
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
            "Runtime failure",
            "Setup required",
        ),
    ):
        preferred = [
            "source_name",
            "health_status",
            "consecutive_failures",
            "last_http_status",
            "raw_jobs_last_run",
            "jobs_found_last_run",
            "eligible_jobs_last_run",
            "inserted_jobs_last_run",
            "duplicate_jobs_last_run",
            "provider_used_last_run",
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
