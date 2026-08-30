from __future__ import annotations

from typing import Any

import requests

from app.ats_common import (
    clean_text,
    html_to_text,
    infer_remote_type,
    normalize_country,
    parse_location,
)


LEVER_GLOBAL_API_ROOT = (
    "https://api.lever.co/v0/postings"
)

LEVER_EU_API_ROOT = (
    "https://api.eu.lever.co/v0/postings"
)


def resolve_lever_api_root(
    careers_url: str | None,
) -> str:
    url = str(
        careers_url or ""
    ).strip().lower()

    if (
        "jobs.eu.lever.co" in url
        or "api.eu.lever.co" in url
    ):
        return LEVER_EU_API_ROOT

    return LEVER_GLOBAL_API_ROOT


def normalize_workplace_type(
    workplace_type: Any,
    location: Any,
    description: Any,
) -> str:
    value = str(
        workplace_type or ""
    ).strip().lower()

    mapping = {
        "remote": "Remote",
        "hybrid": "Hybrid",
        "on-site": "Onsite",
        "onsite": "Onsite",
        "unspecified": "",
    }

    normalized = mapping.get(
        value,
        "",
    )

    if normalized:
        return normalized

    return infer_remote_type(
        location,
        description,
    )


def salary_interval_label(
    interval: Any,
) -> str | None:
    text = clean_text(interval)

    if not text:
        return None

    value = (
        text.lower()
        .replace("per-", "")
        .replace("-salary", "")
        .replace("_", " ")
        .replace("-", " ")
        .strip()
    )

    aliases = {
        "year": "year",
        "annual": "year",
        "annually": "year",
        "month": "month",
        "monthly": "month",
        "week": "week",
        "weekly": "week",
        "day": "day",
        "daily": "day",
        "hour": "hour",
        "hourly": "hour",
    }

    return aliases.get(
        value,
        value or None,
    )


def normalize_salary(
    salary_range: Any,
    salary_description: Any,
) -> dict[str, Any]:
    description = clean_text(
        salary_description
    )

    if not isinstance(
        salary_range,
        dict,
    ):
        return {
            "salary_raw": description,
            "normalized_hourly_min": None,
            "normalized_hourly_max": None,
            "salary_confidence": (
                "medium"
                if description
                else "unknown"
            ),
        }

    currency = clean_text(
        salary_range.get("currency")
    )

    interval = salary_interval_label(
        salary_range.get("interval")
    )

    minimum = salary_range.get("min")
    maximum = salary_range.get("max")

    try:
        minimum_number = (
            float(minimum)
            if minimum is not None
            else None
        )
    except (
        TypeError,
        ValueError,
    ):
        minimum_number = None

    try:
        maximum_number = (
            float(maximum)
            if maximum is not None
            else None
        )
    except (
        TypeError,
        ValueError,
    ):
        maximum_number = None

    salary_parts: list[str] = []

    if currency:
        salary_parts.append(currency)

    if (
        minimum_number is not None
        and maximum_number is not None
    ):
        salary_parts.append(
            f"{minimum_number:g} - "
            f"{maximum_number:g}"
        )
    elif minimum_number is not None:
        salary_parts.append(
            f"From {minimum_number:g}"
        )
    elif maximum_number is not None:
        salary_parts.append(
            f"Up to {maximum_number:g}"
        )

    if interval:
        salary_parts.append(
            f"per {interval}"
        )

    salary_raw = (
        " ".join(salary_parts).strip()
        or description
    )

    hourly_min = None
    hourly_max = None

    if interval == "hour":
        hourly_min = minimum_number
        hourly_max = maximum_number

    return {
        "salary_raw": (
            salary_raw or None
        ),
        "normalized_hourly_min": (
            hourly_min
        ),
        "normalized_hourly_max": (
            hourly_max
        ),
        "salary_confidence": (
            "high"
            if salary_raw
            else "unknown"
        ),
    }


def normalize_lever_lists(
    raw_lists: Any,
) -> dict[str, Any]:
    responsibilities: list[str] = []
    qualifications: list[str] = []
    preferred_skills: list[str] = []
    benefits: list[str] = []
    other_sections: list[str] = []

    if not isinstance(raw_lists, list):
        raw_lists = []

    for item in raw_lists:
        if not isinstance(item, dict):
            continue

        label = str(
            item.get("text") or ""
        ).strip()

        content = html_to_text(
            item.get("content")
        )

        if not content:
            continue

        combined = (
            f"{label}\n{content}"
            if label
            else content
        )

        label_lower = label.lower()

        if any(
            token in label_lower
            for token in (
                "responsibil",
                "what you'll do",
                "what you will do",
                "duties",
                "role",
            )
        ):
            responsibilities.append(
                combined
            )

        elif any(
            token in label_lower
            for token in (
                "qualification",
                "requirement",
                "what you bring",
                "who you are",
                "experience",
            )
        ):
            qualifications.append(
                combined
            )

        elif any(
            token in label_lower
            for token in (
                "preferred",
                "nice to have",
                "bonus",
            )
        ):
            preferred_skills.append(
                combined
            )

        elif any(
            token in label_lower
            for token in (
                "benefit",
                "perk",
                "compensation",
            )
        ):
            benefits.append(
                combined
            )

        else:
            other_sections.append(
                combined
            )

    return {
        "responsibilities": (
            "\n\n".join(
                responsibilities
            )
            or None
        ),
        "qualifications": (
            "\n\n".join(
                qualifications
            )
            or None
        ),
        "preferred_skills": (
            "\n\n".join(
                preferred_skills
            )
            or None
        ),
        "benefits": (
            "\n\n".join(
                benefits
            )
            or None
        ),
        "other_sections": (
            "\n\n".join(
                other_sections
            )
            or None
        ),
    }


def normalize_lever_job(
    *,
    company_name: str,
    site_name: str,
    raw_job: dict[str, Any],
) -> dict[str, Any]:
    categories = raw_job.get(
        "categories"
    )

    if not isinstance(
        categories,
        dict,
    ):
        categories = {}

    location_name = clean_text(
        categories.get("location")
    )

    all_locations = (
        categories.get("allLocations")
    )

    if (
        not location_name
        and isinstance(
            all_locations,
            list,
        )
        and all_locations
    ):
        location_name = clean_text(
            all_locations[0]
        )

    location = parse_location(
        location_name
    )

    raw_country = clean_text(
        raw_job.get("country")
    )

    if raw_country:
        location["country"] = (
            normalize_country(
                raw_country
            )
        )

    description = (
        clean_text(
            raw_job.get(
                "descriptionPlain"
            )
        )
        or html_to_text(
            raw_job.get(
                "description"
            )
        )
        or clean_text(
            raw_job.get(
                "openingPlain"
            )
        )
        or html_to_text(
            raw_job.get("opening")
        )
    )

    list_sections = (
        normalize_lever_lists(
            raw_job.get("lists")
        )
    )

    additional = (
        clean_text(
            raw_job.get(
                "additionalPlain"
            )
        )
        or html_to_text(
            raw_job.get("additional")
        )
    )

    description_parts = [
        part
        for part in (
            description,
            list_sections[
                "other_sections"
            ],
            additional,
        )
        if part
    ]

    full_description = (
        "\n\n".join(
            description_parts
        )
        or "Not specified"
    )

    salary = normalize_salary(
        raw_job.get("salaryRange"),
        (
            raw_job.get(
                "salaryDescriptionPlain"
            )
            or html_to_text(
                raw_job.get(
                    "salaryDescription"
                )
            )
        ),
    )

    hosted_url = clean_text(
        raw_job.get("hostedUrl")
    )

    apply_url = (
        clean_text(
            raw_job.get("applyUrl")
        )
        or hosted_url
    )

    commitment = clean_text(
        categories.get("commitment")
    )

    team = clean_text(
        categories.get("team")
    )

    department = clean_text(
        categories.get("department")
    )

    level = clean_text(
        categories.get("level")
    )

    return {
        "source": (
            f"Lever/{site_name}"
        ),
        "source_tier": 1,
        "ats_job_id": clean_text(
            raw_job.get("id")
        ),
        "company_name": (
            company_name
        ),
        "title": (
            clean_text(
                raw_job.get("text")
            )
            or "Unknown Position"
        ),
        **location,
        "remote_type": (
            normalize_workplace_type(
                raw_job.get(
                    "workplaceType"
                ),
                location_name,
                full_description,
            )
        ),
        "employment_type": (
            commitment
            or "Not specified"
        ),
        "job_url": hosted_url,
        "apply_url": apply_url,
        "description_raw": (
            full_description
        ),
        "responsibilities": (
            list_sections[
                "responsibilities"
            ]
        ),
        "qualifications": (
            list_sections[
                "qualifications"
            ]
        ),
        "preferred_skills": (
            list_sections[
                "preferred_skills"
            ]
        ),
        "work_authorization": None,
        "benefits": (
            list_sections["benefits"]
        ),
        **salary,
        "date_posted": (
            clean_text(
                raw_job.get("createdAt")
            )
            or clean_text(
                raw_job.get("updatedAt")
            )
        ),
        "apply_deadline": None,
        "start_date": None,
        "end_date": None,
        "hours_per_week": None,
        "recruiter": None,
        "recruiter_email": None,
        "company_size": None,
        "industry": None,
        "employer_description": None,
        "lever_team": team,
        "lever_department": department,
        "lever_level": level,
        "lever_all_locations": (
            all_locations
            if isinstance(
                all_locations,
                list,
            )
            else []
        ),
    }


def parse_lever_payload(
    *,
    company_name: str,
    site_name: str,
    payload: Any,
) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError(
            "Lever response was not a "
            "valid postings list."
        )

    return [
        normalize_lever_job(
            company_name=company_name,
            site_name=site_name,
            raw_job=raw_job,
        )
        for raw_job in payload
        if isinstance(
            raw_job,
            dict,
        )
    ]


def fetch_lever_jobs(
    *,
    company_name: str,
    site_name: str,
    careers_url: str | None = None,
    page_size: int = 100,
    max_pages: int = 10,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    clean_site = str(
        site_name or ""
    ).strip()

    if not clean_site:
        raise ValueError(
            "Lever site name is required."
        )

    page_size = max(
        1,
        min(int(page_size), 100),
    )

    max_pages = max(
        1,
        min(int(max_pages), 50),
    )

    api_root = resolve_lever_api_root(
        careers_url
    )

    url = (
        f"{api_root}/{clean_site}"
    )

    raw_jobs: list[
        dict[str, Any]
    ] = []

    response_statuses: list[int] = []

    for page in range(max_pages):
        skip = page * page_size

        response = requests.get(
            url,
            params={
                "mode": "json",
                "skip": skip,
                "limit": page_size,
            },
            timeout=max(
                5,
                min(
                    timeout_seconds,
                    60,
                ),
            ),
            headers={
                "User-Agent": (
                    "Aadil-HR-Hunter/1.0"
                ),
                "Accept": (
                    "application/json"
                ),
            },
        )

        response.raise_for_status()

        response_statuses.append(
            response.status_code
        )

        payload = response.json()

        if not isinstance(
            payload,
            list,
        ):
            raise ValueError(
                "Lever response was not a "
                "valid postings list."
            )

        raw_jobs.extend(payload)

        if len(payload) < page_size:
            break

    jobs = parse_lever_payload(
        company_name=company_name,
        site_name=clean_site,
        payload=raw_jobs,
    )

    return {
        "success": True,
        "source": "Lever",
        "company_name": company_name,
        "site_name": clean_site,
        "api_instance": (
            "eu"
            if api_root
            == LEVER_EU_API_ROOT
            else "global"
        ),
        "http_status": (
            response_statuses[-1]
            if response_statuses
            else None
        ),
        "pages_fetched": len(
            response_statuses
        ),
        "jobs_found": len(jobs),
        "jobs": jobs,
        "errors": [],
    }
