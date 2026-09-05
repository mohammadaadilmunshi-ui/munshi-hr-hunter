"""Profile Workspace V2: immediate extracted-profile preview and explicit promotion.

V1 owns the approved MUNSHI/Tsenta-inspired visual renderer and public-logo resolver.
V2 changes only the handoff state machine so an extracted profile is visible before
confirmation. Confirmation promotes the reviewed snapshot to permanent authority;
it is no longer a prerequisite for seeing the extracted output.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app import native_resume_service_v3 as v3
from app import profile_workspace_v1 as v1
from app.resume_profile_details_v31 import load_candidate_profile_details


def _latest_profile_for_active_source() -> dict[str, Any]:
    source = v3.active_source()
    if not source:
        return {}
    return v3.latest_profile_for_source(str(source["source_id"])) or {}


def _details() -> dict[str, Any]:
    try:
        return load_candidate_profile_details()
    except Exception as error:
        st.warning(f"Candidate application defaults could not be loaded: {error}")
        return {}


def _build_profile(source: dict[str, Any]) -> dict[str, Any]:
    with st.spinner(
        "Building your structured MUNSHI profile from the confirmed Master Resume without inventing missing facts…"
    ):
        return v3.extract_profile_from_source(source)


def _render_uninitialized(source: dict[str, Any]) -> None:
    if not source:
        st.markdown(
            '<div class="profile-empty"><h3>Your MUNSHI profile has no Master Resume yet</h3>'
            '<p>Upload and confirm a Master Resume in Resume Studio. MUNSHI will then be able to build the reusable structured profile shown here.</p>'
            '<a class="profile-edit-link" href="?view=resume-studio" target="_self">Upload Master Resume in Resume Studio →</a></div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<div class="profile-empty"><h3>Your Master Resume is saved</h3>'
        '<p>The evidence source is ready. Build the structured profile now to populate Professional Summary, Education, Experience, Projects, Skills, Certifications, and the reusable application-default workspace.</p></div>',
        unsafe_allow_html=True,
    )
    left, right = st.columns((1.2, 2.8), gap="small")
    with left:
        if st.button(
            "Build profile from Master Resume",
            type="primary",
            use_container_width=True,
            key=f"profile_v2_build_{source['source_id']}",
        ):
            try:
                _build_profile(source)
            except Exception as error:
                st.error(str(error))
                st.markdown(
                    '<a class="profile-edit-link" href="?view=resume-studio" target="_self">Open Resume Studio / AI settings →</a>',
                    unsafe_allow_html=True,
                )
            else:
                st.success("Structured profile created. Review the full preview below, then confirm it as your permanent MUNSHI profile.")
                st.rerun()
    with right:
        st.caption(
            "This uses the confirmed Master Resume as the truth boundary. Missing facts remain missing; confirmation is still required before the snapshot becomes permanent profile authority."
        )


def _render_preview_controls(extracted: dict[str, Any]) -> None:
    status = str(extracted.get("status") or "").upper()
    if status == "CONFIRMED":
        st.success("Permanent MUNSHI profile confirmed. This reviewed snapshot is now the reusable profile authority.")
        return

    st.info(
        "Profile preview — this is the full structured output from your Master Resume. Review it before making it permanent. "
        "Nothing here becomes permanent profile authority until you confirm it."
    )
    left, middle, right = st.columns((1.25, 1.15, 2.2), gap="small")
    with left:
        if st.button(
            "Confirm as permanent profile",
            type="primary",
            use_container_width=True,
            key=f"profile_v2_confirm_{extracted.get('extraction_id')}",
        ):
            try:
                v3.confirm_profile_extract(str(extracted["extraction_id"]))
            except Exception as error:
                st.error(str(error))
            else:
                st.success("Permanent MUNSHI profile confirmed.")
                st.rerun()
    with middle:
        st.markdown(
            '<a class="profile-tab-link" href="?view=resume-studio" target="_self">Edit / re-extract in Resume Studio</a>',
            unsafe_allow_html=True,
        )
    with right:
        st.caption(
            "Logos are resolved from public organization metadata with cached initials fallback. Candidate-private profile data is not sent to the logo resolver."
        )


def render() -> None:
    v1._inject_css()
    tab = v1._tab_from_state()
    v1._subnav(tab)

    if tab == "Resume":
        v1._resume_tab()
        return
    if tab == "Cover Letter":
        v1._cover_letter_tab()
        return

    source = v3.active_source()
    active_profile = _latest_profile_for_active_source()
    confirmed_profile = v1._latest_confirmed_profile()

    # Prefer the latest extraction for the currently active Master Resume so the
    # user sees the newly generated preview immediately. Fall back to the latest
    # confirmed snapshot only when the active source has no extraction yet.
    extracted = active_profile or confirmed_profile
    if not extracted:
        _render_uninitialized(source)
        return

    if source and not active_profile and confirmed_profile:
        st.warning(
            "A newer Master Resume is saved, but it has not been extracted yet. The profile below is your previous confirmed permanent snapshot."
        )
        if st.button(
            "Rebuild profile from current Master Resume",
            type="primary",
            key=f"profile_v2_rebuild_{source['source_id']}",
        ):
            try:
                _build_profile(source)
            except Exception as error:
                st.error(str(error))
            else:
                st.rerun()

    _render_preview_controls(extracted)
    profile = extracted.get("profile") or {}
    v1._render_profile_details(profile, _details())
