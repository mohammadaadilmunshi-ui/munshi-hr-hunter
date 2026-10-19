"""Unified Profile workspace for pre-AutoApply readiness.

V4 preserves the established Resume, Cover Letter, Candidate Truth and encrypted
profile-editing paths while layering the first-class Master Resume card and the
runtime-safe Answer Center. Heavy Profile Details panels are routed lazily so a
hidden panel cannot create duplicate widgets or trigger unintended side effects.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app import application_answer_router_v1 as answer_router
from app import candidate_profile_authority_v1 as profile_authority
from app import candidate_knowledge_center_v1 as knowledge_center
from app import master_resume_visual_ui_v2 as master_resume_ui
from app import profile_answer_center_ui_v3 as answer_center
from app import profile_workspace_v3 as v3
from app.product_ui import page_intro


_DETAIL_SECTIONS = ("Candidate Truth", "Knowledge Center", "Application Answers", "Master Resume")


def _open_unanswered_answers() -> None:
    st.session_state["profile_v4_detail_section"] = "Application Answers"
    st.session_state["answer_center_v3_section"] = "Coverage details"


def _status_cards(authority: dict[str, Any], answers: dict[str, Any]) -> None:
    cards = st.columns(4, gap="small")
    profile = authority["profile"]
    summary = answer_center.coverage_breakdown(answers)
    remaining = max(0, summary["total"] - summary["known"])

    with cards[0]:
        st.metric("Candidate Truth", "Confirmed" if profile["confirmed"] else "Needs review")
        st.caption(f"{int(authority.get('structured_fact_count') or 0)} structured facts")
    with cards[1]:
        st.metric("Answer coverage", f"{summary['known']}/{summary['total']}")
        st.caption(
            (
                f"{remaining} unanswered question meaning{'s' if remaining != 1 else ''} "
                "still need a secure or application-time path; they are not mandatory onboarding questions"
            )
            if remaining
            else "Every current semantic meaning has an authority path"
        )
        if remaining:
            st.button(
                f"Review {remaining} unanswered",
                key="profile_v4_review_unanswered",
                type="primary",
                use_container_width=True,
                on_click=_open_unanswered_answers,
            )
    with cards[2]:
        st.metric("Autofill ready", summary["autofill_ready"])
        st.caption(
            f"Reusable gaps: {summary['reusable_gaps']} · Protected ready: {summary['protected_ready']} configured"
        )
    with cards[3]:
        ready = authority["ready_for_application_planning"] and bool(summary["known"])
        st.metric("AutoApply foundation", "Ready to test" if ready else "Needs setup")
        st.caption("Final-submit authority remains separate")


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
            "Candidate Truth is not confirmed against the current Master Resume. Review and confirm it before an application can use this source."
        )

    st.caption(
        "Preview, source editing, replacement, source-text download and Resume Studio editing are available from the Master Resume card above."
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


def _knowledge_value_editor(row: dict[str, Any]) -> Any:
    value = row.get("value")
    value_type = str(row.get("value_type") or "text").casefold()
    key = f"knowledge_edit_value_{row['concept_id'].replace('.', '_')}"
    if value_type in {"boolean", "bool"}:
        return st.checkbox("Value", value=bool(value), key=key)
    if value_type in {"number", "integer", "float"}:
        try:
            initial = float(value or 0)
        except (TypeError, ValueError):
            initial = 0.0
        return st.number_input("Value", value=initial, key=key)
    if value_type in {"list", "array"}:
        initial = ", ".join(str(item) for item in (value or [])) if isinstance(value, list) else str(value or "")
        raw = st.text_area("Values (comma separated)", value=initial, key=key)
        return [part.strip() for part in raw.split(",") if part.strip()]
    return st.text_input("Value", value="" if value is None else str(value), key=key)


def _knowledge_center_workspace(knowledge: dict[str, Any]) -> None:
    st.markdown("### Candidate Knowledge Center")
    st.caption(
        "Canonical facts and evidence MUNSHI can reuse across employers. Employer wording is not the identity; the concept is."
    )
    if not knowledge.get("ready"):
        st.warning(str(knowledge.get("reason") or "Candidate Knowledge is not ready yet."))
        return

    cols = st.columns(4, gap="small")
    with cols[0]:
        st.metric("Canonical facts", int(knowledge.get("fact_count") or 0))
    with cols[1]:
        st.metric("Evidence claims", int(knowledge.get("claim_count") or 0))
        st.caption(f"Reusable answer memory: {int(knowledge.get('answer_memory_count') or 0)}")
    with cols[2]:
        st.metric("Knowledge revision", int(knowledge.get("knowledge_revision") or 0))
    with cols[3]:
        st.metric("Open conflicts", len(knowledge.get("conflicts") or []))

    conflicts = list(knowledge.get("conflicts") or [])
    if conflicts:
        st.warning(
            f"{len(conflicts)} source disagreement{'s' if len(conflicts) != 1 else ''} recorded. "
            "A higher-authority user correction can win safely; equal-authority conflicts fail closed."
        )

    sections = dict(knowledge.get("sections") or {})
    editable: list[dict[str, Any]] = []
    for section_name, rows in sections.items():
        rows = [dict(row) for row in rows if isinstance(row, dict)]
        if not rows:
            continue
        with st.expander(f"{section_name} · {len(rows)}", expanded=section_name in {"Identity", "Skills & tools"}):
            for row in rows:
                concept_id = str(row.get("concept_id") or "")
                protected = bool(row.get("protected"))
                source = str(row.get("source_type") or "Unknown")
                trust = str(row.get("trust_level") or "UNKNOWN").replace("_", " ").title()
                confidence = float(row.get("confidence") or 0.0)
                if protected:
                    display = "Configured in protected policy store" if row.get("value_reference_available") else "Not configured"
                else:
                    raw_value = row.get("value")
                    if isinstance(raw_value, list):
                        display = ", ".join(str(item) for item in raw_value)
                    elif isinstance(raw_value, dict):
                        display = "Structured value"
                    else:
                        display = str(raw_value) if raw_value is not None else "Not set"
                st.markdown(f"**{row.get('label') or concept_id}**  \n`{concept_id}`")
                st.write(display)
                st.caption(
                    f"Source: {source} · Trust: {trust} · Confidence: {confidence:.0%} · Autofill: {row.get('autofill_policy') or 'policy'}"
                )
                evidence_text = str(row.get("evidence_text") or "").strip()
                if evidence_text:
                    st.caption(f"Evidence: {evidence_text}")
                if row.get("editable") and not protected:
                    editable.append(row)
                st.divider()

    if editable:
        st.markdown("#### Correct a canonical fact")
        st.caption(
            "A correction here is stored once as user-confirmed canonical knowledge and takes precedence over older extracted values."
        )
        options = {str(row["concept_id"]): row for row in editable}
        selected = st.selectbox(
            "Fact",
            list(options),
            format_func=lambda key: f"{options[key].get('label') or key} · {key}",
            key="knowledge_center_edit_concept",
        )
        row = options[selected]
        edited = _knowledge_value_editor(row)
        if st.button("Save canonical correction", type="primary", key="knowledge_center_save"):
            try:
                knowledge_center.save_normal_fact(concept_id=selected, value=edited)
            except Exception as error:  # noqa: BLE001
                st.error(str(error))
            else:
                st.success("Canonical knowledge updated. Future equivalent employer questions will use this fact.")
                st.rerun()

    st.markdown("#### Add knowledge")
    st.caption(
        "Add a missing safe fact or an explicit skill/tool once. MUNSHI stores the canonical meaning, not an employer-specific question."
    )
    available = [dict(row) for row in knowledge.get("available_to_add") or [] if isinstance(row, dict)]
    if available:
        add_options = {str(row["concept_id"]): row for row in available}
        selected_new = st.selectbox(
            "Missing fact",
            [""] + list(add_options),
            format_func=lambda key: "Choose a fact…" if not key else f"{add_options[key].get('label') or key} · {key}",
            key="knowledge_center_add_concept",
        )
        if selected_new:
            new_row = add_options[selected_new]
            description = str(new_row.get("description") or "").strip()
            if description:
                st.caption(description)
            new_value = _knowledge_value_editor(new_row)
            if st.button("Add canonical fact", type="primary", key="knowledge_center_add_fact"):
                try:
                    knowledge_center.save_normal_fact(concept_id=selected_new, value=new_value)
                except Exception as error:  # noqa: BLE001
                    st.error(str(error))
                else:
                    st.success("Canonical fact added. Equivalent employer wording can reuse it automatically.")
                    st.rerun()
    else:
        st.caption("All currently editable canonical fact types already have a value.")

    explicit_skill = st.text_input(
        "Add an explicit skill or tool",
        placeholder="Example: Snowflake, Rippling, Jira, SQL…",
        key="knowledge_center_add_skill",
    )
    if st.button("Add skill/tool", key="knowledge_center_add_skill_button"):
        try:
            knowledge_center.save_explicit_skill(label=explicit_skill)
        except Exception as error:  # noqa: BLE001
            st.error(str(error))
        else:
            st.success("Skill/tool added as user-confirmed canonical knowledge.")
            st.rerun()


def _detail_navigation(authority: dict[str, Any], answers: dict[str, Any], knowledge: dict[str, Any]) -> str:
    """Render Profile Details as first-class authority work areas."""
    current = str(st.session_state.get("profile_v4_detail_section") or "Candidate Truth")
    if current not in _DETAIL_SECTIONS:
        current = "Candidate Truth"
        st.session_state["profile_v4_detail_section"] = current

    summary = answer_center.coverage_breakdown(answers)
    remaining = max(0, summary["total"] - summary["known"])
    facts = int(authority.get("structured_fact_count") or 0)
    master = dict(authority.get("master_resume") or {})

    st.markdown("### Profile workspace")
    st.caption(
        "Review source truth, canonical reusable knowledge, application meanings, and the Master Resume evidence authority."
    )
    cols = st.columns(4, gap="medium")
    cards = (
        (
            "Candidate Truth",
            f"{facts} verified facts",
            "Review the structured source facts MUNSHI is allowed to treat as candidate truth.",
        ),
        (
            "Knowledge Center",
            f"{int(knowledge.get('fact_count') or 0)} canonical facts",
            "See and correct the reusable concepts Universal Brain searches regardless of employer wording.",
        ),
        (
            "Application Answers",
            f"{summary['known']}/{summary['total']} known · {remaining} remaining",
            "See reusable, protected and application-time meanings and how each will be handled.",
        ),
        (
            "Master Resume",
            "Active authority" if master.get("configured") else "Not configured",
            "Inspect the evidence source that Candidate Truth and resume generation are bound to.",
        ),
    )

    for col, (section, status, description) in zip(cols, cards):
        with col:
            with st.container(border=True):
                st.markdown(f"**{section}**")
                st.caption(status)
                st.write(description)
                if st.button(
                    "Open" if current != section else "Viewing",
                    key=f"profile_v4_open_{section.lower().replace(' ', '_')}",
                    type="primary" if current == section else "secondary",
                    use_container_width=True,
                    disabled=current == section,
                ):
                    st.session_state["profile_v4_detail_section"] = section
                    st.rerun()

    return current


def render() -> None:
    v3.v1._inject_css()
    page_intro(
        "Your foundation", "Profile",
        "Manage your experience, source resume, and reusable application answers in one place.",
    )
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
    knowledge = knowledge_center.snapshot()

    st.markdown("## Candidate Profile & Application Truth")
    st.caption(
        "One profile authority for Master Resume evidence, Candidate Truth, reusable answers and protected application defaults."
    )

    # One and only one mount of the interactive Master Resume control surface.
    master_resume_ui.render_compact(authority)
    if st.session_state.pop("_profile_intake_saved", False):
        st.success("Master Resume source saved. Build and review your structured profile below; application authority has not changed.")
    _status_cards(authority, answers)

    for warning in authority.get("warnings") or []:
        st.warning(str(warning))

    section = _detail_navigation(authority, answers, knowledge)
    if section == "Candidate Truth":
        _candidate_truth_workspace()
    elif section == "Knowledge Center":
        _knowledge_center_workspace(knowledge)
    elif section == "Application Answers":
        answer_center.render(authority)
    else:
        _master_resume_details(authority)
