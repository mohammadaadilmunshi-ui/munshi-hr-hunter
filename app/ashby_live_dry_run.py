from __future__ import annotations

import json
from typing import Any

from app.database import get_connection
from app.discovery_config import (
    build_location_search_plan,
    load_target_roles,
)
from app.hunter_worker import (
    deduplicate_jobs,
    matches_location_rule,
)
from app.relevance import match_target_role
from app.source_runtime import (
    get_source_runtime_state,
)
from app.sources.ashby import (
    fetch_ashby_jobs,
)


SOURCE_NAME = "Ashby"


def load_enabled_companies() -> list[
    dict[str, Any]
]:
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                id,
                company_name,
                board_token,
                careers_url,
                enabled,
                priority_weight
            FROM ats_company_registry
            WHERE
                enabled = 1
                AND lower(ats_type) = 'ashby'
                AND trim(
                    COALESCE(board_token, '')
                ) != ''
            ORDER BY
                priority_weight DESC,
                company_name COLLATE NOCASE
            """
        ).fetchall()
    finally:
        connection.close()

    return [
        dict(row)
        for row in rows
    ]


def location_candidates(
    job: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = [job]

    secondary_locations = job.get(
        "ashby_secondary_locations"
    )

    if not isinstance(
        secondary_locations,
        list,
    ):
        return candidates

    for secondary in secondary_locations:
        if not isinstance(
            secondary,
            dict,
        ):
            continue

        candidate = dict(job)

        candidate.update(
            {
                "location_raw": (
                    secondary.get("location")
                ),
                "city": secondary.get(
                    "city"
                ),
                "state": secondary.get(
                    "state"
                ),
                "country": secondary.get(
                    "country"
                ),
            }
        )

        candidates.append(candidate)

    return candidates


def filter_dashboard_jobs(
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    target_roles = load_target_roles()
    location_plan = (
        build_location_search_plan()
    )

    eligible = []
    role_excluded = 0
    location_excluded = 0
    secondary_location_matches = 0

    for job in jobs:
        (
            role_matches,
            matched_target_role,
            role_reason,
        ) = match_target_role(
            job.get("title"),
            target_roles,
        )

        if not role_matches:
            role_excluded += 1
            continue

        matched_rule = None
        location_reason = None
        matched_location_index = None

        for candidate_index, candidate in enumerate(
            location_candidates(job)
        ):
            for rule in location_plan:
                matches, reason = (
                    matches_location_rule(
                        candidate,
                        rule,
                    )
                )

                if matches:
                    matched_rule = rule
                    location_reason = reason
                    matched_location_index = (
                        candidate_index
                    )
                    break

            if matched_rule is not None:
                break

        if matched_rule is None:
            location_excluded += 1
            continue

        if (
            matched_location_index is not None
            and matched_location_index > 0
        ):
            secondary_location_matches += 1

        job["_matched_target_role"] = (
            matched_target_role
        )
        job["_role_match_reason"] = (
            role_reason
        )
        job["_matched_rule_name"] = (
            matched_rule["rule_name"]
        )
        job["_location_match_reason"] = (
            location_reason
        )
        job["_matched_secondary_location"] = (
            bool(
                matched_location_index
                and matched_location_index > 0
            )
        )

        eligible.append(job)

    unique_jobs, duplicates = (
        deduplicate_jobs(eligible)
    )

    return {
        "eligible_jobs": unique_jobs,
        "excluded_by_role": (
            role_excluded
        ),
        "excluded_by_location": (
            location_excluded
        ),
        "duplicates_within_run": len(
            duplicates
        ),
        "secondary_location_matches": (
            secondary_location_matches
        ),
    }


def main() -> None:
    source_state = (
        get_source_runtime_state(
            SOURCE_NAME
        )
    )

    companies = load_enabled_companies()

    if not source_state.get("enabled"):
        raise SystemExit(
            "STOP: Ashby is disabled in "
            "the Sources dashboard."
        )

    if not companies:
        raise SystemExit(
            "STOP: No enabled Ashby company "
            "with a job-board name exists."
        )

    company_results = []
    all_jobs = []
    errors = []

    for company in companies:
        try:
            result = fetch_ashby_jobs(
                company_name=(
                    company["company_name"]
                ),
                board_name=(
                    company["board_token"]
                ),
                include_compensation=True,
                timeout_seconds=20,
            )

            jobs = result.get("jobs") or []
            all_jobs.extend(jobs)

            company_results.append(
                {
                    "registry_id": (
                        company["id"]
                    ),
                    "company": (
                        company["company_name"]
                    ),
                    "board_name": (
                        company["board_token"]
                    ),
                    "api_version": (
                        result.get(
                            "api_version"
                        )
                    ),
                    "http_status": (
                        result["http_status"]
                    ),
                    "raw_jobs_found": len(
                        jobs
                    ),
                    "success": True,
                }
            )

        except Exception as error:
            errors.append(
                {
                    "registry_id": (
                        company["id"]
                    ),
                    "company": (
                        company["company_name"]
                    ),
                    "board_name": (
                        company["board_token"]
                    ),
                    "error": str(error),
                }
            )

    filtered = filter_dashboard_jobs(
        all_jobs
    )

    eligible_jobs = filtered[
        "eligible_jobs"
    ]

    output = {
        "success": not errors,
        "partial_success": bool(
            all_jobs and errors
        ),
        "mode": (
            "ashby-registry-live-dry-run"
        ),
        "configuration_source": (
            "SQLite dashboard"
        ),
        "source_enabled": bool(
            source_state.get("enabled")
        ),
        "enabled_company_count": len(
            companies
        ),
        "network_request_made": True,
        "database_writes": 0,
        "telegram_messages": 0,
        "source_run_notifications": 0,
        "n8n_calls": 0,
        "company_results": (
            company_results
        ),
        "raw_jobs_found": len(all_jobs),
        "excluded_by_target_role": (
            filtered[
                "excluded_by_role"
            ]
        ),
        "excluded_by_location": (
            filtered[
                "excluded_by_location"
            ]
        ),
        "duplicates_within_run": (
            filtered[
                "duplicates_within_run"
            ]
        ),
        "secondary_location_matches": (
            filtered[
                "secondary_location_matches"
            ]
        ),
        "eligible_unique_jobs": len(
            eligible_jobs
        ),
        "raw_sample": [
            {
                "company": job.get(
                    "company_name"
                ),
                "title": job.get("title"),
                "location": job.get(
                    "location_raw"
                ),
                "workplace_type": job.get(
                    "remote_type"
                ),
                "employment_type": job.get(
                    "employment_type"
                ),
                "salary": job.get(
                    "salary_raw"
                ),
                "secondary_location_count": len(
                    job.get(
                        "ashby_secondary_locations"
                    )
                    or []
                ),
                "source": job.get("source"),
                "apply_url_present": bool(
                    job.get("apply_url")
                ),
            }
            for job in all_jobs[:10]
        ],
        "eligible_sample": [
            {
                "company": job.get(
                    "company_name"
                ),
                "title": job.get("title"),
                "location": job.get(
                    "location_raw"
                ),
                "matched_target_role": (
                    job.get(
                        "_matched_target_role"
                    )
                ),
                "matched_location_rule": (
                    job.get(
                        "_matched_rule_name"
                    )
                ),
                "matched_secondary_location": (
                    job.get(
                        "_matched_secondary_location"
                    )
                ),
            }
            for job in eligible_jobs[:10]
        ],
        "errors": errors,
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
