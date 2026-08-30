from __future__ import annotations

import argparse
import io
import json
import time
from datetime import datetime, timezone
from contextlib import redirect_stdout
from typing import Any

from app.database import get_connection, get_setting
from app.job_store import save_job
from app.dashboard_targeting_gate import filter_dashboard_jobs as central_dashboard_filter, record_source_metrics
from app.source_run_notifier import (
    emit_source_run_result,
)
from app.sources.smartrecruiters import (
    fetch_all_postings,
    fetch_posting_detail,
    normalize_posting,
)
from app.telegram_auto_dispatch import (
    attribute_dispatch_to_current_jobs,
    dispatch_unsent_jobs,
)
from app.runtime_config import provider_int, telegram_batch_limit



# SMARTRECRUITERS_SINGLE_JSON_GUARD_V1
_raw_emit_source_run_result = emit_source_run_result


def emit_source_run_result(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Suppress notifier console output and return only
    the compact source notification result.
    """
    captured_output = io.StringIO()

    with redirect_stdout(captured_output):
        result = _raw_emit_source_run_result(
            payload
        )

    if isinstance(result, dict):
        nested = result.get(
            "source_run_notification"
        )

        if isinstance(nested, dict):
            return nested

    return result

SOURCE_NAME = "SmartRecruiters"


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def load_source_enabled() -> bool:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT enabled
            FROM source_health
            WHERE source_name = ?
            """,
            (SOURCE_NAME,),
        ).fetchone()
    finally:
        connection.close()

    return bool(
        row
        and int(row["enabled"] or 0)
    )


def load_companies(
    limit: int,
) -> list[dict[str, Any]]:
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM ats_company_registry
            WHERE
                enabled = 1
                AND lower(ats_type) =
                    'smartrecruiters'
            ORDER BY
                priority_weight DESC,
                company_name
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
    finally:
        connection.close()

    return [
        dict(row)
        for row in rows
    ]


def update_company_health(
    registry_id: int,
    *,
    success: bool,
    jobs_found: int,
    error: str | None,
) -> None:
    connection = get_connection()

    try:
        connection.execute(
            """
            UPDATE ats_company_registry
            SET
                last_run_at =
                    CURRENT_TIMESTAMP,
                last_success_at =
                    CASE
                        WHEN ?
                        THEN CURRENT_TIMESTAMP
                        ELSE last_success_at
                    END,
                last_failure_at =
                    CASE
                        WHEN ?
                        THEN last_failure_at
                        ELSE CURRENT_TIMESTAMP
                    END,
                consecutive_failures =
                    CASE
                        WHEN ?
                        THEN 0
                        ELSE
                            consecutive_failures
                            + 1
                    END,
                jobs_found_last_run = ?,
                health_status =
                    CASE
                        WHEN ?
                        THEN 'healthy'
                        ELSE 'error'
                    END,
                last_error = ?,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                int(success),
                int(success),
                int(success),
                int(jobs_found),
                int(success),
                error,
                registry_id,
            ),
        )

        connection.commit()
    finally:
        connection.close()


def update_source_health(
    *,
    success: bool,
    jobs_found: int,
    response_ms: float | None,
    error: str | None,
) -> None:
    connection = get_connection()

    try:
        connection.execute(
            """
            UPDATE source_health
            SET
                health_status =
                    CASE
                        WHEN ?
                        THEN 'healthy'
                        ELSE 'error'
                    END,
                jobs_found_last_run = ?,
                average_response_ms = ?,
                last_run_at =
                    CURRENT_TIMESTAMP,
                last_success_at =
                    CASE
                        WHEN ?
                        THEN CURRENT_TIMESTAMP
                        ELSE last_success_at
                    END,
                last_failure_at =
                    CASE
                        WHEN ?
                        THEN last_failure_at
                        ELSE CURRENT_TIMESTAMP
                    END,
                consecutive_failures =
                    CASE
                        WHEN ?
                        THEN 0
                        ELSE
                            consecutive_failures
                            + 1
                    END,
                last_error = ?,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE source_name = ?
            """,
            (
                int(success),
                int(jobs_found),
                response_ms,
                int(success),
                int(success),
                int(success),
                error,
                SOURCE_NAME,
            ),
        )

        connection.commit()
    finally:
        connection.close()


def run_worker(
    args: argparse.Namespace,
) -> dict[str, Any]:
    started = time.perf_counter()
    run_started_at = datetime.now(timezone.utc).isoformat()
    args.max_companies = max(
        1,
        int(args.max_companies)
        if args.max_companies is not None
        else provider_int("provider_board_run_limit"),
    )
    args.max_pages = max(
        1,
        int(args.max_pages)
        if args.max_pages is not None
        else provider_int("max_pages_per_board"),
    )
    args.telegram_limit = telegram_batch_limit(args.telegram_limit)
    provider_runtime = get_setting("provider_runtime", {}) or {}
    try:
        max_details_per_board = max(
            1, int(provider_runtime["max_detail_requests_per_board"])
        )
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(
            "Canonical provider_runtime.max_detail_requests_per_board is missing or invalid."
        ) from None

    companies = load_companies(
        args.max_companies
    )

    summary: dict[str, Any] = {
        "success": True,
        "partial_success": False,
        "mode": (
            "smartrecruiters-worker"
        ),
        "source": SOURCE_NAME,
        "run_started_at": run_started_at,
        "worker_action": "run",
        "run_trigger": (
            "manual_run_now"
            if args.run_now
            else "scheduled"
        ),
        "configuration_source": (
            "SQLite dashboard"
        ),
        "enabled_company_count": len(
            companies
        ),
        "successful_companies": 0,
        "failed_companies": 0,
        "company_results": [],
        "raw_jobs_found": 0,
        "request_count": 0,
        "excluded_by_role": 0,
        "excluded_by_location": 0,
        "excluded_by_hard_reject": 0,
        "excluded_by_company_blacklist": 0,
        "detail_requests": 0,
        "normalization_limit_per_board": max_details_per_board,
        "raw_not_normalized_due_to_limit": 0,
        "unique_jobs_ready": 0,
        "jobs_inserted": 0,
        "jobs_rejected": 0,
        "database_duplicates": 0,
        "inserted_job_ids": [],
        "telegram_messages": 0,
        "telegram_dispatch_errors": [],
        "n8n_calls": 0,
        "errors": [],
        "completed_at": None,
    }

    if not load_source_enabled():
        summary.update(
            {
                "success": True,
                "worker_action": (
                    "skip"
                ),
                "skip_reason": (
                    "source_disabled"
                ),
                "completed_at": (
                    utc_now()
                ),
            }
        )

        summary[
            "source_run_notification"
        ] = emit_source_run_result(
            summary
        )

        return summary

    if not companies:
        summary.update(
            {
                "success": True,
                "worker_action": (
                    "skip"
                ),
                "skip_reason": (
                    "no_enabled_company_boards"
                ),
                "completed_at": (
                    utc_now()
                ),
            }
        )

        summary[
            "source_run_notification"
        ] = emit_source_run_result(
            summary
        )

        return summary

    response_values: list[float] = []
    ready_jobs: list[
        dict[str, Any]
    ] = []

    for company in companies:
        registry_id = int(
            company["id"]
        )

        company_name = str(
            company["company_name"]
        )

        identifier = str(
            company["board_token"]
            or ""
        ).strip()

        company_result = {
            "registry_id": registry_id,
            "company": company_name,
            "company_identifier": (
                identifier
            ),
            "raw_jobs_found": 0,
            "detail_requests": 0,
            "success": False,
            "error": None,
        }

        try:
            fetched = fetch_all_postings(
                identifier,
                max_pages=(
                    args.max_pages
                ),
            )

            postings = fetched[
                "postings"
            ]
            summary["request_count"] += max(1, len(fetched.get("pages") or []))

            company_result[
                "raw_jobs_found"
            ] = len(postings)

            summary[
                "raw_jobs_found"
            ] += len(postings)

            for page in fetched[
                "pages"
            ]:
                response_values.append(
                    float(
                        page.get(
                            "response_ms",
                            0,
                        )
                        or 0
                    )
                )

            bounded_postings = postings[:max_details_per_board]
            summary["raw_not_normalized_due_to_limit"] += max(
                0, len(postings) - len(bounded_postings)
            )
            company_result["normalization_limit"] = max_details_per_board
            company_result["raw_not_normalized_due_to_limit"] = max(
                0, len(postings) - len(bounded_postings)
            )

            for posting in bounded_postings:
                posting_id = str(
                    posting.get("id")
                    or posting.get("uuid")
                    or ""
                ).strip()

                if not posting_id:
                    summary["errors"].append(
                        {
                            "company": (
                                company_name
                            ),
                            "title": title,
                            "error": (
                                "missing_posting_id"
                            ),
                        }
                    )
                    continue

                try:
                    summary["request_count"] += 1
                    detail_result = fetch_posting_detail(identifier, posting_id)
                    summary["detail_requests"] += 1
                    company_result["detail_requests"] += 1
                    ready_jobs.append(
                        normalize_posting(
                            posting,
                            detail_result["posting"],
                            registry_company_name=company_name,
                            company_identifier=identifier,
                        )
                    )
                except Exception as detail_error:
                    summary["errors"].append(
                        {
                            "company": company_name,
                            "posting_id": posting_id,
                            "error": f"detail_fetch_or_normalize: {detail_error}",
                        }
                    )

            company_result[
                "success"
            ] = True

            summary[
                "successful_companies"
            ] += 1

            update_company_health(
                registry_id,
                success=True,
                jobs_found=len(
                    postings
                ),
                error=None,
            )

        except Exception as error:
            summary["request_count"] += 1
            error_text = str(error)[:500]

            company_result.update(
                {
                    "success": False,
                    "error": error_text,
                }
            )

            summary[
                "failed_companies"
            ] += 1

            summary["errors"].append(
                {
                    "company": (
                        company_name
                    ),
                    "error": error_text,
                }
            )

            update_company_health(
                registry_id,
                success=False,
                jobs_found=0,
                error=error_text,
            )

        summary[
            "company_results"
        ].append(company_result)

    provider_raw_count = int(summary.get("raw_jobs_found") or 0)
    for job in ready_jobs:
        job["_query_name"] = "Configured employer boards"
    final_filtered = central_dashboard_filter(ready_jobs)
    ready_jobs = list(final_filtered.get("eligible_jobs") or [])
    for key, value in final_filtered.items():
        if key != "eligible_jobs":
            summary[key] = value
    summary["provider_raw_jobs_found"] = provider_raw_count
    summary["raw_jobs_found"] = provider_raw_count
    summary["normalized_jobs"] = int(final_filtered.get("raw_normalized") or 0)
    summary[
        "unique_jobs_ready"
    ] = len(ready_jobs)

    storage_connection = (
        get_connection()
    )

    try:
        for job in ready_jobs:
            save_result = save_job(
                storage_connection,
                job,
                actor=(
                    "smartrecruiters_worker"
                ),
            )

            if save_result[
                "inserted"
            ]:
                summary[
                    "jobs_inserted"
                ] += 1

                summary[
                    "inserted_job_ids"
                ].append(
                    save_result[
                        "job_id"
                    ]
                )

                if (
                    save_result.get(
                        "status"
                    )
                    == "rejected"
                ):
                    summary[
                        "jobs_rejected"
                    ] += 1
            else:
                summary[
                    "database_duplicates"
                ] += 1

        storage_connection.commit()

    except Exception:
        storage_connection.rollback()
        raise

    finally:
        storage_connection.close()

    dispatch = dispatch_unsent_jobs(
        source_prefix=(
            "SmartRecruiters/"
        ),
        limit=args.telegram_limit,
    )
    telegram_attribution = attribute_dispatch_to_current_jobs(
        dispatch,
        summary["inserted_job_ids"],
    )

    summary[
        "telegram_messages"
    ] = int(
        dispatch.get(
            "telegram_messages_sent",
            0,
        )
        or 0
    )
    summary["query_telegram_counts"] = {
        "Configured employer boards": telegram_attribution["current_run_messages"]
    }
    summary["telegram_current_run_messages"] = telegram_attribution[
        "current_run_messages"
    ]
    summary["telegram_backlog_messages"] = telegram_attribution[
        "backlog_messages"
    ]

    summary[
        "telegram_dispatch_errors"
    ] = dispatch.get(
        "errors",
        [],
    )

    summary["success"] = (
        summary[
            "successful_companies"
        ] > 0
        and not summary[
            "telegram_dispatch_errors"
        ]
    )

    summary["partial_success"] = (
        summary[
            "successful_companies"
        ] > 0
        and summary[
            "failed_companies"
        ] > 0
    )

    average_response_ms = (
        round(
            sum(response_values)
            / len(response_values),
            2,
        )
        if response_values
        else None
    )

    update_source_health(
        success=(
            summary[
                "successful_companies"
            ] > 0
        ),
        jobs_found=summary[
            "raw_jobs_found"
        ],
        response_ms=(
            average_response_ms
        ),
        error=(
            None
            if not summary["errors"]
            else json.dumps(
                summary["errors"][:5],
                ensure_ascii=False,
            )
        ),
    )

    rejected_count = (
        int(summary.get("excluded_by_role") or 0)
        + int(summary.get("excluded_by_location") or 0)
        + int(summary.get("excluded_by_hard_reject") or 0)
        + int(summary.get("excluded_by_company_blacklist") or 0)
    )
    summary["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    summary["query_requests"] = [{
        "query_name": "Configured employer boards",
        "role_family": "",
        "requests": int(summary.get("request_count") or 0),
        "raw": int(summary.get("raw_jobs_found") or 0),
        "errors": len(summary.get("errors") or []),
        "duration_ms": summary["duration_ms"],
    }]
    record_source_metrics(
        SOURCE_NAME,
        raw_jobs=int(summary.get("raw_jobs_found") or 0),
        eligible_jobs=len(ready_jobs),
        inserted_jobs=int(summary.get("jobs_inserted") or 0),
        duplicate_jobs=int(summary.get("database_duplicates") or 0),
        rejected_jobs=rejected_count,
        provider_used="smartrecruiters",
        filter_summary=summary,
    )
    # Decision evidence is already persisted by record_source_metrics. Keeping
    # thousands of raw decision rows in launchd stdout recreated diagnostic
    # storage bloat and exposed engineering detail in routine worker output.
    summary.pop("_decision_rows", None)
    summary.pop("rejection_samples", None)

    summary["completed_at"] = (
        utc_now()
    )

    summary[
        "source_run_notification"
    ] = emit_source_run_result(
        summary
    )

    return summary


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--run-now",
        action="store_true",
    )

    parser.add_argument(
        "--max-companies",
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

    args = parser.parse_args()

    try:
        result = run_worker(args)

    except Exception as error:
        result = {
            "success": False,
            "partial_success": False,
            "mode": (
                "smartrecruiters-worker"
            ),
            "source": SOURCE_NAME,
            "worker_action": "error",
            "errors": [
                str(error)[:1000]
            ],
            "n8n_calls": 0,
            "completed_at": utc_now(),
        }

        try:
            result[
                "source_run_notification"
            ] = emit_source_run_result(
                result
            )
        except Exception as notify_error:
            result[
                "notification_error"
            ] = str(
                notify_error
            )[:500]

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            )
        )

        raise

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
