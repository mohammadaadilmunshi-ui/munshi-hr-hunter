"""Session-preserving navigation using Streamlit's own page-link router.

Query URLs remain shareable. No JavaScript event delegation or app reload is
needed. The current entrypoint is resolved for both production and isolated QA.
"""
from pathlib import Path

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

from app.product_experience_vnext import PRIMARY_DESTINATIONS


ICONS = {
    "dashboard": ":material/home:", "jobs": ":material/search:",
    "prepare-application": ":material/assignment:", "auto-prepare": ":material/tune:",
    "tracker": ":material/bar_chart:",
}


def _link(view: str, label: str, current: str, *, mobile: bool = False) -> None:
    context = get_script_run_ctx()
    if context is None:
        return
    state = "selected" if view == current else "idle"
    with st.container(key=f"product_route_{'mobile' if mobile else 'desktop'}_{view}_{state}"):
        st.page_link(
            Path(context.main_script_path).name, label=label,
            icon=ICONS.get(view) if mobile else None,
            query_params={"view": view}, use_container_width=True,
        )


def render_navigation(current: str, destinations) -> None:
    with st.container(key="product_primary_routes", horizontal=True, gap="small"):
        for route, label in (*destinations, ("settings", "Settings")):
            _link(route, label, current)
    with st.container(key="product_mobile_routes", horizontal=True, gap="small"):
        for route, label in PRIMARY_DESTINATIONS:
            _link(route, label, current, mobile=True)
