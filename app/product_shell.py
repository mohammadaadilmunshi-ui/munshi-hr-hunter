'''Top-level product shell and query-param router.'''
from __future__ import annotations

from typing import Callable

import streamlit as st

from app.product_state import valid_view, volume_policy
from app.product_ui import esc, inject_css
from app.product_v22 import brand_logo_data_uri, inject_v22_css, install_product_v22
from app.staging_feature_policy import activate_isolated_staging_preparation_features


RESUME_STUDIO_VIEW = "resume-studio"
PREPARE_APPLICATION_VIEW = "prepare-application"
_SPECIAL_VIEWS = {RESUME_STUDIO_VIEW, PREPARE_APPLICATION_VIEW}

NAVIGATION = (
    ("dashboard", "Dashboard"),
    ("jobs", "Browse jobs"),
    (PREPARE_APPLICATION_VIEW, "Prepare Application"),
    ("auto-prepare", "Auto Prepare"),
    ("tracker", "Tracker"),
    ("profile", "Profile"),
    (RESUME_STUDIO_VIEW, "Resume Studio"),
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
    normalized = str(raw or "").strip().casefold()
    if normalized in _SPECIAL_VIEWS:
        return normalized
    return valid_view(normalized)


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
        f'<a class="{css_class}{active_class}" href="?view={route}" '
        f'target="_self"{aria}>{esc(label)}</a>'
    )


def _resolved_subroute_state(
    view: str,
    *,
    tab: str = "",
    section: str = "",
) -> tuple[str, str] | None:
    '''Resolve a validated deep-link value into Streamlit session state.'''
    if view in _SPECIAL_VIEWS:
        return None
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
    # The staging runtime already proves cloud-shadow isolation before serving the
    # dashboard. Promote only preparation-only Phase 5–7 gates there; production
    # continues to require its existing explicit environment contract.
    activate_isolated_staging_preparation_features()

    inject_css()
    inject_v22_css()

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
    logo = brand_logo_data_uri()

    with st.container(key="product_top_bar"):
        st.markdown(
            f'''<header class="product-header">
                <a class="brand" href="?view=dashboard" target="_self" aria-label="MUNSHI dashboard">
                    <span class="brand-mark" aria-hidden="true"><img src="{logo}" alt=""></span>
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

    # Preserve this established import line verbatim: profile route contract tests
    # and downstream integrations use it as a compatibility marker.
    from app import product_pages, profile_workspace_v1, resume_studio_page
    from app import application_workspace_page
    from app import profile_workspace_v2, profile_workspace_v3
    from app.resume_engine_selector import (
        install_resume_engine_selector,
        render_resume_engine_selector,
    )
    from app.career_os_quality_patch_v1 import install_career_os_quality_patch
    from app.provider_telemetry_window_v1 import install_provider_telemetry_window

    # Keep the V1 import/route contract stable for existing integrations while
    # layering V2 preview/promotion and V3 encrypted candidate editing over the
    # proven V1 visual renderer and public-logo resolver.
    profile_workspace_v1.render = profile_workspace_v3.render

    install_product_v22(product_pages)
    install_resume_engine_selector(product_pages)
    # Additive only: model-free profile parsing and progressive Browse Jobs loading.
    # Existing n8n/legacy operations stay intact.
    install_career_os_quality_patch(product_pages)
    # The active Advanced/System window uses raw provider throughput (the large
    # fetched/normalized/eligible source-run totals), not deduplicated jobs stored.
    install_provider_telemetry_window(product_pages)

    pages: dict[str, Callable[[], None]] = {
        "dashboard": product_pages.dashboard,
        "jobs": product_pages.browse_jobs,
        PREPARE_APPLICATION_VIEW: application_workspace_page.render,
        "auto-prepare": product_pages.auto_prepare,
        "tracker": product_pages.tracker,
        "profile": profile_workspace_v1.render,
        RESUME_STUDIO_VIEW: resume_studio_page.render,
        "research": product_pages.research,
        "settings": product_pages.settings,
    }
    pages[view]()
    render_resume_engine_selector()
