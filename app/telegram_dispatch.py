from __future__ import annotations

import argparse
import json
from typing import Any

from app.database import get_connection, get_setting
from app.runtime_config import telegram_batch_limit
from app.telegram_client import ensure_delivery_claims_schema, send_job_card


def _telegram_contract() -> dict[str, Any]:
    return dict(get_setting("downstream_contract", {}) or {})


def _blocked_statuses() -> set[str]:
    return {
        str(value).strip().casefold()
        for value in _telegram_contract().get("telegram_blocked_job_statuses") or []
        if str(value).strip()
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send unsent stored jobs to Telegram."
        )
    )

    parser.add_argument(
        "--source-prefix",
        default=None,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    return parser.parse_args()


def load_eligible_jobs(
    *,
    source_prefix: str,
    limit: int,
) -> list[dict[str, Any]]:
    bounded_limit = telegram_batch_limit(limit)
    connection = get_connection()

    try:
        ensure_delivery_claims_schema(connection)
        rows = connection.execute(
            """
            SELECT
                id,
                source,
                company_name,
                title,
                location_raw,
                hunter_score,
                match_label,
                status,
                telegram_sent,
                sent_to_n8n
            FROM jobs
            WHERE
                source LIKE ?
                AND telegram_sent = 0
                AND NOT EXISTS (
                    SELECT 1 FROM telegram_delivery_claims claim
                    WHERE claim.job_id=jobs.id
                )
            ORDER BY
                hunter_score DESC,
                id ASC
            LIMIT ?
            """,
            (
                f"{source_prefix}%",
                bounded_limit,
            ),
        ).fetchall()
    finally:
        connection.close()

    jobs = []
    blocked_statuses = _blocked_statuses()

    for row in rows:
        job = dict(row)

        status = str(
            job.get("status") or ""
        ).strip().lower()

        if status in blocked_statuses:
            continue

        jobs.append(job)

    return jobs


def job_still_unsent(job_id: int) -> bool:
    connection = get_connection()

    try:
        ensure_delivery_claims_schema(connection)
        row = connection.execute(
            """
            SELECT jobs.telegram_sent,
                   EXISTS(SELECT 1 FROM telegram_delivery_claims claim WHERE claim.job_id=jobs.id)
                     AS telegram_delivery_claimed
            FROM jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    finally:
        connection.close()

    return bool(
        row is not None
        and int(row["telegram_sent"] or 0) == 0
        and int(row["telegram_delivery_claimed"] or 0) == 0
    )


def main() -> None:
    args = parse_args()
    contract = _telegram_contract()
    source_prefix = str(
        args.source_prefix
        if args.source_prefix is not None
        else contract.get("telegram_default_source_prefix") or ""
    )
    limit = telegram_batch_limit(args.limit)

    jobs = load_eligible_jobs(
        source_prefix=source_prefix,
        limit=limit,
    )

    preview = [
        {
            "job_id": int(job["id"]),
            "company": job["company_name"],
            "title": job["title"],
            "location": job["location_raw"],
            "hunter_score": job["hunter_score"],
            "match_label": job["match_label"],
        }
        for job in jobs
    ]

    if args.dry_run:
        print(
            json.dumps(
                {
                    "success": True,
                    "mode": "telegram-dispatch-dry-run",
                    "source_prefix": (
                        source_prefix
                    ),
                    "eligible_jobs": len(jobs),
                    "jobs": preview,
                    "telegram_messages_sent": 0,
                    "n8n_calls": 0,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    sent = []
    skipped = []
    errors = []

    for job in jobs:
        job_id = int(job["id"])

        if not job_still_unsent(job_id):
            skipped.append(
                {
                    "job_id": job_id,
                    "reason": "already_sent",
                }
            )
            continue

        try:
            message_id = send_job_card(
                job_id
            )

            sent.append(
                {
                    "job_id": job_id,
                    "message_id": message_id,
                    "company": (
                        job["company_name"]
                    ),
                    "title": job["title"],
                }
            )

        except Exception as error:
            errors.append(
                {
                    "job_id": job_id,
                    "error": str(error),
                }
            )

    print(
        json.dumps(
            {
                "success": not errors,
                "mode": "telegram-dispatch",
                "source_prefix": (
                    source_prefix
                ),
                "eligible_jobs": len(jobs),
                "telegram_messages_sent": len(
                    sent
                ),
                "sent": sent,
                "skipped": skipped,
                "errors": errors,
                "n8n_calls": 0,
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
