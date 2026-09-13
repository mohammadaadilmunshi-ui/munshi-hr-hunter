"""First-class Master Resume file UX for the unified Profile workspace.

The current Native Resume source table stores the authoritative normalized source
text and metadata, not the original PDF/DOCX bytes. This UI therefore presents a
professional source authority card: users can preview/download the authoritative
source text, edit it as a new source revision, replace it with PDF/DOCX/TXT/MD,
or open Resume Studio for formatted resume editing.

Any source change creates a new Master Resume authority and intentionally requires
Candidate Truth to be rebuilt/reconfirmed before application planning continues.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from app import native_resume_service_v3 as resume_v3

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
_ALLOWED_SOURCE_KINDS = {"pasted_text", "text_upload", "docx_upload"}


def _active_source() -> dict[str, Any]:
    return resume_v3.v2.v1.active_source() or {}


def _file_type(label: str, source_kind: str) -> str:
    suffix = Path(str(label or "")).suffix.strip(".").upper()
    if suffix:
        return suffix
    mapping = {
        "docx_upload": "DOCX",
        "text_upload": "DOCUMENT",
        "pasted_text": "TEXT",
    }
    return mapping.get(str(source_kind or ""), "DOCUMENT")


def _download_name(label: str) -> str:
    stem = Path(str(label or "Master Resume")).stem.strip() or "Master Resume"
    return f"{stem} - source.txt"


def _reset_source_panels() -> None:
    for key in (
        "master_resume_preview_open",
        "master_resume_edit_open",
        "master_resume_replace_open",
    ):
        st.session_state[key] = False


def _toggle(panel: str) -> None:
    wanted = f"master_resume_{panel}_open"
    current = bool(st.session_state.get(wanted))
    _reset_source_panels()
    st.session_state[wanted] = not current


def _save_replacement(upload: Any) -> dict[str, Any]:
    data = bytes(upload.getvalue())
    if not data:
        raise ValueError("Choose a non-empty resume file.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Master Resume files must be 12 MB or smaller.")
    text, source_kind = resume_v3.extract_uploaded_source(str(upload.name), data)
    return resume_v3.v2.v1.save_confirmed_source(
        content_text=text,
        label=str(upload.name),
        source_kind=source_kind,
    )


def _save_source_edit(source: dict[str, Any], text: str) -> dict[str, Any]:
    source_kind = str(source.get("source_kind") or "pasted_text")
    if source_kind not in _ALLOWED_SOURCE_KINDS:
        source_kind = "pasted_text"
    return resume_v3.v2.v1.save_confirmed_source(
        content_text=text,
        label=str(source.get("label") or "Master Resume"),
        source_kind=source_kind,
    )


def _render_source_panels(source: dict[str, Any]) -> None:
    if st.session_state.get("master_resume_preview_open"):
        with st.container(border=True):
            st.markdown("#### Master Resume source preview")
            st.caption(
                "This is the authoritative normalized source text used for Candidate Truth and resume generation."
            )
            st.text_area(
                "Source text",
                value=str(source.get("content_text") or ""),
                height=430,
                disabled=True,
                label_visibility="collapsed",
                key="master_resume_source_preview",
            )

    if st.session_state.get("master_resume_edit_open"):
        with st.container(border=True):
            st.markdown("#### Edit Master Resume source")
            st.warning(
                "Saving changes creates a new Master Resume source revision. Candidate Truth and derived answers must be rebuilt/reconfirmed against the new source before AutoApply can use them."
            )
            with st.form("master_resume_edit_source_form"):
                edited = st.text_area(
                    "Authoritative source text",
                    value=str(source.get("content_text") or ""),
                    height=500,
                )
                save = st.form_submit_button(
                    "Save as new Master Resume revision",
                    type="primary",
                    use_container_width=True,
                )
            if save:
                try:
                    _save_source_edit(source, edited)
                except Exception as error:  # noqa: BLE001
                    st.error(str(error))
                else:
                    _reset_source_panels()
                    st.success("New Master Resume revision saved. Review Candidate Truth before application use.")
                    st.rerun()

    if st.session_state.get("master_resume_replace_open"):
        with st.container(border=True):
            st.markdown("#### Replace Master Resume")
            st.warning(
                "Replacing the Master Resume changes the evidence authority. Existing confirmed profile facts remain historical until the new source is extracted and confirmed."
            )
            upload = st.file_uploader(
                "Choose PDF, DOCX, TXT, or Markdown",
                type=["pdf", "docx", "txt", "md"],
                accept_multiple_files=False,
                key="master_resume_replacement_upload",
            )
            if st.button(
                "Use this file as the new Master Resume",
                type="primary",
                use_container_width=True,
                disabled=upload is None,
                key="master_resume_replace_commit",
            ):
                try:
                    _save_replacement(upload)
                except Exception as error:  # noqa: BLE001
                    st.error(str(error))
                else:
                    _reset_source_panels()
                    st.success("Master Resume replaced. Build and confirm Candidate Truth for the new source.")
                    st.rerun()


def render_compact(authority: dict[str, Any]) -> None:
    """Render the active Master Resume as a professional evidence-authority card."""
    master = dict(authority.get("master_resume") or {})
    source = _active_source() if master.get("configured") else {}

    with st.container(border=True):
        if not master.get("configured") or not source:
            left, right = st.columns((3.5, 1.2), gap="medium")
            with left:
                st.caption("MASTER RESUME AUTHORITY")
                st.markdown("### Add your Master Resume")
                st.write(
                    "Add the resume you want MUNSHI to treat as permanent evidence for Candidate Truth and future job-specific resumes."
                )
            with right:
                st.link_button("Open Resume Studio", "?view=resume-studio", use_container_width=True)
            return

        label = str(master.get("label") or source.get("label") or "Master Resume")
        kind = _file_type(label, str(master.get("source_kind") or source.get("source_kind") or ""))
        facts = int(authority.get("structured_fact_count") or 0)
        profile_confirmed = bool(authority.get("profile", {}).get("confirmed"))
        truth_label = (
            f"{facts} confirmed Candidate Truth facts"
            if profile_confirmed
            else "Candidate Truth review required"
        )

        info_col, studio_col = st.columns((4.0, 1.25), gap="medium", vertical_alignment="center")
        with info_col:
            st.caption("MASTER RESUME AUTHORITY")
            st.markdown(f"### {label}")
            st.caption(
                f"{kind} · {truth_label} · Updated {master.get('updated_at') or 'unknown'}"
            )
        with studio_col:
            st.link_button("Edit in Resume Studio", "?view=resume-studio", use_container_width=True)

        st.divider()
        preview_col, edit_col, replace_col, download_col = st.columns(4, gap="small")
        with preview_col:
            if st.button("Preview resume", use_container_width=True, key="master_resume_preview_button"):
                _toggle("preview")
                st.rerun()
        with edit_col:
            if st.button("Edit source", use_container_width=True, key="master_resume_edit_button"):
                _toggle("edit")
                st.rerun()
        with replace_col:
            if st.button("Replace file", use_container_width=True, key="master_resume_replace_button"):
                _toggle("replace")
                st.rerun()
        with download_col:
            st.download_button(
                "Download source text",
                data=str(source.get("content_text") or ""),
                file_name=_download_name(label),
                mime="text/plain",
                use_container_width=True,
                key="master_resume_download_source",
            )

        st.caption(
            "Legacy uploads preserve the authoritative extracted text and metadata, not the original PDF/DOCX bytes. Preview uses that same source without inventing or rewriting content."
        )

    _render_source_panels(source)


def render_detail(authority: dict[str, Any]) -> None:
    """Render the Master Resume tab without exposing raw implementation JSON by default."""
    st.markdown("### Master Resume")
    render_compact(authority)
    master = dict(authority.get("master_resume") or {})
    if not master.get("configured"):
        return

    if authority.get("profile", {}).get("confirmed"):
        st.success(
            "Candidate Truth is confirmed against this active Master Resume. Resume generation and application planning bind to the same source/profile digest."
        )
    else:
        st.warning(
            "Candidate Truth is not confirmed against the current Master Resume. Review and confirm it before an application can use this source."
        )

    with st.expander("Advanced source details", expanded=False):
        st.code(
            "\n".join(
                [
                    f"source_id: {master.get('source_id') or 'unknown'}",
                    f"source_kind: {master.get('source_kind') or 'unknown'}",
                    f"sha256: {master.get('sha256') or 'unknown'}",
                    f"profile_state: {authority.get('state') or 'unknown'}",
                ]
            ),
            language="text",
        )
