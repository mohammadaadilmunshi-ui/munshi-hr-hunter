"""Top-level product shell and query-param router."""
from __future__ import annotations

from typing import Callable

import streamlit as st

from app.product_state import valid_view, volume_policy
from app.product_ui import inject_css


NAVIGATION = (("dashboard", "Dashboard"), ("jobs", "Browse jobs"), ("auto-prepare", "Auto Prepare"), ("tracker", "Tracker"), ("profile", "Profile"), ("research", "Research"))


def _query_view() -> str:
    try:
        raw = st.query_params.get("view", "dashboard")
    except Exception:
        raw = st.session_state.get("product_view", "dashboard")
    return valid_view(raw)


def _navigate(view: str) -> None:
    view = valid_view(view)
    st.session_state["product_view"] = view
    try:
        st.query_params["view"] = view
    except Exception:
        pass


def render() -> None:
    inject_css()
    view = _query_view()
    st.session_state["product_view"] = view
    try:
        tab = str(st.query_params.get("tab", "")).strip().casefold()
        section = str(st.query_params.get("section", "")).strip().casefold()
    except Exception:
        tab = section = ""
    # Set subroute state before a widget is constructed. This keeps refresh,
    # browser back/forward, and shareable product deep links predictable.
    if view == "tracker" and tab in {"pipeline", "inbox"}:
        st.session_state["product_tracker_tab"] = tab.title()
    if view == "profile" and tab in {"resume", "cover-letter", "details"}:
        st.session_state["product_profile_tab"] = {"resume": "Resume", "cover-letter": "Cover letters", "details": "Profile details"}[tab]
    if view == "settings" and section:
        aliases = {"apply": "Application preferences", "integrations": "Email integrations", "advanced": "System / Advanced"}
        st.session_state["product_settings_section"] = aliases.get(section, "Application preferences")
    policy = volume_policy()
    status = {"unlimited": "Automation: Unlimited", "custom_limit": f"Custom target: {policy.get('daily_limit')}", "paused": "Automation: Paused", "pause_after_batch": "Pausing after batch"}[policy["mode"]]
    with st.container(key="product_top_bar"):
        left, nav, settings, state = st.columns((1.1, 5.8, .65, 1.15), gap="small")
        with left:
            st.markdown('<div class="brand"><span class="brand-mark">M</span>MUNSHI</div>', unsafe_allow_html=True)
        with nav:
            cols = st.columns(len(NAVIGATION), gap="small")
            for column, (route, label) in zip(cols, NAVIGATION):
                with column:
                    st.markdown('<div class="nav-button active">' if view == route else '<div class="nav-button">', unsafe_allow_html=True)
                    if st.button(label, key=f"product_nav_{route}", use_container_width=True):
                        _navigate(route); st.rerun()
                    st.markdown("</div>", unsafe_allow_html=True)
        with settings:
            st.markdown('<div class="nav-button active">' if view == "settings" else '<div class="nav-button">', unsafe_allow_html=True)
            if st.button("Settings", key="product_nav_settings", use_container_width=True):
                _navigate("settings"); st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        with state:
            st.markdown(f'<span class="status-pill-product">{status}</span>', unsafe_allow_html=True)
    from app import product_pages
    pages: dict[str, Callable[[], None]] = {"dashboard": product_pages.dashboard, "jobs": product_pages.browse_jobs, "auto-prepare": product_pages.auto_prepare, "tracker": product_pages.tracker, "profile": product_pages.profile, "research": product_pages.research, "settings": product_pages.settings}
    pages[view]()
