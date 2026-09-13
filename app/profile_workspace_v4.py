"""Unified Profile workspace for pre-AutoApply readiness.

V4 preserves the established Resume, Cover Letter, Candidate Truth and encrypted
profile-editing paths while layering the first-class Master Resume card and the
runtime-safe Answer Center. The Master Resume control surface is mounted exactly
once per Streamlit run so hidden tab bodies cannot duplicate widget keys.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app import application_answer_router_v1 as answer_router
from app import candidate_profile_authority_v1 as profile_authority
from app import master_resume_file_ui_v1 as master_resume_ui
from app import profile_answer_center_ui_v2 as answer_center
from app import profile_workspace_v3 as v3


def _status_cards(authority: dict[str, Any], answers: dict[str, Any]) -> None:
    cards = st.columns(4, gap="small")
    profile = authority["profile"]
    summary = answer_center.coverage_breakdown(answers)
    remaining = max(0, summary["total"] - summary["known"])

    with cards[0]:
        st.metric("Candidate Truth", "Confirmed" if profile["confirmed"] else "Needs review")
        st.caption(f"{int(authority.get('structured_fact_count') or 0)} structured fact(s)")
    with cards[1]:
        st.metric("Known meanings", f"{summary['known']}/{summary['total']}")
        if remaining:
            st.caption(
                f"{remaining} remaining include protected/contextual meanings; they are not mandatory onboarding questions."
            )
        else:
            st.caption("Every current semantic meaning has an authority path")
    with cards[2]:
        st.metric("Autofill ready", summary["autofill_ready"])
        st.caption(
            f"Reusable gaps: {summary['reusable_gaps']} · Protected configured: {summary['protected_ready']}"
        )
    with cards[3]:
        ready = authority["ready_for_application_planning"] and bool(summary["known"])
        st.metric("AutoApply foundation", "Ready to test" if ready else "Needs setup")
        st.caption("Final-submit authority stays separate.")


def _candidate_truth_workspace() -> None:
    source = v3.v3.active_source()
    active_profile = v3.v2._latest_profile_for_active_source()
    confirmed_profile = v3.v1._latest_confirmed_profile()
    extracted = active_profile or confirmed_profile
    if not extracted:
        v3.v2._render_uninitialized(source)
        return

    if source and not active_profile and confirmed_profile:
        st.warning(
            "A newer Master Resume is saved, but it has not been extracted yet. "
            "The profile below is the previous confirmed snapshot."
        )
        if st.button(
            "Rebuild profile from current Master Resume",
            type="primary",
            key=f"profile_v4_rebuild_{source['source_id']}",
        ):
            try:
                v3.v2._build_profile(source)
            except Exception as error:  # noqa: BLE001
                st.error(str(error))
            else:
                st.rerun()

    v3.v2._render_preview_controls(extracted)
    resolved = v3.resolve_profile(extracted)
    v3._profile_controls(extracted, resolved)
    v3.v1._render_profile_details(resolved, v3.v2._details(resolved))


def _master_resume_details(authority: dict[str, Any]) -> None:
    """Render detail-only source status without remounting the top file controls."""
    master = dict(authority.get("master_resume") or {})
    st.markdown("### Master Resume authority")
    if not master.get("configured"):
        st.warning("No active Master Resume source is configured.")
        return

    if authority.get("profile", {}).get("confirmed"):
        st.success(
            "Candidate Truth is confirmed against this active Master Resume. Resume generation and application planning bind to the same source/profile digest."
        )
    else:
        st.warning(
            "Candidate Truth is not confirmed against the current Master Resume. Review/confirm it before an application can use this source."
        )

    st.caption(
        "Preview, Edit source, Replace, Download source text and Edit in Resume Studio are available from the Master Resume file card above."
    )
    with st.expander("Advanced source details", expanded=False):
        st.code(
            "\n".join(
                [
                    f"label: {master.get('label') or 'Master Resume'}",
                    f"source_id: {master.get('source_id') or 'unknown'}",
                    f"source_kind: {master.get('source_kind') or 'unknown'}",
                    f"sha256: {master.get('sha256') or 'unknown'}",
                    f"profile_state: {authority.get('state') or 'unknown'}",
                ]
            ),
            language="text",
        )


def render() -> None:
    v3.v1._inject_css()
    tab = v3.v1._tab_from_state()
    v3.v1._subnav(tab)
    if tab == "Resume":
        v3.v1._resume_tab()
        return
    if tab == "Cover Letter":
        v3.v1._cover_letter_tab()
        return

    authority = profile_authority.authority_snapshot()
    answers = answer_router.readiness_projection()

    st.markdown("## Candidate Profile & Application Truth")
    st.caption(
        "One profile authority for Master Resume evidence, Candidate Truth, reusable answers, and protected application defaults."
    )

    # One and only one mount of the interactive Master Resume control surface.
    master_resume_ui.render_compact(authority)
    _status_cards(authority, answers)

    for warning in authority.get("warnings") or []:
        st.warning(str(warning))

    candidate_tab, answers_tab, resume_tab = st.tabs(
        ["Candidate Truth", "Application Answers", "Master Resume"]
    )
    with candidate_tab:
        _candidate_truth_workspace()
    with answers_tab:
        answer_center.render(authority)
    with resume_tab:
        _master_resume_details(authority)
