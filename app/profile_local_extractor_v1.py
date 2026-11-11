"""Deterministic Master Resume -> Candidate Profile extraction.

This parser is intentionally evidence-only and network-free. It is used by the
Profile workspace so a candidate can build a useful structured profile without
an OpenAI credential. It preserves resume wording and numeric claims; unknown
fields remain unknown.
"""
from __future__ import annotations

import re
from typing import Any

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{4}(?!\w)"
)
_URL_RE = re.compile(
    r"(?:(?:https?://|www\.)[^\s|]+|(?:linkedin\.com|github\.com)/[^\s|]+)",
    re.I,
)
_BULLET_RE = re.compile(r"^\s*[•●▪◦‣∙]\s*")
_DATE_RANGE_RE = re.compile(
    r"(?P<start>(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+)?\d{4})\s*(?:-|–|—|to)\s*"
    r"(?P<end>(?:Present|Current|Now|(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+)?\d{4}))",
    re.I,
)
# Degree recognition is deliberately anchored at the beginning of a line. Plain
# abbreviations such as MA/BA are common US state or prose tokens, so an
# unanchored regex can turn an institution/location line into a fake degree.
_DEGREE_RE = re.compile(
    r"^(?:degree\s*:\s*)?(?:"
    r"associate(?:'s)?|bachelor(?:'s)?|master(?:'s)?|mba|doctorate|doctoral|"
    r"M\.?S\.?|B\.?S\.?|B\.?A\.?|M\.?A\.?|Ph\.?D\.?)"
    r"(?:\b|(?=\s|$))",
    re.I,
)
_GPA_RE = re.compile(r"\bC?GPA\b\s*:?\s*[^|]+", re.I)
_EXPECTED_RE = re.compile(r"\b(?:expected|graduat(?:e|ion)|anticipated)\b[^|]*", re.I)

_SECTION_ALIASES = {
    "summary": {
        "SUMMARY",
        "PROFESSIONAL SUMMARY",
        "CAREER SUMMARY",
        "PROFILE",
        "PROFESSIONAL PROFILE",
        "OBJECTIVE",
        "CAREER OBJECTIVE",
    },
    "education": {"EDUCATION", "ACADEMIC BACKGROUND", "ACADEMICS"},
    "skills": {
        "SKILLS",
        "TECHNICAL SKILLS",
        "CORE SKILLS",
        "CORE COMPETENCIES",
        "TECHNOLOGIES",
        "TOOLS & TECHNOLOGIES",
        "TOOLS AND TECHNOLOGIES",
    },
    "certifications": {
        "CERTIFICATIONS",
        "CERTIFICATES",
        "LICENSES & CERTIFICATIONS",
        "LICENSES AND CERTIFICATIONS",
    },
    "projects": {
        "PROJECTS",
        "ACADEMIC PROJECTS",
        "SELECTED PROJECTS",
        "HR AND ANALYTICS PROJECTS",
        "HR & ANALYTICS PROJECTS",
    },
    "experience": {
        "EXPERIENCE",
        "WORK EXPERIENCE",
        "PROFESSIONAL EXPERIENCE",
        "EMPLOYMENT",
        "EMPLOYMENT HISTORY",
    },
    "languages": {"LANGUAGES", "LANGUAGE"},
}

_KNOWN_TOOLS = (
    "Microsoft Power BI",
    "Scikit-learn",
    "Google Colab",
    "Power Query",
    "PivotTables",
    "Power BI",
    "Google Sheets",
    "Microsoft Excel",
    "Advanced Excel",
    "Tableau",
    "Python",
    "Pandas",
    "NumPy",
    "Excel",
    "SQL",
    "Workday",
    "Greenhouse",
    "BambooHR",
    "SuccessFactors",
    "ADP Workforce Now",
    "UKG",
)


def _clean(value: str) -> str:
    value = str(value or "").replace("\u00a0", " ").replace("\ufffe", "-")
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def _header_key(line: str) -> str | None:
    raw = _clean(line).rstrip(":")
    if not raw or len(raw) > 70:
        return None
    normalized = re.sub(r"\s+", " ", raw.upper()).strip()
    for key, aliases in _SECTION_ALIASES.items():
        if normalized in aliases:
            return key
    return None


def _split_sections(text: str) -> tuple[list[str], dict[str, list[str]]]:
    preamble: list[str] = []
    sections: dict[str, list[str]] = {key: [] for key in _SECTION_ALIASES}
    active: str | None = None
    for raw in str(text or "").splitlines():
        line = _clean(raw)
        if not line:
            continue
        key = _header_key(line)
        if key:
            active = key
            continue
        if active is None:
            preamble.append(line)
        else:
            sections[active].append(line)
    return preamble, sections


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _clean(value)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            output.append(item)
    return output


def _contact(preamble: list[str]) -> dict[str, str]:
    joined = " | ".join(preamble)
    email_match = _EMAIL_RE.search(joined)
    phone_match = _PHONE_RE.search(joined)
    urls = _URL_RE.findall(joined)
    linkedin = next((url for url in urls if "linkedin.com" in url.casefold()), "")
    portfolio = next(
        (
            url
            for url in urls
            if "linkedin.com" not in url.casefold() and "github.com" not in url.casefold()
        ),
        "",
    )

    full_name = ""
    for line in preamble:
        if _EMAIL_RE.search(line) or _PHONE_RE.search(line) or _URL_RE.search(line):
            continue
        if "|" in line:
            continue
        if len(line.split()) <= 8 and len(line) <= 90:
            full_name = line
            break

    location = ""
    for line in preamble:
        for part in re.split(r"\s*[|•]\s*", line):
            item = _clean(part)
            if not item or item == full_name:
                continue
            if _EMAIL_RE.search(item) or _PHONE_RE.search(item) or _URL_RE.search(item):
                continue
            if re.match(r"^(?:Portfolio|GitHub|LinkedIn)\s*: ?", item, re.I):
                continue
            if "," in item and len(item) <= 100:
                location = item
                break
        if location:
            break

    return {
        "full_name": full_name,
        "location": location,
        "email": email_match.group(0) if email_match else "",
        "phone": _clean(phone_match.group(0)) if phone_match else "",
        "linkedin": linkedin,
        "portfolio": portfolio,
    }


def _summary(lines: list[str]) -> str:
    return _clean(" ".join(_BULLET_RE.sub("", line) for line in lines))


def _parse_institution(line: str) -> tuple[str, str]:
    parts = [_clean(part) for part in line.split(",")]
    if len(parts) >= 3:
        return parts[0], ", ".join(parts[1:])
    return _clean(line), ""


def _parse_degree(line: str) -> tuple[str, str, str, str]:
    parts = [_clean(part) for part in line.split("|") if _clean(part)]
    degree_text = parts[0] if parts else _clean(line)
    degree = degree_text
    field = ""
    match = re.match(r"(.+?)\s+in\s+(.+)$", degree_text, re.I)
    if match:
        degree = _clean(match.group(1))
        field = _clean(match.group(2))
    gpa = ""
    end_date = ""
    for part in parts[1:]:
        if _GPA_RE.search(part):
            gpa = part
        elif _EXPECTED_RE.search(part) or re.search(r"\b(?:19|20)\d{2}\b", part):
            end_date = part
    return degree, field, end_date, gpa


def _education(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current_candidate = ""
    current: dict[str, Any] | None = None
    for raw in lines:
        line = _BULLET_RE.sub("", _clean(raw))
        if not line:
            continue
        if _DEGREE_RE.search(line):
            institution, location = _parse_institution(current_candidate)
            degree, field, end_date, gpa = _parse_degree(line)
            current = {
                "institution": institution,
                "degree": degree,
                "field": field,
                "location": location,
                "start_date": "",
                "end_date": end_date,
                "gpa": gpa,
                "details": [],
            }
            entries.append(current)
            # A degree consumes the pending institution line. Clearing this is a
            # second guard against a later malformed line reusing the old school.
            current_candidate = ""
            continue
        if line.casefold().startswith(
            ("coursework:", "relevant coursework:", "honors:", "activities:")
        ):
            if current is not None:
                current["details"].append(line)
            continue
        if (
            current is not None
            and current["details"]
            and current["details"][-1].casefold().startswith(
                ("coursework:", "relevant coursework:")
            )
            and not re.search(r"\b(?:university|college|institute|school)\b", line, re.I)
            and not _DEGREE_RE.search(line)
        ):
            current["details"][-1] = _clean(current["details"][-1] + " " + line)
            continue
        if current is not None and (
            line.startswith(("(", "-")) or ("," not in line and len(line.split()) > 9)
        ):
            current["details"].append(line)
            continue
        current_candidate = line
    return [item for item in entries if item["institution"] or item["degree"]]


def _skills(lines: list[str]) -> list[dict[str, Any]]:
    logical: list[str] = []
    current = ""
    for raw in lines:
        line = _clean(raw)
        bullet = bool(_BULLET_RE.match(line))
        line = _BULLET_RE.sub("", line)
        if bullet or (":" in line and not current):
            if current:
                logical.append(current)
            current = line
        elif current:
            current += " " + line
        else:
            current = line
    if current:
        logical.append(current)

    groups: list[dict[str, Any]] = []
    for line in logical:
        if ":" in line:
            category, payload = line.split(":", 1)
        else:
            category, payload = "Skills", line
        values = re.split(r"\s*[|;]\s*|\s*,\s*", payload)
        skills = _dedupe([value for value in values if value])
        if skills:
            groups.append({"category": _clean(category), "skills": skills})
    return groups


def _certifications(lines: list[str]) -> list[dict[str, str]]:
    payload = " ".join(_BULLET_RE.sub("", _clean(line)) for line in lines)
    parts = _dedupe(re.split(r"\s*\|\s*|\s*;\s*", payload))
    output: list[dict[str, str]] = []
    for part in parts:
        name, issuer = part, ""
        if " - " in part:
            name, issuer = part.rsplit(" - ", 1)
        output.append(
            {
                "name": _clean(name),
                "issuer": _clean(issuer),
                "date": "",
                "credential_id": "",
            }
        )
    return [item for item in output if item["name"]]


def _project_heading(line: str) -> tuple[str, list[str]]:
    found: list[tuple[int, str]] = []
    for tool in _KNOWN_TOOLS:
        match = re.search(
            rf"(?<![A-Za-z0-9]){re.escape(tool)}(?![A-Za-z0-9])",
            line,
            re.I,
        )
        if match:
            found.append((match.start(), tool))
    if not found:
        return _clean(line), []
    first = min(index for index, _tool in found)
    name = _clean(line[:first].rstrip(" ,-·|"))
    tools = _dedupe([tool for _index, tool in sorted(found)])
    return name or _clean(line), tools


def _projects(lines: list[str]) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in lines:
        line = _clean(raw)
        is_bullet = bool(_BULLET_RE.match(line))
        line = _BULLET_RE.sub("", line)
        if not line:
            continue
        if is_bullet:
            if current is not None:
                current["bullets"].append(line)
            continue
        if current is not None and current["bullets"] and (
            line[:1].islower() or not re.search(r"[.!?]$", current["bullets"][-1])
        ):
            current["bullets"][-1] = _clean(current["bullets"][-1] + " " + line)
            continue
        name, tools = _project_heading(line)
        current = {"name": name, "description": "", "tools": tools, "bullets": []}
        projects.append(current)
    return [item for item in projects if item["name"]]


def _experience(lines: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    awaiting_title = False
    for raw in lines:
        line = _clean(raw)
        is_bullet = bool(_BULLET_RE.match(line))
        line = _BULLET_RE.sub("", line)
        if not line:
            continue

        date_match = _DATE_RANGE_RE.search(line)
        if date_match and not is_bullet:
            employer_part = _clean(line[: date_match.start()].rstrip(" |,-"))
            after = _clean(line[date_match.end() :].lstrip(" |,-"))
            employer = employer_part
            location = after
            if " | " in employer_part:
                employer, location = [_clean(value) for value in employer_part.split(" | ", 1)]
            current = {
                "employer": employer,
                "title": "",
                "location": location,
                "start_date": _clean(date_match.group("start")),
                "end_date": _clean(date_match.group("end")),
                "bullets": [],
            }
            records.append(current)
            awaiting_title = True
            continue

        if current is None:
            continue
        if awaiting_title and not is_bullet:
            current["title"] = line
            awaiting_title = False
            continue
        if is_bullet:
            current["bullets"].append(line)
            awaiting_title = False
            continue
        if current["bullets"]:
            current["bullets"][-1] = _clean(current["bullets"][-1] + " " + line)
        elif not current["title"]:
            current["title"] = line
            awaiting_title = False
    return [item for item in records if item["employer"] or item["title"]]


def _languages(lines: list[str]) -> list[str]:
    values: list[str] = []
    for raw in lines:
        line = _BULLET_RE.sub("", _clean(raw))
        values.extend(re.split(r"\s*[|;,]\s*", line))
    return _dedupe(values)


def extract_profile_data(text: str) -> dict[str, Any]:
    """Return a schema-compatible evidence-only profile dictionary.

    No model is called, sensitive fields are not inferred, and numeric claims
    are never added. The function only restructures text already present in the
    candidate-confirmed Master Resume.
    """
    value = str(text or "").strip()
    if not value:
        raise ValueError("Confirmed Master Resume text is required.")

    preamble, sections = _split_sections(value)
    profile = {
        "professional_summary": _summary(sections["summary"]),
        "contact": _contact(preamble),
        "education": _education(sections["education"]),
        "experience": _experience(sections["experience"]),
        "projects": _projects(sections["projects"]),
        "skills": _skills(sections["skills"]),
        "certifications": _certifications(sections["certifications"]),
        "languages": _languages(sections["languages"]),
        "application_defaults": {
            "work_authorization_country": "",
            "authorization_basis": "",
            "visa_or_permit": "",
            "sponsorship_required": None,
            "willing_to_relocate": None,
            "work_modes": [],
        },
        "extraction_warnings": [
            "Built locally from the confirmed Master Resume; no AI credential was required.",
            "Review the structured preview before confirming it as permanent profile authority.",
        ],
    }

    meaningful = (
        bool(profile["professional_summary"])
        or bool(profile["education"])
        or bool(profile["experience"])
        or bool(profile["projects"])
        or bool(profile["skills"])
        or bool(profile["certifications"])
    )
    if not meaningful:
        raise ValueError(
            "MUNSHI could not identify standard resume sections. Review the extracted Master Resume text and keep section headings such as SUMMARY, EDUCATION, SKILLS, PROJECTS, and EXPERIENCE."
        )
    return profile
