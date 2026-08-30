from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.sources.jobspy import fetch_jobspy_jobs


SEARCH_TERM = (
    '"human resources intern" OR '
    '"talent acquisition intern" OR '
    '"HRIS intern" OR '
    '"people analytics intern" OR '
    '"HR analytics intern" OR '
    '"people operations intern" OR '
    '"HR operations intern" OR '
    '"recruiting coordinator"'
)

SEARCH_LOCATIONS = [
    "New Jersey",
    "New York, NY",
]

ALLOWED_STATES = {
    "NJ",
    "NY",
}


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def canonical_url(value: Any) -> str:
    raw_url = str(value or "").strip()

    if not raw_url:
        return ""

    try:
        parts = urlsplit(raw_url)

        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path.rstrip("/"),
                "",
                "",
            )
        )
    except ValueError:
        return raw_url.lower()


def extract_state(
    job: dict[str, Any],
) -> str:
    direct_state = str(
        job.get("state") or ""
    ).strip().upper()

    if direct_state:
        return direct_state

    location_parts = [
        part.strip().upper()
        for part in str(
            job.get("location_raw") or ""
        ).split(",")
        if part.strip()
    ]

    for part in reversed(location_parts):
        if part in {
            "US",
            "USA",
            "UNITED STATES",
        }:
            continue

        if len(part) == 2:
            return part

    return ""


def semantic_key(
    job: dict[str, Any],
) -> tuple[str, str, str]:
    return (
        normalize_text(job.get("company_name")),
        normalize_text(job.get("title")),
        extract_state(job),
    )


def location_allowed(
    job: dict[str, Any],
) -> tuple[bool, str]:
    state = extract_state(job)

    if state in ALLOWED_STATES:
        return True, "allowed_state"

    return False, (
        f"outside_allowed_area:"
        f"{state or 'unknown'}"
    )


def main() -> None:
    fetched_jobs: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    source_errors: list[dict[str, Any]] = []

    for location in SEARCH_LOCATIONS:
        result = fetch_jobspy_jobs(
            sites=["indeed"],
            search_term=SEARCH_TERM,
            location=location,
            results_wanted=10,
            hours_old=48,
            job_type="",
            remote_only=False,
        )

        jobs = result.get("jobs") or []

        source_results.append(
            {
                "location": location,
                "jobs_found": len(jobs),
                "success": result.get("success"),
                "partial_success": result.get(
                    "partial_success",
                    False,
                ),
            }
        )

        for error in result.get("errors") or []:
            source_errors.append(
                {
                    "location": location,
                    **error,
                }
            )

        for job in jobs:
            job["_search_location"] = location
            fetched_jobs.append(job)

    allowed_jobs: list[dict[str, Any]] = []
    excluded_jobs: list[dict[str, Any]] = []

    for job in fetched_jobs:
        allowed, reason = location_allowed(job)

        if allowed:
            allowed_jobs.append(job)
        else:
            excluded_jobs.append(
                {
                    "title": job.get("title"),
                    "company": job.get(
                        "company_name"
                    ),
                    "location": job.get(
                        "location_raw"
                    ),
                    "reason": reason,
                }
            )

    unique_jobs: list[dict[str, Any]] = []
    duplicate_jobs: list[dict[str, Any]] = []

    seen_urls: set[str] = set()
    seen_semantic_keys: set[
        tuple[str, str, str]
    ] = set()

    for job in allowed_jobs:
        url_key = canonical_url(
            job.get("apply_url")
            or job.get("job_url")
        )

        semantic = semantic_key(job)

        duplicate_reason: str | None = None

        if url_key and url_key in seen_urls:
            duplicate_reason = "same_apply_url"
        elif semantic in seen_semantic_keys:
            duplicate_reason = (
                "same_company_title_state"
            )

        if duplicate_reason:
            duplicate_jobs.append(
                {
                    "title": job.get("title"),
                    "company": job.get(
                        "company_name"
                    ),
                    "location": job.get(
                        "location_raw"
                    ),
                    "reason": duplicate_reason,
                }
            )
            continue

        if url_key:
            seen_urls.add(url_key)

        seen_semantic_keys.add(semantic)
        unique_jobs.append(job)

    output = {
        "success": not source_errors,
        "mode": "jobspy-filtered-dry-run",
        "freshness_hours": 48,
        "allowed_states": sorted(
            ALLOWED_STATES
        ),
        "database_writes": 0,
        "telegram_messages": 0,
        "n8n_calls": 0,
        "source_results": source_results,
        "raw_jobs_fetched": len(fetched_jobs),
        "jobs_after_location_filter": len(
            allowed_jobs
        ),
        "jobs_excluded_by_location": len(
            excluded_jobs
        ),
        "duplicates_removed": len(
            duplicate_jobs
        ),
        "unique_jobs_retained": len(
            unique_jobs
        ),
        "retained_jobs": [
            {
                "title": job.get("title"),
                "company": job.get(
                    "company_name"
                ),
                "location": job.get(
                    "location_raw"
                ),
                "source": job.get("source"),
                "apply_url_present": bool(
                    job.get("apply_url")
                ),
            }
            for job in unique_jobs
        ],
        "excluded_jobs": excluded_jobs,
        "duplicate_jobs": duplicate_jobs,
        "errors": source_errors,
    }

    print(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
