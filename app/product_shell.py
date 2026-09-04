'''Top-level product shell and query-param router.'''
from __future__ import annotations

from typing import Callable

import streamlit as st

from app.product_state import valid_view, volume_policy
from app.product_ui import esc, inject_css


NAVIGATION = (
    ("dashboard", "Dashboard"),
    ("jobs", "Browse jobs"),
    ("auto-prepare", "Auto Prepare"),
    ("tracker", "Tracker"),
    ("profile", "Profile"),
    ("research", "Research"),
)

TRACKER_TABS = {
    "pipeline": "Pipeline",
    "inbox": "Inbox",
}

PROFILE_TABS = {
    "resume": "Resume",
    "cover-letter": "Cover letters",
    "details": "Profile details",
}

SETTINGS_SECTIONS = {
    "apply": "Apply",
    "automation": "Automation",
    "integrations": "Integrations",
    "profile": "Profile & defaults",
    "credentials": "Credentials",
    "advanced": "Advanced / System",
}


def _query_view() -> str:
    try:
        raw = st.query_params.get("view") or st.session_state.get(
            "product_view", "dashboard"
        )
    except Exception:
        raw = st.session_state.get("product_view", "dashboard")
    return valid_view(raw)


def _nav_link(
    route: str,
    label: str,
    current: str,
    *,
    mobile: bool = False,
) -> str:
    active = route == current
    css_class = "mobile-nav-link" if mobile else "product-nav-link"
    aria = ' aria-current="page"' if active else ""
    active_class = " active" if active else ""
    return (
        f'<a class="{css_class}{active_class}" href="?view={route}"'
        f'{aria}>{esc(label)}</a>'
    )


def _resolved_subroute_state(
    view: str,
    *,
    tab: str = "",
    section: str = "",
) -> tuple[str, str] | None:
    '''Resolve a validated deep-link value into Streamlit session state.'''
    view = valid_view(view)
    tab = str(tab or "").strip().casefold()
    section = str(section or "").strip().casefold()

    if view == "tracker" and tab in TRACKER_TABS:
        return ("product_tracker_tab", TRACKER_TABS[tab])

    if view == "profile" and tab in PROFILE_TABS:
        return ("product_profile_tab", PROFILE_TABS[tab])

    if view == "settings" and section in SETTINGS_SECTIONS:
        return ("product_settings_section", SETTINGS_SECTIONS[section])

    return None


def _apply_deep_link_state(view: str) -> None:
    '''Apply valid product subroutes before their widgets are created.'''
    try:
        tab = str(st.query_params.get("tab", ""))
        section = str(st.query_params.get("section", ""))
    except Exception:
        return

    resolved = _resolved_subroute_state(view, tab=tab, section=section)
    if resolved is None:
        return

    key, value = resolved
    st.session_state[key] = value


def render() -> None:
    inject_css()

    view = _query_view()
    st.session_state["product_view"] = view
    _apply_deep_link_state(view)

    policy = volume_policy()
    status = {
        "unlimited": "Automation: Unlimited",
        "custom_limit": f"Custom target: {policy.get('daily_limit')}",
        "paused": "Automation: Paused",
        "pause_after_batch": "Pausing after batch",
    }[policy["mode"]]

    desktop_links = "".join(
        _nav_link(route, label, view)
        for route, label in NAVIGATION
    )
    mobile_links = "".join(
        _nav_link(route, label, view, mobile=True)
        for route, label in (*NAVIGATION, ("settings", "Settings"))
    )
    settings_link = _nav_link("settings", "Settings", view)

    with st.container(key="product_top_bar"):
        st.markdown(
            f'''<header class="product-header">
                <a class="brand" href="?view=dashboard" aria-label="MUNSHI dashboard">
                    <span class="brand-mark" aria-hidden="true">M</span>
                    <span>MUNSHI</span>
                </a>
                <nav class="product-nav" aria-label="Primary navigation">
                    {desktop_links}
                </nav>
                <div class="product-header-actions">
                    {settings_link}
                    <span class="status-pill-product">{esc(status)}</span>
                </div>
                <details class="mobile-nav">
                    <summary aria-label="Open navigation">Menu</summary>
                    <nav aria-label="Mobile navigation">{mobile_links}</nav>
                </details>
            </header>''',
            unsafe_allow_html=True,
        )

    from app import product_pages

    pages: dict[str, Callable[[], None]] = {
        "dashboard": product_pages.dashboard,
        "jobs": product_pages.browse_jobs,
        "auto-prepare": product_pages.auto_prepare,
        "tracker": product_pages.tracker,
        "profile": product_pages.profile,
        "research": product_pages.research,
        "settings": product_pages.settings,
    }
    pages[view]()
