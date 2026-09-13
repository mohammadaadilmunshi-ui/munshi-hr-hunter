"""Presentation-only resume canvas for Master Resume and Native Resume Studio.

This layer deliberately does not change Candidate Truth, job binding, ATS checks,
artifact authority, or submission authority. It turns existing structured resume
or authoritative normalized source content into one professional paper system.
"""
from __future__ import annotations

import html
import re
from typing import Any, Mapping

from app import native_resume_studio as resume_contract

TEMPLATES = ("Standard", "Jake")
FONTS = ("Arial", "Computer Modern", "Georgia")
ALIGNMENTS = ("Left", "Justified")
FONT_SIZES = (9.5, 10.0, 10.5, 11.0)

_FONT_STACKS = {
    "Arial": "Arial, Helvetica, sans-serif",
    "Computer Modern": "'Computer Modern Serif', 'Latin Modern Roman', 'STIX Two Text', 'Times New Roman', serif",
    "Georgia": "Georgia, 'Times New Roman', serif",
}
_SECTION_NAMES = {
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
_DATE_TAIL = re.compile(
    r"^(?P<left>.*?)(?:\s{2,}|\s+\|\s+)?(?P<date>(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+)?(?:19|20)\d{2}(?:\s*(?:-|–|—|to)\s*(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+)?(?:19|20)\d{2}|Present|Current)?)$",
    re.IGNORECASE,
)


def normalized_settings(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(settings or {})
    template = str(raw.get("template") or "Standard")
    if template not in TEMPLATES:
        template = "Standard"
    default_font = "Computer Modern" if template == "Jake" else "Arial"
    font = str(raw.get("font") or default_font)
    alignment = str(raw.get("alignment") or "Left")
    try:
        size = float(raw.get("font_size") or 10.5)
    except (TypeError, ValueError):
        size = 10.5
    if font not in FONTS:
        font = default_font
    if alignment not in ALIGNMENTS:
        alignment = "Left"
    if size not in FONT_SIZES:
        size = 10.5
    return {
        "template": template,
        "font": font,
        "font_size": size,
        "alignment": alignment,
        "fit_one_page": bool(raw.get("fit_one_page", True)),
    }


def _style_tokens(settings: Mapping[str, Any]) -> dict[str, str | float]:
    value = normalized_settings(settings)
    body_font = _FONT_STACKS[value["font"]]
    if value["template"] == "Jake":
        return {
            "body_font": body_font,
            "heading_font": _FONT_STACKS["Computer Modern"],
            "page_padding": ".43in .52in .45in",
            "name_size": 23.5,
            "name_weight": "600",
            "heading_size": 10.8,
            "heading_weight": "650",
            "heading_letter": ".045em",
            "rule_width": "1px",
            "rule_color": "#2d3431",
            "section_margin": "8px",
            "body_line": "1.17",
            "entry_gap": "4px",
        }
    return {
        "body_font": body_font,
        "heading_font": body_font,
        "page_padding": ".46in .56in .48in",
        "name_size": 24.5,
        "name_weight": "800",
        "heading_size": 11.0,
        "heading_weight": "800",
        "heading_letter": ".03em",
        "rule_width": "1.35px",
        "rule_color": "#26312d",
        "section_margin": "9px",
        "body_line": "1.18",
        "entry_gap": "5px",
    }


def _override_css(settings: Mapping[str, Any]) -> str:
    value = normalized_settings(settings)
    tokens = _style_tokens(value)
    justify = "justify" if value["alignment"] == "Justified" else "left"
    size = float(value["font_size"])
    return f"""
<style id="munshi-presentation-v1">
html, body {{ background:#f3f6f4!important; margin:0!important; }}
.resume {{
  width:8.5in!important; min-height:11in!important; box-sizing:border-box!important;
  margin:24px auto 34px!important; padding:{tokens['page_padding']}!important;
  background:#fff!important; color:#111715!important;
  font-family:{tokens['body_font']}!important; font-size:{size}pt!important;
  line-height:{tokens['body_line']}!important;
  box-shadow:0 14px 42px rgba(20,40,34,.11),0 2px 8px rgba(20,40,34,.05)!important;
}}
.resume h1 {{
  font-family:{tokens['heading_font']}!important;
  font-size:{tokens['name_size']}pt!important;
  font-weight:{tokens['name_weight']}!important;
  line-height:1.04!important; letter-spacing:-.01em!important;
}}
.resume .contact {{ text-align:center!important; line-height:1.32!important; color:#27302d!important; }}
.resume .section {{ margin-top:{tokens['section_margin']}!important; }}
.resume .section-heading {{
  font-family:{tokens['heading_font']}!important;
  font-size:{tokens['heading_size']}pt!important;
  font-weight:{tokens['heading_weight']}!important;
  letter-spacing:{tokens['heading_letter']}!important;
  border-bottom:{tokens['rule_width']} solid {tokens['rule_color']}!important;
  padding-bottom:3px!important; margin-bottom:4px!important;
}}
.resume p, .resume li, .resume .entry-sub {{ text-align:{justify}!important; line-height:{tokens['body_line']}!important; }}
.resume .entry {{ margin-bottom:{tokens['entry_gap']}!important; break-inside:avoid!important; }}
@media (max-width:900px) {{
  .resume {{ width:100%!important; min-height:auto!important; margin:0!important; box-shadow:none!important; }}
}}
</style>
"""


def render_document_html(
    document: resume_contract.ResumeDocument | Mapping[str, Any],
    settings: Mapping[str, Any] | None = None,
) -> str:
    value = (
        document
        if isinstance(document, resume_contract.ResumeDocument)
        else resume_contract.ResumeDocument.model_validate(document)
    )
    base = resume_contract.render_ats_html(value)
    css = _override_css(normalized_settings(settings))
    if "</head>" in base:
        return base.replace("</head>", css + "</head>", 1)
    return css + base


def _normalized_heading_key(line: str) -> str:
    raw = str(line or "").strip()
    normalized = re.sub(r"[^a-z0-9& ]+", "", raw.casefold()).strip()
    return re.sub(r"\s+", " ", normalized)


def _section_title(line: str) -> str | None:
    raw = str(line or "").strip()
    normalized = _normalized_heading_key(raw)
    if normalized in _SECTION_NAMES:
        return _SECTION_NAMES[normalized]
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


def _source_line_class(line: str) -> str:
    text = line.strip()
    if not text:
        return "blank"
    if text.startswith(("•", "- ", "* ")):
        return "bullet"
    if _section_title(text):
        return "heading"
    return "line"


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


def _parse_source(source_text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    lines = [
        line.strip()
        for line in str(source_text or "").replace("\r\n", "\n").split("\n")
        if line.strip()
    ]
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for index, line in enumerate(lines):
        title = _section_title(line)
        # Candidate names are frequently exported in all caps. The first source
        # line is header identity unless it is a known canonical section label.
        if index == 0 and _normalized_heading_key(line) not in _SECTION_NAMES:
            title = None
        if title:
            if current_title is not None:
                sections.append((current_title, current_lines))
            elif current_lines:
                preamble.extend(current_lines)
            current_title = title
            current_lines = []
        else:
            current_lines.append(line)

    if current_title is not None:
        sections.append((current_title, current_lines))
    elif current_lines:
        preamble.extend(current_lines)

    if not sections and len(preamble) > 3:
        head = preamble[:3]
        rest = preamble[3:]
        preamble = head
        sections = [("Resume", rest)]
    return preamble, sections


def _source_section_html(title: str, lines: list[str]) -> str:
    rendered: list[str] = []
    bullets: list[str] = []

    def flush_bullets() -> None:
        if not bullets:
            return
        rendered.append(
            '<ul class="source-bullets">'
            + "".join(
                f'<li class="source-bullet">{html.escape(item)}</li>'
                for item in bullets
            )
            + "</ul>"
        )
        bullets.clear()

    for raw in lines:
        text = str(raw or "").strip()
        if not text:
            continue
        if text.startswith(("•", "- ", "* ")):
            bullets.append(re.sub(r"^[•*-]\s*", "", text))
            continue

        flush_bullets()
        left, date = _split_date_tail(text)
        if date:
            rendered.append(
                '<div class="source-entry">'
                '<div class="source-entry-head">'
                f'<span class="source-entry-title">{html.escape(left)}</span>'
                f'<span class="source-date">{html.escape(date)}</span>'
                "</div></div>"
            )
        elif len(text) <= 118 and " | " in text and not text.endswith((".", ";")):
            rendered.append(f'<div class="source-subhead">{html.escape(text)}</div>')
        else:
            rendered.append(f'<div class="source-line">{html.escape(text)}</div>')

    flush_bullets()
    return (
        '<section class="source-section">'
        f'<div class="source-heading">{html.escape(title.upper())}</div>'
        + "".join(rendered)
        + "</section>"
    )


def render_master_source_html(
    source_text: str,
    settings: Mapping[str, Any] | None = None,
) -> str:
    """Render normalized Master Resume source in the same professional page grammar.

    This is presentation only: every source string remains source-derived, and the
    legacy source table still does not claim original-file byte retention.
    """
    cfg = normalized_settings(settings)
    tokens = _style_tokens(cfg)
    justify = "justify" if cfg["alignment"] == "Justified" else "left"
    preamble, sections = _parse_source(source_text)

    name = preamble[0] if preamble else "Master Resume"
    contact = " | ".join(item.strip(" |") for item in preamble[1:] if item.strip())
    body = [f'<h1 class="source-name">{html.escape(name)}</h1>']
    if contact:
        body.append(f'<div class="source-contact">{html.escape(contact)}</div>')
    body.extend(_source_section_html(title, lines) for title, lines in sections)

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}
html,body{{margin:0;background:#f3f6f4;color:#111715}}
.paper{{
 width:8.5in;min-height:11in;box-sizing:border-box;margin:24px auto 34px;
 padding:{tokens['page_padding']};background:#fff;
 box-shadow:0 14px 42px rgba(20,40,34,.11),0 2px 8px rgba(20,40,34,.05);
 font-family:{tokens['body_font']};font-size:{cfg['font_size']}pt;line-height:{tokens['body_line']}
}}
.source-name{{margin:0;text-align:center;font-family:{tokens['heading_font']};font-size:{tokens['name_size']}pt;font-weight:{tokens['name_weight']};line-height:1.04;letter-spacing:-.01em}}
.source-contact{{text-align:center;margin:6px auto 0;max-width:94%;font-size:8.8pt;line-height:1.32;color:#27302d}}
.source-section{{margin-top:{tokens['section_margin']}}}
.source-heading{{font-family:{tokens['heading_font']};font-weight:{tokens['heading_weight']};font-size:{tokens['heading_size']}pt;letter-spacing:{tokens['heading_letter']};border-bottom:{tokens['rule_width']} solid {tokens['rule_color']};margin:0 0 4px;padding-bottom:3px}}
.source-line{{text-align:{justify};margin:1px 0;line-height:{tokens['body_line']}}}
.source-bullets{{margin:2px 0 0 17px;padding:0}}
.source-bullets li{{margin:0 0 2px;text-align:{justify};line-height:{tokens['body_line']}}}
.source-entry{{margin:0 0 {tokens['entry_gap']};break-inside:avoid}}
.source-entry-head{{display:flex;justify-content:space-between;gap:18px;align-items:baseline}}
.source-entry-title,.source-subhead{{font-weight:700}}
.source-date{{white-space:nowrap;font-size:calc({cfg['font_size']}pt - .15pt)}}
.source-subhead{{margin:2px 0 1px}}
@media(max-width:900px){{.paper{{width:100%;min-height:auto;margin:0;box-shadow:none}}}}
</style></head><body><main class="paper">{''.join(body)}</main></body></html>"""
