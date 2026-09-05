"""User-facing native ATS Resume Studio.

The page is intentionally preparation-only. It can create evidence-backed native
resume versions and downloads, but it cannot submit an application or replace
the explicitly designated Master Resume. n8n remains authoritative until a
later parity decision.
"""
from __future__ import annotations

import hashlib
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from app.native_resume_service import (
    active_source,
    build_evidence_bundle,
    ensure_schema,
    extract_uploaded_source,
    generate_resume,
    get_version,
    job_context,
    list_versions,
    model_status,
    resume_job_options,
    safe_filename,
    save_confirmed_source,
    version_diff,
    version_docx,
    version_html,
    version_pdf,
)
from app.product_ui import esc, page_intro


def _toast(tone: str, message: str) -> None:
    icon = {"success": "✅", "warning": "⚠️", "error": "❌", "info": "ℹ️"}.get(tone, "ℹ️")
    st.toast(message, icon=icon)


def _source_workspace() -> dict[str, Any]:
    source = active_source()
    st.markdown("### 1. Candidate evidence source")
    st.caption(
        "Resume Studio writes only from candidate-confirmed source text plus non-sensitive confirmed profile evidence. "
        "Saving a source does not call GPT and does not change your Master Resume designation."
    )

    uploaded = st.file_uploader(
        "Import a source resume",
        type=["txt", "md", "docx"],
        help="TXT, Markdown, and DOCX are parsed locally. PDF import is intentionally not enabled in V1 yet.",
        key="native_resume_source_upload",
    )
    if uploaded is not None:
        raw = uploaded.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        if st.session_state.get("native_resume_last_upload_digest") != digest:
            try:
                extracted, source_kind = extract_uploaded_source(uploaded.name, raw)
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state["native_resume_source_draft"] = extracted
                st.session_state["native_resume_source_kind"] = source_kind
                st.session_state["native_resume_source_label"] = uploaded.name[:240]
                st.session_state["native_resume_last_upload_digest"] = digest
                _toast("success", "Resume source imported locally. Review it before saving.")

    if "native_resume_source_draft" not in st.session_state:
        st.session_state["native_resume_source_draft"] = str(source.get("content_text") or "")
    if "native_resume_source_label" not in st.session_state:
        st.session_state["native_resume_source_label"] = str(source.get("label") or "My confirmed resume source")
    st.session_state.setdefault("native_resume_source_kind", str(source.get("source_kind") or "pasted_text"))

    label = st.text_input("Source label", key="native_resume_source_label", max_chars=240)
    source_text = st.text_area(
        "Confirmed resume / career evidence",
        key="native_resume_source_draft",
        height=280,
        placeholder="Paste the complete truthful resume or career-history source that MUNSHI is allowed to use...",
        help="MUNSHI will not infer missing employers, dates, tools, metrics, education, or certifications.",
    )
    save_columns = st.columns((1, 2.5))
    with save_columns[0]:
        if st.button("Save confirmed source", type="primary", use_container_width=True, key="native_resume_save_source"):
            try:
                saved = save_confirmed_source(
                    content_text=source_text,
                    label=label,
                    source_kind=str(st.session_state.get("native_resume_source_kind") or "pasted_text"),
                )
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                _toast("success", "Confirmed resume source saved.")
                st.session_state["native_resume_source_draft"] = saved["content_text"]
                st.rerun()
    with save_columns[1]:
        if source:
            st.caption(
                f"Active source: {source.get('label') or 'Confirmed source'} · "
                f"SHA-256 {str(source.get('content_sha256') or '')[:12]}… · "
                f"updated {source.get('updated_at') or 'date not recorded'}"
            )
        else:
            st.caption("No confirmed native resume source is saved yet.")

    current = active_source()
    if current:
        try:
            bundle = build_evidence_bundle(source_id=str(current["source_id"]))
        except (LookupError, ValueError, RuntimeError) as error:
            st.warning(str(error))
        else:
            items = bundle["items"]
            facts = st.columns(3, gap="small")
            facts[0].metric("Evidence records", len(items))
            facts[1].metric("Sensitive self-ID", "Excluded")
            facts[2].metric("Evidence digest", str(bundle["evidence_digest"])[:10] + "…")
            with st.expander("Review evidence available to the writer", expanded=False):
                st.caption("Only these records can support native resume claims. Sensitive self-identification is filtered out.")
                for item in items[:40]:
                    st.markdown(f"**{esc(item.get('label') or item.get('kind'))}**", unsafe_allow_html=True)
                    st.caption(str(item.get("text") or "")[:450])
                if len(items) > 40:
                    st.caption(f"{len(items) - 40} additional evidence records are available but collapsed here.")
    return current


def _job_workspace() -> tuple[int | None, dict[str, Any]]:
    st.markdown("### 2. Target job")
    options = resume_job_options(limit=300)
    if not options:
        st.info("No stored job with a complete job description is available yet.")
        return None, {}

    labels: dict[str, dict[str, Any]] = {}
    for row in options:
        score = f" · {float(row['hunter_score']):.0f}% match" if row.get("hunter_score") is not None else ""
        label = f"#{row['id']} · {row.get('company_name') or 'Company'} · {row.get('title') or 'Untitled role'}{score}"
        labels[label] = row
    selected_label = st.selectbox("Choose a stored job", list(labels), key="native_resume_job_choice")
    selected = labels[selected_label]
    job_id = int(selected["id"])
    context = job_context(job_id)

    headline = st.columns((2.2, 1, 1), gap="small")
    headline[0].markdown(
        f"**{esc(context.get('company_name'), 'Company not recorded')} · {esc(context.get('title'), 'Untitled role')}**",
        unsafe_allow_html=True,
    )
    headline[1].metric("Hunter match", f"{float(context['hunter_score']):.0f}%" if context.get("hunter_score") is not None else "N/A")
    headline[2].metric("Source", str(context.get("source") or "Unknown"))
    st.caption(
        f"{context.get('location_raw') or 'Location unknown'} · "
        f"{context.get('employment_type') or 'Employment not recorded'} · "
        f"{context.get('remote_type') or 'Workplace not recorded'}"
    )
    with st.expander("Review stored job description", expanded=False):
        st.write(context.get("description_raw") or "No stored description.")
        if context.get("qualifications"):
            st.markdown("**Qualifications evidence**")
            st.write(context["qualifications"])
        if context.get("skills_keywords"):
            st.markdown("**Stored skills/keyword evidence**")
            st.write(context["skills_keywords"])
    return job_id, context


def _generation_controls(job_id: int, source: dict[str, Any]) -> None:
    status = model_status()
    st.markdown("### 3. Generate ATS resume")
    callout = st.columns((1.25, 1.25, 2.5), gap="small")
    callout[0].metric("Writer", "Ready" if status["configured"] else "Not configured")
    callout[1].metric("Model", status["model"])
    callout[2].caption(
        "Your confirmed source, non-sensitive evidence, and selected job description are sent to the configured OpenAI model only when you click Generate or Apply AI revision."
    )
    if not status["configured"]:
        st.warning(
            "Resume Studio is installed, but this staging runtime does not currently expose an OpenAI API key to the native writer. "
            "No fallback or fabricated resume will be generated."
        )
    if not source:
        st.warning("Save a confirmed resume source first.")

    default_instruction = (
        "Create the strongest truthful one-page ATS resume for this job. Prioritize directly relevant evidence, "
        "keep the writing natural and concise, and omit unsupported requirements rather than inventing them."
    )
    instruction = st.text_area(
        "Generation direction",
        value=default_instruction,
        height=110,
        key=f"native_resume_initial_instruction_{job_id}",
    )
    if st.button(
        "Generate ATS resume",
        type="primary",
        use_container_width=True,
        key=f"native_resume_generate_{job_id}",
        disabled=not (status["configured"] and source),
    ):
        try:
            with st.spinner("Building evidence bundle, writing, truth-checking, and validating the one-page budget…"):
                record = generate_resume(job_id=job_id, instruction=instruction)
        except Exception as error:
            st.error(str(error))
        else:
            st.session_state["native_resume_selected_version"] = record["version_id"]
            _toast("success", f"Native ATS resume v{record['version_number']} validated.")
            st.rerun()


def _download_panel(version_id: str, record: dict[str, Any], context: dict[str, Any]) -> None:
    st.markdown("#### Downloads")
    html = version_html(version_id)
    docx = version_docx(version_id)
    download_columns = st.columns(3, gap="small")
    download_columns[0].download_button(
        "Download HTML",
        data=html.encode("utf-8"),
        file_name=safe_filename(record, context, "html"),
        mime="text/html",
        use_container_width=True,
        key=f"native_resume_html_{version_id}",
    )
    download_columns[1].download_button(
        "Download DOCX",
        data=docx,
        file_name=safe_filename(record, context, "docx"),
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True,
        key=f"native_resume_docx_{version_id}",
    )
    pdf_key = f"native_resume_pdf_bytes_{version_id}"
    pages_key = f"native_resume_pdf_pages_{version_id}"
    with download_columns[2]:
        if pdf_key not in st.session_state:
            if st.button("Build PDF", use_container_width=True, key=f"native_resume_build_pdf_{version_id}"):
                try:
                    with st.spinner("Rendering Letter-size PDF in Chromium…"):
                        pdf, pages = version_pdf(version_id)
                except Exception as error:
                    st.error(str(error))
                else:
                    st.session_state[pdf_key] = pdf
                    st.session_state[pages_key] = pages
                    st.rerun()
        else:
            st.download_button(
                "Download PDF",
                data=st.session_state[pdf_key],
                file_name=safe_filename(record, context, "pdf"),
                mime="application/pdf",
                use_container_width=True,
                key=f"native_resume_pdf_download_{version_id}",
            )
    if pages_key in st.session_state:
        pages = int(st.session_state[pages_key])
        if pages == 1:
            st.success("Physical PDF check: 1 Letter page.")
        else:
            st.warning(f"Physical PDF check: {pages} pages. Ask MUNSHI to shorten the resume before using it.")


def _version_workspace(job_id: int, context: dict[str, Any]) -> None:
    versions = list_versions(job_id=job_id, limit=100)
    if not versions:
        st.markdown(
            '<div class="empty-product"><h3>No native resume versions yet</h3><p>Generate the first evidence-backed ATS resume above. Existing n8n artifacts remain unchanged.</p></div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown("### 4. Resume Studio")
    version_ids = [str(item["version_id"]) for item in versions]
    if st.session_state.get("native_resume_selected_version") not in version_ids:
        st.session_state["native_resume_selected_version"] = version_ids[0]
    by_id = {str(item["version_id"]): item for item in versions}
    selected_id = st.selectbox(
        "Version history",
        version_ids,
        format_func=lambda value: (
            f"v{by_id[value]['version_number']} · {by_id[value]['created_at']} · "
            f"ATS readiness {by_id[value]['diagnostics'].get('ats_readiness_estimate', 'N/A')}"
        ),
        key="native_resume_selected_version",
    )
    record = get_version(selected_id)
    diagnostics = record["diagnostics"]

    top = st.columns(5, gap="small")
    top[0].metric("Version", f"v{record['version_number']}")
    top[1].metric("ATS readiness", f"{diagnostics.get('ats_readiness_estimate', 0)}/100")
    top[2].metric("JD coverage", f"{float(diagnostics.get('jd_term_coverage_percent') or 0):.0f}%")
    top[3].metric("Words", int(diagnostics.get("word_count") or 0))
    top[4].metric("Truth audit", str((diagnostics.get("truth_audit") or {}).get("status") or "Unknown"))
    st.caption(
        "ATS readiness is MUNSHI's explainable deterministic estimate, not a score returned by an employer's ATS. "
        "Truth audit and numeric-claim checks must pass before a version is stored."
    )

    preview, analysis = st.columns((1.65, 1), gap="large")
    with preview:
        st.markdown("#### Resume preview")
        components.html(version_html(selected_id), height=1040, scrolling=True)
    with analysis:
        st.markdown("#### Evidence & ATS diagnostics")
        truth = diagnostics.get("truth_audit") or {}
        st.success(
            f"Evidence audit PASS · {truth.get('evidence_ids_used', 0)} evidence references used · numeric claim guard PASS"
        )
        if diagnostics.get("content_budget_issues"):
            st.warning("Content budget: " + ", ".join(diagnostics["content_budget_issues"]))
        else:
            st.success("Content budget: PASS")
        matched = diagnostics.get("matched_jd_terms") or []
        missing = diagnostics.get("missing_jd_terms") or []
        with st.expander("Matched JD terms", expanded=False):
            st.write(", ".join(matched[:50]) if matched else "No deterministic term matches recorded.")
        with st.expander("JD terms not currently present", expanded=True):
            st.caption("Missing does not mean MUNSHI is allowed to add it. Unsupported skills must remain absent.")
            st.write(", ".join(missing[:30]) if missing else "No tracked terms missing.")
        st.markdown("#### Authority boundary")
        st.info(
            "This version is a native candidate artifact preview only. n8n remains the current authoritative preparation path. "
            "Resume Studio does not mark a job Submitted and does not replace your Master Resume."
        )
        st.button(
            "Use for application",
            disabled=True,
            use_container_width=True,
            help="Enabled only after native-vs-n8n parity and the explicit authority gate pass.",
            key=f"native_resume_use_{selected_id}",
        )

    _download_panel(selected_id, record, context)

    st.markdown("### 5. Edit with MUNSHI")
    st.caption("Create a new immutable version. The current version remains available in history.")
    instruction = st.text_area(
        "Tell MUNSHI what to change",
        placeholder="Examples: Make the summary shorter. Emphasize analytics. Reduce recruiting emphasis. Rewrite the Toyota bullets more concisely.",
        height=110,
        key=f"native_resume_revision_instruction_{selected_id}",
    )
    lock_columns = st.columns(4, gap="small")
    locks: list[str] = []
    lock_specs = [
        ("contact", "Lock contact"),
        ("education", "Lock education"),
        ("certifications", "Lock certifications"),
        ("experience", "Lock experience"),
    ]
    for column, (key, label) in zip(lock_columns, lock_specs):
        with column:
            if st.checkbox(label, key=f"native_resume_lock_{key}_{selected_id}"):
                locks.append(key)
    status = model_status()
    if st.button(
        "Apply AI revision",
        type="primary",
        use_container_width=True,
        key=f"native_resume_revision_{selected_id}",
        disabled=not (status["configured"] and instruction.strip()),
    ):
        try:
            with st.spinner("Rewriting within the same evidence boundary and re-running all guards…"):
                revised = generate_resume(
                    job_id=job_id,
                    parent_version_id=selected_id,
                    instruction=instruction,
                    locked_sections=locks,
                )
        except Exception as error:
            st.error(str(error))
        else:
            st.session_state["native_resume_selected_version"] = revised["version_id"]
            _toast("success", f"Revision saved as v{revised['version_number']}.")
            st.rerun()

    if record.get("parent_version_id"):
        with st.expander("Compare with parent version", expanded=False):
            st.code(version_diff(selected_id), language="diff")


def render() -> None:
    ensure_schema()
    page_intro(
        "RESUME STUDIO",
        "Native ATS Resume Studio",
        "Generate, inspect, revise, and download truthful job-specific resumes directly inside MUNSHI while n8n remains the guarded fallback authority.",
    )
    st.markdown(
        '<div class="product-callout"><div><strong>Evidence-first native writing</strong><span>GPT can rewrite and prioritize. It cannot invent unsupported facts, metrics, skills, dates, or experience.</span></div><span class="status-chip">Prepare only</span></div>',
        unsafe_allow_html=True,
    )
    source = _source_workspace()
    st.divider()
    job_id, context = _job_workspace()
    if job_id is None:
        return
    st.divider()
    _generation_controls(job_id, source)
    st.divider()
    _version_workspace(job_id, context)
