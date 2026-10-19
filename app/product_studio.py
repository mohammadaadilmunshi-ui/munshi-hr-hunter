"""MUNSHI presentation system. No workers, credentials or execution authority.

CSS is packaged locally and cached as an immutable source asset. Live operational
state deliberately never enters this cache.
"""
from __future__ import annotations

from functools import lru_cache
import base64
from pathlib import Path

import streamlit as st

from app.product_ui import esc
from app.product_experience_vnext import product_href


@lru_cache(maxsize=1)
def studio_css() -> str:
    assets = Path(__file__).parent / "assets"
    faces = []
    for name, unicode_range in (
        ("latin-ext", "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF"),
        ("latin", "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"),
    ):
        encoded = base64.b64encode((assets / "fonts" / f"manrope-{name}-variable.woff2").read_bytes()).decode("ascii")
        faces.append(
            '@font-face{font-family:"Manrope";font-style:normal;font-weight:400 800;'
            'font-display:swap;src:url(data:font/woff2;base64,' + encoded + ') format("woff2");'
            'unicode-range:' + unicode_range + ';}'
        )
    return "<style>" + "".join(faces) + (assets / "product_studio.css").read_text() + "</style>"


def inject_studio_css() -> None:
    st.markdown(studio_css(), unsafe_allow_html=True)


@lru_cache(maxsize=1)
def shell_css() -> str:
    """One ordered stylesheet payload prevents partially applied themes."""
    from app.product_ui import product_css
    from app.product_v22 import v22_css
    from app.job_card_interaction_hotfix_v2 import interaction_css
    from app.product_experience_vnext import presentation_css

    return product_css() + v22_css() + interaction_css() + presentation_css() + studio_css()


def dashboard_intro() -> None:
    """Navigation only: shortcuts never queue preparation or imply a receipt."""
    def icon(paths: str) -> str:
        return ('<span class="studio-shortcut-icon" aria-hidden="true">'
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
                'stroke-linecap="round" stroke-linejoin="round">' + paths + '</svg></span>')

    st.markdown(
        '<section class="studio-command" aria-labelledby="command-title">'
        '<div class="studio-command-copy"><span class="studio-context">Your career workspace</span>'
        '<h1 id="command-title">Your next move,<br>with confidence.</h1>'
        '<p>Find the right role. Build a stronger application. '
        'Keep every step in view.</p></div>'
        '<nav class="studio-shortcuts" aria-label="Workspace shortcuts">'
        '<a href="?view=jobs" target="_self">' + icon('<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>') +
        '<span><strong>Find your next role</strong><small>Explore ranked opportunities</small></span></a>'
        '<a href="?view=resume-studio" target="_self">' + icon('<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/>') +
        '<span><strong>Make your experience count</strong><small>Open Resume Studio</small></span></a>'
        '<a href="?view=prepare-application" target="_self">' + icon('<rect x="3" y="6" width="18" height="15" rx="2"/><path d="M8 6V3h8v3M8 13l3 3 5-5"/>') +
        '<span><strong>Move applications forward</strong><small>Review packages and next steps</small></span></a>'
        '</nav></section>', unsafe_allow_html=True,
    )


def workspace_location(label: str) -> str:
    return '<div class="studio-location">Workspace <span aria-hidden="true">/</span> <strong>' + esc(label) + '</strong></div>'


def dashboard_snapshot() -> tuple[dict[str, int], list[dict]]:
    """Read the same lifecycle projection once for counters and recent activity.

    No cached operational state. Legacy n8n package history remains in Tracker;
    it cannot be used to decide whether the current Application Center is empty.
    """
    from app.product_application_flow_v2 import pipeline_rows
    from app.dashboard_operational_metrics_v1 import summarize_pipeline

    rows = pipeline_rows(limit=None)
    summary = summarize_pipeline([
        row["_flow_projection"] for row in rows
        if isinstance(row.get("_flow_projection"), dict)
    ])
    recent = sorted(rows, key=lambda row: str(row.get("updated_at") or ""), reverse=True)[:8]
    return summary, recent


def recent_activity_markup(rows: list[dict]) -> str:
    items = []
    for row in rows:
        href = product_href("prepare-application", job_id=int(row["job_id"]))
        items.append(
            '<li><a href="' + esc(href) + '" target="_self">'
            '<span class="studio-activity-role"><strong>' + esc(row.get("title"), "Untitled role") + '</strong>'
            '<span>' + esc(row.get("company_name"), "Company not recorded") + '</span></span>'
            '<span class="studio-activity-state">' + esc(row.get("display_status"), "Status not recorded") + '</span>'
            '<span class="studio-activity-time">' + esc(row.get("updated_label"), "Update time not recorded") + '</span>'
            '</a></li>'
        )
    return '<ol class="studio-activity" aria-label="Recent application activity">' + "".join(items) + '</ol>'


@st.fragment
def engine_status_panel() -> None:
    """Read engine observations only on request; opening refreshes this fragment.

    A collapsed Streamlit expander normally still executes its Python body.
    State tracking makes this a real lazy boundary, without caching connection
    truth or re-running job-card projections when the panel is toggled.
    """
    panel = st.expander(
        "Application engines · MUNSHI Native & Relay", expanded=False,
        key="studio_engine_status_open", on_change="rerun",
    )
    if panel.open:
        with panel:
            from app.application_provider_ui_v1 import render_dashboard_summary

            render_dashboard_summary()
