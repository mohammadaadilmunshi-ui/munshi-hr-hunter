"""Visual Master Resume file card and paper preview.

Keeps the legacy source/edit/replace authority, but replaces the debug-like
source text preview with a paper canvas. The legacy source table does not retain
original PDF/DOCX bytes, so this remains an honest normalized-text rendering.
"""
from __future__ import annotations

from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from app import master_resume_file_ui_v1 as legacy
from app import native_resume_presentation_v1 as presentation


def _preview_settings() -> dict[str, Any]:
    left, font_col, size_col, align_col, fit_col = st.columns((1.15, 1.45, .9, 1.1, 1.2), gap="small")
    with left:
        template = st.segmented_control(
            "Template",
            list(presentation.TEMPLATES),
            default=st.session_state.get("master_resume_canvas_template", "Standard"),
            key="master_resume_canvas_template",
        ) or "Standard"
    with font_col:
        font = st.selectbox(
            "Font",
            list(presentation.FONTS),
            index=list(presentation.FONTS).index(st.session_state.get("master_resume_canvas_font", "Arial"))
            if st.session_state.get("master_resume_canvas_font", "Arial") in presentation.FONTS else 0,
            key="master_resume_canvas_font",
        )
    with size_col:
        font_size = st.selectbox(
            "Size",
            list(presentation.FONT_SIZES),
            index=list(presentation.FONT_SIZES).index(st.session_state.get("master_resume_canvas_font_size", 10.5))
            if st.session_state.get("master_resume_canvas_font_size", 10.5) in presentation.FONT_SIZES else 2,
            format_func=lambda value: f"{value:g}pt",
            key="master_resume_canvas_font_size",
        )
    with align_col:
        alignment = st.segmented_control(
            "Align",
            list(presentation.ALIGNMENTS),
            default=st.session_state.get("master_resume_canvas_alignment", "Left"),
            key="master_resume_canvas_alignment",
        ) or "Left"
    with fit_col:
        fit = st.toggle(
            "Fit to one page",
            value=bool(st.session_state.get("master_resume_canvas_fit", True)),
            key="master_resume_canvas_fit",
        )
    return presentation.normalized_settings(
        {
            "template": template,
            "font": font,
            "font_size": font_size,
            "alignment": alignment,
            "fit_one_page": fit,
        }
    )


def _render_visual_preview(source: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown("#### Master Resume preview")
        st.caption(
            "Rendered from the authoritative normalized Master Resume source. "
            "Because legacy uploads did not retain the original file bytes, this is a clean reconstruction rather than a pixel-perfect copy of the original PDF/DOCX."
        )
        settings = _preview_settings()
        rendered = presentation.render_master_source_html(
            str(source.get("content_text") or ""),
            settings,
        )
        components.html(rendered, height=1120 if settings["fit_one_page"] else 1320, scrolling=True)


def _render_legacy_edit_replace(source: dict[str, Any]) -> None:
    preview_open = bool(st.session_state.get("master_resume_preview_open"))
    st.session_state["master_resume_preview_open"] = False
    try:
        legacy._render_source_panels(source)
    finally:
        st.session_state["master_resume_preview_open"] = preview_open


def render_compact(authority: dict[str, Any]) -> None:
    """Render the established file card with a visual Preview action."""
    master = dict(authority.get("master_resume") or {})
    source = legacy._active_source() if master.get("configured") else {}

    with st.container(border=True):
        if not master.get("configured") or not source:
            left, right = st.columns((3.5, 1.2), gap="small")
            with left:
                st.markdown("#### 📄 Master Resume")
                st.caption("No active source yet. Add one once and MUNSHI will use it as Candidate Truth evidence.")
            with right:
                st.link_button("Open Resume Studio", "?view=resume-studio", use_container_width=True)
            return

        label = str(master.get("label") or source.get("label") or "Master Resume")
        kind = legacy._file_type(label, str(master.get("source_kind") or source.get("source_kind") or ""))
        facts = int(authority.get("structured_fact_count") or 0)
        confirmed = bool(authority.get("profile", {}).get("confirmed"))

        icon_col, info_col, studio_col = st.columns((0.45, 3.45, 1.2), gap="small")
        with icon_col:
            st.markdown("## 📄")
        with info_col:
            st.markdown(f"#### {label}")
            truth_label = f"{facts} confirmed Candidate Truth fact(s)" if confirmed else "Candidate Truth review required"
            st.caption(
                f"Master Resume · {kind} · {truth_label} · Updated {master.get('updated_at') or 'unknown'}"
            )
        with studio_col:
            st.link_button("Edit in Resume Studio", "?view=resume-studio", use_container_width=True)

        preview_col, edit_col, replace_col, download_col = st.columns(4, gap="small")
        with preview_col:
            if st.button("Preview", use_container_width=True, key="master_resume_visual_preview_button"):
                legacy._toggle("preview")
                st.rerun()
        with edit_col:
            if st.button("Edit source", use_container_width=True, key="master_resume_visual_edit_button"):
                legacy._toggle("edit")
                st.rerun()
        with replace_col:
            if st.button("Replace", use_container_width=True, key="master_resume_visual_replace_button"):
                legacy._toggle("replace")
                st.rerun()
        with download_col:
            st.download_button(
                "Download source text",
                data=str(source.get("content_text") or ""),
                file_name=legacy._download_name(label),
                mime="text/plain",
                use_container_width=True,
                key="master_resume_visual_download_source",
            )

        st.caption(
            "Preview is a clean visual reconstruction of the normalized Master Resume source. "
            "The exact original legacy upload cannot be reproduced because the old source table did not retain its file bytes."
        )

    if st.session_state.get("master_resume_preview_open"):
        _render_visual_preview(source)
    _render_legacy_edit_replace(source)
