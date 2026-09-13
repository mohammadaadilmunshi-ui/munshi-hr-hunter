"""Resume Studio V6 presentation shell.

V6 preserves V5 generation, truth/JD binding, one-page verification and immutable
artifact behavior. It adds a professional Standard/Jake presentation toolbar and
applies those choices to the on-screen Native resume canvas only. Checked DOCX/PDF
artifacts remain the exact V5 authority until presentation metadata is versioned
into the artifact contract in a later tranche.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app import native_resume_presentation_v1 as presentation
from app import native_resume_service_v5 as resume_v5
from app import resume_studio_page_v5 as v5


def _presentation_settings() -> dict[str, Any]:
    st.markdown("### Resume appearance")
    st.caption(
        "Choose how Native resumes look while editing. Standard is a crisp ATS-safe sans-serif layout; Jake uses a distinct Computer-Modern-inspired editorial layout. Truth, ATS checks, JD coverage and one-page generation remain unchanged."
    )
    template_col, font_col, size_col, align_col, fit_col = st.columns(
        (1.2, 1.45, .9, 1.1, 1.2), gap="small"
    )
    with template_col:
        template = st.segmented_control(
            "Template",
            list(presentation.TEMPLATES),
            default=st.session_state.get("native_resume_canvas_template", "Standard"),
            key="native_resume_canvas_template",
        ) or "Standard"

    font_key = "native_resume_canvas_font"
    template_seen_key = "native_resume_canvas_template_seen"
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
        current_size = st.session_state.get("native_resume_canvas_font_size", 10.5)
        font_size = st.selectbox(
            "Size",
            list(presentation.FONT_SIZES),
            index=(
                list(presentation.FONT_SIZES).index(current_size)
                if current_size in presentation.FONT_SIZES
                else 2
            ),
            format_func=lambda value: f"{value:g}pt",
            key="native_resume_canvas_font_size",
        )
    with align_col:
        alignment = st.segmented_control(
            "Align",
            list(presentation.ALIGNMENTS),
            default=st.session_state.get("native_resume_canvas_alignment", "Left"),
            key="native_resume_canvas_alignment",
        ) or "Left"
    with fit_col:
        fit = st.toggle(
            "Fit to one page",
            value=bool(st.session_state.get("native_resume_canvas_fit", True)),
            key="native_resume_canvas_fit",
        )

    settings = presentation.normalized_settings(
        {
            "template": template,
            "font": font,
            "font_size": font_size,
            "alignment": alignment,
            "fit_one_page": fit,
        }
    )
    st.caption(
        "Preview styling is presentation-only in this tranche. The checked PDF/DOCX download remains the immutable Standard V5 artifact until presentation settings are versioned into the artifact digest."
    )
    return settings


def render() -> None:
    settings = _presentation_settings()
    original_version_html = resume_v5.version_html

    def styled_version_html(version_id: str) -> str:
        record = resume_v5.get_version(str(version_id))
        return presentation.render_document_html(record["document"], settings)

    resume_v5.version_html = styled_version_html
    try:
        v5.render()
    finally:
        resume_v5.version_html = original_version_html


__all__ = ["render"]
