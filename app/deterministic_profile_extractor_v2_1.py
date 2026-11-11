"""Deterministic profile extraction V2.1 with project-boundary recovery.

V2 fixed wrapped experience bullets and lost section boundaries.  V2.1 adds one
more conservative layout repair observed in real PDF extraction: the next
project title can be appended to the end of the previous project's final bullet.

The repair is deliberately narrow.  It only runs inside the PROJECTS section,
only splits after sentence punctuation, and only accepts a title-like suffix
containing an explicit project/study/analysis/research noun.  No model, API,
network request, or private external lookup is used.  Persisted output remains a
reviewable draft and still requires explicit user confirmation.
"""
from __future__ import annotations

import re
from typing import Any

from app import deterministic_profile_extractor_v1 as v1
from app import deterministic_profile_extractor_v2 as v2


_PROJECT_KIND_RE = re.compile(
    r"\b(?:project|projects|study|analysis|dashboard|model|research|case study)\b",
    re.I,
)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9])")


def _project_header_parts(value: str) -> tuple[str, str] | None:
    """Return ``(title, trailing_tools)`` for a conservative project header."""
    clean = v1._clean_line(value)
    if not clean or v1._is_bullet(clean) or len(clean) > 180:
        return None

    matches = list(_PROJECT_KIND_RE.finditer(clean[:130]))
    if not matches:
        return None

    # Prefer the last explicit project noun in the title-like prefix so phrases
    # such as "Case Study" remain intact.
    marker = matches[-1]
    title = clean[: marker.end()].strip(" |,-–—")
    trailing = clean[marker.end() :].strip(" |,-–—")

    if not title or not title[:1].isupper() or len(title) > 120:
        return None

    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9/&.'’+-]*", title)
    if not 2 <= len(words) <= 16:
        return None

    # Require a heading-like capitalization pattern.  This prevents ordinary
    # prose such as "This project improved..." from being treated as a title.
    significant = [word for word in words if word.casefold() not in {"and", "of", "for", "the", "with", "in"}]
    capitalized = sum(1 for word in significant if word[:1].isupper() or word.isupper())
    if significant and capitalized < max(2, (len(significant) + 1) // 2):
        return None

    # If text trails the project noun, it should look like a compact tools/
    # platform list rather than a prose sentence.
    if trailing:
        if len(trailing) > 100 or trailing.endswith((".", ";")):
            return None
        trailing_words = trailing.split()
        if len(trailing_words) > 14:
            return None

    return title, trailing


def _split_embedded_project_boundary(line: str) -> tuple[str, str, str] | None:
    """Split ``bullet. Next Project Title tools`` without changing wording."""
    clean = v1._clean_line(line)
    if not v1._is_bullet(clean):
        return None

    payload = v1._strip_bullet(clean)
    for boundary in _SENTENCE_BOUNDARY_RE.finditer(payload):
        suffix = payload[boundary.end() :].strip()
        parts = _project_header_parts(suffix)
        if not parts:
            continue
        bullet = payload[: boundary.start()].rstrip()
        if not bullet:
            continue
        title, tools = parts
        return f"• {bullet}", title, tools
    return None


def repair_resume_text(text: str) -> str:
    """Apply V2 repair plus conservative project-boundary recovery."""
    repaired = v2.repair_resume_text(text)
    output: list[str] = []
    active: str | None = None

    for raw in repaired.splitlines():
        line = v1._clean_line(raw)
        heading = v1._heading_key(line) if line else None
        if heading:
            active = heading
            output.append(line)
            continue

        if active == "projects" and line:
            if v1._is_bullet(line):
                split = _split_embedded_project_boundary(line)
                if split:
                    bullet, title, tools = split
                    output.append(bullet)
                    if output and output[-1] != "":
                        output.append("")
                    output.append(title)
                    if tools:
                        output.append(tools)
                    continue
            else:
                # Normalize a title and compact tool list that were flattened
                # onto one physical line, e.g. "... Projects Power BI, Tableau".
                parts = _project_header_parts(line)
                if parts and parts[1]:
                    output.append(parts[0])
                    output.append(parts[1])
                    continue

        output.append(line)

    while output and output[-1] == "":
        output.pop()
    return "\n".join(output)


def parse_profile(text: str) -> dict[str, Any]:
    repaired = repair_resume_text(text)
    profile = v1.parse_profile(repaired)
    profile["experience"] = v2._consolidate_experience(profile.get("experience") or [])

    warnings = list(profile.get("extraction_warnings") or [])
    normalized_original = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if repaired != normalized_original:
        warnings.append(
            "Layout repair joined wrapped bullets and restored conservative section/project boundaries before extraction. Review before confirmation."
        )
    profile["extraction_warnings"] = warnings
    return profile


def extract_profile_from_source(source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist a deterministic V2.1 draft using the established V3 profile store."""
    from app import native_resume_service_v3 as v3

    source = source or v3.v2.active_source()
    text = str((source or {}).get("content_text") or "").strip()
    if not source or not text:
        raise ValueError("Save a confirmed Master Resume source before extracting a profile.")

    profile = v3.CandidateProfileExtract.model_validate(parse_profile(text))
    return v3._persist_profile(
        source=source,
        profile=profile,
        model="deterministic-local-v2.1",
        response_id="",
    )
