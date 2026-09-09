"""Evidence-backed native resume document contract and ATS-safe renderer.

This module is deliberately additive.  It does not call a model, replace n8n,
write candidate artifacts, or claim native resume authority.  It defines the
structured document boundary that a future GPT writer must satisfy before a
resume can be persisted or rendered by MUNSHI.
"""
from __future__ import annotations

from html import escape
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TEMPLATE_VERSION = "ats-single-column-v1"
_MAX_TEXT = 4_000
_MAX_BULLET_WORDS = 42
_MAX_SUMMARY_WORDS = 90
_MAX_DOCUMENT_WORDS = 900


class EvidenceText(BaseModel):
    """Resume text that must trace to one or more candidate evidence records."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=_MAX_TEXT)
    evidence_ids: list[str] = Field(min_length=1, max_length=32)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        text = " ".join(str(value or "").split())
        if not text:
            raise ValueError("Resume text is required.")
        if "—" in text:
            raise ValueError("ATS resume text must not contain em dashes.")
        return text

    @field_validator("evidence_ids")
    @classmethod
    def clean_evidence_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            item = str(raw or "").strip()
            if not item:
                raise ValueError("Evidence IDs must not be blank.")
            if item not in cleaned:
                cleaned.append(item)
        if not cleaned:
            raise ValueError("At least one evidence ID is required.")
        return cleaned


class ContactInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""

    @field_validator("location", "email", "phone", "linkedin", "github", "portfolio")
    @classmethod
    def clean_contact(cls, value: str) -> str:
        return " ".join(str(value or "").split())[:500]


class EducationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    institution: str = Field(min_length=1, max_length=240)
    degree: str = Field(min_length=1, max_length=320)
    dates: str = Field(default="", max_length=120)
    location: str = Field(default="", max_length=160)
    gpa: str = Field(default="", max_length=80)
    evidence_ids: list[str] = Field(min_length=1, max_length=16)

    @field_validator("institution", "degree", "dates", "location", "gpa")
    @classmethod
    def clean_text(cls, value: str) -> str:
        text = " ".join(str(value or "").split())
        if "—" in text:
            raise ValueError("ATS resume text must not contain em dashes.")
        return text

    @field_validator("evidence_ids")
    @classmethod
    def clean_evidence_ids(cls, value: list[str]) -> list[str]:
        return EvidenceText.clean_evidence_ids(value)


class SkillGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    skills: list[str] = Field(min_length=1, max_length=40)
    evidence_ids: list[str] = Field(min_length=1, max_length=32)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        text = " ".join(str(value or "").split())
        if "—" in text:
            raise ValueError("ATS resume text must not contain em dashes.")
        return text

    @field_validator("skills")
    @classmethod
    def clean_skills(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            item = " ".join(str(raw or "").split())
            if not item:
                raise ValueError("Skill values must not be blank.")
            if "—" in item:
                raise ValueError("ATS resume text must not contain em dashes.")
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                cleaned.append(item)
        return cleaned

    @field_validator("evidence_ids")
    @classmethod
    def clean_evidence_ids(cls, value: list[str]) -> list[str]:
        return EvidenceText.clean_evidence_ids(value)


class ExperienceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=240)
    dates: str = Field(default="", max_length=120)
    location: str = Field(default="", max_length=160)
    bullets: list[EvidenceText] = Field(min_length=1, max_length=8)

    @field_validator("organization", "title", "dates", "location")
    @classmethod
    def clean_text(cls, value: str) -> str:
        text = " ".join(str(value or "").split())
        if "—" in text:
            raise ValueError("ATS resume text must not contain em dashes.")
        return text


class ProjectItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    subtitle: str = Field(default="", max_length=240)
    bullets: list[EvidenceText] = Field(min_length=1, max_length=6)

    @field_validator("name", "subtitle")
    @classmethod
    def clean_text(cls, value: str) -> str:
        text = " ".join(str(value or "").split())
        if "—" in text:
            raise ValueError("ATS resume text must not contain em dashes.")
        return text


class CertificationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)
    issuer: str = Field(default="", max_length=200)
    evidence_ids: list[str] = Field(min_length=1, max_length=16)

    @field_validator("name", "issuer")
    @classmethod
    def clean_text(cls, value: str) -> str:
        text = " ".join(str(value or "").split())
        if "—" in text:
            raise ValueError("ATS resume text must not contain em dashes.")
        return text

    @field_validator("evidence_ids")
    @classmethod
    def clean_evidence_ids(cls, value: list[str]) -> list[str]:
        return EvidenceText.clean_evidence_ids(value)


class ResumeDocument(BaseModel):
    """Canonical V1 resume content returned by the future native writer."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["native-resume-v1"] = "native-resume-v1"
    template_version: Literal["ats-single-column-v1"] = TEMPLATE_VERSION
    candidate_name: str = Field(min_length=1, max_length=240)
    contact: ContactInfo
    summary: EvidenceText
    education: list[EducationItem] = Field(default_factory=list, max_length=8)
    skills: list[SkillGroup] = Field(default_factory=list, max_length=12)
    experience: list[ExperienceItem] = Field(default_factory=list, max_length=12)
    projects: list[ProjectItem] = Field(default_factory=list, max_length=12)
    certifications: list[CertificationItem] = Field(default_factory=list, max_length=24)

    @field_validator("candidate_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        text = " ".join(str(value or "").split())
        if "—" in text:
            raise ValueError("ATS resume text must not contain em dashes.")
        return text

    @model_validator(mode="after")
    def require_content(self) -> "ResumeDocument":
        if not (self.education or self.skills or self.experience or self.projects or self.certifications):
            raise ValueError("Resume must contain at least one substantive section.")
        return self


def _words(value: str) -> int:
    return len([part for part in str(value or "").split() if part])


def document_word_count(document: ResumeDocument) -> int:
    values: list[str] = [document.candidate_name, document.summary.text]
    values.extend(filter(None, document.contact.model_dump().values()))
    for item in document.education:
        values.extend([item.institution, item.degree, item.dates, item.location, item.gpa])
    for group in document.skills:
        values.extend([group.label, *group.skills])
    for item in document.experience:
        values.extend([item.organization, item.title, item.dates, item.location])
        values.extend(bullet.text for bullet in item.bullets)
    for item in document.projects:
        values.extend([item.name, item.subtitle])
        values.extend(bullet.text for bullet in item.bullets)
    for item in document.certifications:
        values.extend([item.name, item.issuer])
    return sum(_words(value) for value in values)


def ats_readiness_issues(document: ResumeDocument) -> list[str]:
    """Return deterministic V1 content-budget issues without inventing an ATS score."""
    issues: list[str] = []
    if _words(document.summary.text) > _MAX_SUMMARY_WORDS:
        issues.append(f"summary_over_{_MAX_SUMMARY_WORDS}_words")
    for section, items in (("experience", document.experience), ("projects", document.projects)):
        for item_index, item in enumerate(items):
            for bullet_index, bullet in enumerate(item.bullets):
                if _words(bullet.text) > _MAX_BULLET_WORDS:
                    issues.append(f"{section}_{item_index}_bullet_{bullet_index}_over_{_MAX_BULLET_WORDS}_words")
    if document_word_count(document) > _MAX_DOCUMENT_WORDS:
        issues.append(f"document_over_{_MAX_DOCUMENT_WORDS}_words")
    return issues


def evidence_ids(document: ResumeDocument) -> set[str]:
    """Collect all evidence references used by the resume without rendering them."""
    result: set[str] = set(document.summary.evidence_ids)
    for item in document.education:
        result.update(item.evidence_ids)
    for group in document.skills:
        result.update(group.evidence_ids)
    for item in document.experience:
        for bullet in item.bullets:
            result.update(bullet.evidence_ids)
    for item in document.projects:
        for bullet in item.bullets:
            result.update(bullet.evidence_ids)
    for item in document.certifications:
        result.update(item.evidence_ids)
    return result


def _contact_line(contact: ContactInfo) -> str:
    parts = [contact.location, contact.email, contact.phone, contact.github, contact.linkedin, contact.portfolio]
    return " | ".join(escape(part) for part in parts if part)


def _section_heading(label: str) -> str:
    return f'<h2 class="section-heading">{escape(label.upper())}</h2>'


def render_ats_html(document: ResumeDocument) -> str:
    """Render a polished, single-column, ATS-safe one-page resume."""

    word_count = document_word_count(document)
    if word_count < 360:
        body_font, line_height, section_gap, bullet_gap = 10.9, 1.24, 9, 3.0
    elif word_count < 500:
        body_font, line_height, section_gap, bullet_gap = 10.6, 1.19, 8, 2.5
    elif word_count < 650:
        body_font, line_height, section_gap, bullet_gap = 10.3, 1.15, 7, 2.0
    else:
        body_font, line_height, section_gap, bullet_gap = 9.9, 1.11, 6, 1.4

    blocks: list[str] = [
        '<main class="resume">',
        '<header class="resume-header">',
        f'<h1>{escape(document.candidate_name)}</h1>',
        f'<div class="contact">{_contact_line(document.contact)}</div>',
        '</header>',
        _section_heading("Professional Summary"),
        f'<p class="summary">{escape(document.summary.text)}</p>',
    ]

    if document.experience:
        blocks.append(_section_heading("Work Experience"))
        for item in document.experience:
            blocks.append('<section class="entry">')
            blocks.append(
                '<div class="entry-head">'
                f'<strong>{escape(item.title)}</strong>'
                f'<span>{escape(item.dates)}</span>'
                '</div>'
            )
            sub = escape(item.organization)
            if item.location:
                sub += f' | {escape(item.location)}'
            blocks.append(f'<div class="entry-sub">{sub}</div>')
            blocks.append('<ul>')
            blocks.extend(
                f'<li>{escape(bullet.text)}</li>' for bullet in item.bullets
            )
            blocks.append('</ul></section>')

    if document.education:
        blocks.append(_section_heading("Education"))
        for item in document.education:
            blocks.append('<section class="entry compact-entry">')
            blocks.append(
                '<div class="entry-head">'
                f'<strong>{escape(item.degree)}</strong>'
                f'<span>{escape(item.dates)}</span>'
                '</div>'
            )
            extras = " | ".join(
                escape(part)
                for part in (item.institution, item.location, item.gpa)
                if part
            )
            if extras:
                blocks.append(f'<div class="entry-sub">{extras}</div>')
            blocks.append('</section>')

    if document.projects:
        blocks.append(_section_heading("Projects"))
        for item in document.projects:
            title = escape(item.name)
            if item.subtitle:
                title += f' | {escape(item.subtitle)}'
            blocks.append('<section class="entry">')
            blocks.append(
                f'<div class="project-title"><strong>{title}</strong></div>'
            )
            blocks.append('<ul>')
            blocks.extend(
                f'<li>{escape(bullet.text)}</li>' for bullet in item.bullets
            )
            blocks.append('</ul></section>')

    if document.skills:
        blocks.append(_section_heading("Skills"))
        groups = []
        for group in document.skills:
            groups.append(
                f'<strong>{escape(group.label)}:</strong> '
                + escape(", ".join(group.skills))
            )
        blocks.append(
            '<p class="skill-summary">'
            + ' <span class="pipe">|</span> '.join(groups)
            + '</p>'
        )

    if document.certifications:
        blocks.append(_section_heading("Certifications & Achievements"))
        items = []
        for item in document.certifications:
            value = escape(item.name)
            if item.issuer:
                value += f' - {escape(item.issuer)}'
            items.append(value)
        blocks.append(
            '<p class="cert-line">'
            + ' <span class="pipe">|</span> '.join(items)
            + '</p>'
        )

    blocks.append('</main>')
    body = "\n".join(blocks)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(document.candidate_name)} Resume</title>
<style>
@page {{ size: Letter; margin: 0.40in 0.55in 0.42in; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: #fff;
  color: #17212b;
  font-family: Arial, Helvetica, sans-serif;
  font-size: {body_font}pt;
  line-height: {line_height};
}}
.resume {{ max-width: 8.5in; margin: 0 auto; }}
.resume-header {{ padding-bottom: 5px; border-bottom: 1px solid #a8b0b7; }}
h1 {{
  margin: 0 0 3px;
  text-align: center;
  font-size: 21.5pt;
  line-height: 1.02;
  letter-spacing: 0.01em;
}}
.contact {{
  text-align: center;
  font-size: 9.4pt;
  line-height: 1.12;
  overflow-wrap: anywhere;
}}
.section-heading {{
  margin: {section_gap}px 0 4px;
  padding-bottom: 2px;
  border-bottom: 1.2px solid #2e3943;
  font-size: 10.8pt;
  line-height: 1.05;
  letter-spacing: 0.075em;
}}
p {{ margin: 0 0 4px; }}
.summary {{ margin-bottom: 3px; }}
ul {{ margin: 2px 0 4px 17px; padding: 0; }}
li {{ margin: 0 0 {bullet_gap}px; padding-left: 1px; }}
.entry {{ page-break-inside: avoid; margin: 0 0 2px; }}
.entry-head {{
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: baseline;
  margin-top: 2px;
}}
.entry-head > :last-child {{ white-space: nowrap; font-size: 9.5pt; }}
.entry-sub {{ margin: 1px 0 2px; font-size: 9.8pt; }}
.compact-entry {{ margin-bottom: 3px; }}
.project-title {{ margin-top: 2px; }}
.skill-summary, .cert-line {{ margin: 0; line-height: 1.18; }}
.pipe {{ padding: 0 3px; color: #6a737b; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
