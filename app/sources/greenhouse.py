from __future__ import annotations

from typing import Any

import requests

from app.ats_common import (
    clean_text,
    html_to_text,
    infer_remote_type,
    parse_location,
)


GREENHOUSE_API_ROOT = (
    "https://boards-api.greenhouse.io/v1/boards"
)


def normalize_greenhouse_job(
    *,
    company_name: str,
    board_token: str,
    raw_job: dict[str, Any],
) -> dict[str, Any]:
    location_name = (
        raw_job.get("location") or {}
    ).get("name")

    location = parse_location(
        location_name
    )

    description = html_to_text(
        raw_job.get("content")
    )

    absolute_url = clean_text(
        raw_job.get("absolute_url")
    )

    job_id = clean_text(
        raw_job.get("id")
    )

    departments = [
        str(item.get("name")).strip()
        for item in (
            raw_job.get("departments")
            or []
        )
        if item.get("name")
    ]

    offices = [
        str(
            item.get("location")
            or item.get("name")
        ).strip()
        for item in (
            raw_job.get("offices")
            or []
        )
        if (
            item.get("location")
            or item.get("name")
        )
    ]

    return {
        "source": (
            f"Greenhouse/{board_token}"
        ),
        "source_tier": 1,
        "ats_job_id": job_id,
        "company_name": company_name,
        "title": clean_text(
            raw_job.get("title")
        ) or "Unknown Position",
        **location,
        "remote_type": infer_remote_type(
            location_name,
            description,
        ),
        "employment_type": (
            "Not specified"
        ),
        "job_url": absolute_url,
        "apply_url": absolute_url,
        "description_raw": (
            description
            or "Not specified"
        ),
        "responsibilities": None,
        "qualifications": None,
        "preferred_skills": None,
        "work_authorization": None,
        "benefits": None,
        "salary_raw": None,
        "normalized_hourly_min": None,
        "normalized_hourly_max": None,
        "salary_confidence": "unknown",
        "date_posted": clean_text(
            raw_job.get("updated_at")
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
        "greenhouse_departments": (
            departments
        ),
        "greenhouse_offices": offices,
    }


def parse_greenhouse_payload(
    *,
    company_name: str,
    board_token: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    jobs = payload.get("jobs")

    if not isinstance(jobs, list):
        raise ValueError(
            "Greenhouse response did not contain "
            "a valid jobs list."
        )

    return [
        normalize_greenhouse_job(
            company_name=company_name,
            board_token=board_token,
            raw_job=raw_job,
        )
        for raw_job in jobs
        if isinstance(
            raw_job,
            dict,
        )
    ]


def fetch_greenhouse_jobs(
    *,
    company_name: str,
    board_token: str,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    clean_token = str(
        board_token or ""
    ).strip()

    if not clean_token:
        raise ValueError(
            "Greenhouse board token is required."
        )

    url = (
        f"{GREENHOUSE_API_ROOT}/"
        f"{clean_token}/jobs"
    )

    response = requests.get(
        url,
        params={
            "content": "true",
        },
        timeout=max(
            5,
            min(timeout_seconds, 60),
        ),
        headers={
            "User-Agent": (
                "Aadil-HR-Hunter/1.0"
            ),
            "Accept": "application/json",
        },
    )

    response.raise_for_status()

    payload = response.json()

    jobs = parse_greenhouse_payload(
        company_name=company_name,
        board_token=clean_token,
        payload=payload,
    )

    return {
        "success": True,
        "source": "Greenhouse",
        "company_name": company_name,
        "board_token": clean_token,
        "http_status": (
            response.status_code
        ),
        "jobs_found": len(jobs),
        "jobs": jobs,
        "errors": [],
    }
