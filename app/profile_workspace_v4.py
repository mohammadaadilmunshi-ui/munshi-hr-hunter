"""Unified Profile workspace for pre-AutoApply readiness.

V4 keeps the existing Resume and Cover Letter tabs intact. Profile Details now
presents one authority view for Candidate Truth, the active Master Resume, and
the reusable Application Answer Vault used by both deterministic MUNSHI Apply
and AutoApply planning.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app import application_answer_autofill_v1 as answer_autofill
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
        st.metric("Known meanings", f"{answers['ready_concept_count']}/{answers['concept_count']}")
        st.caption(
            f"{int(answers.get('autofill_ready_count') or 0)} deterministic answer(s) ready to fill"
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


def _answer_vault_workspace(authority: dict[str, Any]) -> None:
    readiness = answer_router.readiness_projection()
    st.markdown("### Application Answer Vault")
    st.write(
        "MUNSHI prepares reusable answers from Candidate Truth and previously confirmed "
        "answers, derives safe structural variants such as first/last name, and asks you "
        "only for the small unresolved remainder when a real application needs it."
    )
    st.info(
        "Deterministic fill runs first for both MUNSHI Apply and AutoApply. Claude may "
        "help interpret an unusual page or control only after deterministic matching "
        "fails; it does not invent candidate facts."
    )

    profile_confirmed = bool(authority.get("profile", {}).get("confirmed"))
    if not profile_confirmed:
        st.warning(
            "Confirm Candidate Truth once before automatic answer preparation. The Master "
            "Resume can be configured while the confirmed profile still has 0 structured facts."
        )

    if st.button(
        "Prepare answers automatically from Candidate Truth",
        type="primary",
        use_container_width=True,
        disabled=not profile_confirmed,
        key="profile_v4_prepare_answers",
    ):
        try:
            result = answer_autofill.prepare_from_candidate_truth()
        except Exception as error:  # noqa: BLE001
            st.error(str(error))
        else:
            st.success(
                f"Prepared {result['prepared_count']} reusable deterministic answers from the "
                "current confirmed profile."
            )
            st.rerun()

    readiness = answer_router.readiness_projection()
    delta = answer_autofill.onboarding_delta(readiness)
    rows = list(readiness.get("rows") or [])
    protected_ready = sum(
        1
        for row in rows
        if row.get("ready") and str(row.get("sensitivity_class") or "") != "NORMAL"
    )
    truth_ready = sum(
        1
        for row in rows
        if row.get("ready")
        and str(row.get("source") or "")
        in {"candidate_truth", "protected_candidate_truth"}
    )

    metrics = st.columns(4, gap="small")
    metrics[0].metric("Autofill ready", int(readiness.get("autofill_ready_count") or 0))
    metrics[1].metric("Candidate Truth ready", truth_ready)
    metrics[2].metric("Protected ready", protected_ready)
    metrics[3].metric("Reusable gaps", len(delta))

    if not delta and profile_confirmed:
        st.success(
            "No reusable setup is required right now. Job-specific or unusual questions "
            "will be handled contextually when an application actually needs them."
        )
    elif delta:
        st.caption(
            "You do not need to complete a questionnaire. These are only reusable gaps. "
            "MUNSHI can wait until a real application needs one, then remember your confirmed answer."
        )

        with st.expander(
            f"Optional: review {len(delta)} reusable gap(s)",
            expanded=False,
        ):
            table = [
                {
                    "Meaning": row["question_key"],
                    "Example": row["example_question"],
                    "Status": "Ask only when needed",
                }
                for row in delta
            ]
            st.dataframe(table, use_container_width=True, hide_index=True, height=280)

        st.markdown("#### Optional: save one reusable answer now")
        options = {row["question_key"]: row for row in delta}
        selected_key = st.selectbox(
            "Question",
            list(options),
            format_func=lambda key: options[key]["example_question"],
            key="profile_v4_answer_question",
        )
        with st.form("profile_v4_answer_form", clear_on_submit=True):
            answer = st.text_area(
                "Confirmed answer",
                height=100,
                placeholder="Only add this if you want it reusable before a real application asks.",
            )
            allow = st.checkbox(
                "Allow deterministic autofill when this same meaning appears",
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
                st.success("Answer saved under its canonical semantic meaning.")
                st.rerun()

    st.caption(
        "The provider-phrasing recognition catalog stays internal. This page shows only "
        "what is ready and the small reusable delta that may still need confirmation."
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
        _answer_vault_workspace(authority)
    with resume_tab:
        _master_resume_workspace(authority)
