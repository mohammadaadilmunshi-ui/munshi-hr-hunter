from __future__ import annotations

import argparse
import json
import math
import sys
from contextlib import redirect_stdout
from importlib.metadata import version
from typing import Any

import pandas as pd
from jobspy import scrape_jobs


SUPPORTED_SITES = [
    "indeed",
    "google",
    "zip_recruiter",
    "glassdoor",
    "linkedin",
]


def clean(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, (dict, list, tuple)):
        return value

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass

    if isinstance(value, float) and math.isnan(value):
        return None

    text = str(value).strip()

    return text or None


def to_float(value: Any) -> float | None:
    value = clean(value)

    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def hourly_amount(
    amount: Any,
    interval: Any,
) -> float | None:
    numeric = to_float(amount)
    interval_text = str(clean(interval) or "").lower()

    if numeric is None:
        return None

    if interval_text in {"hourly", "hour"}:
        return round(numeric, 2)

    if interval_text in {"yearly", "annual", "annually"}:
        return round(numeric / 2080, 2)

    if interval_text in {"monthly", "month"}:
        return round((numeric * 12) / 2080, 2)

    if interval_text in {"weekly", "week"}:
        return round(numeric / 40, 2)

    if interval_text in {"daily", "day"}:
        return round(numeric / 8, 2)

    return None


def build_salary_raw(row: dict[str, Any]) -> str | None:
    minimum = clean(row.get("min_amount"))
    maximum = clean(row.get("max_amount"))
    interval = clean(row.get("interval"))
    currency = clean(row.get("currency"))

    if minimum is None and maximum is None:
        return None

    amount_text = ""

    if minimum is not None and maximum is not None:
        amount_text = f"{minimum} - {maximum}"
    else:
        amount_text = str(minimum or maximum)

    parts = [
        str(currency) if currency else None,
        amount_text,
        f"per {interval}" if interval else None,
    ]

    return " ".join(
        part
        for part in parts
        if part
    )


def parse_location_parts(
    row: dict[str, Any],
) -> tuple[
    str | None,
    str | None,
    str | None,
    str,
]:
    explicit_location = clean(row.get("location"))
    city = clean(row.get("city"))
    state = clean(row.get("state"))
    country = clean(row.get("country"))

    location_parts = [
        part.strip()
        for part in str(
            explicit_location or ""
        ).split(",")
        if part.strip()
    ]

    if location_parts:
        last_part = location_parts[-1].upper()

        if (
            not country
            and last_part
            in {
                "US",
                "USA",
                "UNITED STATES",
            }
        ):
            country = "US"
            location_parts = location_parts[:-1]

    if location_parts and not state:
        candidate_state = (
            location_parts[-1]
            .strip()
            .upper()
        )

        if (
            len(candidate_state) == 2
            and candidate_state != "US"
        ):
            state = candidate_state
            location_parts = location_parts[:-1]

    if location_parts and not city:
        city = ", ".join(location_parts)

    if explicit_location:
        location_raw = str(explicit_location)
    else:
        location_raw = ", ".join(
            str(part)
            for part in (
                city,
                state,
                country,
            )
            if part
        ) or None

    normalized_country = str(
        country or "US"
    ).strip().upper()

    if normalized_country in {
        "USA",
        "UNITED STATES",
    }:
        normalized_country = "US"

    return (
        location_raw,
        str(city) if city else None,
        str(state).upper() if state else None,
        normalized_country,
    )


def build_email_text(value: Any) -> str | None:
    value = clean(value)

    if value is None:
        return None

    if isinstance(value, (list, tuple)):
        return ", ".join(
            str(item)
            for item in value
            if item
        ) or None

    return str(value)


def normalize_record(
    row: dict[str, Any],
    site: str,
) -> dict[str, Any] | None:
    title = clean(row.get("title"))
    company = clean(row.get("company"))

    if not title or not company:
        return None

    job_url = clean(row.get("job_url"))
    direct_url = clean(row.get("job_url_direct"))

    remote_value = clean(row.get("is_remote"))

    (
        location_raw,
        city,
        state,
        country,
    ) = parse_location_parts(row)

    return {
        "source": f"JobSpy/{site}",
        "source_tier": 3,
        "ats_job_id": clean(row.get("id")),
        "company_name": company,
        "title": title,
        "location_raw": location_raw,
        "city": city,
        "state": state,
        "country": country,
        "remote_type": (
            "Remote"
            if remote_value is True
            else "Not specified"
        ),
        "employment_type": clean(row.get("job_type")),
        "job_url": job_url,
        "apply_url": direct_url or job_url,
        "description_raw": (
            clean(row.get("description"))
            or "Not specified"
        ),
        "salary_raw": build_salary_raw(row),
        "normalized_hourly_min": hourly_amount(
            row.get("min_amount"),
            row.get("interval"),
        ),
        "normalized_hourly_max": hourly_amount(
            row.get("max_amount"),
            row.get("interval"),
        ),
        "salary_confidence": (
            "medium"
            if row.get("min_amount") is not None
            or row.get("max_amount") is not None
            else "unknown"
        ),
        "date_posted": clean(row.get("date_posted")),
        "apply_deadline": None,
        "start_date": None,
        "end_date": None,
        "hours_per_week": None,
        "responsibilities": None,
        "qualifications": None,
        "preferred_skills": None,
        "work_authorization": None,
        "benefits": None,
        "recruiter": None,
        "recruiter_email": build_email_text(
            row.get("emails")
        ),
        "company_size": clean(
            row.get("company_employees_label")
        ),
        "industry": clean(
            row.get("company_industry")
        ),
        "employer_description": clean(
            row.get("company_description")
        ),
    }


def scrape_site(
    site: str,
    *,
    search_term: str,
    location: str,
    results_wanted: int,
    hours_old: int,
    job_type: str | None,
    remote_only: bool,
) -> list[dict[str, Any]]:
    arguments: dict[str, Any] = {
        "site_name": site,
        "search_term": search_term,
        "location": location,
        "results_wanted": results_wanted,
        "verbose": 0,
        "description_format": "markdown",
    }

    if site == "google":
        arguments["google_search_term"] = (
            f"{search_term} jobs near {location} "
            f"posted recently"
        )

    if site in {"indeed", "glassdoor"}:
        arguments["country_indeed"] = "USA"

    if site == "linkedin":
        arguments["linkedin_fetch_description"] = False

    if job_type:
        arguments["job_type"] = job_type

    if remote_only:
        arguments["is_remote"] = True

    # Indeed supports either freshness or job-type/remote
    # filtering in a single request, not both.
    if not (
        site == "indeed"
        and (
            job_type
            or remote_only
        )
    ):
        arguments["hours_old"] = hours_old

    # Keep stdout clean so the parent process receives
    # only our final JSON result.
    with redirect_stdout(sys.stderr):
        frame = scrape_jobs(**arguments)

    if frame is None or frame.empty:
        return []

    normalized: list[dict[str, Any]] = []

    for record in frame.to_dict(orient="records"):
        job = normalize_record(record, site)

        if job is not None:
            normalized.append(job)

    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )
    parser.add_argument(
        "--sites",
        default="indeed,google,zip_recruiter",
    )
    parser.add_argument(
        "--search-term",
        default="human resources intern",
    )
    parser.add_argument(
        "--location",
        default="New Jersey",
    )
    parser.add_argument(
        "--results",
        type=int,
        default=5,
    )
    parser.add_argument(
        "--hours-old",
        type=int,
        default=72,
    )
    parser.add_argument(
        "--job-type",
        default="internship",
    )
    parser.add_argument(
        "--remote-only",
        action="store_true",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.self_test:
        print(
            json.dumps(
                {
                    "success": True,
                    "tool": "JobSpy",
                    "package": "python-jobspy",
                    "version": version(
                        "python-jobspy"
                    ),
                    "supported_sites": SUPPORTED_SITES,
                    "network_request_made": False,
                },
                ensure_ascii=False,
            )
        )
        return

    requested_sites = [
        site.strip().lower()
        for site in args.sites.split(",")
        if site.strip()
    ]

    invalid_sites = [
        site
        for site in requested_sites
        if site not in SUPPORTED_SITES
    ]

    if invalid_sites:
        raise SystemExit(
            "Unsupported JobSpy sites: "
            + ", ".join(invalid_sites)
        )

    result_limit = max(
        1,
        min(args.results, 25),
    )

    hours_old = max(
        1,
        min(args.hours_old, 720),
    )

    all_jobs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    site_counts: dict[str, int] = {}

    for site in requested_sites:
        try:
            jobs = scrape_site(
                site,
                search_term=args.search_term,
                location=args.location,
                results_wanted=result_limit,
                hours_old=hours_old,
                job_type=args.job_type or None,
                remote_only=args.remote_only,
            )

            site_counts[site] = len(jobs)
            all_jobs.extend(jobs)

        except Exception as error:
            site_counts[site] = 0
            errors.append(
                {
                    "site": site,
                    "error": str(error),
                }
            )

    output = {
        "success": len(errors) == 0,
        "partial_success": (
            bool(all_jobs)
            and bool(errors)
        ),
        "tool": "JobSpy",
        "sites_requested": requested_sites,
        "site_counts": site_counts,
        "jobs_found": len(all_jobs),
        "jobs": all_jobs,
        "errors": errors,
    }

    print(
        json.dumps(
            output,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
