"""Settings → Profile & defaults adapter for the unified Candidate Truth authority.

The legacy V2.2 Settings card counted only confirmed legacy candidate facts. That
made the page look empty even when the active Master Resume had already been
extracted into the Native Resume/Candidate Truth workflow. This adapter renders
Profile & defaults from the same read model used by the Profile workspace while
leaving every other Settings section delegated to the established UI stack.
"""
from __future__ import annotations

from typing import Any, Callable

import streamlit as st

from app import application_answer_router_v1 as answer_router
from app import candidate_profile_authority_v1 as profile_authority

_OPTIONS = [
    "Apply",
    "Automation",
    "Integrations",
    "Profile & defaults",
    "Credentials",
    "Advanced / System",
]
_SECTION_ROUTE = {
    "Apply": "apply",
    "Automation": "automation",
    "Integrations": "integrations",
    "Profile & defaults": "profile",
    "Credentials": "credentials",
    "Advanced / System": "advanced",
}


def _profile_settings_page(pages_module: Any) -> None:
    pages_module.page_intro(
        "PREFERENCES",
        "Settings",
        "Readable product controls first. AutoApply configuration and credential "
        "readiness are visible here; engineering diagnostics remain under Advanced / System.",
    )

    left, content = st.columns((1.05, 3.95), gap="large")
    with left:
        section = st.radio(
            "Settings section",
            _OPTIONS,
            label_visibility="collapsed",
            key="product_settings_section",
            on_change=pages_module._sync_subroute,
            args=("section", "product_settings_section", _SECTION_ROUTE),
        )

    # A radio change triggers a Streamlit rerun. Avoid rendering the Profile body
    # beneath another section during the transition frame.
    if section != "Profile & defaults":
        st.rerun()

    authority = profile_authority.authority_snapshot()
    master = dict(authority.get("master_resume") or {})
    profile = dict(authority.get("profile") or {})
    readiness = answer_router.readiness_projection()

    confirmed_count = int(profile.get("structured_fact_count") or 0)
    draft_count = int(profile.get("draft_fact_count") or 0)
    confirmed = bool(profile.get("confirmed"))
    extracted = bool(profile.get("extraction_id"))

    with content:
        st.markdown("### Profile & defaults")
        status_columns = st.columns(3, gap="small")
        with status_columns[0]:
            st.metric(
                "Master Resume",
                "Ready" if master.get("configured") else "Missing",
            )
            st.caption(master.get("label") or "Upload a Master Resume in Resume Studio")
        with status_columns[1]:
            st.metric(
                "Candidate Truth",
                "Confirmed" if confirmed else "Needs review",
            )
            if confirmed:
                st.caption(f"{confirmed_count} confirmed fact(s)")
            elif draft_count:
                st.caption(f"{draft_count} extracted value(s) ready for review")
            elif extracted:
                st.caption("Extraction exists, but no usable values were found")
            else:
                st.caption("Build Candidate Truth from the active Master Resume")
        with status_columns[2]:
            st.metric(
                "Application answers",
                int(readiness.get("autofill_ready_count") or 0),
            )
            st.caption("Reusable deterministic answer(s) currently ready")

        if confirmed:
            st.success(
                "Candidate Truth is confirmed against the active Master Resume. "
                "Profile-driven application preparation can use this authority."
            )
        elif draft_count:
            st.warning(
                f"MUNSHI already extracted {draft_count} profile value(s) from the active "
                "Master Resume. They are intentionally not counted as confirmed Candidate "
                "Truth until you review the profile once."
            )
        elif master.get("configured"):
            st.warning(
                "The Master Resume is configured, but Candidate Truth still needs to be "
                "built or reviewed before AutoApply testing."
            )
        else:
            st.info(
                "Add a Master Resume first. MUNSHI will then extract Candidate Truth and "
                "reuse confirmed facts across applications."
            )

        st.markdown(
            '<a class="product-nav-link" href="?view=profile&amp;tab=details" '
            'target="_self">Open Profile details →</a>',
            unsafe_allow_html=True,
        )


def install_settings_profile_authority_ui(pages_module: Any) -> None:
    """Install a rerun-safe Profile settings adapter over the current Settings UI."""
    if getattr(pages_module, "_settings_profile_authority_ui_installed", False):
        return

    original_settings: Callable[[], None] = pages_module.settings

    def settings_with_profile_authority() -> None:
        section = st.session_state.get("product_settings_section")
        if section == "Profile & defaults":
            _profile_settings_page(pages_module)
            return
        original_settings()

    pages_module.settings = settings_with_profile_authority
    pages_module._settings_profile_authority_ui_installed = True
