"""Deterministic profile extraction V2 with wrapped-line repair.

V1 established the no-model/no-network profile extraction contract.  V2 keeps
that authority and adds conservative cleanup for resume text whose PDF/DOCX
layout was flattened differently across runtimes.  The goal is parity: wrapped
bullet continuations must not become fake jobs, adjacent schools must remain
separate education records, and obvious section-heading variants should still be
recognized.

No model, API, network request, or candidate-private external lookup is used.
The output remains a draft and still requires explicit user confirmation before
it becomes permanent profile authority.
"""
from __future__ import annotations

import re
from typing import Any

from app import deterministic_profile_extractor_v1 as v1


_TERMINAL_PUNCTUATION = (".", ";", ":", ",")


def _canonical_heading(line: str) -> str | None:
    """Recognize conservative section-heading variants before V1 parsing."""
    cleaned = v1._clean_line(line).strip(":-–— ")
    if not cleaned or len(cleaned) > 72:
        return None
    direct = v1._heading_key(cleaned)
    if direct:
        return direct

    folded = cleaned.casefold()
    explicit_variants = {
        "projects & analytics": "projects",
        "projects and analytics": "projects",
        "analytics projects": "projects",
        "work history": "experience",
        "employment history": "experience",
        "skills & tools": "skills",
        "skills and tools": "skills",
        "licenses & certifications": "certifications",
        "licenses and certifications": "certifications",
    }
    if folded in explicit_variants:
        return explicit_variants[folded]

    # Fuzzy heading recovery is deliberately limited to visually heading-like
    # ALL-CAPS lines.  This prevents real titles such as "People Analytics &
    # Benefits Operations Projects" from being swallowed as section headers.
    if cleaned != cleaned.upper():
        return None

    words = set(re.findall(r"[a-z]+", folded))
    if "education" in words or "academics" in words:
        return "education"
    if "experience" in words:
        return "experience"
    if "project" in words or "projects" in words:
        return "projects"
    if "skill" in words or "skills" in words or "competencies" in words:
        return "skills"
    if "certification" in words or "certifications" in words or "certificates" in words:
        return "certifications"
    if "language" in words or "languages" in words:
        return "languages"
    if folded in {"summary", "professional summary", "career summary", "profile"}:
        return "summary"
    if "authorization" in words or "authorisation" in words:
        return "application_defaults"
    return None


def _looks_like_short_header(line: str) -> bool:
    value = v1._clean_line(line)
    if not value or v1._is_bullet(value):
        return False
    if v1._DATE_RANGE_RE.search(value) or v1._YEAR_RANGE_RE.search(value):
        return True
    if len(value) > 100 or value.endswith(_TERMINAL_PUNCTUATION):
        return False

    words = re.findall(r"[A-Za-z][A-Za-z0-9/&.'’+-]*", value)
    if not 1 <= len(words) <= 10:
        return False

    first = value.lstrip()[:1]
    if not first or not first.isupper():
        return False

    # Job-title vocabulary is a strong signal when the line is short and looks
    # like a label rather than prose.
    if v1._TITLE_RE.search(value):
        return True

    # Organization/project headers are commonly title-cased even when they do
    # not contain title vocabulary.
    capitalized = sum(1 for word in words if word[:1].isupper() or word.isupper())
    return capitalized >= max(1, (len(words) + 1) // 2)


def _looks_like_wrapped_continuation(line: str) -> bool:
    value = v1._clean_line(line)
    if not value:
        return False
    if value[:1].islower():
        return True
    if value.endswith(_TERMINAL_PUNCTUATION):
        return True
    if len(value) > 100:
        return True
    return not _looks_like_short_header(value)


def repair_resume_text(text: str) -> str:
    """Normalize layout-only damage while preserving candidate wording."""
    output: list[str] = []
    active: str | None = None
    education_school_seen = False
    last_bullet_index: int | None = None
    saw_bullet_in_record = False

    raw_lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for raw in raw_lines:
        line = v1._clean_line(raw)

        if not line:
            if output and output[-1] != "":
                output.append("")
            education_school_seen = False
            last_bullet_index = None
            saw_bullet_in_record = False
            continue

        heading = _canonical_heading(line)
        if heading:
            canonical = {
                "summary": "PROFESSIONAL SUMMARY",
                "education": "EDUCATION",
                "experience": "EXPERIENCE",
                "projects": "PROJECTS",
                "skills": "SKILLS",
                "certifications": "CERTIFICATIONS",
                "languages": "LANGUAGES",
                "application_defaults": "WORK AUTHORIZATION",
            }[heading]
            if output and output[-1] != "":
                output.append("")
            output.append(canonical)
            active = heading
            education_school_seen = False
            last_bullet_index = None
            saw_bullet_in_record = False
            continue

        if active == "education":
            is_school = bool(v1._SCHOOL_RE.search(line)) and not v1._is_bullet(line)
            if is_school and education_school_seen:
                if output and output[-1] != "":
                    output.append("")
            if is_school:
                education_school_seen = True
            output.append(line)
            continue

        if active in {"experience", "projects"}:
            if v1._is_bullet(line):
                output.append(line)
                last_bullet_index = len(output) - 1
                saw_bullet_in_record = True
                continue

            if last_bullet_index is not None and _looks_like_wrapped_continuation(line):
                output[last_bullet_index] = f"{output[last_bullet_index]} {line}".strip()
                continue

            # A clear short header after bullets indicates the next record even
            # when PDF extraction lost the blank line between jobs/projects.
            if saw_bullet_in_record and _looks_like_short_header(line):
                if output and output[-1] != "":
                    output.append("")
                saw_bullet_in_record = False

            output.append(line)
            last_bullet_index = None
            continue

        output.append(line)

    while output and output[-1] == "":
        output.pop()
    return "\n".join(output)


def _sentence_like(value: str) -> bool:
    text = v1._clean_line(value)
    if not text:
        return False
    return (
        text[:1].islower()
        or text.endswith(_TERMINAL_PUNCTUATION)
        or len(text) > 100
        or len(text.split()) > 12
    )


def _consolidate_experience(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach orphaned prose fragments to the preceding real job record."""
    output: list[dict[str, Any]] = []
    for raw in items or []:
        item = dict(raw)
        title = v1._clean_line(item.get("title") or "")
        employer = v1._clean_line(item.get("employer") or "")
        start = v1._clean_line(item.get("start_date") or "")
        end = v1._clean_line(item.get("end_date") or "")
        location = v1._clean_line(item.get("location") or "")
        bullets = [v1._clean_line(value) for value in item.get("bullets") or [] if v1._clean_line(value)]

        suspicious = False
        if not start and not end and not location:
            if title.casefold() == "role":
                suspicious = True
            elif title and employer == title and _sentence_like(title):
                suspicious = True
            elif not title and employer and _sentence_like(employer):
                suspicious = True
            elif title and not employer and _sentence_like(title):
                suspicious = True

        if suspicious and output:
            fragment = title or employer
            if fragment:
                output[-1].setdefault("bullets", []).append(fragment)
            output[-1].setdefault("bullets", []).extend(bullets)
            continue

        item["title"] = title
        item["employer"] = employer
        item["start_date"] = start
        item["end_date"] = end
        item["location"] = location
        item["bullets"] = bullets
        output.append(item)
    return output


def parse_profile(text: str) -> dict[str, Any]:
    repaired = repair_resume_text(text)
    profile = v1.parse_profile(repaired)
    profile["experience"] = _consolidate_experience(profile.get("experience") or [])
    warnings = list(profile.get("extraction_warnings") or [])
    if repaired != str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip():
        warnings.append(
            "Layout repair joined wrapped bullets and/or restored conservative section boundaries before extraction. Review before confirmation."
        )
    profile["extraction_warnings"] = warnings
    return profile


def extract_profile_from_source(source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist a deterministic V2 draft using the established V3 profile store."""
    from app import native_resume_service_v3 as v3

    source = source or v3.v2.active_source()
    text = str((source or {}).get("content_text") or "").strip()
    if not source or not text:
        raise ValueError("Save a confirmed Master Resume source before extracting a profile.")

    profile = v3.CandidateProfileExtract.model_validate(parse_profile(text))
    return v3._persist_profile(
        source=source,
        profile=profile,
        model="deterministic-local-v2",
        response_id="",
    )
