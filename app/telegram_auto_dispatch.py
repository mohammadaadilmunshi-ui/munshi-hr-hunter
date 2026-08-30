from __future__ import annotations

import json
from typing import Any

from app.database import (
    get_connection,
    get_setting,
)
from app.telegram_client import ensure_delivery_claims_schema, send_job_card
from app.dedupe_policy import dedupe_keeper_allowed


def telegram_contract() -> dict[str, Any]:
    return dict(get_setting("downstream_contract", {}) or {})


def blocked_statuses() -> set[str]:
    return {
        str(value).strip().casefold()
        for value in telegram_contract().get("telegram_blocked_job_statuses") or []
        if str(value).strip()
    }


def telegram_runtime_enabled() -> bool:
    runtime = get_setting(
        "runtime",
        {},
    ) or {}

    return bool(
        runtime.get(
            "telegram_enabled",
            False,
        )
    )


def telegram_score_threshold() -> float:
    scoring = get_setting(
        "scoring",
        {},
    ) or {}

    try:
        return float(scoring["telegram_all_jobs_threshold"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(
            "Canonical scoring.telegram_all_jobs_threshold is missing or invalid."
        ) from None


def load_pending_jobs(
    *,
    source_prefix: str,
    limit: int,
) -> list[dict[str, Any]]:
    threshold = telegram_score_threshold()
    blocked = blocked_statuses()
    try:
        max_batch = max(1, int(telegram_contract()["telegram_max_batch_size"]))
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(
            "Canonical downstream telegram_max_batch_size is missing or invalid."
        ) from None

    connection = get_connection()

    # Pull a slightly wider candidate window, then re-check current targeting
    # before returning a card candidate. This closes the race between source
    # persistence and the quarantine wrapper.
    scan_limit = max(max_batch, min(max(1, int(limit)) * 10, max_batch * 10))
    placeholders = ",".join("?" for _ in blocked) or "?"
    blocked_parameters = sorted(blocked) or ["__none__"]
    try:
        ensure_delivery_claims_schema(connection)
        rows = connection.execute(
            f"""
            SELECT *
            FROM jobs
            WHERE
                source LIKE ?
                AND telegram_sent = 0
                AND lower(COALESCE(status, 'found')) NOT IN ({placeholders})
                AND NOT EXISTS (
                    SELECT 1 FROM telegram_delivery_claims claim
                    WHERE claim.job_id=jobs.id
                )
                AND trim(COALESCE(hard_rejection_reason, '')) = ''
                AND COALESCE(
                    hunter_score,
                    0
                ) >= ?
            ORDER BY
                hunter_score DESC,
                id ASC
            LIMIT ?
            """,
            (
                f"{source_prefix}%",
                *blocked_parameters,
                threshold,
                scan_limit,
            ),
        ).fetchall()
    finally:
        connection.close()

    try:
        from app.dashboard_targeting_gate import load_dashboard_targeting_rules

        dedupe_rules = load_dashboard_targeting_rules()
    except Exception:
        dedupe_rules = None

    jobs: list[dict[str, Any]] = []

    for row in rows:
        job = dict(row)

        status = str(
            job.get("status") or ""
        ).strip().lower()

        if status in blocked:
            continue

        if not dedupe_keeper_allowed(job, rules=dedupe_rules):
            continue

        jobs.append(job)
        if len(jobs) >= max(1, min(int(limit), max_batch)):
            break

    return jobs


def job_is_still_pending(
    job_id: int,
) -> bool:
    connection = get_connection()

    try:
        ensure_delivery_claims_schema(connection)
        row = connection.execute(
            """
            SELECT jobs.*,
                   EXISTS(SELECT 1 FROM telegram_delivery_claims claim WHERE claim.job_id=jobs.id)
                     AS telegram_delivery_claimed
            FROM jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return False

    job = dict(row)
    if int(job.get("telegram_sent") or 0) != 0:
        return False
    if int(job.get("telegram_delivery_claimed") or 0) != 0:
        return False
    if str(job.get("status") or "").strip().casefold() in blocked_statuses():
        return False
    if str(job.get("hard_rejection_reason") or "").strip():
        return False

    return dedupe_keeper_allowed(job)


def verify_delivery(
    job_id: int,
) -> None:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT telegram_sent
            FROM jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        raise RuntimeError(
            f"Job {job_id} disappeared "
            "during Telegram delivery."
        )

    if int(
        row["telegram_sent"] or 0
    ) != 1:
        raise RuntimeError(
            f"Job {job_id} was sent, but "
            "telegram_sent was not updated."
        )


def record_event(
    *,
    event_type: str,
    event_status: str,
    payload: dict[str, Any],
    job_id: int | None = None,
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
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                job_id,
                event_type,
                "hourly_worker",
                event_status,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()
    finally:
        connection.close()


def dispatch_unsent_jobs(
    *,
    source_prefix: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    contract = telegram_contract()
    try:
        source_prefix = str(
            source_prefix
            if source_prefix is not None
            else contract["telegram_default_source_prefix"]
        )
        limit = int(
            limit
            if limit is not None
            else contract["telegram_default_batch_limit"]
        )
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Canonical Telegram dispatch bounds are incomplete.") from None
    enabled = telegram_runtime_enabled()

    if not enabled:
        return {
            "telegram_enabled": False,
            "source_prefix": source_prefix,
            "eligible_jobs": 0,
            "telegram_messages_sent": 0,
            "sent": [],
            "skipped": [
                {
                    "reason": (
                        "telegram_disabled_in_dashboard"
                    ),
                }
            ],
            "errors": [],
            "n8n_calls": 0,
        }

    jobs = load_pending_jobs(
        source_prefix=source_prefix,
        limit=limit,
    )

    sent: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for job in jobs:
        job_id = int(job["id"])

        if not job_is_still_pending(job_id):
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

            verify_delivery(job_id)

            delivery = {
                "job_id": job_id,
                "company": (
                    job["company_name"]
                ),
                "title": job["title"],
                "telegram_message_id": (
                    message_id
                ),
            }

            sent.append(delivery)

            record_event(
                job_id=job_id,
                event_type=(
                    "telegram_job_auto_delivered"
                ),
                event_status="completed",
                payload=delivery,
            )

        except Exception as error:
            failure = {
                "job_id": job_id,
                "company": (
                    job["company_name"]
                ),
                "title": job["title"],
                "error": str(error),
            }

            errors.append(failure)

            record_event(
                job_id=job_id,
                event_type=(
                    "telegram_dispatch_failed"
                ),
                event_status="failed",
                payload=failure,
            )

    result = {
        "telegram_enabled": True,
        "source_prefix": source_prefix,
        "score_threshold": (
            telegram_score_threshold()
        ),
        "eligible_jobs": len(jobs),
        "telegram_messages_sent": len(
            sent
        ),
        "sent": sent,
        "skipped": skipped,
        "errors": errors,
        "n8n_calls": 0,
    }

    record_event(
        event_type=(
            "telegram_dispatch_completed"
        ),
        event_status=(
            "completed"
            if not errors
            else "partial"
        ),
        payload=result,
    )

    return result


def attribute_dispatch_to_current_jobs(
    dispatch: dict[str, Any],
    current_job_ids: list[int] | set[int] | tuple[int, ...],
) -> dict[str, int]:
    """Separate current-cycle cards from valid source backlog delivery."""
    current_ids = {int(value) for value in current_job_ids}
    sent_ids = {
        int(item["job_id"])
        for item in dispatch.get("sent") or []
        if item.get("job_id") is not None
    }
    total = int(dispatch.get("telegram_messages_sent") or len(sent_ids))
    current = len(sent_ids & current_ids)
    return {
        "total_messages": total,
        "current_run_messages": current,
        "backlog_messages": max(0, total - current),
    }

# UNIVERSAL_TELEGRAM_QUALITY_GUARD_V1

# AADIL_TELEGRAM_DASHBOARD_TARGETING_RECHECK_V2
_aadil_dispatch_unsent_jobs_before_targeting_v2 = dispatch_unsent_jobs


def dispatch_unsent_jobs(*args: Any, **kwargs: Any) -> Any:
    from app.strict_dashboard_targeting_v2 import quarantine_unsent_adapter_jobs

    source_prefix = kwargs.get("source_prefix")
    if source_prefix is None and args:
        source_prefix = args[0]
    if kwargs.get("limit") is not None:
        limit = int(kwargs["limit"])
    else:
        try:
            limit = int(telegram_contract()["telegram_default_batch_limit"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError(
                "Canonical telegram_default_batch_limit is missing or invalid."
            ) from None
    quarantine = quarantine_unsent_adapter_jobs(
        source_prefix=source_prefix,
        limit=max(1000, limit * 50),
    )
    result = _aadil_dispatch_unsent_jobs_before_targeting_v2(*args, **kwargs)
    if isinstance(result, dict):
        result = dict(result)
        result["dashboard_targeting_quarantine"] = quarantine
        result["strict_dashboard_targeting"] = True
    return result
