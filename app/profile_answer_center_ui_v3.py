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

# Candidate-owned values observed in the provider-neutral six-page Workday
# reference flow.  These are semantic concepts, never employer selectors or
# candidate-specific values.  Runtime-only / optional policies are deliberately
# kept out of the "unanswered" count below.
_WORKDAY_PROFILE_QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "background.prior_employee",
        "label": "Previously worked for this employer?",
        "field": "prior_employee",
        "editor": "boolean",
        "sensitivity": "NORMAL",
        "storage": "encrypted_candidate_details",
    },
    {
        "key": "application.phone_device_type",
        "label": "Phone device type",
        "field": "phone_device_type",
        "editor": "phone_device",
        "sensitivity": "NORMAL",
        "storage": "encrypted_candidate_details",
    },
    {
        "key": "contact.phone_country_code",
        "label": "Country phone code",
        "field": "phone_country_code",
        "editor": "short_text",
        "sensitivity": "NORMAL",
        "storage": "encrypted_candidate_details",
    },
    {
        "key": "work.location_eligibility",
        "label": "Eligible to work in the job location?",
        "field": "location_work_eligible",
        "editor": "boolean",
        "sensitivity": "PROTECTED",
        "storage": "encrypted_candidate_details",
    },
    {
        "key": "eligibility.age_18_plus",
        "label": "At least 18 years old?",
        "field": "age_18_plus",
        "editor": "boolean",
        "sensitivity": "PROTECTED",
        "storage": "encrypted_candidate_details",
    },
    {
        "key": "work.sponsorship.future",
        "label": "Require visa sponsorship now or in the future?",
        "field": "sponsorship_required",
        "editor": "boolean",
        "sensitivity": "PROTECTED",
        "storage": "encrypted_candidate_details",
    },
    {
        "key": "compensation.salary",
        "label": "Salary requirements",
        "editor": "salary",
        "sensitivity": "NORMAL",
        "storage": "answer_vault",
        "catalog_question": "What are your salary expectations?",
    },
    {
        "key": "self_id.gender",
        "label": "Gender",
        "field": "gender",
        "editor": "gender",
        "sensitivity": "SELF_ID",
        "storage": "encrypted_candidate_details",
    },
    {
        "key": "self_id.race_ethnicity",
        "label": "Race / ethnicity",
        "field": ("race_ethnicity", "ethnicity"),
        "editor": "race_ethnicity",
        "sensitivity": "SELF_ID",
        "storage": "encrypted_candidate_details",
    },
    {
        "key": "self_id.hispanic_latino",
        "label": "Hispanic or Latino",
        "field": "hispanic_latino",
        "editor": "boolean",
        "sensitivity": "SELF_ID",
        "storage": "encrypted_candidate_details",
    },
    {
        "key": "self_id.veteran",
        "label": "Veteran status",
        "field": "veteran",
        "editor": "veteran",
        "sensitivity": "SELF_ID",
        "storage": "encrypted_candidate_details",
    },
    {
        "key": "self_id.disability",
        "label": "Disability self-identification",
        "field": "disability",
        "editor": "disability",
        "sensitivity": "SELF_ID",
        "storage": "encrypted_candidate_details",
    },
)

_WORKDAY_RUNTIME_POLICIES: tuple[tuple[str, str], ...] = (
    (
        "Referral source",
        "Derived from the exact job/source context at application time; never stored as a candidate fact.",
    ),
    (
        "Self-identification name",
        "Uses the confirmed legal full name from Candidate Truth.",
    ),
    (
        "Self-identification date",
        "Generated from the controlled execution date when the form is completed.",
    ),
    (
        "Terms and conditions",
        "Authorized only by the exact execution mandate and exact provider terms identity; never saved as a standing Yes.",
    ),
    (
        "Employee ID",
        "Post-offer only. Blank is correct for an ordinary applicant unless an explicit employee ID exists.",
    ),
    (
        "Phone extension",
        "Optional. Blank is a valid confirmed state and phone digits are never copied into it.",
    ),
)


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


def _workday_profile_value(values: Mapping[str, Any], field: Any) -> Any:
    if isinstance(field, tuple):
        for item in field:
            value = values.get(str(item))
            if v2._configured(value):
                return value
        return None
    return values.get(str(field))


def workday_readiness_projection(
    readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Value-free Workday readiness for the Profile UI.

    The projection reports only configured/not-configured state. Protected and
    self-ID plaintext stays inside the encrypted candidate-details store.
    """
    if readiness is None:
        readiness = v2.answer_router.readiness_projection()
    try:
        details = v2.profile_details.load_candidate_profile_details()
    except Exception:  # noqa: BLE001 - readiness must fail closed.
        details = {}

    readiness_rows = {
        str(row.get("question_key") or ""): dict(row)
        for row in readiness.get("rows") or []
        if isinstance(row, Mapping)
    }

    rows: list[dict[str, Any]] = []
    for raw in _WORKDAY_PROFILE_QUESTIONS:
        spec = dict(raw)
        if spec["storage"] == "answer_vault":
            catalog_row = readiness_rows.get(str(spec["key"]), {})
            configured = bool(catalog_row.get("ready"))
        else:
            configured = v2._configured(
                _workday_profile_value(details, spec.get("field"))
            )
        rows.append(
            {
                "question_key": str(spec["key"]),
                "label": str(spec["label"]),
                "sensitivity": str(spec["sensitivity"]),
                "storage": str(spec["storage"]),
                "configured": configured,
                "editor": str(spec["editor"]),
            }
        )

    unresolved = [row for row in rows if not row["configured"]]
    return {
        "rows": rows,
        "configured_count": len(rows) - len(unresolved),
        "total_count": len(rows),
        "unanswered": unresolved,
        "runtime_policy_count": len(_WORKDAY_RUNTIME_POLICIES),
    }


def _workday_spec(question_key: str) -> dict[str, Any]:
    for raw in _WORKDAY_PROFILE_QUESTIONS:
        if str(raw["key"]) == str(question_key):
            return dict(raw)
    raise LookupError(f"Unknown Workday profile question: {question_key}")


def _save_workday_answer(spec: Mapping[str, Any], value: Any) -> None:
    """Persist one explicit user answer through its existing authority path."""
    if spec.get("storage") == "answer_vault":
        text = str(value or "").strip()
        if not text:
            raise ValueError("Enter the exact reusable salary answer you want MUNSHI to use.")
        v2.answer_router.save_user_answer(
            str(spec["catalog_question"]),
            text,
            autofill_allowed=True,
        )
        return

    field = spec.get("field")
    current = dict(v2.profile_details.load_candidate_profile_details())
    if isinstance(field, tuple):
        # race_ethnicity is canonical; ethnicity remains a backward-compatible
        # mirror so older encrypted records/readers keep working.
        for item in field:
            current[str(item)] = value
    else:
        current[str(field)] = value
    v2.profile_details.save_candidate_profile_details(current)


def _workday_answer_editor(spec: Mapping[str, Any]) -> tuple[bool, Any]:
    editor = str(spec.get("editor") or "short_text")
    key = str(spec["key"]).replace(".", "_")
    if editor in {"boolean", "veteran", "disability"}:
        labels = {
            "boolean": ("Yes", "No"),
            "veteran": ("Yes — veteran", "No — not a veteran"),
            "disability": ("Yes — disability / history of disability", "No — no disability / history"),
        }[editor]
        selected = st.selectbox(
            "Answer",
            ["Choose answer…", *labels],
            key=f"answer_center_v3_workday_value_{key}",
        )
        if selected == "Choose answer…":
            return False, None
        return True, selected.startswith("Yes")

    if editor == "phone_device":
        selected = st.selectbox(
            "Answer",
            ["Choose answer…", "Mobile", "Home", "Work", "Other"],
            key=f"answer_center_v3_workday_value_{key}",
        )
        return (selected != "Choose answer…", "" if selected == "Choose answer…" else selected)

    if editor == "gender":
        selected = st.selectbox(
            "Answer",
            [
                "Choose answer…",
                "Male",
                "Female",
                "Non-binary / another identity",
                "Decline to self-identify",
                "Custom…",
            ],
            key=f"answer_center_v3_workday_value_{key}",
        )
        if selected == "Custom…":
            custom = st.text_input(
                "Exact self-identification",
                key=f"answer_center_v3_workday_custom_{key}",
            ).strip()
            return bool(custom), custom
        return (selected != "Choose answer…", "" if selected == "Choose answer…" else selected)

    if editor == "race_ethnicity":
        selected = st.selectbox(
            "Answer",
            [
                "Choose answer…",
                "American Indian or Alaska Native",
                "Asian",
                "Black or African American",
                "Native Hawaiian or Other Pacific Islander",
                "White",
                "Two or More Races",
                "Decline to self-identify",
                "Custom…",
            ],
            key=f"answer_center_v3_workday_value_{key}",
        )
        if selected == "Custom…":
            custom = st.text_input(
                "Exact self-identification",
                key=f"answer_center_v3_workday_custom_{key}",
            ).strip()
            return bool(custom), custom
        return (selected != "Choose answer…", "" if selected == "Choose answer…" else selected)

    if editor == "salary":
        text = st.text_input(
            "Exact reusable answer",
            placeholder="Enter only the salary answer you want reused.",
            key=f"answer_center_v3_workday_value_{key}",
        ).strip()
        return bool(text), text

    text = st.text_input(
        "Answer",
        placeholder="Enter the exact value to save.",
        key=f"answer_center_v3_workday_value_{key}",
    ).strip()
    return bool(text), text


def _render_workday_readiness(readiness: Mapping[str, Any]) -> None:
    projection = workday_readiness_projection(readiness)
    unanswered = list(projection["unanswered"])

    st.markdown("#### Workday profile readiness")
    st.caption(
        "This is separate from the legacy 50-meaning catalog. It covers the extra candidate-owned "
        "Workday fields observed in the universal six-page flow while preserving encrypted/self-ID boundaries."
    )

    left, right = st.columns((1.15, 1.0), gap="large")
    with left:
        st.markdown(f"**Not answered yet · {len(unanswered)}**")
        st.caption(
            f"{projection['configured_count']}/{projection['total_count']} candidate-owned Workday answers already have an authority path."
        )
        if not unanswered:
            st.success("All candidate-owned Workday profile answers are configured.")
        else:
            for row in unanswered:
                with st.container(border=True):
                    st.markdown(f"**{row['label']}**")
                    sensitivity = str(row["sensitivity"]).replace("_", " ").title()
                    storage = (
                        "Encrypted Candidate Truth"
                        if row["storage"] == "encrypted_candidate_details"
                        else "Reusable Answer Vault"
                    )
                    st.caption(f"{sensitivity} · {storage}")
                    st.write("Not configured — MUNSHI will not guess this answer.")

    with right:
        st.markdown("**Resolve an unanswered Workday question**")
        if not unanswered:
            st.info("Nothing to resolve. Existing values can still be changed from Candidate Truth / protected defaults.")
        else:
            option_map = {str(row["question_key"]): row for row in unanswered}
            selected = st.selectbox(
                "Question",
                list(option_map),
                format_func=lambda key: str(option_map[key]["label"]),
                key="answer_center_v3_workday_question",
            )
            spec = _workday_spec(selected)
            sensitivity = str(spec["sensitivity"])
            if sensitivity in {"PROTECTED", "SELF_ID"}:
                st.info(
                    "This value is candidate-entered only and is saved in encrypted Candidate Truth. "
                    "It is never copied into the normal plaintext Answer Vault."
                )
            valid, value = _workday_answer_editor(spec)
            if st.button(
                "Save Workday answer",
                type="primary",
                use_container_width=True,
                key="answer_center_v3_workday_save",
                disabled=not valid,
            ):
                try:
                    _save_workday_answer(spec, value)
                except Exception as error:  # noqa: BLE001
                    st.error(str(error))
                else:
                    st.success("Saved through the existing MUNSHI truth/answer authority.")
                    st.rerun()

    with st.expander("Workday fields that intentionally do not need a standing saved answer", expanded=False):
        for label, handling in _WORKDAY_RUNTIME_POLICIES:
            st.markdown(f"**{label}**")
            st.caption(handling)


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
    _render_workday_readiness(readiness)
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
