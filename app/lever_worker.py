from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from typing import Any

from app.ats_registry import initialize_registry
from app.database import (
    get_connection,
    initialize_database,
)
from app.discovery_config import (
    build_location_search_plan,
    load_target_roles,
)
from app.hunter_worker import (
    deduplicate_jobs,
    matches_location_rule,
)
from app.job_store import save_job
from app.relevance import match_target_role
from app.source_runtime import (
    get_source_runtime_state,
)
from app.sources.lever import fetch_lever_jobs
from app.telegram_auto_dispatch import (
    attribute_dispatch_to_current_jobs,
    dispatch_unsent_jobs,
)
from app.source_run_notifier import emit_source_run_result, run_guarded_main
from app.runtime_config import provider_int, telegram_batch_limit


SOURCE_NAME = "Lever"


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run enabled Lever company boards "
            "using dashboard targeting rules."
        )
    )

    parser.add_argument(
        "--run-now",
        action="store_true",
        help=(
            "Run immediately while still requiring "
            "Lever to be enabled in the dashboard."
        ),
    )

    parser.add_argument(
        "--max-companies",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--page-size",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--telegram-limit",
        type=int,
        default=None,
    )

    return parser.parse_args()


def load_enabled_companies(
    limit: int,
) -> list[dict[str, Any]]:
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
                priority_weight,
                health_status
            FROM ats_company_registry
            WHERE
                enabled = 1
                AND lower(ats_type) = 'lever'
                AND trim(
                    COALESCE(board_token, '')
                ) != ''
            ORDER BY
                priority_weight DESC,
                company_name COLLATE NOCASE
            LIMIT ?
            """,
            (
                max(
                    1,
                    min(limit, 100),
                ),
            ),
        ).fetchall()
    finally:
        connection.close()

    return [
        dict(row)
        for row in rows
    ]


def filter_dashboard_jobs(
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    from app.dashboard_targeting_gate import filter_dashboard_jobs as _dashboard_gate
    return _dashboard_gate(jobs)


def update_company_success(
    *,
    registry_id: int,
    jobs_found: int,
) -> None:
    timestamp = utc_timestamp()
    connection = get_connection()

    try:
        connection.execute(
            """
            UPDATE ats_company_registry
            SET
                last_success_at = ?,
                last_run_at = ?,
                consecutive_failures = 0,
                jobs_found_last_run = ?,
                health_status = 'healthy',
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                timestamp,
                timestamp,
                jobs_found,
                registry_id,
            ),
        )

        connection.commit()
    finally:
        connection.close()


def update_company_failure(
    *,
    registry_id: int,
    error_message: str,
) -> None:
    timestamp = utc_timestamp()
    connection = get_connection()

    try:
        connection.execute(
            """
            UPDATE ats_company_registry
            SET
                last_failure_at = ?,
                last_run_at = ?,
                consecutive_failures =
                    consecutive_failures + 1,
                jobs_found_last_run = 0,
                health_status = 'unhealthy',
                last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                timestamp,
                timestamp,
                error_message[:2000],
                registry_id,
            ),
        )

        connection.commit()
    finally:
        connection.close()


def update_source_health(
    *,
    successful_companies: int,
    failed_companies: int,
    jobs_found: int,
    average_response_ms: float | None,
    last_error: str | None,
) -> None:
    timestamp = utc_timestamp()

    if (
        successful_companies > 0
        and failed_companies == 0
    ):
        health_status = "healthy"
    elif successful_companies > 0:
        health_status = "degraded"
    else:
        health_status = "unhealthy"

    connection = get_connection()

    try:
        if successful_companies > 0:
            connection.execute(
                """
                UPDATE source_health
                SET
                    last_success_at = ?,
                    last_run_at = ?,
                    consecutive_failures = 0,
                    last_http_status = 200,
                    jobs_found_last_run = ?,
                    average_response_ms = ?,
                    health_status = ?,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE source_name = ?
                """,
                (
                    timestamp,
                    timestamp,
                    jobs_found,
                    average_response_ms,
                    health_status,
                    last_error,
                    SOURCE_NAME,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE source_health
                SET
                    last_failure_at = ?,
                    last_run_at = ?,
                    consecutive_failures =
                        consecutive_failures + 1,
                    jobs_found_last_run = 0,
                    average_response_ms = ?,
                    health_status = ?,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE source_name = ?
                """,
                (
                    timestamp,
                    timestamp,
                    average_response_ms,
                    health_status,
                    last_error,
                    SOURCE_NAME,
                ),
            )

        connection.commit()
    finally:
        connection.close()


def record_summary_event(
    payload: dict[str, Any],
) -> None:
    connection = get_connection()

    try:
        connection.execute(
            """
            INSERT INTO events (
                job_id,
                event_type,
                actor,
                event_status,
                payload_json
            )
            VALUES (
                NULL,
                'lever_worker_completed',
                'lever_worker',
                ?,
                ?
            )
            """,
            (
                (
                    "completed"
                    if not payload["errors"]
                    else "partial"
                ),
                json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()
    finally:
        connection.close()


def print_skip(
    *,
    reason: str,
    source_state: dict[str, Any],
) -> None:
    emit_source_run_result({
                "success": True,
                "mode": "lever-worker",
                "source": SOURCE_NAME,
                "worker_action": "skip",
                "skip_reason": reason,
                "source_state": source_state,
                "network_request_made": False,
                "job_database_writes": 0,
                "telegram_messages": 0,
                "n8n_calls": 0,
            })


def main() -> None:
    run_started = time.perf_counter()
    run_started_at = datetime.now(timezone.utc).isoformat()
    args = parse_args()

    initialize_database()
    initialize_registry()
    args.max_companies = max(
        1,
        int(args.max_companies)
        if args.max_companies is not None
        else provider_int("provider_board_run_limit"),
    )
    args.page_size = max(
        1,
        int(args.page_size)
        if args.page_size is not None
        else provider_int("page_size"),
    )
    args.max_pages = max(
        1,
        int(args.max_pages)
        if args.max_pages is not None
        else provider_int("max_pages_per_board"),
    )
    args.telegram_limit = telegram_batch_limit(args.telegram_limit)
    request_timeout_seconds = provider_int("request_timeout_seconds")

    source_state = (
        get_source_runtime_state(
            SOURCE_NAME
        )
    )

    if not source_state.get("exists"):
        print_skip(
            reason="source_not_configured",
            source_state=source_state,
        )
        return

    if not source_state.get("enabled"):
        print_skip(
            reason="source_disabled",
            source_state=source_state,
        )
        return

    if (
        not source_state.get("due")
        and not args.run_now
    ):
        print_skip(
            reason="cadence_not_due",
            source_state=source_state,
        )
        return

    companies = load_enabled_companies(
        args.max_companies
    )

    if not companies:
        print_skip(
            reason=(
                "no_enabled_lever_companies"
            ),
            source_state=source_state,
        )
        return

    all_jobs: list[dict[str, Any]] = []
    company_results: list[
        dict[str, Any]
    ] = []
    errors: list[dict[str, Any]] = []
    response_times: list[float] = []

    for company in companies:
        started = time.perf_counter()

        try:
            result = fetch_lever_jobs(
                company_name=(
                    company["company_name"]
                ),
                site_name=(
                    company["board_token"]
                ),
                careers_url=(
                    company["careers_url"]
                ),
                page_size=max(
                    1,
                    min(args.page_size, 100),
                ),
                max_pages=max(
                    1,
                    min(args.max_pages, 50),
                ),
                timeout_seconds=request_timeout_seconds,
            )

            elapsed_ms = (
                time.perf_counter()
                - started
            ) * 1000

            response_times.append(
                elapsed_ms
            )

            jobs = result.get("jobs") or []
            all_jobs.extend(jobs)

            update_company_success(
                registry_id=int(
                    company["id"]
                ),
                jobs_found=len(jobs),
            )

            company_results.append(
                {
                    "registry_id": (
                        company["id"]
                    ),
                    "company": (
                        company["company_name"]
                    ),
                    "site_name": (
                        company["board_token"]
                    ),
                    "api_instance": (
                        result["api_instance"]
                    ),
                    "http_status": (
                        result["http_status"]
                    ),
                    "pages_fetched": (
                        result["pages_fetched"]
                    ),
                    "raw_jobs_found": len(
                        jobs
                    ),
                    "response_ms": round(
                        elapsed_ms,
                        2,
                    ),
                    "success": True,
                }
            )

        except Exception as error:
            elapsed_ms = (
                time.perf_counter()
                - started
            ) * 1000

            response_times.append(
                elapsed_ms
            )

            error_message = str(error)

            update_company_failure(
                registry_id=int(
                    company["id"]
                ),
                error_message=error_message,
            )

            errors.append(
                {
                    "registry_id": (
                        company["id"]
                    ),
                    "company": (
                        company["company_name"]
                    ),
                    "site_name": (
                        company["board_token"]
                    ),
                    "error": error_message,
                }
            )

    for job in all_jobs:
        job["_query_name"] = "Configured employer boards"
    filtered = filter_dashboard_jobs(
        all_jobs
    )

    eligible_jobs = filtered[
        "eligible_jobs"
    ]

    stored_results: list[
        dict[str, Any]
    ] = []

    connection = get_connection()

    try:
        for job in eligible_jobs:
            stored_results.append(
                save_job(
                    connection,
                    job,
                    actor="lever_worker",
                )
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    inserted_count = sum(
        1
        for item in stored_results
        if item["inserted"]
    )

    duplicate_count = (
        len(stored_results)
        - inserted_count
    )

    telegram_result = (
        dispatch_unsent_jobs(
            source_prefix="Lever/",
            limit=args.telegram_limit,
        )
    )
    telegram_attribution = attribute_dispatch_to_current_jobs(
        telegram_result,
        [item["job_id"] for item in stored_results if item["inserted"]],
    )

    successful_companies = len(
        company_results
    )

    failed_companies = len(errors)

    request_count = sum(
        max(1, int(item.get("pages_fetched") or 0))
        for item in company_results
    ) + failed_companies
    filtered["request_count"] = request_count
    filtered["telegram_messages"] = int(
        telegram_result.get("telegram_messages_sent") or 0
    )
    filtered["query_telegram_counts"] = {
        "Configured employer boards": telegram_attribution["current_run_messages"]
    }
    filtered["telegram_backlog_messages"] = telegram_attribution["backlog_messages"]
    filtered["errors"] = list(errors)
    filtered["run_started_at"] = run_started_at
    filtered["duration_ms"] = round((time.perf_counter() - run_started) * 1000, 2)
    filtered.setdefault("_stage_durations_ms", {})["FETCH"] = round(
        sum(response_times), 2
    )
    filtered["query_requests"] = [{
        "query_name": "Configured employer boards",
        "role_family": "",
        "requests": request_count,
        "raw": len(all_jobs),
        "errors": failed_companies,
        "duration_ms": round(sum(response_times), 2),
    }]

    average_response_ms = (
        round(
            sum(response_times)
            / len(response_times),
            2,
        )
        if response_times
        else None
    )

    last_error = (
        json.dumps(
            errors,
            ensure_ascii=False,
        )[:2000]
        if errors
        else None
    )

    update_source_health(
        successful_companies=(
            successful_companies
        ),
        failed_companies=(
            failed_companies
        ),
        jobs_found=len(all_jobs),
        average_response_ms=(
            average_response_ms
        ),
        last_error=last_error,
    )

    from app.dashboard_targeting_gate import record_source_metrics
    rejected_count = (
        int(filtered.get("excluded_by_role") or 0)
        + int(filtered.get("excluded_by_location") or 0)
        + int(filtered.get("excluded_by_hard_reject") or 0)
        + int(filtered.get("excluded_by_company_blacklist") or 0)
        + int(filtered.get("excluded_by_other_targeting") or 0)
    )
    record_source_metrics(
        "Lever",
        raw_jobs=len(all_jobs),
        eligible_jobs=len(eligible_jobs),
        inserted_jobs=inserted_count,
        duplicate_jobs=duplicate_count,
        rejected_jobs=rejected_count,
        provider_used="direct_ats",
        filter_summary=filtered,
    )

    output = {
        "success": (
            successful_companies > 0
        ),
        "partial_success": bool(
            successful_companies
            and failed_companies
        ),
        "mode": "lever-worker",
        "source": SOURCE_NAME,
        "worker_action": "run",
        "run_trigger": (
            "manual_run_now"
            if args.run_now
            else "cadence_due"
        ),
        "configuration_source": (
            "SQLite dashboard"
        ),
        "enabled_company_count": len(
            companies
        ),
        "successful_companies": (
            successful_companies
        ),
        "failed_companies": (
            failed_companies
        ),
        "network_request_made": True,
        "company_results": (
            company_results
        ),
        "raw_jobs_found": len(all_jobs),
        "excluded_by_role": filtered[
            "excluded_by_role"
        ],
        "excluded_by_location": filtered[
            "excluded_by_location"
        ],
        "excluded_by_hard_reject": filtered.get("excluded_by_hard_reject", 0),
        "excluded_by_company_blacklist": filtered.get("excluded_by_company_blacklist", 0),
        "targeting_rules_hash": filtered.get("targeting_rules_hash"),
        "duplicates_within_run": (
            filtered[
                "duplicates_within_run"
            ]
        ),
        "unique_jobs_ready": len(
            eligible_jobs
        ),
        "jobs_inserted": inserted_count,
        "inserted_job_ids": [
            item["job_id"]
            for item in stored_results
            if item["inserted"]
        ],
        "database_duplicates": (
            duplicate_count
        ),
        "health_records_updated": (
            1 + len(companies)
        ),
        "auto_telegram_enabled": (
            telegram_result[
                "telegram_enabled"
            ]
        ),
        "telegram_pending_before": (
            telegram_result[
                "eligible_jobs"
            ]
        ),
        "telegram_messages": (
            telegram_result[
                "telegram_messages_sent"
            ]
        ),
        "telegram_current_run_messages": telegram_attribution[
            "current_run_messages"
        ],
        "telegram_backlog_messages": telegram_attribution[
            "backlog_messages"
        ],
        "telegram_dispatch_errors": (
            telegram_result["errors"]
        ),
        "n8n_calls": 0,
        "errors": errors,
    }

    record_summary_event(output)

    emit_source_run_result(output)

    if not output["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    run_guarded_main(SOURCE_NAME, main)
