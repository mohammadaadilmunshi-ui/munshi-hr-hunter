"""Selectable provider-throughput telemetry for Advanced / System.

This is intentionally different from canonical jobs stored. The user-facing
"Jobs fetched" metric is the large raw provider throughput number: SUM(raw_count)
from durable source_runs during the selected time window. Repeated observations
may therefore be represented, exactly like the existing cumulative provider
telemetry. No discovery, targeting, queue, n8n, or submission state is mutated.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app.database import get_connection


_WINDOW_OPTIONS = {
    "Past 1 hour": 1,
    "Past 6 hours": 6,
    "Past 12 hours": 12,
    "Past 24 hours": 24,
    "Past 3 days": 72,
    "Past 7 days": 168,
    "Past 30 days": 720,
    "All time": 0,
}


def provider_window_metrics(hours: int) -> dict[str, int]:
    """Aggregate durable source-run throughput inside a UTC-relative window."""
    window = max(0, int(hours or 0))
    where = ""
    params: tuple[str, ...] = ()
    if window:
        where = (
            "WHERE datetime(COALESCE(completed_at, started_at)) "
            ">= datetime('now', ?)"
        )
        params = (f"-{window} hours",)

    connection = get_connection()
    try:
        row = connection.execute(
            f"""SELECT COUNT(*) AS runs,
                       COALESCE(SUM(raw_count), 0) AS fetched,
                       COALESCE(SUM(normalized_count), 0) AS normalized,
                       COALESCE(SUM(eligible_count), 0) AS eligible,
                       COALESCE(SUM(new_eligible_count), 0) AS new_eligible
                  FROM source_runs
                  {where}""",
            params,
        ).fetchone()
        if row is None:
            return {
                "runs": 0,
                "fetched": 0,
                "normalized": 0,
                "eligible": 0,
                "new_eligible": 0,
            }
        keys = ("runs", "fetched", "normalized", "eligible", "new_eligible")
        try:
            return {key: int(row[key] or 0) for key in keys}
        except (TypeError, IndexError):
            return {key: int(row[index] or 0) for index, key in enumerate(keys)}
    finally:
        connection.close()


def _window_phrase(hours: int) -> str:
    if hours == 0:
        return "across all recorded source runs"
    if hours == 1:
        return "during the past hour"
    if hours < 24:
        return f"during the past {hours} hours"
    days = hours // 24
    return f"during the past {days} day{'s' if days != 1 else ''}"


def _render_metric_cards(product_v22: Any, metrics: dict[str, int], hours: int) -> None:
    phrase = _window_phrase(hours)
    stats = [
        (
            "Jobs fetched",
            f"{metrics['fetched']:,}",
            f"Raw provider records fetched {phrase}. This is the large throughput number and can include repeated provider observations.",
        ),
        (
            "Normalized records",
            f"{metrics['normalized']:,}",
            f"Provider records successfully normalized {phrase}.",
        ),
        (
            "Eligible telemetry",
            f"{metrics['eligible']:,}",
            f"Source-run records counted eligible {phrase}.",
        ),
        (
            "New eligible",
            f"{metrics['new_eligible']:,}",
            f"New eligible records reported by source runs {phrase}.",
        ),
        (
            "Source runs",
            f"{metrics['runs']:,}",
            f"Durable discovery source runs recorded {phrase}.",
        ),
    ]
    for start in range(0, len(stats), 3):
        columns = st.columns(3, gap="medium")
        for column, stat in zip(columns, stats[start:start + 3]):
            with column:
                product_v22._stat_card(*stat)


def install_provider_telemetry_window(pages_module: Any) -> None:
    """Replace only the earlier canonical window card with raw provider telemetry."""
    from app import product_v22

    if getattr(product_v22, "_provider_telemetry_window_installed", False):
        return

    # career_os_quality_patch_v1 stores the real V2.2 Advanced renderer before
    # adding its first interpretation of the custom counter. Bypass that wrapper
    # so the active UI shows provider throughput instead of canonical jobs stored.
    original = getattr(
        product_v22,
        "_career_os_original_advanced_v22",
        product_v22.advanced_v22,
    )
    product_v22._provider_window_original_advanced_v22 = original

    def advanced_with_provider_window() -> None:
        st.markdown("### Custom extraction window")
        selector, explanation = st.columns((1.1, 2.9), gap="medium")
        with selector:
            selected = st.selectbox(
                "Extraction window",
                list(_WINDOW_OPTIONS),
                index=list(_WINDOW_OPTIONS).index("Past 24 hours"),
                key="product_provider_telemetry_window_v1",
            )
        with explanation:
            st.caption(
                "This view measures raw discovery throughput from source runs, not deduplicated jobs stored. Choose a period to see the big fetched/normalized/eligible counts for that window."
            )

        hours = _WINDOW_OPTIONS[selected]
        metrics = provider_window_metrics(hours)
        _render_metric_cards(product_v22, metrics, hours)
        st.caption(
            "Jobs fetched is intentionally allowed to be much larger than Jobs stored because providers can return the same or overlapping opportunities across runs."
        )
        original()

    product_v22.advanced_v22 = advanced_with_provider_window
    pages_module._advanced = advanced_with_provider_window
    product_v22._provider_telemetry_window_installed = True
