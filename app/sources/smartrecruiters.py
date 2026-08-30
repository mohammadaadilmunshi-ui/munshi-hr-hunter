from __future__ import annotations

import html
import os
import re
import time
from typing import Any
from urllib.parse import quote

import requests


SOURCE_NAME = "SmartRecruiters"

BASE_URL = (
    "https://api.smartrecruiters.com"
    "/v1/companies"
)

DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 100


def clean_html(value: Any) -> str:
    text = html.unescape(
        str(value or "")
    )

    text = re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"</p\s*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = text.replace(
        "\u00a0",
        " ",
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = re.sub(
        r"\n\s*\n+",
        "\n\n",
        text,
    )

    return text.strip()


def request_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Aadil-HR-Hunter/1.0"
        ),
    }

    api_key = os.getenv(
        "SMARTRECRUITERS_API_KEY",
        "",
    ).strip()

    if api_key:
        headers[
            "X-SmartToken"
        ] = api_key

    return headers


def company_postings_url(
    company_identifier: str,
) -> str:
    identifier = quote(
        company_identifier.strip(),
        safe="",
    )

    return (
        f"{BASE_URL}/"
        f"{identifier}/postings"
    )


def posting_detail_url(
    company_identifier: str,
    posting_id: str,
) -> str:
    identifier = quote(
        company_identifier.strip(),
        safe="",
    )

    posting = quote(
        str(posting_id).strip(),
        safe="",
    )

    return (
        f"{BASE_URL}/"
        f"{identifier}/postings/"
        f"{posting}"
    )


def fetch_postings_page(
    company_identifier: str,
    *,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    timeout_seconds: int = (
        DEFAULT_TIMEOUT_SECONDS
    ),
    session: Any = requests,
) -> dict[str, Any]:
    if not company_identifier.strip():
        raise ValueError(
            "SmartRecruiters company "
            "identifier is required."
        )

    page_size = max(
        1,
        min(
            int(limit),
            MAX_PAGE_SIZE,
        ),
    )

    started = time.perf_counter()

    response = session.get(
        company_postings_url(
            company_identifier
        ),
        params={
            "offset": max(
                0,
                int(offset),
            ),
            "limit": page_size,
        },
        headers=request_headers(),
        timeout=timeout_seconds,
    )

    elapsed_ms = round(
        (
            time.perf_counter()
            - started
        )
        * 1000,
        2,
    )

    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError(
            "SmartRecruiters list response "
            "was not a JSON object."
        )

    content = payload.get(
        "content",
        [],
    )

    if not isinstance(content, list):
        raise ValueError(
            "SmartRecruiters response "
            "content was not a list."
        )

    return {
        "http_status": (
            response.status_code
        ),
        "response_ms": elapsed_ms,
        "offset": int(
            payload.get(
                "offset",
                offset,
            )
            or 0
        ),
        "limit": int(
            payload.get(
                "limit",
                page_size,
            )
            or page_size
        ),
        "total_found": int(
            payload.get(
                "totalFound",
                len(content),
            )
            or 0
        ),
        "content": content,
    }


def fetch_all_postings(
    company_identifier: str,
    *,
    max_pages: int = 10,
    timeout_seconds: int = (
        DEFAULT_TIMEOUT_SECONDS
    ),
    session: Any = requests,
) -> dict[str, Any]:
    postings: list[dict[str, Any]] = []
    page_results: list[
        dict[str, Any]
    ] = []

    offset = 0

    for _ in range(
        max(
            1,
            int(max_pages),
        )
    ):
        page = fetch_postings_page(
            company_identifier,
            offset=offset,
            limit=DEFAULT_PAGE_SIZE,
            timeout_seconds=(
                timeout_seconds
            ),
            session=session,
        )

        content = page["content"]

        postings.extend(
            item
            for item in content
            if isinstance(item, dict)
        )

        page_results.append(
            {
                "offset": page[
                    "offset"
                ],
                "jobs": len(content),
                "http_status": page[
                    "http_status"
                ],
                "response_ms": page[
                    "response_ms"
                ],
            }
        )

        total_found = page[
            "total_found"
        ]

        if (
            not content
            or len(postings)
            >= total_found
        ):
            break

        offset += len(content)

    return {
        "company_identifier": (
            company_identifier
        ),
        "total_found": (
            page_results[0].get(
                "jobs",
                0,
            )
            if not page_results
            else max(
                len(postings),
                page.get(
                    "total_found",
                    len(postings),
                ),
            )
        ),
        "postings": postings,
        "pages": page_results,
        "network_requests": len(
            page_results
        ),
    }


def fetch_posting_detail(
    company_identifier: str,
    posting_id: str,
    *,
    timeout_seconds: int = (
        DEFAULT_TIMEOUT_SECONDS
    ),
    session: Any = requests,
) -> dict[str, Any]:
    started = time.perf_counter()

    response = session.get(
        posting_detail_url(
            company_identifier,
            posting_id,
        ),
        headers=request_headers(),
        timeout=timeout_seconds,
    )

    elapsed_ms = round(
        (
            time.perf_counter()
            - started
        )
        * 1000,
        2,
    )

    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError(
            "SmartRecruiters detail "
            "response was not an object."
        )

    return {
        "http_status": (
            response.status_code
        ),
        "response_ms": elapsed_ms,
        "posting": payload,
    }


def section_text(
    detail: dict[str, Any],
    section_name: str,
) -> str:
    sections = (
        detail.get("jobAd", {})
        .get("sections", {})
    )

    section = sections.get(
        section_name,
        {},
    )

    if not isinstance(section, dict):
        return ""

    return clean_html(
        section.get("text")
    )


def label_value(
    value: Any,
) -> str | None:
    if isinstance(value, dict):
        label = str(
            value.get("label")
            or ""
        ).strip()

        return label or None

    text = str(
        value or ""
    ).strip()

    return text or None


def normalize_posting(
    summary: dict[str, Any],
    detail: dict[str, Any] | None = None,
    *,
    registry_company_name: str | None = None,
    company_identifier: str,
) -> dict[str, Any]:
    full = detail or summary

    company = (
        full.get("company")
        or summary.get("company")
        or {}
    )

    location = (
        full.get("location")
        or summary.get("location")
        or {}
    )

    company_name = str(
        company.get("name")
        or registry_company_name
        or company_identifier
    ).strip()

    title = str(
        full.get("name")
        or summary.get("name")
        or "Unknown Position"
    ).strip()

    city = str(
        location.get("city")
        or ""
    ).strip() or None

    state = str(
        location.get("region")
        or ""
    ).strip() or None

    country = str(
        location.get("country")
        or "US"
    ).strip().upper()

    location_parts = [
        value
        for value in (
            city,
            state,
            country,
        )
        if value
    ]

    location_raw = (
        ", ".join(location_parts)
        or "Not specified"
    )

    remote_value = location.get(
        "remote"
    )

    if remote_value is True:
        remote_type = "Remote"
    elif remote_value is False:
        remote_type = "Onsite"
    else:
        remote_type = (
            "Not specified"
        )

    descriptions = [
        section_text(
            full,
            "companyDescription",
        ),
        section_text(
            full,
            "jobDescription",
        ),
        section_text(
            full,
            "qualifications",
        ),
        section_text(
            full,
            "additionalInformation",
        ),
    ]

    description_raw = "\n\n".join(
        value
        for value in descriptions
        if value
    )

    if not description_raw:
        description_raw = (
            "Not specified"
        )

    qualifications = section_text(
        full,
        "qualifications",
    )

    responsibilities = section_text(
        full,
        "jobDescription",
    )

    posting_id = str(
        full.get("id")
        or summary.get("id")
        or full.get("uuid")
        or summary.get("uuid")
        or ""
    ).strip()

    apply_url = str(
        full.get("applyUrl")
        or summary.get("applyUrl")
        or ""
    ).strip() or None

    public_url = (
        apply_url
        or str(
            summary.get("ref")
            or ""
        ).strip()
        or None
    )

    released_date = str(
        full.get("releasedDate")
        or summary.get(
            "releasedDate"
        )
        or ""
    ).strip()

    date_posted = (
        released_date[:10]
        if released_date
        else None
    )

    employment_type = (
        label_value(
            full.get(
                "typeOfEmployment"
            )
        )
        or label_value(
            summary.get(
                "typeOfEmployment"
            )
        )
        or "Not specified"
    )

    experience_level = (
        label_value(
            full.get(
                "experienceLevel"
            )
        )
        or label_value(
            summary.get(
                "experienceLevel"
            )
        )
    )

    department = (
        label_value(
            full.get("department")
        )
        or label_value(
            summary.get(
                "department"
            )
        )
    )

    return {
        "source": (
            f"{SOURCE_NAME}/"
            f"{company_identifier}"
        ),
        "source_tier": 1,
        "ats_job_id": (
            posting_id or None
        ),
        "company_name": company_name,
        "title": title,
        "location_raw": (
            location_raw
        ),
        "city": city,
        "state": state,
        "country": country,
        "remote_type": remote_type,
        "employment_type": (
            employment_type
        ),
        "job_url": public_url,
        "apply_url": apply_url,
        "description_raw": (
            description_raw
        ),
        "responsibilities": (
            responsibilities or None
        ),
        "qualifications": (
            qualifications or None
        ),
        "preferred_skills": None,
        "salary_raw": None,
        "normalized_hourly_min": (
            None
        ),
        "normalized_hourly_max": (
            None
        ),
        "salary_confidence": (
            "unknown"
        ),
        "date_posted": date_posted,
        "apply_deadline": None,
        "start_date": None,
        "end_date": None,
        "work_authorization": (
            description_raw
        ),
        "department": department,
        "experience_level": (
            experience_level
        ),
        "smartrecruiters_company_identifier": (
            company_identifier
        ),
    }
