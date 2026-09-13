"""Visual Resume Studio V6 presentation layer.

This module is deliberately presentation-only. It upgrades two candidate-facing
surfaces without changing Candidate Truth, Native Resume content authority,
application plans, browser automation, or final-submit authority:

* Master Resume Preview becomes a real Letter-size resume canvas instead of a
  disabled raw-text textarea. The source text remains authoritative and is never
  rewritten by this preview renderer.
* Native Resume preview gains a compact design toolbar inspired by familiar
  resume-builder interaction patterns: ATS-safe templates, font/size, alignment,
  one-page preview fitting, section visibility/order, and a centered page canvas.

Verified Native Resume DOCX/PDF application artifacts remain the established ATS
Standard artifacts in this tranche. Design controls are preview-only until a
separate design-to-export parity gate is explicitly enabled.
"""
from __future__ import annotations

import re
from html import escape
from typing import Any, Callable

import streamlit as st
import streamlit.components.v1 as components

from app import master_resume_file_ui_v1 as master_ui
from app import native_resume_artifact_v5 as artifacts
from app import native_resume_service_v5 as resume_v5
from app import resume_studio_page_v4 as studio_v4

_TEMPLATES = ("ATS Standard", "Modern Clean", "Classic Serif")
_FONTS = ("Arial", "Helvetica", "Georgia", "Times New Roman")
_FONT_SIZES = (9.5, 10.0, 10.5, 11.0, 11.5)
_ALIGNMENTS = ("Left", "Justified")
_ORDER_PRESETS = (
    "Experience first",
    "Education first",
    "Skills first",
)
_CANONICAL_SECTIONS = (
    "Summary",
    "Experience",
    "Education",
    "Skills",
    "Projects",
    "Certifications",
)
_SECTION_HEADINGS = {
    "professional summary": "Professional Summary",
    "summary": "Professional Summary",
    "profile": "Professional Summary",
    "education": "Education",
    "skills": "Skills",
    "technical skills": "Skills",
    "work experience": "Work Experience",
    "professional experience": "Work Experience",
    "experience": "Work Experience",
    "projects": "Projects",
    "project experience": "Projects",
    "certifications": "Certifications",
    "certifications & achievements": "Certifications & Achievements",
    "certifications and achievements": "Certifications & Achievements",
    "achievements": "Achievements",
    "awards": "Awards",
    "languages": "Languages",
    "leadership": "Leadership",
}

_ORIGINAL_MASTER_PANELS: Callable[[dict[str, Any]], None] | None = None
_ORIGINAL_VERSION_WORKSPACE: Callable[[int, dict[str, Any], bool], None] | None = None


def _css_font(name: str) -> str:
    mapping = {
        "Arial": "Arial, Helvetica, sans-serif",
        "Helvetica": "Helvetica, Arial, sans-serif",
        "Georgia": "Georgia, 'Times New Roman', serif",
        "Times New Roman": "'Times New Roman', Times, serif",
    }
    return mapping.get(name, "Arial, Helvetica, sans-serif")


def _section_order(preset: str) -> list[str]:
    if preset == "Education first":
        return ["Summary", "Education", "Skills", "Experience", "Projects", "Certifications"]
    if preset == "Skills first":
        return ["Summary", "Skills", "Experience", "Education", "Projects", "Certifications"]
    return ["Summary", "Experience", "Education", "Skills", "Projects", "Certifications"]


def _toolbar(key_prefix: str, *, available_sections: list[str] | None = None) -> dict[str, Any]:
    """Render a compact resume-builder toolbar with context-unique widget keys."""
    st.markdown(
        """<style>
        [class*="st-key-resume_visual_toolbar_"] {
            padding:.35rem .55rem .15rem!important;
            border:1px solid rgba(31,55,48,.10);
            border-radius:14px;
            background:#fbfcfb;
        }
        </style>""",
        unsafe_allow_html=True,
    )
    with st.container(key=f"resume_visual_toolbar_{key_prefix}"):
        columns = st.columns((1.35, 1.45, .85, 1.05, .95, 1.3), gap="small")
        with columns[0]:
            template = st.selectbox(
                "Template",
                list(_TEMPLATES),
                key=f"{key_prefix}_template",
            )
        with columns[1]:
            font = st.selectbox(
                "Font",
                list(_FONTS),
                key=f"{key_prefix}_font",
            )
        with columns[2]:
            size = st.selectbox(
                "Size",
                list(_FONT_SIZES),
                index=2,
                format_func=lambda value: f"{value:g} pt",
                key=f"{key_prefix}_size",
            )
        with columns[3]:
            alignment = st.selectbox(
                "Align",
                list(_ALIGNMENTS),
                key=f"{key_prefix}_align",
            )
        with columns[4]:
            fit = st.toggle(
                "Fit one page",
                value=True,
                key=f"{key_prefix}_fit",
            )
        with columns[5]:
            order = st.selectbox(
                "Sections",
                list(_ORDER_PRESETS),
                key=f"{key_prefix}_order",
            )

        sections = list(available_sections or [])
        visible = sections
        if sections:
            with st.expander("Section visibility", expanded=False):
                visible = st.multiselect(
                    "Visible in preview",
                    sections,
                    default=sections,
                    key=f"{key_prefix}_visible_sections",
                )
                st.caption(
                    "Visibility/order controls affect this live preview only. "
                    "They do not alter Candidate Truth or the verified application artifact."
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
    template = str(preferences.get("template") or "ATS Standard")
    body_font = _css_font(str(preferences.get("font") or "Arial"))
    font_size = float(preferences.get("font_size") or 10.5)
    align = "justify" if preferences.get("alignment") == "Justified" else "left"

    if template == "Classic Serif":
        heading_font = "Georgia, 'Times New Roman', serif"
        heading_rule = "1px solid #303733"
        name_weight = 700
        heading_letter = ".02em"
    elif template == "Modern Clean":
        heading_font = body_font
        heading_rule = "2px solid #1f4b3f"
        name_weight = 750
        heading_letter = ".055em"
    else:
        heading_font = body_font
        heading_rule = "1px solid #313a36"
        name_weight = 750
        heading_letter = ".035em"

    return f"""
    :root {{ --body-font:{body_font}; --body-size:{font_size}pt; }}
    * {{ box-sizing:border-box; }}
    html,body {{ margin:0; padding:0; background:#f4f6f5; }}
    body {{ font-family:var(--body-font); color:#111917; }}
    .canvas {{ display:flex; justify-content:center; padding:26px 18px 38px; min-height:100vh; }}
    .sheet {{
        width:816px; min-height:1056px; background:#fff; padding:42px 50px 44px;
        box-shadow:0 10px 34px rgba(18,35,30,.12); transform-origin:top center;
    }}
    .name {{ margin:0; text-align:center; font-family:{heading_font}; font-weight:{name_weight}; font-size:25px; line-height:1.08; }}
    .contact {{ text-align:center; margin-top:6px; font-size:8.8pt; line-height:1.3; }}
    .section {{ margin-top:10px; }}
    .section h2 {{
        margin:0 0 5px; padding:0 0 4px; border-bottom:{heading_rule};
        font-family:{heading_font}; font-size:11.2pt; letter-spacing:{heading_letter};
        text-transform:uppercase; line-height:1.1;
    }}
    .body, .entry, .skills, .certs {{ font-size:var(--body-size); line-height:1.16; text-align:{align}; }}
    .summary {{ margin:0; font-size:var(--body-size); line-height:1.17; text-align:{align}; }}
    .entry {{ margin:0 0 5px; break-inside:avoid; }}
    .entry-head {{ display:flex; justify-content:space-between; gap:14px; align-items:baseline; }}
    .entry-title {{ font-weight:700; }}
    .entry-sub {{ margin-top:1px; font-style:italic; }}
    ul {{ margin:3px 0 0 17px; padding:0; }}
    li {{ margin:0 0 2px; padding-left:1px; }}
    .skills p, .certs p {{ margin:0 0 3px; }}
    .raw-line {{ margin:0 0 3px; white-space:pre-wrap; font-size:var(--body-size); line-height:1.17; text-align:{align}; }}
    .raw-bullet {{ margin:0 0 2px 16px; padding-left:2px; font-size:var(--body-size); line-height:1.17; text-align:{align}; }}
    .raw-subhead {{ font-weight:700; margin:2px 0 1px; }}
    @media print {{
      html,body {{ background:#fff; }}
      .canvas {{ padding:0; }}
      .sheet {{ box-shadow:none; width:8.5in; min-height:11in; }}
    }}
    """


def _fit_script(enabled: bool) -> str:
    if not enabled:
        return ""
    return """
    <script>
    (function(){
      const sheet = document.getElementById('resume-sheet');
      if(!sheet) return;
      const target = 1056;
      const actual = Math.max(sheet.scrollHeight, sheet.offsetHeight);
      if(actual > target){
        const scale = Math.max(0.80, Math.min(1, target / actual));
        sheet.style.transform = `scale(${scale})`;
        const canvas = document.querySelector('.canvas');
        if(canvas) canvas.style.height = `${Math.ceil(target * scale + 68)}px`;
      }
    })();
    </script>
    """


def _html_page(body: str, preferences: dict[str, Any], *, title: str = "Resume") -> str:
    return f"""<!doctype html>
    <html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{escape(title)}</title><style>{_theme_css(preferences)}</style></head>
    <body><div class="canvas"><main class="sheet" id="resume-sheet">{body}</main></div>
    {_fit_script(bool(preferences.get('fit_one_page')))}</body></html>"""


def _section_alias(line: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9& ]+", "", str(line or "").casefold()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if normalized in _SECTION_HEADINGS:
        return _SECTION_HEADINGS[normalized]
    # Treat short all-caps labels as section headings, but avoid dates/contact lines.
    raw = str(line or "").strip()
    if (
        raw
        and len(raw) <= 48
        and raw.upper() == raw
        and any(char.isalpha() for char in raw)
        and not re.search(r"\d", raw)
        and "@" not in raw
    ):
        return raw.title()
    return None


def _parse_master_source(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    lines = [str(line).rstrip() for line in str(text or "").replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line.strip()]
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in lines:
        heading = _section_alias(line)
        if heading:
            if current_title is not None:
                sections.append((current_title, current_lines))
            elif current_lines:
                preamble.extend(current_lines)
            current_title = heading
            current_lines = []
            continue
        current_lines.append(line)

    if current_title is not None:
        sections.append((current_title, current_lines))
    elif current_lines:
        preamble.extend(current_lines)

    if not sections and preamble:
        # Keep at least a visual body for unusual source layouts.
        name_and_contact = preamble[:3]
        rest = preamble[3:]
        preamble = name_and_contact
        if rest:
            sections = [("Resume", rest)]
    return preamble, sections


def _master_section_html(title: str, lines: list[str]) -> str:
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("•", "- ", "* ")):
            text = stripped.lstrip("•*- ").strip()
            rendered.append(f'<div class="raw-bullet">• {escape(text)}</div>')
        elif len(stripped) <= 96 and (" | " in stripped or re.search(r"\b(?:19|20)\d{2}\b", stripped)):
            rendered.append(f'<div class="raw-subhead">{escape(stripped)}</div>')
        else:
            rendered.append(f'<div class="raw-line">{escape(stripped)}</div>')
    return f'<section class="section"><h2>{escape(title)}</h2>{"".join(rendered)}</section>'


def master_source_html(source: dict[str, Any], preferences: dict[str, Any]) -> str:
    """Render authoritative Master Resume text without rewriting any source content."""
    text = str(source.get("content_text") or "")
    preamble, sections = _parse_master_source(text)
    if preamble:
        name = preamble[0]
        contact = " | ".join(item.strip(" |") for item in preamble[1:] if item.strip())
    else:
        name = str(source.get("label") or "Master Resume")
        contact = ""

    body = [f'<h1 class="name">{escape(name)}</h1>']
    if contact:
        body.append(f'<div class="contact">{escape(contact)}</div>')
    for title, lines in sections:
        body.append(_master_section_html(title, lines))
    return _html_page("".join(body), preferences, title=str(source.get("label") or "Master Resume"))


def _present_sections(document: dict[str, Any]) -> list[str]:
    mapping = {
        "Summary": document.get("summary"),
        "Experience": document.get("experience"),
        "Education": document.get("education"),
        "Skills": document.get("skills"),
        "Projects": document.get("projects"),
        "Certifications": document.get("certifications"),
    }
    return [label for label in _CANONICAL_SECTIONS if mapping.get(label)]


def _contact_line(contact: dict[str, Any]) -> str:
    fields = (
        contact.get("location"),
        contact.get("email"),
        contact.get("phone"),
        contact.get("github"),
        contact.get("linkedin"),
        contact.get("portfolio"),
    )
    return " | ".join(escape(str(value)) for value in fields if str(value or "").strip())


def _native_section_html(label: str, document: dict[str, Any]) -> str:
    if label == "Summary":
        summary = document.get("summary") or {}
        text = str(summary.get("text") or "")
        return f'<section class="section"><h2>Professional Summary</h2><p class="summary">{escape(text)}</p></section>'

    if label == "Experience":
        rows: list[str] = []
        for item in document.get("experience") or []:
            rows.append('<div class="entry">')
            rows.append(
                '<div class="entry-head">'
                f'<span class="entry-title">{escape(str(item.get("title") or ""))}</span>'
                f'<span>{escape(str(item.get("dates") or ""))}</span></div>'
            )
            subtitle = " | ".join(
                escape(str(value)) for value in (item.get("organization"), item.get("location")) if str(value or "").strip()
            )
            if subtitle:
                rows.append(f'<div class="entry-sub">{subtitle}</div>')
            bullets = item.get("bullets") or []
            if bullets:
                rows.append('<ul>')
                rows.extend(f'<li>{escape(str((bullet or {}).get("text") or ""))}</li>' for bullet in bullets)
                rows.append('</ul>')
            rows.append('</div>')
        return f'<section class="section"><h2>Work Experience</h2>{"".join(rows)}</section>'

    if label == "Education":
        rows = []
        for item in document.get("education") or []:
            rows.append('<div class="entry">')
            rows.append(
                '<div class="entry-head">'
                f'<span class="entry-title">{escape(str(item.get("institution") or ""))}</span>'
                f'<span>{escape(str(item.get("dates") or ""))}</span></div>'
            )
            subtitle = " | ".join(
                escape(str(value)) for value in (item.get("degree"), item.get("location"), item.get("gpa")) if str(value or "").strip()
            )
            if subtitle:
                rows.append(f'<div class="entry-sub">{subtitle}</div>')
            rows.append('</div>')
        return f'<section class="section"><h2>Education</h2>{"".join(rows)}</section>'

    if label == "Skills":
        rows = []
        for group in document.get("skills") or []:
            values = ", ".join(str(value) for value in group.get("skills") or [] if str(value).strip())
            rows.append(f'<p><strong>{escape(str(group.get("label") or "Skills"))}:</strong> {escape(values)}</p>')
        return f'<section class="section skills"><h2>Skills</h2>{"".join(rows)}</section>'

    if label == "Projects":
        rows = []
        for item in document.get("projects") or []:
            title = escape(str(item.get("name") or "Project"))
            subtitle = str(item.get("subtitle") or "").strip()
            if subtitle:
                title += f' | {escape(subtitle)}'
            rows.append(f'<div class="entry"><div class="entry-title">{title}</div>')
            bullets = item.get("bullets") or []
            if bullets:
                rows.append('<ul>')
                rows.extend(f'<li>{escape(str((bullet or {}).get("text") or ""))}</li>' for bullet in bullets)
                rows.append('</ul>')
            rows.append('</div>')
        return f'<section class="section"><h2>Projects</h2>{"".join(rows)}</section>'

    if label == "Certifications":
        values = []
        for item in document.get("certifications") or []:
            value = str(item.get("name") or "").strip()
            issuer = str(item.get("issuer") or "").strip()
            if issuer:
                value += f" - {issuer}"
            if value:
                values.append(escape(value))
        return f'<section class="section certs"><h2>Certifications & Achievements</h2><p>{" | ".join(values)}</p></section>'

    return ""


def native_document_html(document: dict[str, Any], preferences: dict[str, Any]) -> str:
    available = _present_sections(document)
    visible = set(preferences.get("visible_sections") or available)
    ordered = [label for label in _section_order(str(preferences.get("order") or "Experience first")) if label in available and label in visible]

    body = [f'<h1 class="name">{escape(str(document.get("candidate_name") or "Candidate"))}</h1>']
    contact = _contact_line(dict(document.get("contact") or {}))
    if contact:
        body.append(f'<div class="contact">{contact}</div>')
    body.extend(_native_section_html(label, document) for label in ordered)
    return _html_page("".join(body), preferences, title=f"{document.get('candidate_name') or 'Candidate'} Resume")


def _render_master_source_panels(source: dict[str, Any]) -> None:
    """Replace only Master Resume Preview; preserve edit/replace behavior unchanged."""
    if st.session_state.get("master_resume_preview_open"):
        with st.container(border=True):
            st.markdown("#### Master Resume visual preview")
            st.caption(
                "A faithful formatted view of the authoritative normalized source. "
                "MUNSHI does not rewrite source content in this preview."
            )
            preferences = _toolbar("master_resume_visual")
            components.html(
                master_source_html(source, preferences),
                height=1140,
                scrolling=True,
            )
            with st.expander("View raw normalized source", expanded=False):
                st.code(str(source.get("content_text") or ""), language="text")

    # Let the proven V1 component handle source editing and replacement, while
    # temporarily suppressing its old disabled-textarea preview.
    if _ORIGINAL_MASTER_PANELS is None:
        return
    old_preview = bool(st.session_state.get("master_resume_preview_open"))
    st.session_state["master_resume_preview_open"] = False
    try:
        _ORIGINAL_MASTER_PANELS(source)
    finally:
        st.session_state["master_resume_preview_open"] = old_preview


def _native_version_workspace(job_id: int, context: dict[str, Any], writer_ready: bool) -> None:
    versions = [
        row
        for row in resume_v5.list_versions(job_id=job_id, limit=100)
        if row.get("stage_b_bound") is True
    ]
    if not versions:
        return

    st.divider()
    st.markdown("### Resume preview & design")
    st.caption(
        "Review the exact Native Resume content in a familiar page canvas. "
        "Design controls are preview-only in V6; AutoApply continues using the verified ATS Standard artifact."
    )

    ids = [str(row["version_id"]) for row in versions]
    selected = st.session_state.get("native_resume_selected_version")
    if selected not in ids:
        st.session_state["native_resume_selected_version"] = ids[0]

    by_id = {str(row["version_id"]): row for row in versions}
    selector_col, status_col = st.columns((2.6, 1.1), gap="medium")
    with selector_col:
        selected_id = st.selectbox(
            "Version",
            ids,
            format_func=lambda value: (
                f"v{by_id[value]['version_number']} · "
                f"{studio_v4.STYLE_LABELS.get((by_id[value].get('diagnostics') or {}).get('rewrite_mode'), 'Tailored')}"
            ),
            key="native_resume_selected_version",
        )
    record = resume_v5.get_version(selected_id)
    diagnostics = record.get("diagnostics") or {}
    artifact_rows, quality = studio_v4._artifact_bundle(selected_id)
    with status_col:
        if quality.get("status") == "PASS":
            st.success("Verified one-page artifact")
        else:
            st.warning("Artifact needs page check")

    document = dict(record.get("document") or {})
    preferences = _toolbar(
        f"native_resume_visual_{selected_id}",
        available_sections=_present_sections(document),
    )

    metrics = st.columns(4, gap="small")
    metrics[0].metric("Version", f"v{record['version_number']}")
    metrics[1].metric(
        "Truth check",
        str((diagnostics.get("truth_audit") or {}).get("status") or "PASS"),
    )
    metrics[2].metric(
        "JD coverage",
        f"{float(diagnostics.get('jd_term_coverage_percent') or 0):.0f}%",
    )
    metrics[3].metric(
        "Verified page fit",
        (
            f"{float(quality.get('occupied_height_ratio') or 0) * 100:.0f}%"
            if quality.get("status") == "PASS"
            else "Needs check"
        ),
    )

    components.html(
        native_document_html(document, preferences),
        height=1140,
        scrolling=True,
    )

    if quality.get("status") == "PASS":
        st.success(
            "Verified ATS artifact: 1 page · truth/evidence guards passed. "
            "The canvas above may use different preview styling, but its content is the same immutable resume version."
        )
    elif quality:
        st.warning(str(quality.get("error") or "Page fit needs to be rebuilt."))

    studio_v4._downloads(selected_id, artifact_rows)

    with st.expander("Refine this resume", expanded=False):
        prior = diagnostics.get("rewrite_mode")
        default = prior if prior in studio_v4.STYLE_LABELS else "medium"
        mode = studio_v4._style_selector(
            f"resume_studio_v6_revision_mode_{selected_id}",
            default=default,
        )
        instruction = st.text_area(
            "What should MUNSHI improve?",
            placeholder=(
                "Example: shorten the summary, move Power BI earlier, "
                "and reduce recruiting emphasis."
            ),
            height=90,
            key=f"resume_studio_v6_revision_instruction_{selected_id}",
        )
        if st.button(
            "Create revised version",
            type="primary",
            use_container_width=True,
            key=f"resume_studio_v6_revision_{selected_id}",
            disabled=not (writer_ready and instruction.strip()),
        ):
            try:
                with st.spinner(
                    "Creating a new immutable version and re-running all checks…"
                ):
                    revised, bundle = studio_v4._generate_checked(
                        job_id=job_id,
                        rewrite_mode=mode,
                        instruction=instruction.strip(),
                        parent_version_id=selected_id,
                    )
            except Exception as error:  # noqa: BLE001
                st.error(str(error))
            else:
                version_id = str(revised["version_id"])
                st.session_state["native_resume_selected_version"] = version_id
                st.session_state[
                    f"native_resume_page_verification_{version_id}"
                ] = bundle["page_verification"]
                st.rerun()

    with st.expander("Advanced diagnostics", expanded=False):
        st.write(
            {
                "ats_readiness": diagnostics.get("ats_readiness_estimate"),
                "matched_jd_terms": diagnostics.get("matched_jd_terms") or [],
                "missing_jd_terms": diagnostics.get("missing_jd_terms") or [],
                "writer_model": diagnostics.get("writer_model"),
                "writer_reasoning": diagnostics.get("writer_reasoning_effort"),
                "writer_calls": diagnostics.get("writer_api_calls"),
            }
        )


def install_resume_visual_studio_v6() -> None:
    """Install rerun-safe presentation overrides for Profile and Resume Studio."""
    global _ORIGINAL_MASTER_PANELS, _ORIGINAL_VERSION_WORKSPACE

    if getattr(studio_v4, "_resume_visual_studio_v6_installed", False):
        return

    _ORIGINAL_MASTER_PANELS = master_ui._render_source_panels
    _ORIGINAL_VERSION_WORKSPACE = studio_v4._version_workspace

    master_ui._render_source_panels = _render_master_source_panels
    studio_v4._version_workspace = _native_version_workspace
    studio_v4._resume_visual_studio_v6_installed = True
