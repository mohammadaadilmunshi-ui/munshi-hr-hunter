"""Resume Studio V3 UI: PDF intake + native structured profile extraction."""
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
    extract_uploaded_source,
    latest_profile_for_source,
    save_confirmed_source,
)
from app.product_ui import esc, page_intro


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
        help="PDF, DOCX, TXT, and Markdown are accepted. Image-only/scanned PDFs need OCR support in a later pass.",
        key="native_resume_v3_source_upload",
    )
    if uploaded is not None:
        raw = uploaded.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        if st.session_state.get("native_resume_v3_last_upload_digest") != digest:
            try:
                extracted, source_kind = extract_uploaded_source(uploaded.name, raw)
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state["native_resume_v3_source_draft"] = extracted
                st.session_state["native_resume_v3_source_kind"] = source_kind
                st.session_state["native_resume_v3_source_label"] = uploaded.name[:240]
                st.session_state["native_resume_v3_last_upload_digest"] = digest
                st.session_state["native_resume_v3_truth_confirm"] = False
                _toast("success", "Master Resume parsed. Review the extracted evidence before saving it.")

    if "native_resume_v3_source_draft" not in st.session_state:
        st.session_state["native_resume_v3_source_draft"] = str(source.get("content_text") or "")
    if "native_resume_v3_source_label" not in st.session_state:
        st.session_state["native_resume_v3_source_label"] = str(source.get("label") or "Master Resume")
    st.session_state.setdefault("native_resume_v3_source_kind", str(source.get("source_kind") or "pasted_text"))

    label = st.text_input("Source label", key="native_resume_v3_source_label", max_chars=240)
    source_text = st.text_area(
        "Extracted master-resume evidence",
        key="native_resume_v3_source_draft",
        height=320,
        placeholder="Upload a PDF/DOCX or paste the complete truthful resume/career-history source here...",
        help="Review this before confirming. MUNSHI must not infer missing employers, dates, tools, metrics, education, certifications, or self-identification data.",
    )
    confirmed = st.checkbox(
        "I confirm this source is truthful and MUNSHI may use it for profile extraction and JD-specific resume writing.",
        key="native_resume_v3_truth_confirm",
    )
    save_columns = st.columns((1.2, 2.8), gap="small")
    with save_columns[0]:
        if st.button(
            "Save master source",
            type="primary",
            use_container_width=True,
            key="native_resume_v3_save_source",
            disabled=not (confirmed and source_text.strip()),
        ):
            try:
                saved = save_confirmed_source(
                    content_text=source_text,
                    label=label or "Master Resume",
                    source_kind=str(st.session_state.get("native_resume_v3_source_kind") or "pasted_text"),
                )
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                st.session_state["native_resume_v3_source_draft"] = saved["content_text"]
                st.session_state["native_resume_v3_truth_confirm"] = False
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
            facts[1].metric("Sensitive self-ID", "Excluded")
            facts[2].metric("Evidence digest", str(bundle["evidence_digest"])[:10] + "…")
    return current


def _render_profile(profile: dict[str, Any]) -> None:
    left, right = st.columns((1.65, 1), gap="large")
    with left:
        with st.container(border=True):
            st.markdown("#### Professional summary")
            st.write(profile.get("professional_summary") or "Not found in Master Resume.")

        with st.container(border=True):
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
                meta = " · ".join(part for part in [dates, item.get("gpa")] if part)
                if meta:
                    st.caption(meta)

        with st.container(border=True):
            st.markdown("#### Experience")
            experience = profile.get("experience") or []
            if not experience:
                st.caption("No experience entries extracted.")
            for item in experience:
                st.markdown(f"**{esc(item.get('title'), 'Role')} · {esc(item.get('employer'), 'Employer')}**", unsafe_allow_html=True)
                dates = " – ".join(part for part in [item.get("start_date"), item.get("end_date")] if part)
                if dates or item.get("location"):
                    st.caption(" · ".join(part for part in [dates, item.get("location")] if part))
                for bullet in item.get("bullets") or []:
                    st.markdown(f"- {esc(bullet)}", unsafe_allow_html=True)

        with st.container(border=True):
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
            st.markdown("#### Contact")
            contact = profile.get("contact") or {}
            for label, key in [
                ("Name", "full_name"), ("Location", "location"), ("Email", "email"),
                ("Phone", "phone"), ("LinkedIn", "linkedin"), ("Portfolio", "portfolio"),
            ]:
                value = contact.get(key)
                if value:
                    st.markdown(f"**{label}:** {esc(value)}", unsafe_allow_html=True)

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

        with st.container(border=True):
            st.markdown("#### Application defaults")
            defaults = profile.get("application_defaults") or {}
            explicit = {
                "Work authorization": defaults.get("authorization_basis") or defaults.get("work_authorization_country"),
                "Visa / permit": defaults.get("visa_or_permit"),
                "Sponsorship required": defaults.get("sponsorship_required"),
                "Willing to relocate": defaults.get("willing_to_relocate"),
                "Work modes": ", ".join(defaults.get("work_modes") or []),
            }
            shown = False
            for label, value in explicit.items():
                if value not in (None, "", []):
                    shown = True
                    st.markdown(f"**{label}:** {esc(value)}", unsafe_allow_html=True)
            if not shown:
                st.caption("Not inferred from the resume. Collect these separately through candidate onboarding.")
            st.info("MUNSHI never infers gender, race/ethnicity, disability, veteran status, religion, age, citizenship, or other voluntary self-ID from a resume.")


def _profile_workspace(source: dict[str, Any], writer_status: dict[str, Any]) -> None:
    st.markdown("### Native profile extractor")
    st.caption(
        "Turn the confirmed Master Resume into a reusable candidate profile like the examples you shared: summary, education, experience, projects, skills, certifications, and only resume-explicit application defaults."
    )
    if not source:
        st.info("Save a confirmed Master Resume first.")
        return
    current = latest_profile_for_source(str(source["source_id"]))
    actions = st.columns((1.2, 1.2, 2.2), gap="small")
    with actions[0]:
        label = "Re-extract profile" if current else "Extract profile"
        if st.button(
            label,
            type="primary",
            use_container_width=True,
            key=f"native_resume_v3_extract_profile_{source['source_id']}",
            disabled=not writer_status.get("configured"),
        ):
            try:
                with st.spinner("Reading the confirmed Master Resume and building a structured profile without inventing missing facts…"):
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
                key=f"native_resume_v3_confirm_profile_{current['extraction_id']}",
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
            st.caption(f"Status: {current.get('status')} · Storage: {storage} · Model: {current.get('model_name')}")

    if current:
        if current.get("storage_mode") != "aes_gcm_vault":
            st.warning("The structured profile is currently using the plaintext SQLite fallback because MUNSHI_VAULT_KEY is unavailable on this runtime. Configure the vault to make new profile snapshots AES-GCM encrypted at rest.")
        _render_profile(current.get("profile") or {})
        warnings = (current.get("profile") or {}).get("extraction_warnings") or []
        if warnings:
            with st.expander("Extraction warnings", expanded=False):
                for warning in warnings:
                    st.markdown(f"- {esc(warning)}", unsafe_allow_html=True)


def render() -> None:
    ensure_schema()
    page_intro(
        "RESUME STUDIO",
        "Native Resume & Candidate Profile Studio",
        "Upload a Master Resume, extract a reusable profile, choose a target JD, control rewrite strength and GPT usage, then preview and download a truthful job-specific resume inside MUNSHI.",
    )
    st.markdown(
        '<div class="product-callout"><div><strong>Master Resume → Candidate Profile → JD Resume</strong><span>PDF/DOCX intake, structured profile extraction, and Slight / Medium / Aggressive rewriting inside the same truth boundary.</span></div><span class="status-chip">Prepare only</span></div>',
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
