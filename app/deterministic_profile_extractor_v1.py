"""Deterministic Master Resume -> candidate profile extraction.

This module intentionally performs no model/API/network calls. It is a conservative
parser for candidate-confirmed resume text: values are copied from the resume or
left blank. The resulting draft still requires explicit confirmation before it
becomes permanent profile authority.
"""
from __future__ import annotations

import re
from typing import Any


_HEADING_ALIASES = {
    "summary": {
        "summary", "professional summary", "profile", "professional profile",
        "career summary", "objective", "professional objective",
    },
    "education": {"education", "academic background", "academics"},
    "experience": {
        "experience", "work experience", "professional experience",
        "employment", "employment history", "relevant experience",
    },
    "projects": {"projects", "project experience", "selected projects", "academic projects"},
    "skills": {
        "skills", "technical skills", "core skills", "core competencies",
        "competencies", "tools & technologies", "tools and technologies",
    },
    "certifications": {"certifications", "certificates", "licenses & certifications", "licenses and certifications"},
    "languages": {"languages", "language"},
    "application_defaults": {
        "work authorization", "work authorisation", "authorization", "authorisation",
        "application defaults", "preferences", "work preferences",
    },
}

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.()-]*)?(?:\(?\d{3}\)?[\s.-]*)\d{3}[\s.-]*\d{4}(?!\d)")
_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s|]+", re.I)
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[^\s|]+", re.I)
_DATE_TOKEN = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|Spring|Summer|Fall|Winter)?\s*\d{4}"
_DATE_RANGE_RE = re.compile(rf"(?P<start>{_DATE_TOKEN})\s*(?:-|–|—|to)\s*(?P<end>{_DATE_TOKEN}|Present|Current)", re.I)
_YEAR_RANGE_RE = re.compile(r"(?P<start>(?:19|20)\d{2})\s*(?:-|–|—|to)\s*(?P<end>(?:19|20)\d{2}|Present|Current)", re.I)
_BULLET_RE = re.compile(r"^(?:[•●▪◦‣⁃*]|[-–—]\s+)\s*")
_DEGREE_RE = re.compile(r"\b(?:bachelor|master|mba|m\.b\.a|m\.s\.?|ms\b|m\.a\.?|ma\b|b\.s\.?|bs\b|b\.a\.?|ba\b|ph\.?d|doctor|associate|diploma|degree)\b", re.I)
_SCHOOL_RE = re.compile(r"\b(?:university|college|institute|school|academy|polytechnic)\b", re.I)
_TITLE_RE = re.compile(
    r"\b(?:intern|analyst|coordinator|specialist|assistant|associate|recruiter|manager|director|consultant|administrator|partner|engineer|developer|scientist|lead|officer|representative|advisor|adviser|owner|collector)\b",
    re.I,
)


def _clean_line(value: str) -> str:
    value = str(value or "").replace("\u00a0", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", value).strip()


def _heading_key(line: str) -> str | None:
    candidate = _clean_line(line).strip(":-–— ").casefold()
    if not candidate or len(candidate) > 64:
        return None
    for key, aliases in _HEADING_ALIASES.items():
        if candidate in aliases:
            return key
    return None


def _split_resume(text: str) -> tuple[list[str], dict[str, list[str]]]:
    preamble: list[str] = []
    sections: dict[str, list[str]] = {key: [] for key in _HEADING_ALIASES}
    active: str | None = None
    for raw in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _clean_line(raw)
        if not line:
            if active and sections[active] and sections[active][-1] != "":
                sections[active].append("")
            continue
        heading = _heading_key(line)
        if heading:
            active = heading
            continue
        if active:
            sections[active].append(line)
        else:
            preamble.append(line)
    return preamble, sections


def _strip_bullet(line: str) -> str:
    return _BULLET_RE.sub("", _clean_line(line)).strip()


def _is_bullet(line: str) -> bool:
    return bool(_BULLET_RE.match(_clean_line(line)))


def _blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if not line:
            if current:
                blocks.append(current)
                current = []
            continue
        if current and not _is_bullet(line) and any(_is_bullet(item) for item in current):
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _split_parts(line: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s*(?:\||·|•)\s*", _clean_line(line)) if part.strip()]


def _date_range(lines: list[str]) -> tuple[str, str]:
    for line in lines:
        match = _DATE_RANGE_RE.search(line) or _YEAR_RANGE_RE.search(line)
        if match:
            return _clean_line(match.group("start")), _clean_line(match.group("end"))
    return "", ""


def _without_dates(line: str) -> str:
    value = _DATE_RANGE_RE.sub("", line)
    value = _YEAR_RANGE_RE.sub("", value)
    return value.strip(" |·,-–—")


def _contact(preamble: list[str]) -> dict[str, str]:
    text = " | ".join(preamble)
    email_match = _EMAIL_RE.search(text)
    email = email_match.group(0) if email_match else ""
    phone_match = _PHONE_RE.search(text)
    phone = phone_match.group(0).strip() if phone_match else ""
    linkedin_match = _LINKEDIN_RE.search(text)
    linkedin = linkedin_match.group(0).rstrip(".,;") if linkedin_match else ""

    portfolio = ""
    for match in _URL_RE.finditer(text):
        value = match.group(0).rstrip(".,;")
        if "linkedin.com" not in value.casefold():
            portfolio = value
            break

    full_name = ""
    location = ""
    for line in preamble:
        candidate = _clean_line(line)
        if not candidate:
            continue
        if _EMAIL_RE.search(candidate) or _PHONE_RE.search(candidate) or _URL_RE.search(candidate):
            for part in _split_parts(candidate):
                if _EMAIL_RE.search(part) or _PHONE_RE.search(part) or _URL_RE.search(part):
                    continue
                if "," in part and len(part) <= 80:
                    location = location or part
            continue
        words = re.findall(r"[A-Za-z][A-Za-z.'’-]*", candidate)
        if not full_name and 2 <= len(words) <= 7 and "," not in candidate and len(candidate) <= 80:
            full_name = candidate
            continue
        if not location and "," in candidate and len(candidate) <= 80:
            location = candidate

    return {
        "full_name": full_name,
        "location": location,
        "email": email,
        "phone": phone,
        "linkedin": linkedin,
        "portfolio": portfolio,
    }


def _summary(lines: list[str]) -> str:
    return " ".join(_strip_bullet(line) for line in lines if line).strip()


def _education(lines: list[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for block in _blocks(lines):
        clean = [_strip_bullet(line) for line in block if _strip_bullet(line)]
        if not clean:
            continue
        start, end = _date_range(clean)
        institution = ""
        degree = ""
        for line in clean:
            for part in _split_parts(_without_dates(line)):
                if not institution and _SCHOOL_RE.search(part):
                    institution = part
                if not degree and _DEGREE_RE.search(part):
                    degree = part
        if not institution:
            institution = _without_dates(clean[0])
        if not degree and len(clean) > 1:
            second = _without_dates(clean[1])
            if second and second != institution:
                degree = second
        location = ""
        for line in clean:
            candidate = _without_dates(line)
            for part in _split_parts(candidate):
                if part in {institution, degree}:
                    continue
                if "," in part and len(part) <= 80 and not _DEGREE_RE.search(part):
                    location = part
                    break
            if location:
                break
        details = []
        for line in clean:
            parts = _split_parts(_without_dates(line))
            if all(part in {institution, degree, location} for part in parts if part):
                continue
            if _DATE_RANGE_RE.search(line) or _YEAR_RANGE_RE.search(line):
                residue = _without_dates(line)
                if not residue or all(part in {institution, degree, location} for part in _split_parts(residue)):
                    continue
            if line not in {institution, degree, location}:
                details.append(line)
        gpa = ""
        for line in clean:
            match = re.search(r"\bGPA\s*[:=]?\s*([0-4](?:\.\d{1,2})?(?:\s*/\s*4(?:\.0)?)?)", line, re.I)
            if match:
                gpa = match.group(1)
                break
        output.append({
            "institution": institution,
            "degree": degree,
            "field": "",
            "location": location,
            "start_date": start,
            "end_date": end,
            "gpa": gpa,
            "details": details,
        })
    return output


def _experience(lines: list[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for block in _blocks(lines):
        bullets = [_strip_bullet(line) for line in block if _is_bullet(line) and _strip_bullet(line)]
        headers = [_strip_bullet(line) for line in block if not _is_bullet(line) and _strip_bullet(line)]
        if not headers and not bullets:
            continue
        start, end = _date_range(headers)
        parts: list[str] = []
        for line in headers:
            candidate = _without_dates(line)
            parts.extend(_split_parts(candidate) if candidate else [])
        parts = list(dict.fromkeys(part for part in parts if part))

        title = next((part for part in parts if _TITLE_RE.search(part)), "")
        location = next((part for part in parts if "," in part and len(part) <= 80 and part != title), "")
        employer = next((part for part in parts if part not in {title, location}), "")
        if not employer and parts:
            employer = parts[0]
        if not title and len(parts) > 1:
            title = next((part for part in parts if part != employer and part != location), "")
        if parts and _TITLE_RE.search(parts[0]) and len(parts) > 1:
            title = parts[0]
            employer = next((part for part in parts[1:] if part != location), employer)

        output.append({
            "employer": employer,
            "title": title,
            "location": location,
            "start_date": start,
            "end_date": end,
            "bullets": bullets,
        })
    return output


def _projects(lines: list[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for block in _blocks(lines):
        bullets = [_strip_bullet(line) for line in block if _is_bullet(line) and _strip_bullet(line)]
        headers = [_strip_bullet(line) for line in block if not _is_bullet(line) and _strip_bullet(line)]
        if not headers and not bullets:
            continue
        name = headers[0] if headers else (bullets[0] if bullets else "")
        description = " ".join(headers[1:]) if len(headers) > 1 else ""
        output.append({"name": name, "description": description, "tools": [], "bullets": bullets})
    return output


def _skills(lines: list[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for line in lines:
        value = _strip_bullet(line)
        if not value:
            continue
        category = "Resume-listed skills"
        payload = value
        if ":" in value:
            possible, rest = value.split(":", 1)
            if 1 <= len(possible.split()) <= 6 and rest.strip():
                category, payload = possible.strip(), rest.strip()
        skills = [item.strip() for item in re.split(r"\s*(?:,|;|\||·)\s*", payload) if item.strip()]
        if not skills:
            skills = [payload]
        output.append({"category": category, "skills": list(dict.fromkeys(skills))})
    return output


def _certifications(lines: list[str]) -> list[dict[str, str]]:
    return [
        {"name": _strip_bullet(line), "issuer": "", "date": "", "credential_id": ""}
        for line in lines if _strip_bullet(line)
    ]


def _languages(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in lines:
        values.extend(item.strip() for item in re.split(r"\s*(?:,|;|\||·)\s*", _strip_bullet(line)) if item.strip())
    return list(dict.fromkeys(values))


def _application_defaults(lines: list[str]) -> dict[str, Any]:
    text = " ".join(_strip_bullet(line) for line in lines if line)
    lowered = text.casefold()
    sponsorship: bool | None = None
    if re.search(r"\b(?:do not|does not|no)\s+(?:currently\s+)?require\w*\s+(?:employment\s+)?sponsorship\b", lowered):
        sponsorship = False
    elif re.search(r"\b(?:require|requires|requiring|need|needs)\w*\s+(?:employment\s+)?sponsorship\b", lowered):
        sponsorship = True

    relocate: bool | None = None
    if "willing to relocate" in lowered or "open to relocation" in lowered:
        relocate = True
    elif "not willing to relocate" in lowered:
        relocate = False

    modes = []
    for label, patterns in (
        ("Remote", ("remote",)),
        ("Hybrid", ("hybrid",)),
        ("On-site", ("on-site", "onsite", "in-person")),
    ):
        if any(re.search(rf"\b{re.escape(pattern)}\b", lowered) for pattern in patterns):
            modes.append(label)

    country = "United States" if re.search(r"\b(?:united states|u\.s\.|usa)\b", lowered) else ""
    permit_tokens = []
    for pattern, label in ((r"\bF-?1\b", "F-1"), (r"\bSTEM\s+OPT\b", "STEM OPT"), (r"\bOPT\b", "OPT"), (r"\bCPT\b", "CPT")):
        if re.search(pattern, text, re.I):
            permit_tokens.append(label)

    return {
        "work_authorization_country": country,
        "authorization_basis": "",
        "visa_or_permit": " / ".join(dict.fromkeys(permit_tokens)),
        "sponsorship_required": sponsorship,
        "willing_to_relocate": relocate,
        "work_modes": modes,
    }


def parse_profile(text: str) -> dict[str, Any]:
    """Return a conservative profile dict copied only from supplied resume text."""
    if not str(text or "").strip():
        raise ValueError("Master Resume text is empty.")
    preamble, sections = _split_resume(text)
    profile = {
        "professional_summary": _summary(sections["summary"]),
        "contact": _contact(preamble),
        "education": _education(sections["education"]),
        "experience": _experience(sections["experience"]),
        "projects": _projects(sections["projects"]),
        "skills": _skills(sections["skills"]),
        "certifications": _certifications(sections["certifications"]),
        "languages": _languages(sections["languages"]),
        "application_defaults": _application_defaults(sections["application_defaults"]),
        "extraction_warnings": [],
    }
    missing = [
        label for key, label in (
            ("education", "Education"),
            ("experience", "Experience"),
            ("skills", "Skills"),
        ) if not profile[key]
    ]
    if missing:
        profile["extraction_warnings"].append(
            "Deterministic parser did not find labeled section(s): " + ", ".join(missing) + ". Review before confirmation."
        )
    if not profile["contact"]["full_name"]:
        profile["extraction_warnings"].append("Full name was not confidently located in the resume header.")
    return profile


def extract_profile_from_source(source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist a local deterministic draft using the existing V3 profile store."""
    from app import native_resume_service_v3 as v3

    source = source or v3.v2.active_source()
    text = str((source or {}).get("content_text") or "").strip()
    if not source or not text:
        raise ValueError("Save a confirmed Master Resume source before extracting a profile.")
    profile = v3.CandidateProfileExtract.model_validate(parse_profile(text))
    return v3._persist_profile(
        source=source,
        profile=profile,
        model="deterministic-local-v1",
        response_id="",
    )
