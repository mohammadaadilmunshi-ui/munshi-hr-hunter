from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from app.database import ROOT_DIR, get_connection
from app.job_store import save_job
from app.dashboard_targeting_gate import (
    build_dashboard_search_queries,
    filter_dashboard_jobs,
    load_dashboard_targeting_rules,
    record_source_metrics,
)
from app.source_run_notifier import emit_source_run_result
from app.source_runtime import get_source_runtime_state
from app.telegram_auto_dispatch import dispatch_unsent_jobs
from app.runtime_config import telegram_batch_limit

SOURCE_NAME = "Adzuna"
SOURCE_PREFIX = "Adzuna"
BASE_URL = "https://api.adzuna.com/v1/api/jobs/us/search/1"
ENV_PATH = ROOT_DIR / ".env"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def credentials() -> tuple[str, str]:
    load_dotenv(ENV_PATH, override=False)
    return (
        str(os.getenv("ADZUNA_APP_ID") or "").strip(),
        str(os.getenv("ADZUNA_APP_KEY") or "").strip(),
    )


def _update_health(
    success: bool,
    jobs_found: int,
    error: str | None = None,
    http_status: int | None = None,
    elapsed_ms: int | None = None,
) -> None:
    connection = get_connection()
    try:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(source_health)"
            ).fetchall()
        }
        assignments = [
            "last_run_at = CURRENT_TIMESTAMP",
            "jobs_found_last_run = ?",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        values: list[Any] = [int(jobs_found)]

        if "average_response_ms" in columns and elapsed_ms is not None:
            assignments.append("average_response_ms = ?")
            values.append(int(elapsed_ms))
        if "last_http_status" in columns:
            assignments.append("last_http_status = ?")
            values.append(http_status)

        if success:
            assignments.extend(
                [
                    "health_status = 'healthy'",
                    "last_success_at = CURRENT_TIMESTAMP",
                    "consecutive_failures = 0",
                    "last_error = NULL",
                ]
            )
        else:
            assignments.extend(
                [
                    "health_status = 'failed'",
                    "last_failure_at = CURRENT_TIMESTAMP",
                    "consecutive_failures = consecutive_failures + 1",
                    "last_error = ?",
                ]
            )
            values.append(str(error or "unknown error")[:2000])

        values.append(SOURCE_NAME)
        connection.execute(
            f"""
            UPDATE source_health
            SET {', '.join(assignments)}
            WHERE lower(source_name) = lower(?)
            """,
            values,
        )
        connection.commit()
    finally:
        connection.close()


def _normalize(item: dict[str, Any]) -> dict[str, Any]:
    company = item.get("company")
    location = item.get("location")
    category = item.get("category")
    contract_time = str(item.get("contract_time") or "").strip()
    contract_type = str(item.get("contract_type") or "").strip()

    return {
        "site": "adzuna",
        "source": "Adzuna",
        "id": item.get("id"),
        "title": item.get("title"),
        "company": (
            company.get("display_name")
            if isinstance(company, dict)
            else company
        ),
        "location": (
            location.get("display_name")
            if isinstance(location, dict)
            else location
        ),
        "description": item.get("description"),
        "job_url": item.get("redirect_url"),
        "job_url_direct": item.get("redirect_url"),
        "date_posted": item.get("created"),
        "min_amount": item.get("salary_min"),
        "max_amount": item.get("salary_max"),
        "interval": "yearly",
        "job_type": " ".join(
            value
            for value in (contract_time, contract_type)
            if value
        ),
        "category": (
            category.get("label")
            if isinstance(category, dict)
            else category
        ),
    }


def fetch_jobs(
    app_id: str,
    app_key: str,
    results_per_search: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    diagnostics: dict[str, Any] = {
        "requests_attempted": 0,
        "requests_succeeded": 0,
        "http_errors": [],
        "configuration_source": "SQLite dashboard",
        "personal_rules_hardcoded": False,
    }
    queries, rules_hash = build_dashboard_search_queries(terms_per_query=3, max_queries=12)
    rules = load_dashboard_targeting_rules()
    locations = []
    for plan in rules.location_plan:
        value = str(plan.get("search_location") or "").strip() or "United States"
        if value.casefold() not in {item.casefold() for item in locations}:
            locations.append(value)
    diagnostics["targeting_rules_hash"] = rules_hash
    diagnostics["search_queries"] = queries
    diagnostics["search_locations"] = locations

    for term in queries:
        for location in locations:
            diagnostics["requests_attempted"] += 1
            response = requests.get(
                BASE_URL,
                params={
                    "app_id": app_id,
                    "app_key": app_key,
                    "results_per_page": results_per_search,
                    "what": term,
                    "where": location,
                    "content-type": "application/json",
                    "sort_by": "date",
                },
                timeout=30,
            )
            if response.status_code != 200:
                diagnostics["http_errors"].append({
                    "term": term, "location": location, "status": response.status_code,
                    "body": response.text[:500],
                })
                continue
            diagnostics["requests_succeeded"] += 1
            payload = response.json()
            for item in payload.get("results") or []:
                if not isinstance(item, dict):
                    continue
                job = _normalize(item)
                key = str(job.get("id") or job.get("job_url") or "|".join([
                    str(job.get("company") or ""), str(job.get("title") or ""), str(job.get("location") or "")
                ])).strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                jobs.append(job)
    if diagnostics["requests_succeeded"] == 0:
        raise RuntimeError("Every Adzuna request failed: " + json.dumps(diagnostics["http_errors"][:3], ensure_ascii=False))
    return jobs, diagnostics


def self_test() -> dict[str, Any]:
    app_id, app_key = credentials()
    sample = _normalize(
        {
            "id": "example",
            "title": "HR Intern",
            "company": {"display_name": "Example"},
            "location": {"display_name": "New York, NY"},
            "description": "Support HR operations.",
            "redirect_url": "https://example.com/job",
            "created": "2026-07-05T00:00:00Z",
        }
    )
    assert sample["company"] == "Example"
    return {
        "success": True,
        "network_request_made": False,
        "credentials_configured": bool(app_id and app_key),
        "sample_title": sample["title"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adzuna discovery worker using project storage and filters."
    )
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--results", type=int, default=20)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return

    source_state = get_source_runtime_state(SOURCE_NAME)
    if not source_state["enabled"]:
        rejected_count = (
            int(filtered.get("excluded_by_role") or 0)
            + int(filtered.get("excluded_by_location") or 0)
            + int(filtered.get("excluded_by_hard_reject") or 0)
            + int(filtered.get("excluded_by_company_blacklist") or 0)
            + int(filtered.get("excluded_by_other_targeting") or 0)
        )
        record_source_metrics(
            SOURCE_NAME,
            raw_jobs=len(raw_jobs),
            eligible_jobs=len(eligible_jobs),
            inserted_jobs=inserted,
            duplicate_jobs=duplicates,
            rejected_jobs=rejected_count,
            provider_used="adzuna",
            filter_summary=filtered,
        )

        output = {
            "success": True,
            "source": SOURCE_NAME,
            "worker_action": "skip",
            "skip_reason": "source_disabled",
            "source_state": source_state,
            "network_request_made": False,
            "jobs_inserted": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
        }
        emit_source_run_result(output)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    if not source_state["due"] and not args.run_now:
        output = {
            "success": True,
            "source": SOURCE_NAME,
            "worker_action": "skip",
            "skip_reason": "cadence_not_due",
            "source_state": source_state,
            "network_request_made": False,
            "jobs_inserted": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
        }
        emit_source_run_result(output)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    app_id, app_key = credentials()
    if not app_id or not app_key:
        error = (
            "Missing ADZUNA_APP_ID or ADZUNA_APP_KEY in "
            f"{ENV_PATH}"
        )
        _update_health(False, 0, error=error)
        output = {
            "success": False,
            "source": SOURCE_NAME,
            "worker_action": "configuration_error",
            "network_request_made": False,
            "jobs_inserted": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
            "errors": [error],
        }
        emit_source_run_result(output)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    started = time.monotonic()
    try:
        raw_jobs, diagnostics = fetch_jobs(
            app_id,
            app_key,
            max(1, min(args.results, 50)),
        )
        filtered = filter_dashboard_jobs(raw_jobs)
        eligible_jobs = list(filtered.get("eligible_jobs") or [])
        connection = get_connection()
        stored: list[dict[str, Any]] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            for raw_job in eligible_jobs:
                stored.append(
                    save_job(
                        connection,
                        raw_job,
                        actor="adzuna_worker",
                    )
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        inserted = sum(bool(item.get("inserted")) for item in stored)
        duplicates = len(stored) - inserted
        telegram_result = dispatch_unsent_jobs(
            source_prefix=SOURCE_PREFIX,
            limit=telegram_batch_limit(),
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _update_health(
            True,
            len(raw_jobs),
            http_status=200,
            elapsed_ms=elapsed_ms,
        )
        output = {
            "success": True,
            "source": SOURCE_NAME,
            "worker_action": "run",
            "run_reason": (
                "manual_run_now" if args.run_now else "scheduled_due"
            ),
            "network_request_made": True,
            "raw_jobs_found": len(raw_jobs),
            "jobs_after_dashboard_filters": len(eligible_jobs),
            "jobs_inserted": inserted,
            "database_duplicates": duplicates,
            "telegram_messages": int(
                telegram_result.get("telegram_messages_sent") or 0
            ),
            "dispatch": telegram_result,
            "diagnostics": diagnostics,
            "n8n_calls": 0,
            "errors": [],
            "completed_at": utc_now(),
        }
        emit_source_run_result(output)
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    except Exception as error:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        status = None
        text = str(error)
        for code in (401, 403, 429, 500, 502, 503):
            if str(code) in text:
                status = code
                break
        _update_health(
            False,
            0,
            error=text,
            http_status=status,
            elapsed_ms=elapsed_ms,
        )
        output = {
            "success": False,
            "source": SOURCE_NAME,
            "worker_action": "failed",
            "network_request_made": True,
            "raw_jobs_found": 0,
            "jobs_inserted": 0,
            "database_duplicates": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
            "errors": [text],
            "completed_at": utc_now(),
        }
        emit_source_run_result(output)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        raise


if __name__ == "__main__":
    main()
