"""Resume Studio V3.1 UI: resilient PDF intake + complete profile details."""
from __future__ import annotations

import hashlib
from typing import Any

import streamlit as st

from app import resume_studio_page_v2 as v2page
from app.native_resume_service_v3 import (
    active_source,
    build_evidence_bundle,
    confirm_profile_extract,
    ensure_schema,
    extract_profile_from_source,
    latest_profile_for_source,
    save_confirmed_source,
)
from app.product_ui import esc, page_intro
from app.resume_profile_details_v31 import (
    candidate_profile_details_encryption_available,
    extract_uploaded_source,
    load_candidate_profile_details,
    save_candidate_profile_details,
)


def _toast(tone: str, message: str) -> None:
    v2page._toast(tone, message)


def _source_workspace() -> dict[str, Any]:
    source = active_source()
    st.markdown("### 1. Master resume")
    st.caption(
        "Upload the truthful Master Resume that MUNSHI may use as its primary writing evidence. "
        "PDF, DOCX, TXT, and Markdown are parsed inside the Hunter runtime before you review and confirm the extracted text."
    )

    uploaded = st.file_uploader(
        "Upload master resume",
        type=["pdf", "docx", "txt", "md"],
        help=(
            "PDF, DOCX, TXT, and Markdown are accepted. PDF extraction now uses coordinate-aware layout reconstruction "
            "plus a fragmentation repair pass. Image-only/scanned PDFs still require OCR in a later pass."
        ),
        key="native_resume_v31_source_upload",
    )
    if uploaded is not None:
        raw = uploaded.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        if st.session_state.get("native_resume_v31_last_upload_digest") != digest:
            try:
                extracted, source_kind = extract_uploaded_source(uploaded.name, raw)
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state["native_resume_v31_source_draft"] = extracted
                st.session_state["native_resume_v31_source_kind"] = source_kind
                st.session_state["native_resume_v31_source_label"] = uploaded.name[:240]
                st.session_state["native_resume_v31_last_upload_digest"] = digest
                st.session_state["native_resume_v31_truth_confirm"] = False
                _toast("success", "Master Resume parsed and reflowed. Review the reconstructed evidence before saving it.")

    if "native_resume_v31_source_draft" not in st.session_state:
        st.session_state["native_resume_v31_source_draft"] = str(source.get("content_text") or "")
    if "native_resume_v31_source_label" not in st.session_state:
        st.session_state["native_resume_v31_source_label"] = str(source.get("label") or "Master Resume")
    st.session_state.setdefault("native_resume_v31_source_kind", str(source.get("source_kind") or "pasted_text"))

    label = st.text_input("Source label", key="native_resume_v31_source_label", max_chars=240)
    source_text = st.text_area(
        "Extracted master-resume evidence",
        key="native_resume_v31_source_draft",
        height=360,
        placeholder="Upload a PDF/DOCX or paste the complete truthful resume/career-history source here...",
        help=(
            "This text is the evidence boundary for profile extraction and JD writing. It should read as coherent sections and paragraphs, "
            "not one token per line. Review it before confirming."
        ),
    )
    confirmed = st.checkbox(
        "I confirm this source is truthful and MUNSHI may use it for profile extraction and JD-specific resume writing.",
        key="native_resume_v31_truth_confirm",
    )
    save_columns = st.columns((1.2, 2.8), gap="small")
    with save_columns[0]:
        if st.button(
            "Save master source",
            type="primary",
            use_container_width=True,
            key="native_resume_v31_save_source",
            disabled=not (confirmed and source_text.strip()),
        ):
            try:
                saved = save_confirmed_source(
                    content_text=source_text,
                    label=label or "Master Resume",
                    source_kind=str(st.session_state.get("native_resume_v31_source_kind") or "pasted_text"),
                )
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                st.session_state["native_resume_v31_source_draft"] = saved["content_text"]
                st.session_state["native_resume_v31_truth_confirm"] = False
                _toast("success", "Confirmed Master Resume source saved.")
                st.rerun()
    with save_columns[1]:
        if source:
            st.caption(
                f"Active source: {source.get('label') or 'Master Resume'} · "
                f"SHA-256 {str(source.get('content_sha256') or '')[:12]}… · "
                f"updated {source.get('updated_at') or 'date not recorded'}"
            )
        else:
            st.caption("No confirmed Master Resume source is saved yet.")

    current = active_source()
    if current:
        try:
            bundle = build_evidence_bundle(source_id=str(current["source_id"]))
        except (LookupError, ValueError, RuntimeError) as error:
            st.warning(str(error))
        else:
            facts = st.columns(3, gap="small")
            facts[0].metric("Evidence records", len(bundle["items"]))
            facts[1].metric("Sensitive self-ID", "Excluded from AI")
            facts[2].metric("Evidence digest", str(bundle["evidence_digest"])[:10] + "…")
    return current


def _bool_text(value: bool | None) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "Not answered"


def _tri_index(value: bool | None) -> int:
    return 1 if value is True else 2 if value is False else 0


def _tri_state(label: str, value: bool | None, *, key: str) -> bool | None:
    selection = st.selectbox(label, ["Not answered", "Yes", "No"], index=_tri_index(value), key=key)
    return True if selection == "Yes" else False if selection == "No" else None


def _effective_details(profile: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    extracted = profile.get("application_defaults") or {}
    effective = dict(candidate)
    for key in (
        "work_authorization_country",
        "authorization_basis",
        "visa_or_permit",
        "sponsorship_required",
        "willing_to_relocate",
        "work_modes",
    ):
        if effective.get(key) in (None, "", []):
            value = extracted.get(key)
            if value not in (None, "", []):
                effective[key] = value
    return effective


def _profile_completion(profile: dict[str, Any], details: dict[str, Any]) -> int:
    contact = profile.get("contact") or {}
    checks = [
        contact.get("full_name"),
        contact.get("location"),
        contact.get("email"),
        contact.get("phone"),
        profile.get("professional_summary"),
        bool(profile.get("education")),
        bool(profile.get("experience")),
        bool(profile.get("projects")),
        bool(profile.get("skills")),
        bool(profile.get("certifications")),
        details.get("work_authorization_country"),
        details.get("visa_or_permit") or details.get("authorization_basis"),
        details.get("authorized_to_work") is not None,
        details.get("sponsorship_required") is not None,
        details.get("willing_to_relocate") is not None,
    ]
    return round(100 * sum(bool(value) for value in checks) / len(checks))


def _render_profile(profile: dict[str, Any], candidate_details: dict[str, Any]) -> None:
    effective = _effective_details(profile, candidate_details)
    contact = profile.get("contact") or {}
    completion = _profile_completion(profile, effective)

    with st.container(border=True):
        header_left, header_right = st.columns((4.2, 1), gap="small")
        with header_left:
            st.markdown(f"### {esc(contact.get('full_name'), 'Candidate profile')}", unsafe_allow_html=True)
            contact_line = " · ".join(
                str(value)
                for value in [contact.get("location"), contact.get("email"), contact.get("phone")]
                if value
            )
            if contact_line:
                st.caption(contact_line)
        with header_right:
            st.metric("Profile complete", f"{completion}%")
        st.progress(completion / 100)

    left, right = st.columns((1.7, 1), gap="large")
    with left:
        with st.container(border=True):
            st.markdown("#### Professional summary")
            st.write(profile.get("professional_summary") or "Not found in Master Resume.")

            st.markdown("#### Education")
            education = profile.get("education") or []
            if not education:
                st.caption("No education entries extracted.")
            for item in education:
                st.markdown(f"**{esc(item.get('institution'), 'Institution')}**", unsafe_allow_html=True)
                degree = " · ".join(part for part in [item.get("degree"), item.get("field")] if part)
                if degree:
                    st.write(degree)
                dates = " – ".join(part for part in [item.get("start_date"), item.get("end_date")] if part)
                meta = " · ".join(part for part in [dates, item.get("gpa"), item.get("location")] if part)
                if meta:
                    st.caption(meta)
                for detail in item.get("details") or []:
                    st.markdown(f"- {esc(detail)}", unsafe_allow_html=True)

            st.markdown("#### Experience")
            experience = profile.get("experience") or []
            if not experience:
                st.caption("No experience entries extracted.")
            for item in experience:
                st.markdown(f"**{esc(item.get('title'), 'Role')}**")
                st.write(esc(item.get("employer"), "Employer"))
                dates = " – ".join(part for part in [item.get("start_date"), item.get("end_date")] if part)
                if dates or item.get("location"):
                    st.caption(" · ".join(part for part in [dates, item.get("location")] if part))
                for bullet in item.get("bullets") or []:
                    st.markdown(f"- {esc(bullet)}", unsafe_allow_html=True)

            st.markdown("#### Projects")
            projects = profile.get("projects") or []
            if not projects:
                st.caption("No project entries extracted.")
            for item in projects:
                st.markdown(f"**{esc(item.get('name'), 'Project')}**", unsafe_allow_html=True)
                if item.get("tools"):
                    st.caption(" · ".join(str(value) for value in item["tools"]))
                if item.get("description"):
                    st.write(item["description"])
                for bullet in item.get("bullets") or []:
                    st.markdown(f"- {esc(bullet)}", unsafe_allow_html=True)

    with right:
        with st.container(border=True):
            st.markdown("#### Application defaults")
            st.caption("Candidate-confirmed values override resume-explicit values.")
            st.markdown("**WORK AUTHORIZATION**")
            for label, key in [
                ("Country", "work_authorization_country"),
                ("Authorization basis", "authorization_basis"),
                ("Work permit / visa", "visa_or_permit"),
                ("Status", "authorization_status"),
            ]:
                st.write(f"{label}: {effective.get(key) or 'Not answered'}")
            st.write(f"Authorized to work: {_bool_text(effective.get('authorized_to_work'))}")
            st.write(f"Needs sponsorship: {_bool_text(effective.get('sponsorship_required'))}")

            st.markdown("**WORK PREFERENCES**")
            for label, key in [
                ("In-person OK", "in_person_ok"),
                ("Can relocate", "willing_to_relocate"),
                ("Start immediately", "start_immediately"),
                ("Has transport", "has_transport"),
                ("Needs accommodations", "needs_accommodations"),
            ]:
                st.write(f"{label}: {_bool_text(effective.get(key))}")
            work_modes = effective.get("work_modes") or []
            if work_modes:
                st.write("Work modes: " + ", ".join(str(value) for value in work_modes))

            st.markdown("**BACKGROUND**")
            st.write(f"Prior employee: {_bool_text(effective.get('prior_employee'))}")
            st.write(f"Government clearance: {_bool_text(effective.get('government_clearance'))}")
            st.write(f"Government ties: {_bool_text(effective.get('government_ties'))}")

            st.markdown("**VOLUNTARY SELF-ID · CANDIDATE ENTERED ONLY**")
            st.write(f"Gender: {effective.get('gender') or 'Not answered'}")
            st.write(f"Ethnicity: {effective.get('ethnicity') or 'Not answered'}")
            st.write(f"Veteran: {_bool_text(effective.get('veteran'))}")
            st.write(f"Disability: {_bool_text(effective.get('disability'))}")
            st.caption("These fields are never inferred from the resume and never sent to the profile-extraction LLM.")

        with st.container(border=True):
            st.markdown("#### Skills")
            categories = profile.get("skills") or []
            if not categories:
                st.caption("No skills extracted.")
            for group in categories:
                st.caption(str(group.get("category") or "Other").upper())
                skills = group.get("skills") or []
                if skills:
                    st.write(" · ".join(str(value) for value in skills))

        with st.container(border=True):
            st.markdown("#### Certifications")
            certs = profile.get("certifications") or []
            if not certs:
                st.caption("No certifications extracted.")
            for cert in certs:
                st.markdown(f"**{esc(cert.get('name'), 'Certification')}**", unsafe_allow_html=True)
                details = " · ".join(part for part in [cert.get("issuer"), cert.get("date")] if part)
                if details:
                    st.caption(details)

        languages = profile.get("languages") or []
        if languages:
            with st.container(border=True):
                st.markdown("#### Languages")
                st.write(" · ".join(str(value) for value in languages))


def _candidate_details_editor(profile: dict[str, Any], candidate_details: dict[str, Any]) -> None:
    st.markdown("### Candidate application defaults")
    st.caption(
        "Complete the recurring ATS questions once. These answers are candidate-entered, separated from resume extraction, "
        "and stored only in the AES-GCM vault. Sensitive fields never use plaintext fallback storage."
    )
    encrypted = candidate_profile_details_encryption_available()
    if not encrypted:
        st.error(
            "Encrypted candidate profile storage is not configured on this runtime. "
            "MUNSHI will not save work-authorization or voluntary self-ID answers until MUNSHI_VAULT_KEY is available."
        )

    extracted = profile.get("application_defaults") or {}
    initial = _effective_details(profile, candidate_details)

    with st.form("native_resume_v31_candidate_details_form"):
        st.markdown("#### Work authorization")
        c1, c2 = st.columns(2)
        with c1:
            country = st.text_input("Country", value=str(initial.get("work_authorization_country") or ""))
            visa = st.text_input("Work permit or visa", value=str(initial.get("visa_or_permit") or ""))
            authorized = _tri_state("Authorized to work", initial.get("authorized_to_work"), key="v31_authorized")
        with c2:
            basis = st.text_input("Authorization basis", value=str(initial.get("authorization_basis") or ""))
            auth_status = st.text_input("Authorization status", value=str(initial.get("authorization_status") or ""))
            sponsorship = _tri_state("Needs sponsorship", initial.get("sponsorship_required"), key="v31_sponsorship")

        st.markdown("#### Work preferences")
        c1, c2, c3 = st.columns(3)
        with c1:
            in_person = _tri_state("In-person OK", initial.get("in_person_ok"), key="v31_in_person")
            relocate = _tri_state("Can relocate", initial.get("willing_to_relocate"), key="v31_relocate")
        with c2:
            start_now = _tri_state("Start immediately", initial.get("start_immediately"), key="v31_start_now")
            transport = _tri_state("Has transport", initial.get("has_transport"), key="v31_transport")
        with c3:
            accommodations = _tri_state("Needs accommodations", initial.get("needs_accommodations"), key="v31_accommodations")
            open_to_work = _tri_state("Open to work", initial.get("open_to_work"), key="v31_open_to_work")
        work_modes = st.multiselect(
            "Preferred work modes",
            ["On-site", "Hybrid", "Remote"],
            default=[mode for mode in (initial.get("work_modes") or []) if mode in {"On-site", "Hybrid", "Remote"}],
        )

        st.markdown("#### Background")
        c1, c2, c3 = st.columns(3)
        with c1:
            prior_employee = _tri_state("Prior employee", initial.get("prior_employee"), key="v31_prior_employee")
        with c2:
            clearance = _tri_state("Government clearance", initial.get("government_clearance"), key="v31_clearance")
        with c3:
            gov_ties = _tri_state("Government ties", initial.get("government_ties"), key="v31_gov_ties")

        st.markdown("#### Voluntary self-ID")
        st.caption("Optional. Candidate-entered only. Never inferred from your name, schools, location, resume, or other proxies.")
        c1, c2 = st.columns(2)
        with c1:
            gender = st.text_input("Gender", value=str(initial.get("gender") or ""))
            veteran = _tri_state("Veteran", initial.get("veteran"), key="v31_veteran")
        with c2:
            ethnicity = st.text_input("Ethnicity", value=str(initial.get("ethnicity") or ""))
            disability = _tri_state("Disability", initial.get("disability"), key="v31_disability")

        submitted = st.form_submit_button(
            "Save encrypted profile details",
            type="primary",
            use_container_width=True,
            disabled=not encrypted,
        )
        if submitted:
            values = {
                "open_to_work": open_to_work,
                "work_authorization_country": country.strip(),
                "authorization_basis": basis.strip(),
                "visa_or_permit": visa.strip(),
                "authorization_status": auth_status.strip(),
                "authorized_to_work": authorized,
                "sponsorship_required": sponsorship,
                "in_person_ok": in_person,
                "willing_to_relocate": relocate,
                "start_immediately": start_now,
                "has_transport": transport,
                "needs_accommodations": accommodations,
                "work_modes": work_modes,
                "prior_employee": prior_employee,
                "government_clearance": clearance,
                "government_ties": gov_ties,
                "gender": gender.strip(),
                "ethnicity": ethnicity.strip(),
                "veteran": veteran,
                "disability": disability,
            }
            try:
                save_candidate_profile_details(values)
            except Exception as error:
                st.error(str(error))
            else:
                _toast("success", "Candidate profile details saved encrypted in the MUNSHI vault.")
                st.rerun()

    if extracted:
        st.caption("Resume-explicit defaults remain visible as evidence, but candidate-confirmed answers take precedence for future ATS use.")


def _profile_workspace(source: dict[str, Any], writer_status: dict[str, Any]) -> None:
    st.markdown("### 2. Native profile extractor")
    st.caption(
        "Turn the confirmed Master Resume into a reusable candidate profile: summary, education, experience, projects, grouped skills, certifications, languages, and resume-explicit defaults."
    )
    if not source:
        st.info("Save a confirmed Master Resume first.")
        return

    current = latest_profile_for_source(str(source["source_id"]))
    try:
        candidate_details = load_candidate_profile_details()
    except Exception as error:
        candidate_details = {}
        st.warning(str(error))

    actions = st.columns((1.2, 1.2, 2.2), gap="small")
    with actions[0]:
        label = "Re-extract profile" if current else "Extract profile"
        if st.button(
            label,
            type="primary",
            use_container_width=True,
            key=f"native_resume_v31_extract_profile_{source['source_id']}",
            disabled=not writer_status.get("configured"),
        ):
            try:
                with st.spinner("Building the structured profile from the confirmed Master Resume without inventing missing facts…"):
                    current = extract_profile_from_source(source)
            except Exception as error:
                st.error(str(error))
            else:
                _toast("success", "Structured candidate profile extracted. Review it before confirming.")
                st.rerun()
    with actions[1]:
        if current and current.get("status") != "CONFIRMED":
            if st.button(
                "Confirm profile",
                use_container_width=True,
                key=f"native_resume_v31_confirm_profile_{current['extraction_id']}",
            ):
                try:
                    confirm_profile_extract(str(current["extraction_id"]))
                except Exception as error:
                    st.error(str(error))
                else:
                    _toast("success", "Profile snapshot confirmed.")
                    st.rerun()
    with actions[2]:
        if not writer_status.get("configured"):
            st.warning("Configure an OpenAI key above before extracting the structured profile.")
        elif current:
            storage = "AES-GCM encrypted" if current.get("storage_mode") == "aes_gcm_vault" else "SQLite fallback"
            st.caption(f"Status: {current.get('status')} · Extracted profile storage: {storage} · Model: {current.get('model_name')}")

    if current:
        if current.get("storage_mode") != "aes_gcm_vault":
            st.warning(
                "The AI-extracted profile snapshot is using the existing plaintext SQLite fallback because the vault is unavailable. "
                "Candidate-entered application/self-ID details remain stricter: they are never saved without AES-GCM."
            )
        profile = current.get("profile") or {}
        _render_profile(profile, candidate_details)
        warnings = profile.get("extraction_warnings") or []
        if warnings:
            with st.expander("Extraction warnings", expanded=False):
                for warning in warnings:
                    st.markdown(f"- {esc(warning)}", unsafe_allow_html=True)
        st.divider()
        _candidate_details_editor(profile, candidate_details)
    else:
        st.info("Extract the profile first. Candidate defaults will then be shown alongside resume evidence.")


def render() -> None:
    ensure_schema()
    page_intro(
        "RESUME STUDIO",
        "Native Resume & Candidate Profile Studio",
        "Upload a Master Resume, reconstruct clean evidence, extract a reusable profile, complete encrypted application defaults, choose a target JD, and generate a truthful job-specific resume inside MUNSHI.",
    )
    st.markdown(
        '<div class="product-callout"><div><strong>Master Resume → Candidate Profile → Application Defaults → JD Resume</strong><span>Resilient PDF/DOCX intake, structured profile extraction, encrypted candidate-entered defaults, and Slight / Medium / Aggressive rewriting inside the same truth boundary.</span></div><span class="status-chip">Prepare only</span></div>',
        unsafe_allow_html=True,
    )
    status = v2page._writer_settings_panel()
    st.divider()
    source = _source_workspace()
    st.divider()
    _profile_workspace(source, status)
    st.divider()
    job_id, context = v2page._job_workspace()
    if job_id is None:
        return
    st.divider()
    v2page._generation_controls(job_id, source, status)
    st.divider()
    v2page._version_workspace(job_id, context, status)
