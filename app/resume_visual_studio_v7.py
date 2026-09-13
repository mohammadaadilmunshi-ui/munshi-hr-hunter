"""Professional Resume Studio V7 presentation refinements.

V7 is intentionally presentation-only. It keeps V5/V6 Candidate Truth, Native
Resume, artifact, application-plan and final-submit authority unchanged while
making the two user-facing resume styles match the product contract more closely:

* Standard: crisp ATS-safe sans-serif layout with strong information hierarchy.
* Jake: genuinely distinct classic/Computer-Modern-inspired editorial layout.

The same renderer is used by Master Resume visual preview and Native Resume
preview so users no longer see two unrelated resume presentations.
"""
from __future__ import annotations

import re
from html import escape
from typing import Any

import streamlit as st

from app import resume_visual_studio_v6 as v6

_TEMPLATES = ("Standard", "Jake")
_FONTS = ("Arial", "Computer Modern", "Helvetica", "Georgia", "Times New Roman")


def _font_stack(name: str) -> str:
    mapping = {
        "Arial": "Arial, Helvetica, sans-serif",
        "Helvetica": "Helvetica, Arial, sans-serif",
        "Computer Modern": "'Computer Modern Serif', 'Latin Modern Roman', 'STIX Two Text', 'Times New Roman', serif",
        "Georgia": "Georgia, 'Times New Roman', serif",
        "Times New Roman": "'Times New Roman', Times, serif",
    }
    return mapping.get(name, mapping["Arial"])


def _toolbar(key_prefix: str, *, available_sections: list[str] | None = None) -> dict[str, Any]:
    """Render the V7 design toolbar and switch font defaults with the template."""
    st.markdown(
        """<style>
        [class*="st-key-resume_visual_toolbar_"] {
            padding:.55rem .7rem .35rem!important;
            border:1px solid rgba(24,67,55,.12);
            border-radius:16px;
            background:#fbfcfb;
            box-shadow:0 1px 2px rgba(20,42,35,.03);
        }
        </style>""",
        unsafe_allow_html=True,
    )
    with st.container(key=f"resume_visual_toolbar_{key_prefix}"):
        columns = st.columns((1.2, 1.45, .78, 1.0, .9, 1.25), gap="small")
        with columns[0]:
            template_key = f"{key_prefix}_template"
            if st.session_state.get(template_key) not in _TEMPLATES:
                st.session_state[template_key] = "Standard"
            template = st.selectbox("Template", list(_TEMPLATES), key=template_key)

        font_key = f"{key_prefix}_font"
        template_seen_key = f"{key_prefix}_v7_template_seen"
        previous_template = st.session_state.get(template_seen_key)
        if previous_template != template:
            st.session_state[template_seen_key] = template
            st.session_state[font_key] = "Computer Modern" if template == "Jake" else "Arial"
        elif st.session_state.get(font_key) not in _FONTS:
            st.session_state[font_key] = "Computer Modern" if template == "Jake" else "Arial"

        with columns[1]:
            font = st.selectbox("Font", list(_FONTS), key=font_key)
        with columns[2]:
            size = st.selectbox(
                "Size",
                list(v6._FONT_SIZES),
                index=2,
                format_func=lambda value: f"{value:g} pt",
                key=f"{key_prefix}_size",
            )
        with columns[3]:
            alignment = st.selectbox(
                "Align",
                list(v6._ALIGNMENTS),
                key=f"{key_prefix}_align",
            )
        with columns[4]:
            fit = st.toggle("Fit one page", value=True, key=f"{key_prefix}_fit")
        with columns[5]:
            order = st.selectbox(
                "Sections",
                list(v6._ORDER_PRESETS),
                key=f"{key_prefix}_order",
            )

        sections = list(available_sections or [])
        visible = sections
        if sections:
            with st.expander("Section visibility & order", expanded=False):
                visible = st.multiselect(
                    "Visible in preview",
                    sections,
                    default=sections,
                    key=f"{key_prefix}_visible_sections",
                )
                st.caption(
                    "These controls change the live presentation only. Candidate Truth and the verified application artifact remain immutable."
                )

    return {
        "template": template,
        "font": font,
        "font_size": float(size),
        "alignment": alignment,
        "fit_one_page": bool(fit),
        "order": order,
        "visible_sections": visible,
    }


def _theme_css(preferences: dict[str, Any]) -> str:
    template = str(preferences.get("template") or "Standard")
    font_name = str(preferences.get("font") or ("Computer Modern" if template == "Jake" else "Arial"))
    body_font = _font_stack(font_name)
    font_size = float(preferences.get("font_size") or 10.5)
    align = "justify" if preferences.get("alignment") == "Justified" else "left"

    if template == "Jake":
        heading_font = "'Computer Modern Serif', 'Latin Modern Roman', 'STIX Two Text', 'Times New Roman', serif"
        name_font = heading_font
        name_size = "25.5px"
        name_weight = 600
        page_padding = "42px 51px 43px"
        section_margin = "9px"
        rule = "1px solid #2a312e"
        heading_size = "11.1pt"
        heading_weight = 650
        letter_spacing = ".045em"
        contact_size = "8.9pt"
        entry_gap = "4px"
        subtitle_style = "italic"
    else:
        heading_font = body_font
        name_font = body_font
        name_size = "26px"
        name_weight = 800
        page_padding = "44px 55px 46px"
        section_margin = "10px"
        rule = "1.35px solid #26312d"
        heading_size = "11.25pt"
        heading_weight = 800
        letter_spacing = ".035em"
        contact_size = "8.7pt"
        entry_gap = "5px"
        subtitle_style = "normal"

    return f"""
    :root {{ --body-font:{body_font}; --body-size:{font_size}pt; }}
    * {{ box-sizing:border-box; }}
    html,body {{ margin:0; padding:0; background:#f3f6f4; }}
    body {{ font-family:var(--body-font); color:#111715; }}
    .canvas {{ display:flex; justify-content:center; padding:30px 18px 44px; min-height:100vh; }}
    .sheet {{
        width:816px; min-height:1056px; background:#fff; padding:{page_padding};
        box-shadow:0 14px 42px rgba(20,40,34,.11), 0 2px 8px rgba(20,40,34,.05);
        transform-origin:top center;
    }}
    .name {{
        margin:0; text-align:center; font-family:{name_font}; font-weight:{name_weight};
        font-size:{name_size}; line-height:1.04; letter-spacing:-.01em;
    }}
    .contact {{
        text-align:center; margin:7px auto 0; max-width:94%; font-size:{contact_size};
        line-height:1.34; color:#27302d;
    }}
    .section {{ margin-top:{section_margin}; }}
    .section h2 {{
        margin:0 0 5px; padding:0 0 4px; border-bottom:{rule};
        font-family:{heading_font}; font-size:{heading_size}; font-weight:{heading_weight};
        letter-spacing:{letter_spacing}; text-transform:uppercase; line-height:1.08;
    }}
    .body,.entry,.skills,.certs {{ font-size:var(--body-size); line-height:1.18; text-align:{align}; }}
    .summary {{ margin:0; font-size:var(--body-size); line-height:1.2; text-align:{align}; }}
    .entry {{ margin:0 0 {entry_gap}; break-inside:avoid; }}
    .entry-head,.source-entry-head {{
        display:flex; justify-content:space-between; gap:18px; align-items:baseline;
    }}
    .entry-head > :last-child,.source-entry-head > :last-child {{ white-space:nowrap; }}
    .entry-title {{ font-weight:700; }}
    .entry-sub {{ margin-top:1px; font-style:{subtitle_style}; color:#232a27; }}
    ul {{ margin:3px 0 0 17px; padding:0; }}
    li {{ margin:0 0 2px; padding-left:1px; }}
    .skills p,.certs p {{ margin:0 0 3px; }}
    .raw-line {{ margin:0 0 3px; white-space:normal; font-size:var(--body-size); line-height:1.19; text-align:{align}; }}
    .raw-bullet {{ margin:0; font-size:var(--body-size); line-height:1.18; text-align:{align}; }}
    .raw-subhead {{ font-weight:700; margin:3px 0 1px; }}
    .source-entry {{ margin:0 0 4px; break-inside:avoid; }}
    .source-entry-title {{ font-weight:700; }}
    .source-date {{ font-size:calc(var(--body-size) - .15pt); }}
    .source-bullets {{ margin-top:2px; }}
    @media print {{
      html,body {{ background:#fff; }}
      .canvas {{ padding:0; }}
      .sheet {{ box-shadow:none; width:8.5in; min-height:11in; }}
    }}
    """


_DATE_TAIL = re.compile(
    r"^(?P<left>.*?)(?:\s{2,}|\s+\|\s+)?(?P<date>(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+)?(?:19|20)\d{2}(?:\s*(?:-|–|—|to)\s*(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+)?(?:19|20)\d{2}|Present|Current)?)$",
    re.IGNORECASE,
)


def _split_date_tail(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    match = _DATE_TAIL.match(text)
    if not match:
        return text, ""
    left = str(match.group("left") or "").strip(" |")
    date = str(match.group("date") or "").strip()
    if not left:
        return text, ""
    return left, date


def _master_section_html(title: str, lines: list[str]) -> str:
    """Format normalized source into the same polished page grammar as Native Resume."""
    rendered: list[str] = []
    bullets: list[str] = []

    def flush_bullets() -> None:
        if not bullets:
            return
        items = "".join(f"<li>{escape(item)}</li>" for item in bullets)
        rendered.append(f'<ul class="source-bullets">{items}</ul>')
        bullets.clear()

    for raw in lines:
        stripped = str(raw or "").strip()
        if not stripped:
            continue
        if stripped.startswith(("•", "- ", "* ")):
            bullets.append(stripped.lstrip("•*- ").strip())
            continue

        flush_bullets()
        left, date = _split_date_tail(stripped)
        looks_like_entry = bool(date) or (
            len(stripped) <= 118
            and " | " in stripped
            and not stripped.endswith((".", ";"))
        )
        if looks_like_entry:
            if not date and " | " in left:
                rendered.append(f'<div class="raw-subhead">{escape(left)}</div>')
            else:
                rendered.append(
                    '<div class="source-entry"><div class="source-entry-head">'
                    f'<span class="source-entry-title">{escape(left)}</span>'
                    f'<span class="source-date">{escape(date)}</span>'
                    '</div></div>'
                )
        else:
            rendered.append(f'<div class="raw-line">{escape(stripped)}</div>')

    flush_bullets()
    return f'<section class="section"><h2>{escape(title)}</h2>{"".join(rendered)}</section>'


def install_resume_visual_studio_v7() -> None:
    """Install V7 styling before (or on top of) the proven V6 presentation hooks."""
    if getattr(v6.studio_v4, "_resume_visual_studio_v7_installed", False):
        return

    v6._TEMPLATES = _TEMPLATES
    v6._FONTS = _FONTS
    v6._toolbar = _toolbar
    v6._theme_css = _theme_css
    v6._master_section_html = _master_section_html
    v6.install_resume_visual_studio_v6()
    v6.studio_v4._resume_visual_studio_v7_installed = True
