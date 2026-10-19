"""Session-preserving navigation using native Streamlit callbacks.

Query URLs remain shareable. No JavaScript event delegation or app reload is
needed. The current entrypoint is resolved for both production and isolated QA.
"""
import streamlit as st

from app.product_experience_vnext import ALLOWED_VIEWS, PRIMARY_DESTINATIONS


ICONS = {
    "dashboard": ":material/home:", "jobs": ":material/search:",
    "prepare-application": ":material/assignment:", "auto-prepare": ":material/tune:",
    "tracker": ":material/bar_chart:",
}


def navigate(view: str) -> None:
    if view not in ALLOWED_VIEWS:
        raise ValueError("Unknown MUNSHI route")
    st.session_state["product_view"] = view
    st.query_params.from_dict({"view": view})


def _link(view: str, label: str, current: str, *, mobile: bool = False) -> None:
    state = "selected" if view == current else "idle"
    with st.container(key=f"product_route_{'mobile' if mobile else 'desktop'}_{view}_{state}"):
        st.button(
            label=label, key=f"route_{'mobile' if mobile else 'desktop'}_{view}",
            icon=ICONS.get(view) if mobile else None,
            on_click=navigate, args=(view,), use_container_width=True,
            type="primary" if current == view else "tertiary",
        )


def render_navigation(current: str, destinations) -> None:
    with st.container(key="product_primary_routes", horizontal=True, gap="small"):
        for route, label in (*destinations, ("settings", "Settings")):
            _link(route, label, current)
    with st.container(key="product_mobile_routes", horizontal=True, gap="small"):
        for route, label in PRIMARY_DESTINATIONS:
            _link(route, label, current, mobile=True)
