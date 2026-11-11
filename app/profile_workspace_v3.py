"""Profile Workspace V3: encrypted candidate editing over immutable resume evidence."""
from __future__ import annotations

from typing import Any

import streamlit as st

from app import profile_workspace_v1 as v1
from app import profile_workspace_v2 as v2
from app import native_resume_service_v3 as v3
from app.profile_truth_overrides_v1 import (
    EDITABLE_SECTIONS,
    load_profile_overrides,
    override_encryption_available,
    reset_all_profile_overrides,
    reset_profile_section,
    resolve_profile,
    save_profile_override,
)
from app.resume_profile_details_v31 import (
    candidate_profile_details_encryption_available,
    load_candidate_profile_details,
    save_candidate_profile_details,
)

_SECTION_LABELS = {
    "professional_summary": "Professional summary",
    "contact": "Contact",
    "education": "Education",
    "experience": "Experience",
    "projects": "Projects",
    "skills": "Skills",
    "certifications": "Certifications",
    "languages": "Languages",
    "application_defaults": "Application defaults & self-ID",
}


def _split(value: str, separator: str = "||") -> list[str]:
    return [part.strip() for part in str(value or "").split(separator) if part.strip()]


def _join(values: list[str], separator: str = " || ") -> str:
    return separator.join(str(value).strip() for value in values or [] if str(value).strip())


def _bool_value(value: Any) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "Not answered"


def _bool_select(label: str, value: Any, *, key: str) -> bool | None:
    options = ["Not answered", "Yes", "No"]
    selected = st.selectbox(label, options, index=options.index(_bool_value(value)), key=key)
    return {"Not answered": None, "Yes": True, "No": False}[selected]


def _rows(value: Any, fields: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in value or []:
        row: dict[str, str] = {}
        for field, _label, kind in fields:
            raw = item.get(field) if isinstance(item, dict) else ""
            if kind == "list_pipe":
                row[field] = _join(raw or [])
            elif kind == "list_comma":
                row[field] = ", ".join(str(part).strip() for part in raw or [] if str(part).strip())
            else:
                row[field] = str(raw or "")
        rows.append(row)
    return rows


def _from_rows(rows: Any, fields: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in list(rows or []):
        item = dict(raw)
        if not any(str(item.get(field) or "").strip() for field, _label, _kind in fields):
            continue
        normalized: dict[str, Any] = {}
        for field, _label, kind in fields:
            value = str(item.get(field) or "").strip()
            if kind == "list_pipe":
                normalized[field] = _split(value)
            elif kind == "list_comma":
                normalized[field] = [part.strip() for part in value.split(",") if part.strip()]
            else:
                normalized[field] = value
        output.append(normalized)
    return output


def _data_editor(label: str, value: Any, fields: list[tuple[str, str, str]], *, key: str) -> Any:
    st.caption(label)
    column_config = {field: st.column_config.TextColumn(display) for field, display, _kind in fields}
    return st.data_editor(
        _rows(value, fields),
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config=column_config,
        key=key,
    )


def _edit_profile_section(extracted: dict[str, Any], profile: dict[str, Any], section: str) -> None:
    form_key = f"profile_v3_form_{extracted.get('extraction_id')}_{section}"
    with st.form(form_key):
        if section == "professional_summary":
            value: Any = st.text_area(
                "Professional summary",
                value=str(profile.get(section) or ""),
                height=180,
            )
        elif section == "contact":
            contact = dict(profile.get("contact") or {})
            left, right = st.columns(2)
            with left:
                full_name = st.text_input("Full name", value=str(contact.get("full_name") or ""))
                location = st.text_input("Location", value=str(contact.get("location") or ""))
                email = st.text_input("Email", value=str(contact.get("email") or ""))
            with right:
                phone = st.text_input("Phone", value=str(contact.get("phone") or ""))
                linkedin = st.text_input("LinkedIn", value=str(contact.get("linkedin") or ""))
                portfolio = st.text_input("Portfolio / website", value=str(contact.get("portfolio") or ""))
            value = {
                "full_name": full_name,
                "location": location,
                "email": email,
                "phone": phone,
                "linkedin": linkedin,
                "portfolio": portfolio,
            }
        elif section == "education":
            fields = [
                ("institution", "Institution", "text"), ("degree", "Degree", "text"),
                ("field", "Field", "text"), ("location", "Location", "text"),
                ("start_date", "Start", "text"), ("end_date", "End", "text"),
                ("gpa", "GPA", "text"), ("details", "Details (use || between items)", "list_pipe"),
            ]
            edited = _data_editor("Add, remove, or edit education records.", profile.get(section), fields, key=f"{form_key}_grid")
            value = _from_rows(edited, fields)
        elif section == "experience":
            fields = [
                ("employer", "Employer", "text"), ("title", "Title", "text"),
                ("location", "Location", "text"), ("start_date", "Start", "text"),
                ("end_date", "End", "text"), ("bullets", "Bullets (use || between bullets)", "list_pipe"),
            ]
            edited = _data_editor("Edit experience records without changing the underlying Master Resume evidence.", profile.get(section), fields, key=f"{form_key}_grid")
            value = _from_rows(edited, fields)
        elif section == "projects":
            fields = [
                ("name", "Project", "text"), ("description", "Description", "text"),
                ("tools", "Tools (comma separated)", "list_comma"),
                ("bullets", "Bullets (use || between bullets)", "list_pipe"),
            ]
            edited = _data_editor("Edit projects and tools.", profile.get(section), fields, key=f"{form_key}_grid")
            value = _from_rows(edited, fields)
        elif section == "skills":
            fields = [("category", "Category", "text"), ("skills", "Skills (comma separated)", "list_comma")]
            edited = _data_editor("Group skills into reusable categories.", profile.get(section), fields, key=f"{form_key}_grid")
            value = _from_rows(edited, fields)
        elif section == "certifications":
            fields = [
                ("name", "Certification", "text"), ("issuer", "Issuer", "text"),
                ("date", "Date", "text"), ("credential_id", "Credential ID", "text"),
            ]
            edited = _data_editor("Edit certifications. Public issuer names are also used for logo resolution.", profile.get(section), fields, key=f"{form_key}_grid")
            value = _from_rows(edited, fields)
        elif section == "languages":
            value = [line.strip() for line in st.text_area(
                "Languages — one per line",
                value="\n".join(str(item) for item in profile.get(section) or []),
                height=140,
            ).splitlines() if line.strip()]
        else:
            st.error("Unsupported profile section.")
            return

        save = st.form_submit_button("Save encrypted profile edit", type="primary", use_container_width=True)
    if save:
        try:
            save_profile_override(extracted, section, value)
        except Exception as error:
            st.error(str(error))
        else:
            st.success(f"{_SECTION_LABELS[section]} saved as an encrypted candidate-confirmed override.")
            st.rerun()


def _edit_application_defaults() -> None:
    current = load_candidate_profile_details()
    with st.form("profile_v3_application_defaults"):
        st.markdown("#### Application defaults")
        left, right = st.columns(2)
        with left:
            country = st.text_input("Work authorization country", value=str(current.get("work_authorization_country") or ""))
            basis = st.text_input("Authorization basis", value=str(current.get("authorization_basis") or ""))
            permit = st.text_input("Visa / work permit", value=str(current.get("visa_or_permit") or ""))
            status = st.text_input("Authorization status", value=str(current.get("authorization_status") or ""))
            authorized = _bool_select("Authorized to work", current.get("authorized_to_work"), key="profile_v3_authorized")
            sponsorship = _bool_select("Needs sponsorship", current.get("sponsorship_required"), key="profile_v3_sponsorship")
        with right:
            in_person = _bool_select("In-person OK", current.get("in_person_ok"), key="profile_v3_in_person")
            relocate = _bool_select("Can relocate", current.get("willing_to_relocate"), key="profile_v3_relocate")
            immediate = _bool_select("Can start immediately", current.get("start_immediately"), key="profile_v3_immediate")
            transport = _bool_select("Has reliable transportation", current.get("has_transport"), key="profile_v3_transport")
            accommodations = _bool_select("Needs accommodations", current.get("needs_accommodations"), key="profile_v3_accommodations")
            modes = st.multiselect(
                "Work modes",
                ["Remote", "Hybrid", "On-site"],
                default=[mode for mode in current.get("work_modes") or [] if mode in {"Remote", "Hybrid", "On-site"}],
            )

        st.markdown("#### Background")
        b1, b2, b3 = st.columns(3)
        with b1:
            prior = _bool_select("Prior employee", current.get("prior_employee"), key="profile_v3_prior")
        with b2:
            clearance = _bool_select("Government clearance", current.get("government_clearance"), key="profile_v3_clearance")
        with b3:
            ties = _bool_select("Government ties", current.get("government_ties"), key="profile_v3_ties")

        st.markdown("#### Voluntary self-identification")
        st.caption("These fields are candidate-entered only. MUNSHI never infers them from your name, resume, schools, location, or other proxies.")
        s1, s2 = st.columns(2)
        with s1:
            gender = st.text_input("Gender (optional)", value=str(current.get("gender") or ""))
            veteran = _bool_select("Veteran", current.get("veteran"), key="profile_v3_veteran")
        with s2:
            ethnicity = st.text_input("Ethnicity (optional)", value=str(current.get("ethnicity") or ""))
            disability = _bool_select("Disability", current.get("disability"), key="profile_v3_disability")

        open_to_work = _bool_select("Open to work", current.get("open_to_work"), key="profile_v3_open_to_work")
        save = st.form_submit_button("Save encrypted application defaults", type="primary", use_container_width=True)

    if save:
        values = dict(current)
        values.update({
            "open_to_work": open_to_work,
            "work_authorization_country": country,
            "authorization_basis": basis,
            "visa_or_permit": permit,
            "authorization_status": status,
            "authorized_to_work": authorized,
            "sponsorship_required": sponsorship,
            "in_person_ok": in_person,
            "willing_to_relocate": relocate,
            "start_immediately": immediate,
            "has_transport": transport,
            "needs_accommodations": accommodations,
            "work_modes": modes,
            "prior_employee": prior,
            "government_clearance": clearance,
            "government_ties": ties,
            "gender": gender,
            "ethnicity": ethnicity,
            "veteran": veteran,
            "disability": disability,
        })
        try:
            save_candidate_profile_details(values)
        except Exception as error:
            st.error(str(error))
        else:
            st.success("Application defaults saved as AES-GCM encrypted candidate data.")
            st.rerun()


def _editor(extracted: dict[str, Any], profile: dict[str, Any]) -> None:
    st.markdown("### Edit Profile Details")
    if not override_encryption_available() or not candidate_profile_details_encryption_available():
        st.warning(
            "Encrypted profile editing is unavailable because the MUNSHI vault is not configured. No profile edit will be written to plaintext storage."
        )
        return

    envelope = load_profile_overrides(str(extracted.get("extraction_id")))
    if envelope.sections:
        st.caption(
            f"Encrypted candidate overrides active: {len(envelope.sections)} section(s) · revision {envelope.revision}. The Master Resume extraction remains unchanged."
        )
    label_to_section = {label: section for section, label in _SECTION_LABELS.items()}
    selected_label = st.selectbox("Section to edit", list(label_to_section), key="profile_v3_edit_section")
    section = label_to_section[selected_label]
    if section == "application_defaults":
        _edit_application_defaults()
        return

    _edit_profile_section(extracted, profile, section)
    if section in envelope.sections:
        if st.button("Reset this section to Master Resume extraction", key=f"profile_v3_reset_{section}"):
            try:
                reset_profile_section(extracted, section)
            except Exception as error:
                st.error(str(error))
            else:
                st.success("Candidate override removed. The extracted Master Resume value is active again.")
                st.rerun()


def _profile_controls(extracted: dict[str, Any], resolved: dict[str, Any]) -> None:
    left, middle, right = st.columns((1.1, 1.1, 2.1), gap="small")
    with left:
        if st.button("Edit Profile Details", type="primary", use_container_width=True, key="profile_v3_edit_toggle"):
            st.session_state["profile_v3_edit_open"] = not bool(st.session_state.get("profile_v3_edit_open"))
            st.rerun()
    with middle:
        if override_encryption_available():
            envelope = load_profile_overrides(str(extracted.get("extraction_id")))
            if envelope.sections and st.button("Reset all profile edits", use_container_width=True, key="profile_v3_reset_all"):
                reset_all_profile_overrides(extracted)
                st.session_state["profile_v3_edit_open"] = False
                st.rerun()
    with right:
        st.caption("Edits are stored separately from resume evidence, encrypted with AES-GCM, and can be reset without changing your Master Resume.")
    if st.session_state.get("profile_v3_edit_open"):
        with st.container(border=True):
            _editor(extracted, resolved)


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
    active_profile = v2._latest_profile_for_active_source()
    confirmed_profile = v1._latest_confirmed_profile()
    extracted = active_profile or confirmed_profile
    if not extracted:
        v2._render_uninitialized(source)
        return

    if source and not active_profile and confirmed_profile:
        st.warning("A newer Master Resume is saved, but it has not been extracted yet. The profile below is your previous confirmed permanent snapshot.")
        if st.button("Rebuild profile from current Master Resume", type="primary", key=f"profile_v3_rebuild_{source['source_id']}"):
            try:
                v2._build_profile(source)
            except Exception as error:
                st.error(str(error))
            else:
                st.rerun()

    v2._render_preview_controls(extracted)
    resolved = resolve_profile(extracted)
    _profile_controls(extracted, resolved)
    v1._render_profile_details(resolved, v2._details())
