"""Unified Profile workspace for pre-AutoApply readiness.

V4 keeps the existing Resume and Cover Letter tabs intact.  Profile Details now
presents one authority view for Candidate Truth, the active Master Resume, and
the reusable Application Answer Vault used by both deterministic MUNSHI Apply
and AutoApply planning.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app import application_answer_router_v1 as answer_router
from app import application_question_catalog_v1 as question_catalog
from app import candidate_profile_authority_v1 as profile_authority
from app import profile_workspace_v3 as v3


def _status_cards(authority: dict[str, Any], answers: dict[str, Any]) -> None:
    cards = st.columns(4, gap="small")
    master = authority["master_resume"]
    profile = authority["profile"]
    with cards[0]:
        st.metric("Master Resume", "Ready" if master["configured"] else "Missing")
        st.caption(master.get("label") or "Upload/confirm a Master Resume")
    with cards[1]:
        st.metric("Candidate Truth", "Confirmed" if profile["confirmed"] else "Needs review")
        st.caption(f"{int(authority.get('structured_fact_count') or 0)} structured fact(s)")
    with cards[2]:
        st.metric("Common Q&A", f"{answers['coverage_percent']:.0f}%")
        st.caption(
            f"{answers['ready_concept_count']} of {answers['concept_count']} canonical answers ready"
        )
    with cards[3]:
        ready = authority["ready_for_application_planning"] and bool(
            answers["ready_concept_count"]
        )
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


def _answer_filter(rows: list[dict[str, Any]], selection: str) -> list[dict[str, Any]]:
    if selection == "Ready":
        return [row for row in rows if row["ready"]]
    if selection == "Needs input":
        return [row for row in rows if not row["ready"] and row["sensitivity_class"] == "NORMAL"]
    if selection == "Protected / ask when needed":
        return [row for row in rows if row["sensitivity_class"] != "NORMAL"]
    return rows


def _answer_vault_workspace() -> None:
    readiness = answer_router.readiness_projection()
    st.markdown("### Application Answer Vault")
    st.write(
        "MUNSHI normalizes provider wording into one semantic answer before the "
        "Application Plan is built. The same mapping is used whether execution "
        "continues through MUNSHI Apply or AutoApply."
    )
    st.info(
        f"Catalog: {readiness['common_question_count']} common question phrasings → "
        f"{readiness['concept_count']} canonical concepts. Unknown or ambiguous "
        "questions remain NEEDS_INPUT instead of being guessed."
    )

    metrics = st.columns(4, gap="small")
    metrics[0].metric("Common phrasings", int(readiness["common_question_count"]))
    metrics[1].metric("Canonical concepts", int(readiness["concept_count"]))
    metrics[2].metric("Ready concepts", int(readiness["ready_concept_count"]))
    metrics[3].metric("Coverage", f"{float(readiness['coverage_percent']):.0f}%")

    filter_value = st.segmented_control(
        "Answer status",
        ["All", "Ready", "Needs input", "Protected / ask when needed"],
        default="All",
        key="profile_v4_answer_filter",
    ) or "All"
    visible = _answer_filter(list(readiness["rows"]), filter_value)
    table = [
        {
            "Concept": row["question_key"],
            "Example": row["example_question"],
            "Status": "Ready" if row["ready"] else row["source"].replace("_", " ").title(),
            "Domain": row["sensitivity_class"],
        }
        for row in visible
    ]
    st.dataframe(table, use_container_width=True, hide_index=True, height=360)

    normal_missing = [
        row
        for row in readiness["rows"]
        if not row["ready"] and row["sensitivity_class"] == "NORMAL"
    ]
    if normal_missing:
        st.markdown("#### Add a reusable answer")
        options = {row["question_key"]: row for row in normal_missing}
        selected_key = st.selectbox(
            "Question",
            list(options),
            format_func=lambda key: options[key]["example_question"],
            key="profile_v4_answer_question",
        )
        selected = options[selected_key]
        with st.form("profile_v4_answer_form", clear_on_submit=True):
            answer = st.text_area(
                "Confirmed answer",
                height=100,
                placeholder="Enter the answer MUNSHI may reuse when this meaning appears again.",
            )
            allow = st.checkbox(
                "Allow deterministic autofill when this same semantic question appears",
                value=True,
            )
            save = st.form_submit_button(
                "Save confirmed reusable answer",
                type="primary",
                use_container_width=True,
            )
        if save:
            concept = next(
                item for item in question_catalog.CONCEPTS if item.key == selected_key
            )
            try:
                answer_router.save_user_answer(
                    concept.aliases[0],
                    answer,
                    autofill_allowed=allow,
                )
            except Exception as error:  # noqa: BLE001
                st.error(str(error))
            else:
                st.success("Answer saved under its canonical semantic concept.")
                st.rerun()
    else:
        st.success("All normal catalog concepts currently have a reusable source or answer.")

    st.caption(
        "Work authorization, self-identification, credentials, and post-offer "
        "sensitive fields are intentionally not copied into the normal plaintext answer vault."
    )


def _master_resume_workspace(authority: dict[str, Any]) -> None:
    master = authority["master_resume"]
    st.markdown("### Master Resume authority")
    if not master["configured"]:
        st.warning("No active Master Resume source is configured.")
        st.link_button("Open Resume Studio", "?view=resume-studio", use_container_width=True)
        return
    st.success("The uploaded/confirmed Resume Studio source is the Master Resume authority.")
    details = {
        "Label": master.get("label") or "Master Resume",
        "Source type": master.get("source_kind") or "unknown",
        "Updated": master.get("updated_at") or "unknown",
        "Source ID": master.get("source_id") or "unknown",
        "SHA-256": master.get("sha256") or "unknown",
        "Profile state": authority.get("state"),
    }
    st.json(details, expanded=False)
    if authority["profile"].get("confirmed"):
        st.caption(
            "Candidate Truth is confirmed against this active source. Resume generation "
            "and application planning should bind to this same source/profile digest."
        )
    else:
        st.warning(
            "The Master Resume is saved, but Candidate Truth is not yet confirmed for this source."
        )
    st.link_button("Open Resume Studio", "?view=resume-studio", use_container_width=True)


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
        "One profile authority for Master Resume evidence, Candidate Truth, and "
        "reusable application answers."
    )
    _status_cards(authority, answers)
    for warning in authority.get("warnings") or []:
        st.warning(str(warning))

    candidate_tab, answers_tab, resume_tab = st.tabs(
        ["Candidate Truth", "Application Answers", "Master Resume"]
    )
    with candidate_tab:
        _candidate_truth_workspace()
    with answers_tab:
        _answer_vault_workspace()
    with resume_tab:
        _master_resume_workspace(authority)
