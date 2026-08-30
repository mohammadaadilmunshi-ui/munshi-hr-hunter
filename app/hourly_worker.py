from __future__ import annotations
# AADIL_JOBSPY_HOURLY_ZIPRECRUITER_ONLY_V1_1

import argparse
import json
import time
from datetime import datetime, timezone
from typing import Any

from app.database import (
    get_connection,
    initialize_database,
)
from app.job_store import save_job
from app.dashboard_targeting_gate import filter_dashboard_jobs, record_source_metrics
from app.jobspy_pipeline import (
    collect_jobspy_jobs,
)
from app.telegram_auto_dispatch import dispatch_unsent_jobs
from app.source_runtime import (
    get_source_runtime_state,
)
from app.source_run_notifier import emit_source_run_result, run_guarded_main


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


# AADIL_JOBSPY_MULTI_ENGINE_DEFAULT_V1
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Aadil HR Hunter hourly discovery worker"
        )
    )

    parser.add_argument(
        "--sites",
        default="zip_recruiter",
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
        "--run-now",
        action="store_true",
        help=(
            "Run an enabled source immediately "
            "without waiting for cadence. This "
            "never bypasses the dashboard Enabled switch."
        ),
    )

    return parser.parse_args()


def record_failed_run(
    error: Exception,
) -> None:
    connection = get_connection()

    try:
        connection.execute(
            """
            UPDATE source_health
            SET
                last_failure_at =
                    CURRENT_TIMESTAMP,
                last_run_at =
                    CURRENT_TIMESTAMP,
                consecutive_failures =
                    consecutive_failures + 1,
                health_status = 'failed',
                last_error = ?,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE source_name = 'JobSpy'
            """,
            (str(error)[:2000],),
        )

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
                'source_run_failed',
                'hourly_worker',
                'failed',
                ?
            )
            """,
            (
                json.dumps(
                    {
                        "source": "JobSpy",
                        "error": str(error),
                        "failed_at": utc_now(),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()
    finally:
        connection.close()


def main() -> None:
    args = parse_args()

    initialize_database()

    source_state = get_source_runtime_state(
        "JobSpy"
    )

    if not source_state["enabled"]:
        emit_source_run_result({
                    "success": True,
                    "mode": "hourly-worker",
                    "source": "JobSpy",
                    "worker_action": "skip",
                    "skip_reason": (
                        "source_disabled"
                    ),
                    "source_state": source_state,
                    "network_request_made": False,
                    "database_writes": 0,
                    "telegram_messages": 0,
                    "n8n_calls": 0,
                })
        return

    if (
        not source_state["due"]
        and not args.run_now
    ):
        emit_source_run_result({
                    "success": True,
                    "mode": "hourly-worker",
                    "source": "JobSpy",
                    "worker_action": "skip",
                    "skip_reason": (
                        "cadence_not_due"
                    ),
                    "source_state": source_state,
                    "network_request_made": False,
                    "database_writes": 0,
                    "telegram_messages": 0,
                    "n8n_calls": 0,
                })
        return

    sites = [
        site.strip()
        for site in args.sites.split(",")
        if site.strip()
    ]

    try:
        run_started_at = utc_now()
        run_clock_started = time.perf_counter()
        jobs, discovery_summary = (
            collect_jobspy_jobs(
                sites=sites,
                results_wanted=max(
                    1,
                    min(args.results, 25),
                ),
                hours_old=max(
                    1,
                    min(args.hours_old, 720),
                ),
                source_name="JobSpy",
            )
        )

        filtered = filter_dashboard_jobs(jobs)
        eligible_jobs = list(filtered.get("eligible_jobs") or [])
        discovery_summary.update(
            {
                key: value
                for key, value in filtered.items()
                if key != "eligible_jobs"
            }
        )

        connection = get_connection()

        try:
            stored_results: list[
                dict[str, Any]
            ] = []

            for job in eligible_jobs:
                stored_results.append(
                    save_job(
                        connection,
                        job,
                        actor="hourly_worker",
                    )
                )

            inserted_count = sum(
                1
                for item in stored_results
                if item["inserted"]
            )

            duplicate_count = sum(
                1
                for item in stored_results
                if not item["inserted"]
            )
            query_new_eligible_counts: dict[str, int] = {}
            query_database_duplicate_counts: dict[str, int] = {}
            inserted_job_queries: dict[int, str] = {}
            for job, item in zip(eligible_jobs, stored_results):
                query_name = str(job.get("_query_name") or "Unattributed")
                target = (
                    query_new_eligible_counts
                    if item.get("inserted")
                    else query_database_duplicate_counts
                )
                target[query_name] = target.get(query_name, 0) + 1
                if item.get("inserted") and int(item.get("job_id") or 0) > 0:
                    inserted_job_queries[int(item["job_id"])] = query_name
            discovery_summary["query_new_eligible_counts"] = query_new_eligible_counts
            discovery_summary["query_database_duplicate_counts"] = query_database_duplicate_counts

            errors = (
                discovery_summary.get(
                    "errors"
                )
                or []
            )

            health_status = (
                "healthy"
                if not errors
                else "degraded"
            )

            connection.execute(
                """
                UPDATE source_health
                SET
                    last_success_at =
                        CURRENT_TIMESTAMP,
                    last_run_at =
                        CURRENT_TIMESTAMP,
                    consecutive_failures = 0,
                    jobs_found_last_run = ?,
                    health_status = ?,
                    last_error = ?,
                    updated_at =
                        CURRENT_TIMESTAMP
                WHERE source_name = 'JobSpy'
                """,
                (
                    len(eligible_jobs),
                    health_status,
                    (
                        json.dumps(
                            errors,
                            ensure_ascii=False,
                        )[:2000]
                        if errors
                        else None
                    ),
                ),
            )

            run_payload = {
                "source": "JobSpy",
                "sites": sites,
                "discovery": discovery_summary,
                "stored_results": stored_results,
                "inserted_count": inserted_count,
                "duplicate_count": (
                    duplicate_count
                ),
                "telegram_messages": 0,
                "n8n_calls": 0,
                "completed_at": utc_now(),
            }

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
                    'source_run_completed',
                    'hourly_worker',
                    'completed',
                    ?
                )
                """,
                (
                    json.dumps(
                        run_payload,
                        ensure_ascii=False,
                    ),
                ),
            )

            connection.commit()

        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        telegram_result = dispatch_unsent_jobs(source_prefix="JobSpy/")
        discovery_summary["telegram_messages"] = int(
            telegram_result.get("telegram_messages_sent") or 0
        )
        query_telegram_counts: dict[str, int] = {}
        for sent_item in telegram_result.get("sent") or []:
            if not isinstance(sent_item, dict):
                continue
            query_name = inserted_job_queries.get(int(sent_item.get("job_id") or 0))
            if query_name:
                query_telegram_counts[query_name] = (
                    query_telegram_counts.get(query_name, 0) + 1
                )
        discovery_summary["query_telegram_counts"] = query_telegram_counts
        discovery_summary["telegram_backlog_messages"] = max(
            0,
            int(discovery_summary["telegram_messages"])
            - sum(query_telegram_counts.values()),
        )
        discovery_summary["run_started_at"] = run_started_at
        discovery_summary["elapsed_ms"] = round(
            (time.perf_counter() - run_clock_started) * 1000,
            2,
        )
        raw_count = int(discovery_summary.get("raw_jobs_found") or len(jobs))
        rejected_count = max(0, raw_count - len(eligible_jobs))
        record_source_metrics(
            "JobSpy",
            raw_jobs=raw_count,
            eligible_jobs=len(eligible_jobs),
            inserted_jobs=inserted_count,
            duplicate_jobs=duplicate_count,
            rejected_jobs=rejected_count,
            provider_used=",".join(sites),
            filter_summary=discovery_summary,
        )

    except Exception as error:
        record_failed_run(error)
        raise

    emit_source_run_result({
                "success": True,
                "mode": "hourly-worker",
                "source": "JobSpy",
                "worker_action": "run",
                "run_trigger": (
                    "manual_run_now"
                    if args.run_now
                    else "cadence_due"
                ),
                "configuration_source": (
                    "SQLite dashboard"
                ),
                "sites": sites,
                **discovery_summary,
                "jobs_inserted": inserted_count,
                "inserted_job_ids": [
                    item["job_id"]
                    for item in stored_results
                    if item["inserted"]
                ],
                "database_duplicates": (
                    duplicate_count
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
                "telegram_dispatch_errors": (
                    telegram_result["errors"]
                ),
                "n8n_calls": 0,
            })


if __name__ == "__main__":
    run_guarded_main("JobSpy", main)
