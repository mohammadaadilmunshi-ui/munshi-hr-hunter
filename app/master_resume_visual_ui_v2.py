"""Visual Master Resume authority card and professional paper preview.

Keeps the established source/edit/replace authority while presenting the Master
Resume as a first-class evidence source. The legacy source table does not retain
original PDF/DOCX bytes, so visual preview remains an honest normalized-text
reconstruction rather than pretending to reproduce the original file pixel for
pixel.
"""
from __future__ import annotations

from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from app import master_resume_file_ui_v1 as legacy
from app import native_resume_presentation_v1 as presentation


def _preview_settings(source_text: str) -> dict[str, Any]:
    left, font_col, size_col, align_col, fit_col, order_col = st.columns(
        (1.05, 1.3, .78, .95, 1.0, 1.2), gap="small"
    )
    with left:
        template = st.segmented_control(
            "Template",
            list(presentation.TEMPLATES),
            default=st.session_state.get("master_resume_canvas_template", "Standard"),
            key="master_resume_canvas_template",
        ) or "Standard"

    font_key = "master_resume_canvas_font"
    template_seen_key = "master_resume_canvas_template_seen"
    previous_template = st.session_state.get(template_seen_key)
    if previous_template != template:
        st.session_state[template_seen_key] = template
        st.session_state[font_key] = "Computer Modern" if template == "Jake" else "Arial"
    elif st.session_state.get(font_key) not in presentation.FONTS:
        st.session_state[font_key] = "Computer Modern" if template == "Jake" else "Arial"

    with font_col:
        current_font = st.session_state.get(font_key, "Arial")
        font = st.selectbox(
            "Font",
            list(presentation.FONTS),
            index=(
                list(presentation.FONTS).index(current_font)
                if current_font in presentation.FONTS
                else 0
            ),
            key=font_key,
        )
    with size_col:
        current_size = st.session_state.get("master_resume_canvas_font_size", 10.5)
        font_size = st.selectbox(
            "Size",
            list(presentation.FONT_SIZES),
            index=(
                list(presentation.FONT_SIZES).index(current_size)
                if current_size in presentation.FONT_SIZES
                else 2
            ),
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
    with order_col:
        section_order = st.selectbox(
            "Sections",
            list(presentation.SECTION_ORDER_PRESETS),
            key="master_resume_canvas_section_order",
        )

    available = presentation.available_source_sections(source_text)
    with st.expander("Section visibility & order", expanded=False):
        visible_sections = st.multiselect(
            "Visible in preview",
            available,
            default=available,
            key="master_resume_canvas_visible_sections",
        )
        st.caption(
            "These controls change only this visual reconstruction. The "
            "authoritative Master Resume source and Candidate Truth stay unchanged."
        )

    return presentation.normalized_settings(
        {
            "template": template,
            "font": font,
            "font_size": font_size,
            "alignment": alignment,
            "fit_one_page": fit,
            "section_order": section_order,
            "visible_sections": visible_sections,
        }
    )


def _render_visual_preview(source: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown("#### Master Resume preview")
        st.caption(
            "Rendered from the authoritative normalized Master Resume source using the same Standard/Jake presentation system as Native Resume Studio. "
            "Legacy uploads did not retain original file bytes, so this is a clean reconstruction rather than a pixel-perfect copy."
        )
        source_text = str(source.get("content_text") or "")
        settings = _preview_settings(source_text)
        rendered = presentation.render_master_source_html(
            source_text,
            settings,
        )
        components.html(
            rendered,
            height=1120 if settings["fit_one_page"] else 1320,
            scrolling=True,
        )


def _render_legacy_edit_replace(source: dict[str, Any]) -> None:
    preview_open = bool(st.session_state.get("master_resume_preview_open"))
    st.session_state["master_resume_preview_open"] = False
    try:
        legacy._render_source_panels(source)
    finally:
        st.session_state["master_resume_preview_open"] = preview_open


def render_compact(authority: dict[str, Any]) -> None:
    """Render the active Master Resume as a professional evidence-authority card."""
    master = dict(authority.get("master_resume") or {})
    source = legacy._active_source() if master.get("configured") else {}

    with st.container(border=True):
        if not master.get("configured") or not source:
            left, right = st.columns((3.5, 1.2), gap="medium")
            with left:
                st.caption("MASTER RESUME AUTHORITY")
                st.markdown("### Add your Master Resume")
                st.write(
                    "Add the resume MUNSHI should treat as permanent evidence for Candidate Truth and future job-specific resumes."
                )
            with right:
                st.link_button(
                    "Open Resume Studio",
                    "?view=resume-studio",
                    use_container_width=True,
                )
            return

        label = str(master.get("label") or source.get("label") or "Master Resume")
        kind = legacy._file_type(
            label,
            str(master.get("source_kind") or source.get("source_kind") or ""),
        )
        facts = int(authority.get("structured_fact_count") or 0)
        confirmed = bool(authority.get("profile", {}).get("confirmed"))
        truth_label = (
            f"{facts} confirmed Candidate Truth facts"
            if confirmed
            else "Candidate Truth review required"
        )

        info_col, studio_col = st.columns(
            (4.0, 1.25), gap="medium", vertical_alignment="center"
        )
        with info_col:
            st.caption("MASTER RESUME AUTHORITY")
            st.markdown(f"### {label}")
            st.caption(
                f"{kind} · {truth_label} · Updated {master.get('updated_at') or 'unknown'}"
            )
        with studio_col:
            st.link_button(
                "Edit in Resume Studio",
                "?view=resume-studio",
                use_container_width=True,
            )

        st.divider()
        preview_col, edit_col, replace_col, download_col = st.columns(4, gap="small")
        with preview_col:
            if st.button(
                "Preview resume",
                use_container_width=True,
                key="master_resume_visual_preview_button",
            ):
                legacy._toggle("preview")
                st.rerun()
        with edit_col:
            if st.button(
                "Edit source",
                use_container_width=True,
                key="master_resume_visual_edit_button",
            ):
                legacy._toggle("edit")
                st.rerun()
        with replace_col:
            if st.button(
                "Replace file",
                use_container_width=True,
                key="master_resume_visual_replace_button",
            ):
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
            "Legacy uploads preserve authoritative extracted text and metadata, not the original PDF/DOCX bytes. Preview uses that same source without inventing or rewriting content."
        )

    if st.session_state.get("master_resume_preview_open"):
        _render_visual_preview(source)
    _render_legacy_edit_replace(source)
