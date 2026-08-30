from __future__ import annotations

import argparse
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.discovery_config import (
    build_location_search_plan,
    build_search_term,
    load_source_configuration,
    load_target_roles,
)
from app.relevance import match_target_role
from app.sources.jobspy import (
    fetch_jobspy_jobs,
    jobspy_self_test,
)


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def normalize_country(value: Any) -> str:
    country = str(value or "US").strip().upper()

    aliases = {
        "USA": "US",
        "UNITED STATES": "US",
        "UK": "GB",
        "UNITED KINGDOM": "GB",
    }

    return aliases.get(country, country)


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


def semantic_key(
    job: dict[str, Any],
) -> tuple[str, str, str, str, str]:
    return (
        normalize_text(job.get("company_name")),
        normalize_text(job.get("title")),
        normalize_text(job.get("city")),
        normalize_text(job.get("state")),
        normalize_country(job.get("country")),
    )


def matches_location_rule(
    job: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[bool, str]:
    job_city = normalize_text(job.get("city"))
    job_state = str(
        job.get("state") or ""
    ).strip().upper()
    job_country = normalize_country(
        job.get("country")
    )
    remote_type = normalize_text(
        job.get("remote_type")
    )

    rule_city = normalize_text(
        plan.get("city")
    )
    rule_state = str(
        plan.get("state") or ""
    ).strip().upper()
    rule_country = normalize_country(
        plan.get("country")
    )
    rule_type = normalize_text(
        plan.get("rule_type")
    )

    if plan.get("remote_only"):
        if (
            remote_type == "remote"
            and job_country == rule_country
        ):
            return True, "remote_country_match"

        return False, "not_remote_country_match"

    if rule_type == "city":
        if (
            job_city == rule_city
            and job_state == rule_state
            and job_country == rule_country
        ):
            return True, "city_state_match"

        return False, "outside_city_rule"

    if rule_type == "state":
        if (
            job_state == rule_state
            and job_country == rule_country
        ):
            return True, "state_match"

        return False, "outside_state_rule"

    if rule_type in {
        "country",
        "entire country",
    }:
        if job_country == rule_country:
            return True, "country_match"

        return False, "outside_country_rule"

    if rule_state:
        if (
            job_state == rule_state
            and job_country == rule_country
        ):
            return True, "state_fallback_match"

        return False, "outside_state_fallback"

    if rule_city:
        if (
            job_city == rule_city
            and job_country == rule_country
        ):
            return True, "city_fallback_match"

        return False, "outside_city_fallback"

    if job_country == rule_country:
        return True, "country_fallback_match"

    return False, "outside_location_rule"


def deduplicate_jobs(
    jobs: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    unique_jobs: list[dict[str, Any]] = []
    duplicate_jobs: list[dict[str, Any]] = []

    seen_urls: set[str] = set()
    seen_semantic: set[
        tuple[str, str, str, str, str]
    ] = set()

    for job in jobs:
        url_key = canonical_url(
            job.get("apply_url")
            or job.get("job_url")
        )
        semantic = semantic_key(job)

        duplicate_reason: str | None = None

        if url_key and url_key in seen_urls:
            duplicate_reason = "same_apply_url"
        elif semantic in seen_semantic:
            duplicate_reason = (
                "same_company_title_location"
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

        seen_semantic.add(semantic)
        unique_jobs.append(job)

    return unique_jobs, duplicate_jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Aadil HR Hunter source orchestration worker"
        )
    )

    parser.add_argument(
        "--mode",
        choices=[
            "self-test",
            "jobspy-dry-run",
            "dashboard-jobspy-dry-run",
        ],
        default="self-test",
    )
    parser.add_argument(
        "--sites",
        default="indeed",
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
        default=10,
    )
    parser.add_argument(
        "--hours-old",
        type=int,
        default=48,
    )
    parser.add_argument(
        "--job-type",
        default="",
    )
    parser.add_argument(
        "--remote-only",
        action="store_true",
    )

    return parser.parse_args()


def build_basic_summary(
    result: dict[str, Any],
) -> dict[str, Any]:
    jobs = result.get("jobs") or []

    return {
        "success": result.get("success"),
        "partial_success": result.get(
            "partial_success",
            False,
        ),
        "mode": "jobspy-dry-run",
        "database_writes": 0,
        "telegram_messages": 0,
        "n8n_calls": 0,
        "site_counts": result.get(
            "site_counts",
            {},
        ),
        "jobs_found": result.get(
            "jobs_found",
            0,
        ),
        "sample": [
            {
                "source": job.get("source"),
                "title": job.get("title"),
                "company": job.get(
                    "company_name"
                ),
                "location": job.get(
                    "location_raw"
                ),
                "apply_url_present": bool(
                    job.get("apply_url")
                ),
            }
            for job in jobs[:10]
        ],
        "errors": result.get(
            "errors",
            [],
        ),
    }


def run_dashboard_jobspy_dry_run(
    args: argparse.Namespace,
) -> dict[str, Any]:
    target_roles = load_target_roles()
    search_term = build_search_term(
        target_roles
    )
    search_plan = build_location_search_plan()
    source_configuration = (
        load_source_configuration("JobSpy")
    )

    sites = [
        site.strip()
        for site in args.sites.split(",")
        if site.strip()
    ]

    fetched_jobs: list[dict[str, Any]] = []
    excluded_jobs: list[dict[str, Any]] = []
    source_errors: list[dict[str, Any]] = []
    plan_results: list[dict[str, Any]] = []

    for plan in search_plan:
        result = fetch_jobspy_jobs(
            sites=sites,
            search_term=search_term,
            location=plan["search_location"],
            results_wanted=args.results,
            hours_old=args.hours_old,
            job_type="",
            remote_only=bool(
                plan["remote_only"]
            ),
        )

        raw_jobs = result.get("jobs") or []
        retained_for_rule = 0
        excluded_for_rule = 0
        role_excluded_for_rule = 0

        for error in result.get("errors") or []:
            source_errors.append(
                {
                    "rule_id": plan["rule_id"],
                    "rule_name": plan["rule_name"],
                    **error,
                }
            )

        for job in raw_jobs:
            (
                role_matches,
                matched_target_role,
                role_reason,
            ) = match_target_role(
                job.get("title"),
                target_roles,
            )

            if not role_matches:
                excluded_for_rule += 1
                role_excluded_for_rule += 1

                excluded_jobs.append(
                    {
                        "rule_name": (
                            plan["rule_name"]
                        ),
                        "title": job.get(
                            "title"
                        ),
                        "company": job.get(
                            "company_name"
                        ),
                        "location": job.get(
                            "location_raw"
                        ),
                        "exclusion_stage": (
                            "target_role"
                        ),
                        "reason": role_reason,
                    }
                )
                continue

            matches, reason = (
                matches_location_rule(
                    job,
                    plan,
                )
            )

            if matches:
                job["_matched_rule_id"] = (
                    plan["rule_id"]
                )
                job["_matched_rule_name"] = (
                    plan["rule_name"]
                )
                job["_matched_target_role"] = (
                    matched_target_role
                )
                job["_role_match_reason"] = (
                    role_reason
                )
                job["_location_match_reason"] = (
                    reason
                )

                fetched_jobs.append(job)
                retained_for_rule += 1
            else:
                excluded_for_rule += 1

                excluded_jobs.append(
                    {
                        "rule_name": (
                            plan["rule_name"]
                        ),
                        "title": job.get(
                            "title"
                        ),
                        "company": job.get(
                            "company_name"
                        ),
                        "location": job.get(
                            "location_raw"
                        ),
                        "exclusion_stage": (
                            "location"
                        ),
                        "reason": reason,
                    }
                )

        plan_results.append(
            {
                "rule_id": plan["rule_id"],
                "rule_name": plan["rule_name"],
                "search_location": (
                    plan["search_location"]
                ),
                "remote_only": (
                    plan["remote_only"]
                ),
                "raw_jobs_found": len(
                    raw_jobs
                ),
                "jobs_matching_rule": (
                    retained_for_rule
                ),
                "jobs_excluded": (
                    excluded_for_rule
                ),
                "jobs_excluded_by_role": (
                    role_excluded_for_rule
                ),
                "success": result.get(
                    "success"
                ),
                "partial_success": result.get(
                    "partial_success",
                    False,
                ),
            }
        )

    unique_jobs, duplicate_jobs = (
        deduplicate_jobs(fetched_jobs)
    )

    return {
        "success": not source_errors,
        "partial_success": (
            bool(unique_jobs)
            and bool(source_errors)
        ),
        "mode": (
            "dashboard-jobspy-dry-run"
        ),
        "configuration_source": (
            "SQLite dashboard"
        ),
        "location_values_hardcoded": False,
        "target_roles_hardcoded": False,
        "freshness_hours": args.hours_old,
        "sites": sites,
        "source_configuration": (
            source_configuration
        ),
        "source_currently_enabled": bool(
            source_configuration
            and source_configuration.get(
                "enabled"
            )
        ),
        "source_current_cadence_minutes": (
            source_configuration.get(
                "cadence_minutes"
            )
            if source_configuration
            else None
        ),
        "generated_search_term": (
            search_term
        ),
        "active_location_rule_count": len(
            search_plan
        ),
        "database_writes": 0,
        "telegram_messages": 0,
        "n8n_calls": 0,
        "plan_results": plan_results,
        "jobs_matching_location_rules": len(
            fetched_jobs
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
                "matched_rule": job.get(
                    "_matched_rule_name"
                ),
                "matched_target_role": job.get(
                    "_matched_target_role"
                ),
                "role_match_reason": job.get(
                    "_role_match_reason"
                ),
                "location_match_reason": (
                    job.get(
                        "_location_match_reason"
                    )
                ),
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


def main() -> None:
    args = parse_args()

    if args.mode == "self-test":
        result = jobspy_self_test()

        result.update(
            {
                "worker_import": "passed",
                "database_writes": 0,
                "telegram_messages": 0,
                "n8n_calls": 0,
            }
        )

    elif args.mode == "jobspy-dry-run":
        result = fetch_jobspy_jobs(
            sites=[
                site.strip()
                for site in args.sites.split(",")
                if site.strip()
            ],
            search_term=args.search_term,
            location=args.location,
            results_wanted=args.results,
            hours_old=args.hours_old,
            job_type=args.job_type,
            remote_only=args.remote_only,
        )

        result = build_basic_summary(result)

    else:
        result = (
            run_dashboard_jobspy_dry_run(
                args
            )
        )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()


# UNIVERSAL_CANONICAL_FINGERPRINT_V1
from app.job_quality import (
    create_canonical_job_fingerprint
    as _create_canonical_job_fingerprint,
)


def create_job_fingerprint(
    job: dict[str, Any],
) -> str:
    return (
        _create_canonical_job_fingerprint(
            job
        )
    )
