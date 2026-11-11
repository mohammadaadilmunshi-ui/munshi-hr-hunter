"""Guided Profile Answer Center over Candidate Truth and Answer Brain.

The provider-phrasing catalog remains internal recognition intelligence. This UI
shows semantic readiness, lets candidates complete the small reusable delta in a
batch, keeps protected/self-ID values on their encrypted profile path, and offers
a future-use teaching form for recognized normal-domain questions.
"""
from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from app import application_answer_autofill_v1 as answer_autofill
from app import application_answer_router_v1 as answer_router
from app import application_question_catalog_v1 as question_catalog
from app import profile_workspace_v3 as profile_v3

_CONTEXTUAL_FAMILIES = {
    "open_ended_job_specific",
    "post_offer_sensitive",
    "credential_requirement",
}
_CONTEXTUAL_KEYS = {
    "experience.relevant_years",
    "experience.skill_years",
}


def _concept_map() -> dict[str, Any]:
    return {item.key: item for item in question_catalog.CONCEPTS}


def coverage_breakdown(readiness: Mapping[str, Any]) -> dict[str, int]:
    rows = [dict(row) for row in readiness.get("rows") or []]
    reusable = answer_autofill.onboarding_delta(readiness)
    reusable_keys = {str(row.get("question_key") or "") for row in reusable}

    protected_ready = 0
    protected_pending = 0
    contextual = 0
    for row in rows:
        sensitivity = str(row.get("sensitivity_class") or "")
        family = str(row.get("family") or "")
        key = str(row.get("question_key") or "")
        if sensitivity in {"PROTECTED", "SELF_ID"}:
            if row.get("ready"):
                protected_ready += 1
            else:
                protected_pending += 1
            continue
        if row.get("ready") or key in reusable_keys:
            continue
        if family in _CONTEXTUAL_FAMILIES or key in _CONTEXTUAL_KEYS or sensitivity in {"CREDENTIAL", "POST_OFFER"}:
            contextual += 1
        else:
            # A normal unresolved concept that is intentionally excluded from
            # up-front onboarding is still application-time work, not a hidden
            # mandatory questionnaire item.
            contextual += 1

    return {
        "known": int(readiness.get("ready_concept_count") or 0),
        "total": int(readiness.get("concept_count") or 0),
        "autofill_ready": int(readiness.get("autofill_ready_count") or 0),
        "reusable_gaps": len(reusable),
        "protected_ready": protected_ready,
        "protected_pending": protected_pending,
        "contextual": contextual,
    }


def _humanize_key(value: str) -> str:
    tail = str(value or "").split(".")[-1]
    return tail.replace("_", " ").strip().title()


def _batch_input(concept: Any, row: Mapping[str, Any], *, index: int) -> tuple[str, Any]:
    key = str(row["question_key"])
    widget_key = f"answer_center_batch_{index}_{key.replace('.', '_')}"
    label = str(row.get("example_question") or _humanize_key(key))
    answer_type = str(getattr(concept, "answer_type", "text") or "text")

    if answer_type == "boolean":
        value = st.selectbox(
            label,
            ["Skip for now", "Yes", "No"],
            key=widget_key,
        )
        return key, {"Skip for now": "", "Yes": "Yes", "No": "No"}[value]
    if answer_type == "long_text":
        return key, st.text_area(label, height=110, key=widget_key)
    if answer_type == "number":
        return key, st.text_input(label, placeholder="Enter a number or leave blank", key=widget_key)
    if answer_type == "date":
        return key, st.text_input(label, placeholder="e.g. Dec 17, 2026", key=widget_key)
    return key, st.text_input(label, key=widget_key)


def _save_batch(delta: list[dict[str, Any]], values: Mapping[str, Any]) -> tuple[int, list[str]]:
    concepts = _concept_map()
    saved = 0
    errors: list[str] = []
    for row in delta:
        key = str(row["question_key"])
        value = str(values.get(key) or "").strip()
        if not value:
            continue
        concept = concepts.get(key)
        if concept is None:
            errors.append(f"{key}: semantic concept is unavailable")
            continue
        try:
            answer_router.save_user_answer(
                concept.aliases[0],
                value,
                autofill_allowed=True,
            )
        except Exception as error:  # noqa: BLE001
            errors.append(f"{row.get('example_question') or key}: {error}")
        else:
            saved += 1
    return saved, errors


def _render_reusable(readiness: dict[str, Any]) -> None:
    delta = answer_autofill.onboarding_delta(readiness)
    st.markdown("#### Complete reusable profile")
    st.caption(
        "These are the only normal reusable meanings MUNSHI cannot currently derive. Fill any you want remembered now, or leave them blank and MUNSHI will ask only if a real application needs them."
    )
    if not delta:
        st.success("No normal reusable gaps are waiting for setup.")
        return

    concepts = _concept_map()
    with st.form("profile_answer_center_batch_form"):
        values: dict[str, Any] = {}
        for index, row in enumerate(delta):
            concept = concepts.get(str(row["question_key"]))
            if concept is None:
                continue
            key, value = _batch_input(concept, row, index=index)
            values[key] = value
        save = st.form_submit_button(
            "Save completed reusable answers",
            type="primary",
            use_container_width=True,
        )
    if save:
        saved, errors = _save_batch(delta, values)
        if saved:
            st.success(f"Saved {saved} reusable answer(s) for future applications.")
        for error in errors:
            st.error(error)
        if saved and not errors:
            st.rerun()


def _render_protected(readiness: dict[str, Any]) -> None:
    rows = [
        dict(row)
        for row in readiness.get("rows") or []
        if str(row.get("sensitivity_class") or "") in {"PROTECTED", "SELF_ID"}
    ]
    ready = sum(1 for row in rows if row.get("ready"))
    st.markdown("#### Protected & self-ID")
    st.caption(
        "Protected does not mean manual every time. Candidate-confirmed protected values stay on the encrypted Candidate Truth path and can be reused only through the dedicated secure execution policy."
    )
    cols = st.columns(2, gap="small")
    cols[0].metric("Protected meanings ready", ready)
    cols[1].metric("Protected meanings not configured", max(0, len(rows) - ready))
    st.info(
        "Work authorization, sponsorship and voluntary self-identification are intentionally not copied into the normal plaintext Answer Vault. Configure them here once instead."
    )
    profile_v3._edit_application_defaults()


def _render_teach() -> None:
    st.markdown("#### Teach MUNSHI for future use")
    st.caption(
        "Save a normal application question and your confirmed answer once. MUNSHI normalizes recognized provider wording to the same semantic meaning on future applications."
    )
    with st.form("profile_answer_center_teach_form", clear_on_submit=True):
        question = st.text_input(
            "Application question",
            placeholder="e.g. Are you able to work weekends?",
        )
        answer = st.text_area(
            "Confirmed reusable answer",
            height=100,
            placeholder="Enter the answer you want MUNSHI to reuse when this meaning appears again.",
        )
        allow = st.checkbox(
            "Allow deterministic autofill when this same semantic meaning appears",
            value=True,
        )
        save = st.form_submit_button(
            "Save for future applications",
            type="primary",
            use_container_width=True,
        )
    if save:
        try:
            answer_router.save_user_answer(
                question,
                answer,
                autofill_allowed=allow,
            )
        except Exception as error:  # noqa: BLE001
            st.error(str(error))
            st.caption(
                "If this is work authorization, sponsorship, self-ID, credentials, or other protected data, use Protected & self-ID instead. Unknown company-specific questions remain contextual rather than being guessed into a global meaning."
            )
        else:
            st.success("Reusable semantic answer saved for future applications.")
            st.rerun()


def _render_coverage(readiness: dict[str, Any]) -> None:
    delta_keys = {
        str(row.get("question_key") or "")
        for row in answer_autofill.onboarding_delta(readiness)
    }
    rows = [dict(row) for row in readiness.get("rows") or []]
    protected: list[dict[str, str]] = []
    contextual: list[dict[str, str]] = []
    reusable: list[dict[str, str]] = []

    for row in rows:
        if row.get("ready"):
            continue
        key = str(row.get("question_key") or "")
        item = {
            "Meaning": _humanize_key(key),
            "Example": str(row.get("example_question") or ""),
            "Status": "",
        }
        sensitivity = str(row.get("sensitivity_class") or "")
        if key in delta_keys:
            item["Status"] = "Reusable if you choose"
            reusable.append(item)
        elif sensitivity in {"PROTECTED", "SELF_ID"}:
            item["Status"] = "Encrypted setup / policy"
            protected.append(item)
        else:
            item["Status"] = "Handled when an application needs it"
            contextual.append(item)

    st.markdown("#### What does the remaining coverage mean?")
    st.caption(
        "A meaning can be intentionally application-time or protected. It is not automatically a missing onboarding answer. The internal provider-phrasing catalog remains hidden."
    )
    if reusable:
        st.markdown("**Reusable gaps**")
        st.dataframe(reusable, use_container_width=True, hide_index=True)
    if protected:
        st.markdown("**Protected / self-ID setup**")
        st.dataframe(protected, use_container_width=True, hide_index=True)
    if contextual:
        st.markdown("**Application-time meanings**")
        st.dataframe(contextual, use_container_width=True, hide_index=True)
    if not any((reusable, protected, contextual)):
        st.success("All current semantic meanings already have an authority path.")


def render(authority: dict[str, Any]) -> None:
    readiness = answer_router.readiness_projection()
    summary = coverage_breakdown(readiness)

    st.markdown("### Application Answer Center")
    st.write(
        "MUNSHI prepares answers from Candidate Truth, encrypted protected defaults and previously confirmed reusable answers. Deterministic matching runs first for MUNSHI Apply and AutoApply; Claude may interpret an unusual control only after deterministic matching fails and never invents candidate facts."
    )

    profile_confirmed = bool(authority.get("profile", {}).get("confirmed"))
    if not profile_confirmed:
        st.warning("Confirm Candidate Truth once before automatic answer preparation.")

    if st.button(
        "Prepare / refresh answers from Candidate Truth",
        type="primary",
        use_container_width=True,
        disabled=not profile_confirmed,
        key="answer_center_prepare_truth",
    ):
        try:
            result = answer_autofill.prepare_from_candidate_truth()
        except Exception as error:  # noqa: BLE001
            st.error(str(error))
        else:
            st.success(
                f"Prepared or refreshed {result['prepared_count']} truth-bound deterministic answer(s)."
            )
            st.rerun()

    readiness = answer_router.readiness_projection()
    summary = coverage_breakdown(readiness)
    metrics = st.columns(4, gap="small")
    metrics[0].metric("Known meanings", f"{summary['known']}/{summary['total']}")
    metrics[1].metric("Autofill ready now", summary["autofill_ready"])
    metrics[2].metric("Reusable gaps", summary["reusable_gaps"])
    metrics[3].metric("Protected ready", summary["protected_ready"])

    unresolved = max(0, summary["total"] - summary["known"])
    if unresolved:
        st.info(
            f"{unresolved} semantic meaning(s) are not currently counted as known. That does not mean {unresolved} onboarding questions: some are protected/self-ID choices and some are intentionally handled in the context of a real job."
        )
    else:
        st.success("Every current semantic meaning has an authority path.")

    reusable_tab, protected_tab, teach_tab, coverage_tab = st.tabs(
        ["Complete reusable profile", "Protected & self-ID", "Teach MUNSHI", "Coverage"]
    )
    with reusable_tab:
        _render_reusable(readiness)
    with protected_tab:
        _render_protected(readiness)
    with teach_tab:
        _render_teach()
    with coverage_tab:
        _render_coverage(readiness)

    st.caption(
        "Provider wording stays internal. MUNSHI stores/reuses only confirmed semantic answers and asks contextually when a real application needs something new."
    )
