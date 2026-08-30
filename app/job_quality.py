from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from app.database import get_setting


QUALITY_VERSION = "universal_quality_v1"

TITLE_ONLY_KEYWORDS = {
    "director",
    "vice president",
}

COMPANY_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "limited",
    "plc",
}

REQUIREMENT_CONTEXT_TERMS = {
    "at least",
    "background",
    "experience",
    "minimum",
    "must have",
    "preferred",
    "qualification",
    "qualifications",
    "required",
    "requirement",
    "requirements",
    "working in",
    "years of",
}

EXPERIENCE_PATTERN = re.compile(
    r"""
    \b
    (?:
        minimum\s+of\s+|
        at\s+least\s+
    )?
    \d+
    \s*
    (?:
        \+|
        (?:-|to)\s*\d+
    )?
    \s*
    years?
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize(
        "NFKD",
        str(value or ""),
    )

    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )

    text = (
        text.replace("\\-", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
        .casefold()
    )

    text = re.sub(
        r"(\d+)\s*-\s*(\d+)\s*years?",
        r"\1 to \2 years",
        text,
    )

    text = re.sub(
        r"(\d+)\s+to\s+(\d+)\s*years?",
        r"\1 to \2 years",
        text,
    )

    text = re.sub(
        r"(\d+)\s*\+\s*years?",
        r"\1+ years",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def meaningful_tokens(value: Any) -> list[str]:
    return re.findall(
        r"[a-z0-9]+",
        normalize_text(value),
    )


def canonical_company_name(value: Any) -> str:
    tokens = meaningful_tokens(value)

    while (
        tokens
        and tokens[-1] in COMPANY_SUFFIXES
    ):
        tokens.pop()

    return "".join(tokens)


def canonical_value(value: Any) -> str:
    return "".join(
        meaningful_tokens(value)
    )


def collect_job_text(
    job: dict[str, Any],
) -> str:
    fields = (
        "title",
        "description_raw",
        "responsibilities",
        "qualifications",
        "preferred_skills",
        "manual_job_text",
    )

    return "\n".join(
        str(job.get(field) or "")
        for field in fields
    )


def experience_requirement_phrases(
    text: Any,
) -> list[str]:
    normalized = normalize_text(text)
    phrases: list[str] = []

    for match in EXPERIENCE_PATTERN.finditer(
        normalized
    ):
        start = max(
            0,
            match.start() - 120,
        )

        end = min(
            len(normalized),
            match.end() + 120,
        )

        context = normalized[start:end]

        if not any(
            term in context
            for term in REQUIREMENT_CONTEXT_TERMS
        ):
            continue

        phrase = normalize_text(
            match.group(0)
        )

        if phrase not in phrases:
            phrases.append(phrase)

    return phrases


def configured_hard_reject_matches(
    job: dict[str, Any],
) -> dict[str, Any]:
    targeting = get_setting(
        "targeting",
        {},
    ) or {}

    configured = [
        str(value).strip()
        for value in (
            targeting.get(
                "rejected_keywords"
            )
            or []
        )
        if str(value).strip()
    ]

    title_text = normalize_text(
        job.get("title")
    )

    combined_text = normalize_text(
        collect_job_text(job)
    )

    experience_phrases = (
        experience_requirement_phrases(
            combined_text
        )
    )

    matches: list[str] = []

    for original_keyword in configured:
        keyword = normalize_text(
            original_keyword
        )

        if not keyword:
            continue

        if keyword in TITLE_ONLY_KEYWORDS:
            matched = keyword in title_text

        elif "year" in keyword:
            matched = any(
                keyword in phrase
                or phrase in keyword
                for phrase in experience_phrases
            )

            if (
                not matched
                and re.fullmatch(
                    r"\d+\s+years?",
                    keyword,
                )
            ):
                matched = any(
                    keyword in phrase
                    for phrase in experience_phrases
                )

        else:
            matched = keyword in combined_text

        if matched and original_keyword not in matches:
            matches.append(
                original_keyword
            )

    return {
        "matched_keywords": matches,
        "experience_phrases": (
            experience_phrases
        ),
    }


def _load_breakdown(
    value: Any,
) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)

    if not value:
        return {}

    try:
        loaded = json.loads(
            str(value)
        )
    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return {}

    return (
        loaded
        if isinstance(loaded, dict)
        else {}
    )


def apply_quality_gate(
    job: dict[str, Any],
) -> dict[str, Any]:
    result = configured_hard_reject_matches(
        job
    )

    matched_keywords = result[
        "matched_keywords"
    ]

    if not matched_keywords:
        return {}

    reason = (
        "dashboard_hard_reject: "
        + ", ".join(matched_keywords)
    )

    existing_reason = str(
        job.get(
            "hard_rejection_reason"
        )
        or ""
    ).strip()

    if (
        existing_reason
        and reason not in existing_reason
    ):
        reason = (
            existing_reason
            + " | "
            + reason
        )

    breakdown = _load_breakdown(
        job.get(
            "score_breakdown_json"
        )
    )

    breakdown.update(
        {
            "quality_gate_version": (
                QUALITY_VERSION
            ),
            "hard_reject_keywords": (
                matched_keywords
            ),
            "detected_experience_requirements": (
                result[
                    "experience_phrases"
                ]
            ),
            "hard_rejection_reason": (
                reason
            ),
            "final_score": 0.0,
        }
    )

    return {
        "hunter_score": 0.0,
        "match_label": "REJECTED",
        "status": "rejected",
        "hard_rejection_reason": reason,
        "score_breakdown_json": (
            json.dumps(
                breakdown,
                ensure_ascii=False,
            )
        ),
        "scoring_version": (
            QUALITY_VERSION
        ),
    }


def canonical_duplicate_payload(
    job: dict[str, Any],
) -> dict[str, str]:
    location_components = [
        canonical_value(
            job.get("city")
        ),
        canonical_value(
            job.get("state")
        ),
        canonical_value(
            job.get("country")
        ),
    ]

    location = "".join(
        value
        for value in location_components
        if value
    )

    if not location:
        location = canonical_value(
            job.get("location_raw")
        )

    salary = canonical_value(
        job.get("salary_raw")
    )

    if not salary:
        salary = "|".join(
            [
                str(
                    job.get(
                        "normalized_hourly_min"
                    )
                    or ""
                ),
                str(
                    job.get(
                        "normalized_hourly_max"
                    )
                    or ""
                ),
            ]
        )

    date_posted = str(
        job.get("date_posted")
        or ""
    ).strip()[:10]

    return {
        "company": canonical_company_name(
            job.get("company_name")
        ),
        "title": canonical_value(
            job.get("title")
        ),
        "location": location,
        "remote_type": canonical_value(
            job.get("remote_type")
        ),
        "salary": salary,
        "date_posted": date_posted,
    }


def create_canonical_job_fingerprint(
    job: dict[str, Any],
) -> str:
    payload = canonical_duplicate_payload(
        job
    )

    serialized = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()
