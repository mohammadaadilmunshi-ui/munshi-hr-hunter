from __future__ import annotations

import argparse
import json
from typing import Any

from app.database import (
    get_connection,
    initialize_database,
)
from app.discovery_config import (
    build_location_search_plan,
    build_search_term,
    load_target_roles,
)
from app.hunter_worker import (
    deduplicate_jobs,
    matches_location_rule,
)
from app.job_store import save_job
from app.relevance import match_target_role
from app.sources.jobspy import fetch_jobspy_jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--sites",
        default="indeed",
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

    return parser.parse_args()


def collect_jobs(
    args: argparse.Namespace,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
]:
    target_roles = load_target_roles()
    search_term = build_search_term(
        target_roles
    )
    search_plan = build_location_search_plan()

    sites = [
        site.strip()
        for site in args.sites.split(",")
        if site.strip()
    ]

    matched_jobs: list[dict[str, Any]] = []
    excluded_role = 0
    excluded_location = 0
    raw_jobs_found = 0
    errors: list[dict[str, Any]] = []

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
        raw_jobs_found += len(raw_jobs)

        for error in result.get("errors") or []:
            errors.append(
                {
                    "rule_name": (
                        plan["rule_name"]
                    ),
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
                excluded_role += 1
                continue

            (
                location_matches,
                location_reason,
            ) = matches_location_rule(
                job,
                plan,
            )

            if not location_matches:
                excluded_location += 1
                continue

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
                location_reason
            )

            matched_jobs.append(job)

    unique_jobs, run_duplicates = (
        deduplicate_jobs(matched_jobs)
    )

    summary = {
        "configuration_source": (
            "SQLite dashboard"
        ),
        "location_values_hardcoded": False,
        "target_roles_hardcoded": False,
        "raw_jobs_found": raw_jobs_found,
        "excluded_by_role": excluded_role,
        "excluded_by_location": (
            excluded_location
        ),
        "duplicates_within_run": len(
            run_duplicates
        ),
        "unique_jobs_ready": len(
            unique_jobs
        ),
        "errors": errors,
    }

    return unique_jobs, summary


def main() -> None:
    args = parse_args()

    initialize_database()

    jobs, discovery_summary = collect_jobs(
        args
    )

    connection = get_connection()

    try:
        before_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM jobs
            """
        ).fetchone()[0]

        first_pass = [
            save_job(
                connection,
                job,
                actor="jobspy_store_test",
            )
            for job in jobs
        ]

        connection.commit()

        replay_pass = [
            save_job(
                connection,
                job,
                actor=(
                    "jobspy_idempotency_test"
                ),
            )
            for job in jobs
        ]

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
                health_status = 'healthy',
                last_error = NULL,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE source_name = 'JobSpy'
            """,
            (len(jobs),),
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
                'source_run_completed',
                'jobspy_store_test',
                'completed',
                ?
            )
            """,
            (
                json.dumps(
                    {
                        **discovery_summary,
                        "first_pass": first_pass,
                        "replay_pass": (
                            replay_pass
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()

        after_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM jobs
            """
        ).fetchone()[0]

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    output = {
        "success": True,
        "mode": "jobspy-sqlite-store-test",
        "database_writes": True,
        "telegram_messages": 0,
        "n8n_calls": 0,
        **discovery_summary,
        "jobs_before": before_count,
        "jobs_after": after_count,
        "database_count_delta": (
            after_count - before_count
        ),
        "first_pass_inserted": sum(
            1
            for item in first_pass
            if item["inserted"]
        ),
        "first_pass_duplicates": sum(
            1
            for item in first_pass
            if not item["inserted"]
        ),
        "replay_inserted": sum(
            1
            for item in replay_pass
            if item["inserted"]
        ),
        "replay_duplicates": sum(
            1
            for item in replay_pass
            if not item["inserted"]
        ),
        "stored_jobs": first_pass,
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
