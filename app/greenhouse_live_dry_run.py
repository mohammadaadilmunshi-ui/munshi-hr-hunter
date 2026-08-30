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
from app.sources.greenhouse import (
    fetch_greenhouse_jobs,
)


def load_enabled_companies() -> list[dict[str, Any]]:
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                id,
                company_name,
                ats_type,
                board_token,
                careers_url,
                enabled,
                priority_weight
            FROM ats_company_registry
            WHERE
                enabled = 1
                AND lower(ats_type) = 'greenhouse'
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


def match_dashboard_rules(
    jobs: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    int,
    int,
]:
    target_roles = load_target_roles()
    location_plan = (
        build_location_search_plan()
    )

    eligible: list[dict[str, Any]] = []
    role_excluded = 0
    location_excluded = 0

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

        matched_location = None
        location_reason = None

        for plan in location_plan:
            matches, reason = (
                matches_location_rule(
                    job,
                    plan,
                )
            )

            if matches:
                matched_location = plan
                location_reason = reason
                break

        if matched_location is None:
            location_excluded += 1
            continue

        job["_matched_target_role"] = (
            matched_target_role
        )
        job["_role_match_reason"] = (
            role_reason
        )
        job["_matched_rule_name"] = (
            matched_location["rule_name"]
        )
        job["_location_match_reason"] = (
            location_reason
        )

        eligible.append(job)

    unique_jobs, _ = deduplicate_jobs(
        eligible
    )

    return (
        unique_jobs,
        role_excluded,
        location_excluded,
    )


def main() -> None:
    source_state = (
        get_source_runtime_state(
            "Greenhouse"
        )
    )

    companies = (
        load_enabled_companies()
    )

    if not source_state.get("enabled"):
        raise SystemExit(
            "STOP: Greenhouse is disabled in "
            "the Sources dashboard."
        )

    if not companies:
        raise SystemExit(
            "STOP: No enabled Greenhouse "
            "company with a board token exists "
            "in the ATS registry."
        )

    company_results = []
    all_raw_jobs: list[
        dict[str, Any]
    ] = []
    errors = []

    for company in companies:
        try:
            result = fetch_greenhouse_jobs(
                company_name=(
                    company["company_name"]
                ),
                board_token=(
                    company["board_token"]
                ),
            )

            jobs = result.get("jobs") or []
            all_raw_jobs.extend(jobs)

            company_results.append(
                {
                    "registry_id": (
                        company["id"]
                    ),
                    "company": (
                        company["company_name"]
                    ),
                    "board_token": (
                        company["board_token"]
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
                    "error": str(error),
                }
            )

    (
        eligible_jobs,
        role_excluded,
        location_excluded,
    ) = match_dashboard_rules(
        all_raw_jobs
    )

    output = {
        "success": not errors,
        "partial_success": bool(
            all_raw_jobs and errors
        ),
        "mode": (
            "greenhouse-registry-live-dry-run"
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
        "n8n_calls": 0,
        "company_results": (
            company_results
        ),
        "raw_jobs_found": len(
            all_raw_jobs
        ),
        "excluded_by_target_role": (
            role_excluded
        ),
        "excluded_by_location": (
            location_excluded
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
                "source": job.get("source"),
                "apply_url_present": bool(
                    job.get("apply_url")
                ),
            }
            for job in all_raw_jobs[:5]
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
