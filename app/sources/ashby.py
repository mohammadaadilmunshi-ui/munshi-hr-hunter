from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlparse

import requests

from app.ats_common import (
    clean_text,
    html_to_text,
    normalize_country,
    parse_location,
)


ASHBY_API_ROOT = (
    "https://api.ashbyhq.com/"
    "posting-api/job-board"
)


US_STATE_NAME_TO_CODE = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
    "washington dc": "DC",
    "washington, dc": "DC",
}


def normalize_state(
    value: Any,
) -> str | None:
    text = clean_text(value)

    if not text:
        return None

    if (
        len(text) == 2
        and text.isalpha()
    ):
        return text.upper()

    return US_STATE_NAME_TO_CODE.get(
        text.casefold(),
        text,
    )


def normalize_workplace_type(
    workplace_type: Any,
    is_remote: Any,
    location_text: Any,
) -> str:
    value = re.sub(
        r"[^a-z]",
        "",
        str(
            workplace_type or ""
        ).lower(),
    )

    mapping = {
        "remote": "Remote",
        "hybrid": "Hybrid",
        "onsite": "Onsite",
    }

    if value in mapping:
        return mapping[value]

    if bool(is_remote):
        return "Remote"

    location_lower = str(
        location_text or ""
    ).lower()

    if "hybrid" in location_lower:
        return "Hybrid"

    if "remote" in location_lower:
        return "Remote"

    return "Onsite"


def normalize_employment_type(
    value: Any,
) -> str:
    text = re.sub(
        r"[^a-z]",
        "",
        str(value or "").lower(),
    )

    mapping = {
        "fulltime": "Full-Time",
        "parttime": "Part-Time",
        "intern": "Internship",
        "internship": "Internship",
        "contract": "Contract",
        "temporary": "Temporary",
    }

    return mapping.get(
        text,
        clean_text(value)
        or "Not specified",
    )


def extract_job_identifier(
    raw_job: dict[str, Any],
) -> str | None:
    explicit_id = clean_text(
        raw_job.get("id")
    )

    if explicit_id:
        return explicit_id

    for field in (
        "jobUrl",
        "applyUrl",
    ):
        url = clean_text(
            raw_job.get(field)
        )

        if not url:
            continue

        path = urlparse(url).path.strip("/")

        if not path:
            continue

        pieces = [
            piece
            for piece in path.split("/")
            if piece
        ]

        if not pieces:
            continue

        if pieces[-1].lower() == "apply":
            pieces = pieces[:-1]

        if pieces:
            return pieces[-1]

    return None


def normalize_address(
    raw_job: dict[str, Any],
) -> dict[str, str | None]:
    location_text = clean_text(
        raw_job.get("location")
    )

    parsed = parse_location(
        location_text
    )

    address = raw_job.get("address")

    if not isinstance(address, dict):
        address = {}

    postal = address.get(
        "postalAddress"
    )

    if not isinstance(postal, dict):
        postal = {}

    city = (
        clean_text(
            postal.get(
                "addressLocality"
            )
        )
        or parsed.get("city")
    )

    state = (
        normalize_state(
            postal.get(
                "addressRegion"
            )
        )
        or parsed.get("state")
    )

    country_raw = (
        clean_text(
            postal.get(
                "addressCountry"
            )
        )
        or parsed.get("country")
        or "US"
    )

    country = normalize_country(
        country_raw
    )

    return {
        "location_raw": location_text,
        "city": city,
        "state": state,
        "country": country,
    }


def parse_compensation(
    compensation: Any,
) -> dict[str, Any]:
    if not isinstance(
        compensation,
        dict,
    ):
        return {
            "salary_raw": None,
            "normalized_hourly_min": None,
            "normalized_hourly_max": None,
            "salary_confidence": "unknown",
        }

    salary_raw = (
        clean_text(
            compensation.get(
                "scrapeableCompensationSalarySummary"
            )
        )
        or clean_text(
            compensation.get(
                "compensationTierSummary"
            )
        )
    )

    hourly_min = None
    hourly_max = None

    components = compensation.get(
        "summaryComponents"
    )

    if not isinstance(
        components,
        list,
    ):
        components = []

    for component in components:
        if not isinstance(
            component,
            dict,
        ):
            continue

        compensation_type = re.sub(
            r"[^a-z]",
            "",
            str(
                component.get(
                    "compensationType"
                )
                or ""
            ).lower(),
        )

        interval = re.sub(
            r"[^a-z]",
            "",
            str(
                component.get("interval")
                or ""
            ).lower(),
        )

        if (
            compensation_type
            not in {
                "salary",
                "hourly",
                "hourlyrate",
            }
        ):
            continue

        if interval not in {
            "hour",
            "onehour",
            "1hour",
        }:
            continue

        try:
            hourly_min = (
                float(
                    component.get(
                        "minValue"
                    )
                )
                if component.get(
                    "minValue"
                )
                is not None
                else None
            )
        except (
            TypeError,
            ValueError,
        ):
            hourly_min = None

        try:
            hourly_max = (
                float(
                    component.get(
                        "maxValue"
                    )
                )
                if component.get(
                    "maxValue"
                )
                is not None
                else None
            )
        except (
            TypeError,
            ValueError,
        ):
            hourly_max = None

        break

    return {
        "salary_raw": salary_raw,
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


def normalize_secondary_locations(
    value: Any,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    output: list[
        dict[str, Any]
    ] = []

    for item in value:
        if not isinstance(
            item,
            dict,
        ):
            continue

        address = item.get("address")

        if not isinstance(
            address,
            dict,
        ):
            address = {}

        output.append(
            {
                "location": clean_text(
                    item.get(
                        "location"
                    )
                ),
                "city": clean_text(
                    address.get(
                        "addressLocality"
                    )
                ),
                "state": normalize_state(
                    address.get(
                        "addressRegion"
                    )
                ),
                "country": (
                    normalize_country(
                        address.get(
                            "addressCountry"
                        )
                        or "US"
                    )
                ),
            }
        )

    return output


def normalize_ashby_job(
    *,
    company_name: str,
    board_name: str,
    raw_job: dict[str, Any],
) -> dict[str, Any]:
    location = normalize_address(
        raw_job
    )

    description = (
        clean_text(
            raw_job.get(
                "descriptionPlain"
            )
        )
        or html_to_text(
            raw_job.get(
                "descriptionHtml"
            )
        )
        or "Not specified"
    )

    compensation = parse_compensation(
        raw_job.get("compensation")
    )

    return {
        "source": (
            f"Ashby/{board_name}"
        ),
        "source_tier": 1,
        "ats_job_id": (
            extract_job_identifier(
                raw_job
            )
        ),
        "company_name": company_name,
        "title": (
            clean_text(
                raw_job.get("title")
            )
            or "Unknown Position"
        ),
        **location,
        "remote_type": (
            normalize_workplace_type(
                raw_job.get(
                    "workplaceType"
                ),
                raw_job.get(
                    "isRemote"
                ),
                raw_job.get(
                    "location"
                ),
            )
        ),
        "employment_type": (
            normalize_employment_type(
                raw_job.get(
                    "employmentType"
                )
            )
        ),
        "job_url": clean_text(
            raw_job.get("jobUrl")
        ),
        "apply_url": (
            clean_text(
                raw_job.get("applyUrl")
            )
            or clean_text(
                raw_job.get("jobUrl")
            )
        ),
        "description_raw": description,
        "responsibilities": None,
        "qualifications": None,
        "preferred_skills": None,
        "work_authorization": None,
        "benefits": None,
        **compensation,
        "date_posted": clean_text(
            raw_job.get(
                "publishedAt"
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
        "ashby_department": (
            clean_text(
                raw_job.get(
                    "department"
                )
            )
        ),
        "ashby_team": clean_text(
            raw_job.get("team")
        ),
        "ashby_is_listed": bool(
            raw_job.get(
                "isListed",
                True,
            )
        ),
        "ashby_secondary_locations": (
            normalize_secondary_locations(
                raw_job.get(
                    "secondaryLocations"
                )
            )
        ),
    }


def parse_ashby_payload(
    *,
    company_name: str,
    board_name: str,
    payload: Any,
    include_unlisted: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Ashby response was not "
            "a valid JSON object."
        )

    jobs = payload.get("jobs")

    if not isinstance(
        jobs,
        list,
    ):
        raise ValueError(
            "Ashby response did not "
            "contain a valid jobs list."
        )

    output: list[
        dict[str, Any]
    ] = []

    for raw_job in jobs:
        if not isinstance(
            raw_job,
            dict,
        ):
            continue

        if (
            raw_job.get(
                "isListed"
            )
            is False
            and not include_unlisted
        ):
            continue

        output.append(
            normalize_ashby_job(
                company_name=company_name,
                board_name=board_name,
                raw_job=raw_job,
            )
        )

    return output


def fetch_ashby_jobs(
    *,
    company_name: str,
    board_name: str,
    include_compensation: bool = True,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    clean_board_name = str(
        board_name or ""
    ).strip()

    if not clean_board_name:
        raise ValueError(
            "Ashby job board name is required."
        )

    encoded_board_name = quote(
        clean_board_name,
        safe="",
    )

    url = (
        f"{ASHBY_API_ROOT}/"
        f"{encoded_board_name}"
    )

    response = requests.get(
        url,
        params={
            "includeCompensation": (
                "true"
                if include_compensation
                else "false"
            ),
        },
        timeout=max(
            5,
            min(
                int(timeout_seconds),
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

    payload = response.json()

    jobs = parse_ashby_payload(
        company_name=company_name,
        board_name=clean_board_name,
        payload=payload,
    )

    return {
        "success": True,
        "source": "Ashby",
        "company_name": company_name,
        "board_name": clean_board_name,
        "http_status": (
            response.status_code
        ),
        "api_version": (
            payload.get("apiVersion")
            if isinstance(
                payload,
                dict,
            )
            else None
        ),
        "jobs_found": len(jobs),
        "jobs": jobs,
        "errors": [],
    }
