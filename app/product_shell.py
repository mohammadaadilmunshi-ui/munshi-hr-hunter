'''Top-level product shell and query-param router.'''
from __future__ import annotations

from typing import Callable

import streamlit as st

from app.autoapply_settings_ui import install_autoapply_settings_ui
from app.job_card_interaction_hotfix_v2 import inject_job_card_interaction_css
from app.product_state import valid_view, volume_policy
from app.product_ui import esc, inject_css
from app.product_v22 import brand_logo_data_uri, inject_v22_css, install_product_v22
from app.product_experience_vnext import inject_presentation_css, render_mobile_dock
from app.staging_feature_policy import activate_isolated_staging_preparation_features


RESUME_STUDIO_VIEW = "resume-studio"
PREPARE_APPLICATION_VIEW = "prepare-application"
_SPECIAL_VIEWS = {RESUME_STUDIO_VIEW, PREPARE_APPLICATION_VIEW}

NAVIGATION = (
    ("dashboard", "Dashboard"),
    ("jobs", "Browse jobs"),
    (PREPARE_APPLICATION_VIEW, "Applications"),
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
    inject_job_card_interaction_css()
    inject_presentation_css()

    view = _query_view()
    st.session_state["product_view"] = view
    _apply_deep_link_state(view)

    policy = volume_policy()
    status = {
        "unlimited": "Discovery: Unlimited",
        "custom_limit": f"Discovery limit: {policy.get('daily_limit')}",
        "paused": "Discovery: Paused",
        "pause_after_batch": "Discovery: Pausing",
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
            f'''<a class="munshi-skip-link" href="#munshi-main">Skip to content</a>
            <header class="product-header">
                <a class="brand" href="?view=dashboard" target="_self" aria-label="MUNSHI dashboard">
                    <span class="brand-mark" aria-hidden="true"><img src="{logo}" alt=""></span>
                    <span>MUNSHI</span>
                </a>
                <nav class="product-nav" aria-label="Primary navigation">
                    {desktop_links}
                </nav>
                <div class="product-header-actions">
                    {settings_link}
                    <span class="status-pill-product" title="Job discovery and preparation volume. Not a final-submission confirmation.">{esc(status)}</span>
                </div>
                <details class="mobile-nav">
                    <summary aria-label="Open navigation">Menu</summary>
                    <nav aria-label="Mobile navigation">{mobile_links}</nav>
                </details>
            </header>''',
            unsafe_allow_html=True,
        )

    render_mobile_dock(view)
    st.markdown('<div id="munshi-main" tabindex="-1"></div>', unsafe_allow_html=True)

    # Preserve this established import line verbatim: profile route contract tests
    # and downstream integrations use it as a compatibility marker.
    from app import product_pages, profile_workspace_v1, resume_studio_page
    from app import application_workspace_page
    from app import candidate_profile_authority_v1 as profile_authority
    from app import profile_workspace_v2, profile_workspace_v3, profile_workspace_v4
    from app.n8n_live_console_v1 import render_n8n_live_console
    from app.resume_engine_selector import (
        install_resume_engine_selector,
        render_resume_engine_selector,
    )
    from app.career_os_quality_patch_v1 import install_career_os_quality_patch
    from app.job_card_runtime_hotfix_v1 import install_job_card_runtime_hotfix
    from app.provider_telemetry_window_v1 import install_provider_telemetry_window
    from app.profile_runtime_repair_v2 import install_profile_runtime_repair_v2
    from app.resume_studio_session_state_repair_v1 import (
        install_resume_studio_session_state_repair_v1,
    )
    from app.secure_api_vault_ui import install_secure_api_vault_ui
    from app.settings_profile_authority_ui_v1 import (
        install_settings_profile_authority_ui,
    )
    from app.integration_credentials_ui_v1 import (
        install_integration_credentials_ui,
    )
    from app.ats_account_readiness_ui_v1 import (
        install_ats_account_readiness_ui,
    )
    from app.application_notification_ui_v1 import (
        install_application_notification_ui,
    )
    from app.unified_job_workspaces_runtime_patch_v1 import (
        install_unified_job_workspaces,
    )

    # Preserve the established V3 route contract first, then layer the V4
    # unified Profile authority on the same long-lived V1 entrypoint.
    profile_workspace_v1.render = profile_workspace_v3.render
    profile_workspace_v1.render = profile_workspace_v4.render

    # Product Settings V2.3 historically imported the retired n8n-generated
    # artifact designation as its Master Resume signal. Point only its read
    # surface at the same Native Resume/Candidate Truth authority used by Profile
    # and Resume Studio, without mutating the old artifact history.
    product_pages.master_resume = profile_authority.master_resume
    product_pages.candidate_facts = profile_authority.candidate_facts

    install_product_v22(product_pages)
    # The dedicated drawer hardening is intentionally layered immediately after
    # V2.2 so optional legacy history cannot crash Browse before later wrappers.
    install_job_card_runtime_hotfix()
    # Keep the current AutoApply Settings layer first; the secure vault wrapper
    # is deliberately installed after it so the vault appends to the same
    # Credentials surface instead of replacing the newer AutoApply controls.
    install_autoapply_settings_ui(product_pages)
    # Streamlit reruns this shell on every interaction. AutoApply deliberately
    # reassigns product_pages.settings above, so an old one-time vault marker
    # would otherwise prevent the secure-vault wrapper from being reattached.
    # Clear only that wrapper marker, then install exactly one fresh wrapper over
    # the current Settings function for this run.
    product_pages._secure_api_vault_ui_installed = False
    install_secure_api_vault_ui(product_pages)
    # The Profile settings adapter is also rerun-safe and intentionally installed
    # last so Profile & defaults is projected from the same Candidate Truth read
    # model while every other Settings section keeps the established wrappers.
    product_pages._settings_profile_authority_ui_installed = False
    install_settings_profile_authority_ui(product_pages)
    install_resume_engine_selector(product_pages)
    # Additive only: model-free profile parsing and progressive Browse Jobs loading.
    # Existing n8n/legacy operations stay intact.
    install_career_os_quality_patch(product_pages)
    # The active Advanced/System window uses raw provider throughput (the large
    # fetched/normalized/eligible source-run totals), not deduplicated jobs stored.
    install_provider_telemetry_window(product_pages)
    # Harden only the profile parser/rebuild path after the established Career OS
    # layers. Rebuild remains draft-only and never changes permanent authority.
    install_profile_runtime_repair_v2()
    # Resume Studio encrypted-key saves previously crashed after a successful save
    # because Streamlit forbids changing a widget-owned key later in the same run.
    # Install the narrow rerun-safe repair before the Resume Studio page renders.
    install_resume_studio_session_state_repair_v1()
    # Last presentation override: Tracker, Research, and Applications all reuse
    # the same Dashboard/Browse Jobs card renderer and lazy user-facing sections.
    # This does not change preparation, targeting, or submission authority.
    install_unified_job_workspaces(product_pages, application_workspace_page)

    # Restore the staging application activity projection after the final
    # workspace wrapper. Advanced/System notification preferences render directly
    # from settings_v23, avoiding persistent Settings wrapper accumulation.
    install_application_notification_ui(product_pages)

    # AutoApply replaces product_pages.settings on every Streamlit rerun. Reset
    # only the integration wrapper marker so the final wrapper is reattached to
    # the current Settings callable instead of silently disappearing.
    product_pages._integration_credentials_ui_v1_installed = False
    install_integration_credentials_ui(product_pages)

    # Credentials parity: append non-secret ATS managed-account/application-mail
    # readiness after the existing API vault and integration controls. Settings
    # is replaced on every rerun, so this wrapper is deliberately reattached too.
    product_pages._ats_account_readiness_ui_v1_installed = False
    install_ats_account_readiness_ui(product_pages)

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
    # Persistent presentation-only observer for explicit dashboard n8n runs.
    # It survives navigation, reads durable queue/execution/callback evidence,
    # and never owns dispatch/retry/submission authority.
    render_n8n_live_console()
