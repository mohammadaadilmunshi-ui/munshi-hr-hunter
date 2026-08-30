from __future__ import annotations

import time
from typing import Any

from app.discovery_config import (
    build_location_search_plan,
    build_search_term,
    load_target_roles,
)
from app.sources.jobspy import fetch_jobspy_jobs
from app.database import get_setting
from app.query_planner import select_queries


def collect_jobspy_jobs(
    *,
    sites: list[str],
    results_wanted: int,
    hours_old: int,
    source_name: str = "JobSpy",
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
]:
    target_roles = load_target_roles()
    selected_queries = select_queries(source_name)
    if not selected_queries:
        search_term = build_search_term(target_roles)
        selected_queries = [{"family": "Configured roles", "query": search_term, "selection_mode": "fallback"}]
    search_plan = build_location_search_plan()
    query_strategy = dict(get_setting("query_strategy", {}) or {})
    location_limit = max(
        1, int(query_strategy.get("max_location_plans_per_source_cycle") or 2)
    )
    search_plan = search_plan[:location_limit]

    normalized_jobs: list[dict[str, Any]] = []
    source_errors: list[dict[str, Any]] = []
    plan_results: list[dict[str, Any]] = []

    raw_jobs_found = 0

    query_requests: list[dict[str, Any]] = []

    for plan in search_plan:
      for selected_query in selected_queries:
        query_name = str(selected_query["query"])
        request_started = time.perf_counter()
        result = fetch_jobspy_jobs(
            sites=sites,
            search_term=query_name,
            location=plan["search_location"],
            results_wanted=results_wanted,
            hours_old=hours_old,
            job_type="",
            remote_only=bool(plan["remote_only"]),
        )
        request_duration_ms = round(
            (time.perf_counter() - request_started) * 1000,
            2,
        )

        raw_jobs = result.get("jobs") or []
        raw_jobs_found += len(raw_jobs)

        for error in result.get("errors") or []:
            source_errors.append(
                {
                    "rule_id": plan["rule_id"],
                    "rule_name": plan["rule_name"],
                    **error,
                    "query_name": query_name,
                }
            )

        for job in raw_jobs:
            job["_query_name"] = query_name
            job["_role_family"] = str(selected_query.get("family") or "")
            job["_matched_rule_id"] = (
                plan["rule_id"]
            )
            job["_matched_rule_name"] = (
                plan["rule_name"]
            )
            normalized_jobs.append(job)

        request_error_count = len(result.get("errors") or [])
        query_requests.append(
            {
                "query_name": query_name,
                "role_family": selected_query.get("family"),
                "requests": 1,
                "raw": len(raw_jobs),
                "errors": request_error_count,
                "duration_ms": request_duration_ms,
                "selection_mode": selected_query.get("selection_mode"),
            }
        )
        plan_results.append(
            {
                "rule_id": plan["rule_id"],
                "rule_name": plan["rule_name"],
                "search_location": (
                    plan["search_location"]
                ),
                "remote_only": bool(
                    plan["remote_only"]
                ),
                "raw_jobs_found": len(raw_jobs),
                "normalized_jobs": len(raw_jobs),
                "success": result.get("success"),
                "partial_success": result.get(
                    "partial_success",
                    False,
                ),
                "query_name": query_name,
                "role_family": selected_query.get("family"),
                "duration_ms": request_duration_ms,
            }
        )

    summary = {
        "configuration_source": (
            "SQLite dashboard"
        ),
        "location_values_hardcoded": False,
        "target_roles_hardcoded": False,
        "active_location_rule_count": len(
            search_plan
        ),
        "target_role_count": len(
            target_roles
        ),
        "selected_queries": selected_queries,
        "query_requests": query_requests,
        "raw_jobs_found": raw_jobs_found,
        "normalized_jobs": len(normalized_jobs),
        "provider_policy_filtering": False,
        "canonical_targeting_pending": True,
        "plan_results": plan_results,
        "errors": source_errors,
    }

    return normalized_jobs, summary
