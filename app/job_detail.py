from __future__ import annotations

import json
import re
from typing import Any

DETAIL_VERSION = "job_detail_extractor_v1"

PLACEHOLDERS = {
    "",
    "n/a",
    "na",
    "none",
    "not available",
    "not listed",
    "not provided",
    "not separately specified",
    "not specified",
    "not specified in posting",
    "unknown",
}

LIST_FIELDS = {
    "responsibilities",
    "qualifications",
    "preferred_qualifications",
    "preferred_skills",
    "skills_keywords",
    "benefits",
}

HEADING_ALIASES: dict[str, str] = {
    "job title": "title",
    "title": "title",
    "company name": "company_name",
    "company": "company_name",
    "location": "location_raw",
    "remote hybrid onsite": "remote_type",
    "work arrangement": "remote_type",
    "internship part time full time": "employment_type",
    "employment type": "employment_type",
    "job type": "employment_type",
    "pay wage salary": "salary_raw",
    "salary": "salary_raw",
    "posted date": "date_posted",
    "date posted": "date_posted",
    "apply deadline": "apply_deadline",
    "application deadline": "apply_deadline",
    "start date": "start_date",
    "end date": "end_date",
    "hours per week": "hours_per_week",
    "job description": "job_description",
    "description": "job_description",
    "responsibilities": "responsibilities",
    "duties": "responsibilities",
    "qualifications": "qualifications",
    "requirements": "qualifications",
    "preferred qualifications": "preferred_qualifications",
    "preferred skills": "preferred_skills",
    "skills keywords": "skills_keywords",
    "skills and keywords": "skills_keywords",
    "skills": "skills_keywords",
    "work authorization": "work_authorization",
    "work authorization sponsorship": "work_authorization",
    "benefits": "benefits",
    "industry": "industry",
    "company size": "company_size",
    "employer description": "employer_description",
    "application link": "apply_url",
    "apply link": "apply_url",
    "job link": "apply_url",
}

KNOWN_SKILLS = (
    "Human Resources", "HR Operations", "Recruiting", "Talent Acquisition",
    "Employee Engagement", "Onboarding", "Offboarding", "HR Analytics",
    "People Analytics", "Workforce Analytics", "Diversity Metrics",
    "Survey Analysis", "Reporting", "Excel", "Microsoft Word", "Power BI",
    "Tableau", "Python", "HRIS", "ATS", "NeoGov", "Workday", "Canva",
    "Event Planning", "Branding", "Outreach", "Communications",
    "Data Analysis", "Public Administration", "Benefits", "Compensation",
    "Total Rewards", "Payroll", "Employee Relations", "Compliance",
)


def clean_text(value: Any) -> str:
    text = str(value or "").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_heading(value: Any) -> str:
    text = clean_text(value).casefold().replace("&", " and ")
    text = re.sub(r"[/|\\_\-–—]+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def useful(value: Any) -> bool:
    if isinstance(value, (list, tuple, set)):
        return any(useful(item) for item in value)
    text = clean_text(value)
    return bool(text and text.casefold() not in PLACEHOLDERS)


def is_meaningful_description(value: Any) -> bool:
    text = clean_text(value)
    return useful(text) and len(text) >= 80


def _dedupe(items: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = clean_text(item)
        key = cleaned.casefold()
        if not cleaned or key in PLACEHOLDERS or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return _dedupe([clean_text(item) for item in value])
    if isinstance(value, dict):
        return _dedupe([f"{key}: {item}" for key, item in value.items()])

    text = clean_text(value)
    if not useful(text):
        return []

    if text[:1] in "[{":
        try:
            loaded = json.loads(text)
            if loaded is not value:
                return as_list(loaded)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    parts: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"^\s*[•●▪◦*\-–—]+\s*", "", raw_line).strip()
        if line:
            parts.append(line)

    if len(parts) <= 1 and ";" in text:
        parts = [part.strip() for part in text.split(";") if part.strip()]

    return _dedupe(parts)


def parse_labeled_sections(raw_text: Any) -> dict[str, Any]:
    text = clean_text(raw_text)
    parsed: dict[str, Any] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    scalar_fields = {
        "title", "company_name", "location_raw", "remote_type",
        "employment_type", "salary_raw", "date_posted", "apply_deadline",
        "start_date", "end_date", "hours_per_week", "industry",
        "company_size", "employer_description", "apply_url",
        "work_authorization", "job_description",
    }

    def flush() -> None:
        nonlocal current_key, current_lines
        if current_key is None:
            return
        value = clean_text("\n".join(current_lines))
        if useful(value):
            if current_key in LIST_FIELDS:
                existing = as_list(parsed.get(current_key))
                parsed[current_key] = _dedupe(existing + as_list(value))
            elif current_key in scalar_fields:
                if not useful(parsed.get(current_key)):
                    parsed[current_key] = value
            else:
                parsed[current_key] = value
        current_key = None
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        match = re.match(r"^\s*([^:\n]{1,100})\s*:\s*(.*)$", line)
        if match:
            mapped = HEADING_ALIASES.get(normalize_heading(match.group(1)))
            if mapped:
                flush()
                current_key = mapped
                inline = match.group(2).strip()
                if inline:
                    current_lines.append(inline)
                continue
        heading_only = HEADING_ALIASES.get(normalize_heading(line)) if len(line.strip()) <= 100 else None
        if heading_only:
            flush()
            current_key = heading_only
            continue
        if current_key is not None:
            current_lines.append(line)

    flush()
    return parsed


def _first_useful(*values: Any, fallback: str = "") -> str:
    for value in values:
        if useful(value):
            return clean_text(value)
    return fallback


def _infer_employment_type(title: str, text: str) -> str:
    combined = f"{title} {text}".casefold()
    if "internship" in combined or re.search(r"\bintern\b", combined):
        return "Internship"
    if "part-time" in combined or "part time" in combined:
        return "Part-Time"
    if "full-time" in combined or "full time" in combined:
        return "Full-Time"
    if "contract" in combined:
        return "Contract"
    return "Not specified"


def _infer_hours(text: str) -> str:
    patterns = (
        r"(?:up to\s+)?\d{1,2}(?:\s*[-–]\s*\d{1,2})?\s+hours?\s+(?:per|a)\s+week(?:\s*\([^)]*\))?",
        r"\d{1,2}(?:\.\d+)?\s*hours?\/week",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean_text(match.group(0))
    return "Not specified"


def _infer_work_authorization(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+|\n+", clean_text(text))
    matches = [
        sentence.strip()
        for sentence in sentences
        if re.search(
            r"work authorization|authorized to work|visa sponsorship|"
            r"sponsorship|citizenship|green card|CPT|OPT",
            sentence,
            re.IGNORECASE,
        )
    ]
    return " ".join(_dedupe(matches)) or "Not specified"


def _infer_skills(text: str) -> list[str]:
    found = [skill for skill in KNOWN_SKILLS if re.search(rf"\b{re.escape(skill)}\b", text, re.I)]
    return _dedupe(found)


def enrich_job_details(raw_job: dict[str, Any]) -> dict[str, Any]:
    job = dict(raw_job)
    description = _first_useful(
        job.get("description_raw"),
        job.get("description"),
        job.get("job_description"),
        fallback="Not specified",
    )
    sections = parse_labeled_sections(description)

    job["description_raw"] = description
    job["employment_type"] = _first_useful(
        job.get("employment_type"),
        job.get("job_type"),
        sections.get("employment_type"),
        fallback=_infer_employment_type(
            clean_text(job.get("title")),
            description,
        ),
    )
    job["hours_per_week"] = _first_useful(
        job.get("hours_per_week"),
        sections.get("hours_per_week"),
        fallback=_infer_hours(description),
    )

    for field in (
        "responsibilities", "qualifications", "preferred_qualifications",
        "preferred_skills", "skills_keywords", "benefits",
    ):
        explicit = as_list(job.get(field))
        parsed = as_list(sections.get(field))
        job[field] = _dedupe(explicit + parsed)

    if not job["skills_keywords"]:
        job["skills_keywords"] = _infer_skills(description)

    job["work_authorization"] = _first_useful(
        job.get("work_authorization"),
        sections.get("work_authorization"),
        fallback=_infer_work_authorization(description),
    )
    job["company_size"] = _first_useful(
        job.get("company_size"), sections.get("company_size"), fallback="Not specified"
    )
    job["industry"] = _first_useful(
        job.get("industry"), sections.get("industry"), fallback="Not specified"
    )
    job["employer_description"] = _first_useful(
        job.get("employer_description"),
        sections.get("employer_description"),
        fallback="Not specified",
    )
    job["job_description_overview"] = _first_useful(
        job.get("job_description_overview"),
        sections.get("job_description"),
        fallback=description,
    )

    meaningful = is_meaningful_description(description)
    extracted_count = sum(
        bool(job.get(field))
        for field in (
            "responsibilities", "qualifications", "preferred_qualifications",
            "skills_keywords", "work_authorization", "benefits",
        )
    )
    if not meaningful:
        status = "missing_detail"
    elif extracted_count >= 3:
        status = "structured"
    else:
        status = "description_only"

    job["detail_extraction_status"] = status
    job["detail_extraction_version"] = DETAIL_VERSION
    job["detail_extraction_json"] = json.dumps(
        {
            "status": status,
            "version": DETAIL_VERSION,
            "description_chars": len(description),
            "extracted_section_count": extracted_count,
        },
        ensure_ascii=False,
    )
    return job


def _format_list(value: Any) -> str:
    items = as_list(value)
    return "\n".join(f"• {item}" for item in items) if items else "Not specified"


def build_manual_job_text(raw_job: dict[str, Any]) -> str:
    job = enrich_job_details(raw_job)
    link = _first_useful(job.get("apply_url"), job.get("job_url"), fallback="Not specified")

    sections: list[tuple[str, str]] = [
        ("Job Title", _first_useful(job.get("title"), fallback="Not specified")),
        ("Company Name", _first_useful(job.get("company_name"), job.get("company"), fallback="Not specified")),
        ("Location", _first_useful(job.get("location_raw"), job.get("location"), fallback="Not specified")),
        ("Remote / Hybrid / Onsite", _first_useful(job.get("remote_type"), job.get("workplace_type"), fallback="Not specified")),
        ("Internship / Part-Time / Full-Time", _first_useful(job.get("employment_type"), fallback="Not specified")),
        ("Pay / Wage / Salary", _first_useful(job.get("salary_raw"), job.get("salary"), fallback="Not specified")),
        ("Posted Date", _first_useful(job.get("date_posted"), job.get("posted_date"), fallback="Not specified")),
        ("Apply Deadline", _first_useful(job.get("apply_deadline"), fallback="Not specified")),
        ("Start Date", _first_useful(job.get("start_date"), fallback="Not specified")),
        ("End Date", _first_useful(job.get("end_date"), fallback="Not specified")),
        ("Hours per Week", _first_useful(job.get("hours_per_week"), fallback="Not specified")),
        ("Job Description", _first_useful(job.get("job_description_overview"), job.get("description_raw"), fallback="Not specified")),
        ("Responsibilities", _format_list(job.get("responsibilities"))),
        ("Qualifications", _format_list(job.get("qualifications"))),
        ("Preferred Qualifications", _format_list(job.get("preferred_qualifications") or job.get("preferred_skills"))),
        ("Skills & Keywords", _format_list(job.get("skills_keywords") or job.get("preferred_skills"))),
        ("Work Authorization", _first_useful(job.get("work_authorization"), fallback="Not specified")),
        ("Benefits", _format_list(job.get("benefits"))),
        ("Company", _first_useful(job.get("company_name"), job.get("company"), fallback="Not specified")),
        ("Industry", _first_useful(job.get("industry"), fallback="Not specified")),
        ("Company Size", _first_useful(job.get("company_size"), fallback="Not specified")),
        ("Application Link", link),
    ]
    return "\n\n".join(f"{label}:\n{value}" for label, value in sections)


def serialize_job_details(raw_job: dict[str, Any]) -> dict[str, Any]:
    job = dict(raw_job)
    for field in LIST_FIELDS:
        value = job.get(field)
        if isinstance(value, (list, tuple, set, dict)):
            job[field] = json.dumps(as_list(value), ensure_ascii=False)
        elif value is None:
            job[field] = None
        else:
            parsed = as_list(value)
            job[field] = json.dumps(parsed, ensure_ascii=False) if parsed else None
    return job
