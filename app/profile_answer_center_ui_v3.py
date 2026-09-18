"""Truth-safe, first-class Application Answer Center UI.

V3 preserves V2 storage and deterministic matching behavior while fixing two UX
problems: protected/contextual meanings are never overstated as configured, and
the meanings behind a partial coverage count are visible without hunting through
a secondary Coverage route.
"""
from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from app import profile_answer_center_ui_v2 as v2


_TRUTH_SAFE_PROTECTED_DETAIL_FIELDS = {
    "eligibility.work_authorized_us": "authorized_to_work",
    "eligibility.visa_status": "visa_or_permit",
    "self_id.veteran": "veteran",
    "self_id.disability": "disability",
    "self_id.gender": "gender",
    "self_id.race_ethnicity": "ethnicity",
}
_ANSWER_SECTIONS = (
    "Reusable answers",
    "Protected & self-ID",
    "Teach MUNSHI",
    "Coverage details",
)
_LEGACY_SECTION_MAP = {
    "Complete reusable profile": "Reusable answers",
    "Protected & self-ID": "Protected & self-ID",
    "Teach MUNSHI": "Teach MUNSHI",
    "Coverage": "Coverage details",
}


def _install_truth_safe_policy() -> None:
    # One legacy sponsorship_required boolean must not prove two independent
    # semantic concepts (sponsorship now vs sponsorship in the future).
    v2._PROTECTED_DETAIL_FIELDS.clear()
    v2._PROTECTED_DETAIL_FIELDS.update(_TRUTH_SAFE_PROTECTED_DETAIL_FIELDS)


def coverage_breakdown(readiness: Mapping[str, Any]) -> dict[str, int]:
    _install_truth_safe_policy()
    return v2.coverage_breakdown(readiness)


def _remaining_meanings(readiness: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return every currently unresolved semantic meaning in user-facing form."""
    rows = [dict(row) for row in readiness.get("rows") or []]
    reusable_keys = {
        str(row.get("question_key") or "")
        for row in v2.answer_autofill.onboarding_delta(readiness)
    }
    protected_config = v2._protected_configuration()
    remaining: list[dict[str, str]] = []

    for row in rows:
        key = str(row.get("question_key") or "")
        if row.get("ready") or protected_config.get(key):
            continue

        sensitivity = str(row.get("sensitivity_class") or "")
        family = str(row.get("family") or "")
        if key in reusable_keys:
            handling = "Reusable answer you may save now"
        elif sensitivity in {"PROTECTED", "SELF_ID"}:
            handling = "Protected/self-ID: secure or application-time handling"
        elif family in v2._CONTEXTUAL_FAMILIES or key in v2._CONTEXTUAL_KEYS:
            handling = "Application-time: answered only when a real job requires it"
        else:
            handling = "Application-time: MUNSHI will request truth instead of guessing"

        remaining.append(
            {
                "question_key": key,
                "Meaning": v2._humanize_key(key),
                "Example application question": str(
                    row.get("example_question") or v2._humanize_key(key)
                ),
                "Handling": handling,
            }
        )
    return remaining


def _open_reusable_answers() -> None:
    st.session_state["answer_center_v3_section"] = "Reusable answers"


def _prefill_teach_question(question: str) -> None:
    st.session_state["answer_center_v3_section"] = "Teach MUNSHI"
    st.session_state["answer_center_v2_teach_question"] = str(question or "").strip()


def _render_remaining_meanings(readiness: Mapping[str, Any]) -> None:
    rows = _remaining_meanings(readiness)
    if not rows:
        st.success("Every current application meaning has a confirmed authority path.")
        return

    st.markdown(f"#### Unanswered questions · {len(rows)}")
    st.caption(
        "These are the actual meanings MUNSHI does not currently know. Each item now has a direct action. "
        "Protected/self-ID answers stay encrypted; contextual questions are never guessed."
    )

    for index, row in enumerate(rows):
        handling = str(row["Handling"])
        question = str(row["Example application question"])
        with st.container(
            key=f"munshi_surface_unanswered_{index}",
        ):
            left, right = st.columns((3.2, 1.15), gap="medium", vertical_alignment="center")
            with left:
                st.markdown(f"**{row['Meaning']}**")
                st.caption(question)
                st.write(handling)
            with right:
                if handling.startswith("Reusable answer"):
                    st.button(
                        "Answer now",
                        key=f"answer_center_v3_answer_{index}",
                        type="primary",
                        use_container_width=True,
                        on_click=_open_reusable_answers,
                    )
                elif handling.startswith("Protected/self-ID"):
                    st.button(
                        "Edit secure default",
                        key=f"answer_center_v3_secure_{index}",
                        type="primary",
                        use_container_width=True,
                        on_click=v2._open_protected_editor,
                    )
                else:
                    st.button(
                        "Teach if reusable",
                        key=f"answer_center_v3_teach_{index}",
                        use_container_width=True,
                        on_click=_prefill_teach_question,
                        args=(question,),
                        help=(
                            "Prefills Teach MUNSHI. The answer router will still reject "
                            "protected or unsafe global answers."
                        ),
                    )


def _selected_section() -> str:
    current = str(st.session_state.get("answer_center_v3_section") or "")
    if not current:
        legacy = str(st.session_state.get("answer_center_v2_section") or "")
        current = _LEGACY_SECTION_MAP.get(legacy, "Reusable answers")
    if current not in _ANSWER_SECTIONS:
        current = "Reusable answers"
    st.session_state["answer_center_v3_section"] = current
    return current


def _section_navigation() -> str:
    current = _selected_section()
    st.markdown("#### Answer workspace")
    cols = st.columns(4, gap="small")
    labels = {
        "Reusable answers": "Normal answers MUNSHI may safely remember",
        "Protected & self-ID": "Encrypted defaults and voluntary choices",
        "Teach MUNSHI": "Confirm a new reusable semantic answer",
        "Coverage details": "Full unresolved-category explanation",
    }
    for col, section in zip(cols, _ANSWER_SECTIONS):
        with col:
            with st.container(border=True):
                st.markdown(f"**{section}**")
                st.caption(labels[section])
                if st.button(
                    "Open" if current != section else "Viewing",
                    key=f"answer_center_v3_open_{section.lower().replace(' ', '_').replace('&', 'and')}",
                    type="primary" if current == section else "secondary",
                    use_container_width=True,
                    disabled=current == section,
                ):
                    st.session_state["answer_center_v3_section"] = section
                    st.rerun()
    return current


def render(authority: dict[str, Any]) -> None:
    _install_truth_safe_policy()
    readiness = v2.answer_router.readiness_projection()
    summary = coverage_breakdown(readiness)

    st.markdown("### Application Answer Center")
    st.write(
        "MUNSHI prepares application answers from Candidate Truth, encrypted protected defaults and previously confirmed reusable answers. Deterministic matching runs first; unusual controls may be interpreted only after deterministic matching fails, and candidate facts are never invented."
    )

    profile_confirmed = bool(authority.get("profile", {}).get("confirmed"))
    if not profile_confirmed:
        st.warning("Confirm Candidate Truth once before automatic answer preparation.")

    if st.button(
        "Prepare / refresh answers from Candidate Truth",
        type="primary",
        use_container_width=True,
        disabled=not profile_confirmed,
        key="answer_center_v3_prepare_truth",
    ):
        try:
            result = v2.answer_autofill.prepare_from_candidate_truth()
        except Exception as error:  # noqa: BLE001
            st.error(str(error))
        else:
            st.success(
                f"Prepared or refreshed {result['prepared_count']} truth-bound deterministic answer(s)."
            )
            st.rerun()

    readiness = v2.answer_router.readiness_projection()
    summary = coverage_breakdown(readiness)
    metrics = st.columns(4, gap="small")
    metrics[0].metric("Known meanings", f"{summary['known']}/{summary['total']}")
    metrics[1].metric("Autofill ready now", summary["autofill_ready"])
    metrics[2].metric("Reusable gaps", summary["reusable_gaps"])
    metrics[3].metric("Protected configured", summary["protected_ready"])

    unresolved = max(0, summary["total"] - summary["known"])
    if unresolved:
        st.info(
            f"{unresolved} semantic meaning(s) are not currently known. They are listed below so the coverage number is explainable rather than hidden."
        )
    _render_remaining_meanings(readiness)

    section = _section_navigation()
    if section == "Reusable answers":
        v2._render_reusable(readiness)
    elif section == "Protected & self-ID":
        v2._render_protected(readiness)
    elif section == "Teach MUNSHI":
        v2._render_teach()
    else:
        v2._render_coverage(readiness)

    st.caption(
        "Provider wording stays internal. Coverage describes semantic meanings, not a promise that every possible employer question can be known before a real application exists."
    )
