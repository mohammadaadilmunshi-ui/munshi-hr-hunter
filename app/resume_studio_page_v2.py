"""User-facing Native ATS Resume Studio V2.

V2 adds a candidate-managed encrypted OpenAI credential, per-candidate model and
API-call constraints, and explicit Slight / Medium / Aggressive JD rewrite
strengths while preserving the V1 preparation-only and truth-first boundary.
"""
from __future__ import annotations

import hashlib
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from app.native_resume_service_v2 import (
    MODEL_OPTIONS,
    REWRITE_PRESETS,
    REWRITE_MODES,
    REASONING_OPTIONS,
    active_source,
    build_evidence_bundle,
    delete_personal_api_key,
    ensure_schema,
    extract_uploaded_source,
    generate_resume,
    get_version,
    job_context,
    list_versions,
    resume_job_options,
    safe_filename,
    save_confirmed_source,
    save_personal_api_key,
    save_writer_settings,
    version_diff,
    version_docx,
    version_html,
    version_pdf,
    writer_status,
)
from app.product_ui import esc, page_intro
from app.secure_vault import VaultError


MODE_LABELS = {
    "slight": "Slight",
    "medium": "Medium",
    "aggressive": "Aggressive",
}
MODE_HELP = {
    "slight": "Light tailoring. Preserve most structure and wording; make targeted JD-specific changes.",
    "medium": "Balanced rewrite. Rework summary and bullets, reorder supported evidence, and condense lower-relevance content.",
    "aggressive": "Full truthful rebuild around the JD. Strongest restructuring and prioritization without inventing or exaggerating anything.",
}
MODEL_LABELS = {
    "gpt-5.6-luna": "GPT-5.6 Luna · lowest-cost option",
    "gpt-5.6-terra": "GPT-5.6 Terra · balanced (recommended)",
    "gpt-5.6-sol": "GPT-5.6 Sol · highest-quality option",
}


def _toast(tone: str, message: str) -> None:
    icon = {"success": "✅", "warning": "⚠️", "error": "❌", "info": "ℹ️"}.get(tone, "ℹ️")
    st.toast(message, icon=icon)


def _writer_settings_panel() -> dict[str, Any]:
    status = writer_status()
    st.markdown("### GPT writer")
    cards = st.columns(4, gap="small")
    cards[0].metric("Connection", "Ready" if status["configured"] else "Needs key")
    credential_label = {
        "personal_encrypted": "Personal key",
        "server_environment": "Server key",
        "personal_key_locked": "Key locked",
        "none": "None",
    }.get(status["key_source"], "Unknown")
    cards[1].metric("Credential", credential_label)
    cards[2].metric("Model", status["model"])
    cards[3].metric("GPT calls / resume", f"≤ {status['max_calls_per_generation']}")

    with st.expander("OpenAI API & cost controls", expanded=not status["configured"]):
        st.caption(
            "You can use your own OpenAI API key. A saved personal key is encrypted at rest with the MUNSHI server vault, "
            "is never written to Git, and is never displayed back to the browser."
        )
        key_value = st.text_input(
            "Personal OpenAI API key",
            type="password",
            key="native_resume_v2_api_key",
            placeholder="Paste a key only when you want to save or replace it",
            help="The field is cleared after a successful encrypted save.",
        )
        key_actions = st.columns((1.2, 1, 2.4), gap="small")
        with key_actions[0]:
            save_disabled = not status["secure_storage_available"] or not key_value.strip()
            if st.button(
                "Save encrypted key",
                type="primary",
                use_container_width=True,
                disabled=save_disabled,
                key="native_resume_v2_save_api_key",
            ):
                try:
                    save_personal_api_key(key_value)
                except (ValueError, VaultError, RuntimeError) as error:
                    st.error(str(error))
                else:
                    st.session_state["native_resume_v2_api_key"] = ""
                    _toast("success", "Personal OpenAI API key saved securely.")
                    st.rerun()
        with key_actions[1]:
            if st.button(
                "Remove key",
                use_container_width=True,
                disabled=not status["personal_key_saved"],
                key="native_resume_v2_remove_api_key",
            ):
                try:
                    removed = delete_personal_api_key()
                except Exception as error:
                    st.error(str(error))
                else:
                    _toast("success", "Personal API key removed." if removed else "No personal API key was stored.")
                    st.rerun()
        with key_actions[2]:
            if not status["secure_storage_available"]:
                st.warning("Encrypted personal-key storage is not available on this runtime. Server administrators must configure MUNSHI_VAULT_KEY first.")
            elif status["personal_key_saved"]:
                st.success("An encrypted personal key is saved for your Resume Studio account.")
            elif status["key_source"] == "server_environment":
                st.info("Resume Studio is currently using the server-level OpenAI credential. Saving a personal key will take precedence for your account.")
            else:
                st.info("No OpenAI credential is currently available to Resume Studio.")

        model_options = list(MODEL_OPTIONS)
        if status["model"] not in model_options:
            model_options.insert(0, status["model"])
        model = st.selectbox(
            "Model",
            model_options,
            index=model_options.index(status["model"]),
            format_func=lambda value: MODEL_LABELS.get(value, value),
            key="native_resume_v2_model",
        )
        settings_columns = st.columns(3, gap="small")
        effort = settings_columns[0].selectbox(
            "Reasoning",
            list(REASONING_OPTIONS),
            index=list(REASONING_OPTIONS).index(status["reasoning_effort"]),
            key="native_resume_v2_reasoning",
        )
        tokens = settings_columns[1].select_slider(
            "Max output tokens",
            options=list(range(2000, 12001, 500)),
            value=min(12000, max(2000, int(status["max_output_tokens"]) // 500 * 500)),
            key="native_resume_v2_tokens",
            help="A hard output ceiling per GPT request. Lower values reduce worst-case output usage but can make complex structured resumes fail early.",
        )
        calls = settings_columns[2].radio(
            "Max GPT calls / resume",
            [1, 2],
            index=0 if int(status["max_calls_per_generation"]) == 1 else 1,
            horizontal=True,
            key="native_resume_v2_max_calls",
            help="2 allows one automatic content-budget repair pass. 1 is the strictest per-resume request cap.",
        )
        if st.button("Save writer settings", use_container_width=True, key="native_resume_v2_save_writer_settings"):
            try:
                save_writer_settings(
                    model_name=model,
                    reasoning_effort=effort,
                    max_output_tokens=int(tokens),
                    max_calls_per_generation=int(calls),
                )
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                _toast("success", "Resume writer settings saved.")
                st.rerun()

        st.caption(
            "Cost control here limits model choice, output ceiling, and GPT requests per resume. It is not an OpenAI account-level spending cap."
        )
    return writer_status()


def _source_workspace() -> dict[str, Any]:
    source = active_source()
    st.markdown("### 1. Master resume")
    st.caption(
        "Upload the truthful master resume that Resume Studio may use as its primary writing evidence. "
        "It becomes the active Resume Studio source after you review and confirm it; it does not silently replace the application-authority Master Resume."
    )

    uploaded = st.file_uploader(
        "Upload master resume",
        type=["txt", "md", "docx"],
        help="DOCX, TXT, and Markdown are parsed locally. PDF parsing is not enabled in this source version yet.",
        key="native_resume_v2_source_upload",
    )
    if uploaded is not None:
        raw = uploaded.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        if st.session_state.get("native_resume_v2_last_upload_digest") != digest:
            try:
                extracted, source_kind = extract_uploaded_source(uploaded.name, raw)
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state["native_resume_v2_source_draft"] = extracted
                st.session_state["native_resume_v2_source_kind"] = source_kind
                st.session_state["native_resume_v2_source_label"] = uploaded.name[:240]
                st.session_state["native_resume_v2_last_upload_digest"] = digest
                st.session_state["native_resume_v2_truth_confirm"] = False
                _toast("success", "Master resume imported locally. Review the extracted text before saving it as confirmed evidence.")

    if "native_resume_v2_source_draft" not in st.session_state:
        st.session_state["native_resume_v2_source_draft"] = str(source.get("content_text") or "")
    if "native_resume_v2_source_label" not in st.session_state:
        st.session_state["native_resume_v2_source_label"] = str(source.get("label") or "Master Resume")
    st.session_state.setdefault("native_resume_v2_source_kind", str(source.get("source_kind") or "pasted_text"))

    label = st.text_input("Source label", key="native_resume_v2_source_label", max_chars=240)
    source_text = st.text_area(
        "Extracted master-resume evidence",
        key="native_resume_v2_source_draft",
        height=300,
        placeholder="Upload a DOCX or paste the complete truthful resume/career-history source here...",
        help="MUNSHI will not infer missing employers, dates, tools, metrics, education, or certifications.",
    )
    confirmed = st.checkbox(
        "I confirm this source is truthful and MUNSHI may use it to create JD-specific resumes.",
        key="native_resume_v2_truth_confirm",
    )
    save_columns = st.columns((1.2, 2.8), gap="small")
    with save_columns[0]:
        if st.button(
            "Save master source",
            type="primary",
            use_container_width=True,
            key="native_resume_v2_save_source",
            disabled=not (confirmed and source_text.strip()),
        ):
            try:
                saved = save_confirmed_source(
                    content_text=source_text,
                    label=label or "Master Resume",
                    source_kind=str(st.session_state.get("native_resume_v2_source_kind") or "pasted_text"),
                )
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                st.session_state["native_resume_v2_source_draft"] = saved["content_text"]
                st.session_state["native_resume_v2_truth_confirm"] = False
                _toast("success", "Confirmed master-resume source saved.")
                st.rerun()
    with save_columns[1]:
        if source:
            st.caption(
                f"Active source: {source.get('label') or 'Master Resume'} · "
                f"SHA-256 {str(source.get('content_sha256') or '')[:12]}… · "
                f"updated {source.get('updated_at') or 'date not recorded'}"
            )
        else:
            st.caption("No confirmed master-resume source is saved yet.")

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
            with st.expander("Review evidence available to GPT", expanded=False):
                st.caption("Only these evidence records may support resume claims. Sensitive self-identification is filtered out before the writer payload is built.")
                for item in bundle["items"][:40]:
                    st.markdown(f"**{esc(item.get('label') or item.get('kind'))}**", unsafe_allow_html=True)
                    st.caption(str(item.get("text") or "")[:450])
                if len(bundle["items"]) > 40:
                    st.caption(f"{len(bundle['items']) - 40} additional evidence records are available but collapsed here.")
    return current


def _job_workspace() -> tuple[int | None, dict[str, Any]]:
    st.markdown("### 2. Target job description")
    options = resume_job_options(limit=300)
    if not options:
        st.info("No stored job with a complete job description is available yet.")
        return None, {}
    labels: dict[str, dict[str, Any]] = {}
    for row in options:
        score = f" · {float(row['hunter_score']):.0f}% match" if row.get("hunter_score") is not None else ""
        label = f"#{row['id']} · {row.get('company_name') or 'Company'} · {row.get('title') or 'Untitled role'}{score}"
        labels[label] = row
    selected_label = st.selectbox("Choose a stored job", list(labels), key="native_resume_v2_job_choice")
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
    with st.expander("Review the JD used for tailoring", expanded=False):
        st.write(context.get("description_raw") or "No stored description.")
        if context.get("qualifications"):
            st.markdown("**Qualifications**")
            st.write(context["qualifications"])
        if context.get("skills_keywords"):
            st.markdown("**Stored skills / keyword evidence**")
            st.write(context["skills_keywords"])
    return job_id, context


def _rewrite_selector(key: str, default: str = "medium") -> str:
    mode = st.radio(
        "Rewrite strength",
        list(REWRITE_MODES),
        index=list(REWRITE_MODES).index(default),
        format_func=lambda value: MODE_LABELS[value],
        horizontal=True,
        key=key,
    )
    st.info(MODE_HELP[mode])
    with st.expander("What each strength changes", expanded=False):
        for value in REWRITE_MODES:
            st.markdown(f"**{MODE_LABELS[value]}**")
            st.caption(REWRITE_PRESETS[value])
    return mode


def _generation_controls(job_id: int, source: dict[str, Any], status: dict[str, Any]) -> None:
    st.markdown("### 3. Rewrite for this JD")
    mode = _rewrite_selector(f"native_resume_v2_initial_mode_{job_id}")
    instruction = st.text_area(
        "Optional direction",
        placeholder="Example: emphasize people analytics and Power BI; keep recruiting operations visible but secondary.",
        height=100,
        key=f"native_resume_v2_initial_instruction_{job_id}",
    )
    if not status["configured"]:
        st.warning("Add an OpenAI API key in GPT writer settings before generating a resume.")
    if not source:
        st.warning("Save a confirmed master-resume source first.")
    if st.button(
        f"Generate {MODE_LABELS[mode].lower()} rewrite",
        type="primary",
        use_container_width=True,
        key=f"native_resume_v2_generate_{job_id}",
        disabled=not (status["configured"] and source),
    ):
        try:
            with st.spinner("Building evidence, rewriting against the JD, truth-checking, and validating the one-page budget…"):
                record = generate_resume(job_id=job_id, instruction=instruction, rewrite_mode=mode)
        except Exception as error:
            st.error(str(error))
        else:
            st.session_state["native_resume_v2_selected_version"] = record["version_id"]
            _toast("success", f"{MODE_LABELS[mode]} JD rewrite saved as v{record['version_number']}.")
            st.rerun()


def _download_panel(version_id: str, record: dict[str, Any], context: dict[str, Any]) -> None:
    st.markdown("#### Downloads")
    html = version_html(version_id)
    docx = version_docx(version_id)
    download_columns = st.columns(3, gap="small")
    download_columns[0].download_button(
        "Download HTML", data=html.encode("utf-8"), file_name=safe_filename(record, context, "html"),
        mime="text/html", use_container_width=True, key=f"native_resume_v2_html_{version_id}",
    )
    download_columns[1].download_button(
        "Download DOCX", data=docx, file_name=safe_filename(record, context, "docx"),
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True, key=f"native_resume_v2_docx_{version_id}",
    )
    pdf_key = f"native_resume_v2_pdf_bytes_{version_id}"
    pages_key = f"native_resume_v2_pdf_pages_{version_id}"
    with download_columns[2]:
        if pdf_key not in st.session_state:
            if st.button("Build PDF", use_container_width=True, key=f"native_resume_v2_build_pdf_{version_id}"):
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
                "Download PDF", data=st.session_state[pdf_key], file_name=safe_filename(record, context, "pdf"),
                mime="application/pdf", use_container_width=True, key=f"native_resume_v2_pdf_download_{version_id}",
            )
    if pages_key in st.session_state:
        pages = int(st.session_state[pages_key])
        if pages == 1:
            st.success("Physical PDF check: 1 Letter page.")
        else:
            st.warning(f"Physical PDF check: {pages} pages. Create a shorter revision before using it.")


def _version_workspace(job_id: int, context: dict[str, Any], status: dict[str, Any]) -> None:
    versions = list_versions(job_id=job_id, limit=100)
    if not versions:
        st.info("No native resume versions exist for this job yet.")
        return
    st.markdown("### 4. Resume Studio")
    version_ids = [str(item["version_id"]) for item in versions]
    if st.session_state.get("native_resume_v2_selected_version") not in version_ids:
        st.session_state["native_resume_v2_selected_version"] = version_ids[0]
    by_id = {str(item["version_id"]): item for item in versions}
    selected_id = st.selectbox(
        "Version history",
        version_ids,
        format_func=lambda value: (
            f"v{by_id[value]['version_number']} · {by_id[value]['created_at']} · "
            f"{MODE_LABELS.get((by_id[value]['diagnostics'] or {}).get('rewrite_mode'), 'Legacy')} · "
            f"ATS readiness {by_id[value]['diagnostics'].get('ats_readiness_estimate', 'N/A')}"
        ),
        key="native_resume_v2_selected_version",
    )
    record = get_version(selected_id)
    diagnostics = record["diagnostics"]
    top = st.columns(6, gap="small")
    top[0].metric("Version", f"v{record['version_number']}")
    top[1].metric("Rewrite", MODE_LABELS.get(diagnostics.get("rewrite_mode"), "Legacy"))
    top[2].metric("ATS readiness", f"{diagnostics.get('ats_readiness_estimate', 0)}/100")
    top[3].metric("JD coverage", f"{float(diagnostics.get('jd_term_coverage_percent') or 0):.0f}%")
    top[4].metric("Words", int(diagnostics.get("word_count") or 0))
    top[5].metric("Truth audit", str((diagnostics.get("truth_audit") or {}).get("status") or "Unknown"))
    st.caption(
        "ATS readiness is MUNSHI's explainable estimate, not a score returned by an employer ATS. "
        "Truth and numeric-claim guards must pass before a version is stored."
    )

    preview, analysis = st.columns((1.65, 1), gap="large")
    with preview:
        st.markdown("#### Resume preview")
        components.html(version_html(selected_id), height=1040, scrolling=True)
    with analysis:
        st.markdown("#### Evidence & writer diagnostics")
        truth = diagnostics.get("truth_audit") or {}
        st.success(f"Evidence audit PASS · {truth.get('evidence_ids_used', 0)} evidence references · numeric claim guard PASS")
        if diagnostics.get("content_budget_issues"):
            st.warning("Content budget: " + ", ".join(diagnostics["content_budget_issues"]))
        else:
            st.success("Content budget: PASS")
        if diagnostics.get("writer_model"):
            st.caption(
                f"Writer: {diagnostics.get('writer_model')} · reasoning {diagnostics.get('writer_reasoning_effort')} · "
                f"API calls {diagnostics.get('writer_api_calls')}/{diagnostics.get('writer_api_call_limit')}"
            )
        with st.expander("Matched JD terms", expanded=False):
            matched = diagnostics.get("matched_jd_terms") or []
            st.write(", ".join(matched[:50]) if matched else "No deterministic term matches recorded.")
        with st.expander("JD terms not currently present", expanded=True):
            st.caption("Missing does not mean MUNSHI is allowed to add it. Unsupported skills and claims must remain absent.")
            missing = diagnostics.get("missing_jd_terms") or []
            st.write(", ".join(missing[:30]) if missing else "No tracked terms missing.")
        st.markdown("#### Authority boundary")
        st.info(
            "This is still a native candidate artifact preview. n8n remains the current authoritative application-preparation path until parity is explicitly approved."
        )
        st.button(
            "Use for application", disabled=True, use_container_width=True,
            help="Enabled only after native-vs-n8n parity and the explicit authority gate pass.",
            key=f"native_resume_v2_use_{selected_id}",
        )

    _download_panel(selected_id, record, context)

    st.markdown("### 5. Create another revision")
    st.caption("Every revision is immutable. The selected version remains available in history.")
    prior_mode = diagnostics.get("rewrite_mode") if diagnostics.get("rewrite_mode") in REWRITE_MODES else "medium"
    mode = _rewrite_selector(f"native_resume_v2_revision_mode_{selected_id}", default=prior_mode)
    instruction = st.text_area(
        "Tell MUNSHI what else to change",
        placeholder="Example: make the summary shorter, move Power BI earlier, and reduce recruiting emphasis.",
        height=100,
        key=f"native_resume_v2_revision_instruction_{selected_id}",
    )
    lock_specs = [
        ("contact", "Contact"), ("education", "Education"), ("skills", "Skills"),
        ("experience", "Experience"), ("projects", "Projects"),
        ("certifications", "Certifications"), ("summary", "Summary"),
    ]
    st.caption("Optional section locks")
    locks: list[str] = []
    lock_columns = st.columns(4, gap="small")
    for index, (key, label) in enumerate(lock_specs):
        with lock_columns[index % 4]:
            if st.checkbox(label, key=f"native_resume_v2_lock_{key}_{selected_id}"):
                locks.append(key)
    if st.button(
        "Apply AI revision", type="primary", use_container_width=True,
        key=f"native_resume_v2_revision_{selected_id}",
        disabled=not (status["configured"] and instruction.strip()),
    ):
        try:
            with st.spinner("Rewriting inside the same evidence boundary and re-running all guards…"):
                revised = generate_resume(
                    job_id=job_id,
                    parent_version_id=selected_id,
                    instruction=instruction,
                    rewrite_mode=mode,
                    locked_sections=locks,
                )
        except Exception as error:
            st.error(str(error))
        else:
            st.session_state["native_resume_v2_selected_version"] = revised["version_id"]
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
        "Upload your master resume, choose a target JD, control rewrite strength and GPT usage, then inspect and download a truthful job-specific resume inside MUNSHI.",
    )
    st.markdown(
        '<div class="product-callout"><div><strong>Evidence-first personal writer</strong><span>Choose Slight, Medium, or Aggressive rewriting. Aggressive changes structure and emphasis, never the truth boundary.</span></div><span class="status-chip">Prepare only</span></div>',
        unsafe_allow_html=True,
    )
    status = _writer_settings_panel()
    st.divider()
    source = _source_workspace()
    st.divider()
    job_id, context = _job_workspace()
    if job_id is None:
        return
    st.divider()
    _generation_controls(job_id, source, status)
    st.divider()
    _version_workspace(job_id, context, status)
