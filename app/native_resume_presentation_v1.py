"""Presentation-only resume canvas for Master Resume and Native Resume Studio.

This layer deliberately does not change Candidate Truth, job binding, ATS checks,
or submission authority. It turns the existing structured resume/source content
into a polished paper preview with a small ATS-safe presentation vocabulary.
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
    "Computer Modern": "'Computer Modern', 'Latin Modern Roman', Georgia, serif",
    "Georgia": "Georgia, 'Times New Roman', serif",
}


def normalized_settings(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(settings or {})
    template = str(raw.get("template") or "Standard")
    font = str(raw.get("font") or "Arial")
    alignment = str(raw.get("alignment") or "Left")
    try:
        size = float(raw.get("font_size") or 10.5)
    except (TypeError, ValueError):
        size = 10.5
    if template not in TEMPLATES:
        template = "Standard"
    if font not in FONTS:
        font = "Arial"
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


def _override_css(settings: Mapping[str, Any]) -> str:
    value = normalized_settings(settings)
    font_stack = _FONT_STACKS[value["font"]]
    justify = "justify" if value["alignment"] == "Justified" else "left"
    template = value["template"]
    size = float(value["font_size"])
    if template == "Jake":
        heading_family = _FONT_STACKS["Computer Modern"]
        heading_weight = 600
        rule = "#394047"
        name_size = 20.5
        heading_size = 10.6
        letter_spacing = ".02em"
    else:
        heading_family = font_stack
        heading_weight = 700
        rule = "#283138"
        name_size = 21.5
        heading_size = 10.8
        letter_spacing = ".01em"
    return f"""
<style id="munshi-presentation-v1">
html, body {{ background:#f4f6f5!important; margin:0!important; }}
.resume {{
  width:8.5in!important; min-height:11in!important; box-sizing:border-box!important;
  margin:20px auto 28px!important; padding:.42in .48in!important;
  background:#fff!important; color:#111!important;
  font-family:{font_stack}!important; font-size:{size}pt!important;
  box-shadow:0 8px 28px rgba(22,35,42,.11)!important;
}}
.resume h1 {{ font-family:{heading_family}!important; font-size:{name_size}pt!important; font-weight:{heading_weight}!important; }}
.resume .section-heading {{
  font-family:{heading_family}!important; font-size:{heading_size}pt!important;
  font-weight:{heading_weight}!important; letter-spacing:{letter_spacing}!important;
  border-bottom:1px solid {rule}!important;
}}
.resume p, .resume li, .resume .entry-sub {{ text-align:{justify}!important; }}
.resume .contact {{ text-align:center!important; }}
@media (max-width:900px) {{ .resume {{ width:100%!important; min-height:auto!important; margin:0!important; box-shadow:none!important; }} }}
</style>
"""


def render_document_html(document: resume_contract.ResumeDocument | Mapping[str, Any], settings: Mapping[str, Any] | None = None) -> str:
    value = document if isinstance(document, resume_contract.ResumeDocument) else resume_contract.ResumeDocument.model_validate(document)
    base = resume_contract.render_ats_html(value)
    css = _override_css(normalized_settings(settings))
    if "</head>" in base:
        return base.replace("</head>", css + "</head>", 1)
    return css + base


def _source_line_class(line: str) -> str:
    text = line.strip()
    if not text:
        return "blank"
    if text.startswith(("•", "- ", "* ")):
        return "bullet"
    if len(text) <= 42 and re.fullmatch(r"[A-Z][A-Z &/+.-]+", text):
        return "heading"
    if re.match(r"^(PROFESSIONAL SUMMARY|SUMMARY|EDUCATION|SKILLS|WORK EXPERIENCE|EXPERIENCE|PROJECTS|CERTIFICATIONS|ACHIEVEMENTS)$", text, re.I):
        return "heading"
    return "line"


def render_master_source_html(source_text: str, settings: Mapping[str, Any] | None = None) -> str:
    """Render legacy normalized Master Resume text as an honest visual paper preview.

    The legacy source table does not retain original PDF/DOCX bytes, so this does
    not claim pixel-perfect reconstruction. It preserves source text while making
    headings/bullets readable inside the same paper canvas used by Native resumes.
    """
    cfg = normalized_settings(settings)
    font_stack = _FONT_STACKS[cfg["font"]]
    justify = "justify" if cfg["alignment"] == "Justified" else "left"
    heading_family = _FONT_STACKS["Computer Modern"] if cfg["template"] == "Jake" else font_stack
    body: list[str] = []
    for raw in str(source_text or "").replace("\r\n", "\n").split("\n"):
        text = raw.strip()
        cls = _source_line_class(raw)
        if cls == "blank":
            body.append('<div class="source-gap"></div>')
        elif cls == "heading":
            body.append(f'<div class="source-heading">{html.escape(text.upper())}</div>')
        elif cls == "bullet":
            clean = re.sub(r"^[•*-]\s*", "", text)
            body.append(f'<div class="source-bullet"><span>•</span><div>{html.escape(clean)}</div></div>')
        else:
            body.append(f'<div class="source-line">{html.escape(text)}</div>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;background:#f4f6f5;color:#111}}
.paper{{width:8.5in;min-height:11in;box-sizing:border-box;margin:18px auto 28px;padding:.42in .48in;background:#fff;box-shadow:0 8px 28px rgba(22,35,42,.11);font-family:{font_stack};font-size:{cfg['font_size']}pt;line-height:1.16}}
.source-heading{{font-family:{heading_family};font-weight:700;font-size:10.8pt;border-bottom:1px solid #283138;margin:7px 0 4px;padding-bottom:2px}}
.source-line{{text-align:{justify};margin:1px 0}}
.source-bullet{{display:grid;grid-template-columns:14px 1fr;gap:2px;margin:1px 0;text-align:{justify}}}
.source-gap{{height:5px}}
@media(max-width:900px){{.paper{{width:100%;min-height:auto;margin:0;box-shadow:none}}}}
</style></head><body><main class="paper">{''.join(body)}</main></body></html>"""
