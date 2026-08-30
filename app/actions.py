from __future__ import annotations

import json
from typing import Any

from app.database import (
    get_connection,
    get_setting,
    save_setting,
)


VALID_ACTIONS = {
    "hold",
    "approve_for_n8n",
    "already_applied",
    "reject_similar",
    "blacklist_company",
    "boost_company",
    "restore",
}


def normalize_company(value: Any) -> str:
    return " ".join(
        str(value or "").strip().lower().split()
    )


def add_unique_company(
    companies: list[str],
    company_name: str,
) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()

    for company in companies + [company_name]:
        cleaned = str(company or "").strip()

        if not cleaned:
            continue

        normalized = normalize_company(cleaned)

        if normalized in seen:
            continue

        seen.add(normalized)
        output.append(cleaned)

    return output


def remove_company(
    companies: list[str],
    company_name: str,
) -> list[str]:
    normalized_target = normalize_company(company_name)

    return [
        company
        for company in companies
        if normalize_company(company)
        != normalized_target
    ]


def get_job(job_id: int) -> dict[str, Any] | None:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    finally:
        connection.close()

    return dict(row) if row else None


def get_company_rule_state(
    company_name: str,
) -> str:
    targeting = get_setting("targeting", {})

    normalized_company = normalize_company(
        company_name
    )

    blacklist = {
        normalize_company(company)
        for company in targeting.get(
            "company_blacklist",
            [],
        )
    }

    watchlist = {
        normalize_company(company)
        for company in targeting.get(
            "company_watchlist",
            [],
        )
    }

    if normalized_company in blacklist:
        return "blacklisted"

    if normalized_company in watchlist:
        return "boosted"

    return "normal"


def record_event(
    job_id: int,
    event_type: str,
    actor: str,
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
            VALUES (?, ?, ?, 'recorded', ?)
            """,
            (
                job_id,
                event_type,
                actor,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()
    finally:
        connection.close()


def get_match_label(
    score: int,
    cpt_trapdoor: bool,
    scoring: dict[str, Any],
) -> str:
    auto_threshold = int(
        scoring.get(
            "auto_n8n_threshold",
            93,
        )
    )

    high_threshold = int(
        scoring.get(
            "telegram_high_alert_threshold",
            85,
        )
    )

    if score >= auto_threshold:
        label = "URGENT MATCH"
    elif score >= high_threshold:
        label = "HIGH MATCH"
    elif score >= 75:
        label = "GOOD MATCH"
    else:
        label = "LOW PRIORITY"

    if cpt_trapdoor:
        label += " - CPT REVIEW"

    return label


def blacklist_existing_company_jobs(
    company_name: str,
) -> int:
    connection = get_connection()
    affected = 0

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE LOWER(TRIM(company_name))
                = LOWER(TRIM(?))
            """,
            (company_name,),
        ).fetchall()

        for row in rows:
            try:
                breakdown = json.loads(
                    row["score_breakdown_json"]
                    or "{}"
                )
            except (
                TypeError,
                json.JSONDecodeError,
            ):
                breakdown = {}

            breakdown.update(
                {
                    "company_score": 0,
                    "hard_rejection_reason":
                        "Company is blacklisted",
                    "final_score": 0,
                }
            )

            connection.execute(
                """
                UPDATE jobs
                SET
                    status = 'blacklisted',
                    hunter_score = 0,
                    match_label = 'REJECTED',
                    hard_rejection_reason =
                        'Company is blacklisted',
                    score_breakdown_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    json.dumps(
                        breakdown,
                        ensure_ascii=False,
                    ),
                    row["id"],
                ),
            )

            affected += 1

        connection.commit()
    finally:
        connection.close()

    return affected


def boost_existing_company_jobs(
    company_name: str,
) -> int:
    scoring = get_setting("scoring", {})
    authorization = get_setting(
        "authorization",
        {},
    )

    connection = get_connection()
    affected = 0

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE LOWER(TRIM(company_name))
                = LOWER(TRIM(?))
            """,
            (company_name,),
        ).fetchall()

        for row in rows:
            try:
                breakdown = json.loads(
                    row["score_breakdown_json"]
                    or "{}"
                )
            except (
                TypeError,
                json.JSONDecodeError,
            ):
                breakdown = {}

            if not breakdown:
                continue

            previous_company_score = float(
                breakdown.get(
                    "company_score",
                    0,
                )
                or 0
            )

            previous_raw_score = float(
                breakdown.get(
                    "raw_score",
                    row["hunter_score"] or 0,
                )
                or 0
            )

            new_company_score = 10

            raw_score = (
                previous_raw_score
                - previous_company_score
                + new_company_score
            )

            final_score = max(
                0,
                min(
                    int(round(raw_score)),
                    100,
                ),
            )

            cpt_trapdoor = bool(
                row["cpt_trapdoor"]
            )

            if cpt_trapdoor:
                final_score = min(
                    final_score,
                    int(
                        authorization.get(
                            "immediate_start_score_cap",
                            84,
                        )
                    ),
                )

            age_days = breakdown.get("age_days")

            if (
                age_days is not None
                and age_days > 45
            ):
                final_score = min(
                    final_score,
                    int(
                        scoring.get(
                            "ghost_job_score_cap",
                            92,
                        )
                    ),
                )

            match_label = get_match_label(
                final_score,
                cpt_trapdoor,
                scoring,
            )

            current_status = str(
                row["status"] or "found"
            )

            if current_status == "blacklisted":
                current_status = "found"

            breakdown.update(
                {
                    "company_score":
                        new_company_score,
                    "raw_score": raw_score,
                    "final_score": final_score,
                    "hard_rejection_reason": None,
                }
            )

            connection.execute(
                """
                UPDATE jobs
                SET
                    status = ?,
                    hunter_score = ?,
                    match_label = ?,
                    hard_rejection_reason = NULL,
                    score_breakdown_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    current_status,
                    final_score,
                    match_label,
                    json.dumps(
                        breakdown,
                        ensure_ascii=False,
                    ),
                    row["id"],
                ),
            )

            affected += 1

        connection.commit()
    finally:
        connection.close()

    return affected


def _apply_job_action_local(
    job_id: int,
    action: str,
    actor: str = "Aadil",
) -> tuple[bool, str]:
    if action not in VALID_ACTIONS:
        return False, f"Unsupported action: {action}"

    job = get_job(job_id)

    if not job:
        return False, f"Job {job_id} was not found."

    company_name = job["company_name"]
    title = job["title"]

    if action in {
        "hold",
        "approve_for_n8n",
        "already_applied",
        "reject_similar",
        "restore",
    }:
        connection = get_connection()

        try:
            if action == "hold":
                connection.execute(
                    """
                    UPDATE jobs
                    SET
                        status = 'held',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (job_id,),
                )

                message = "Job placed on hold."

            elif action == "approve_for_n8n":
                connection.execute(
                    """
                    UPDATE jobs
                    SET
                        status = 'approved_for_n8n',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (job_id,),
                )

                message = (
                    "Job approved locally for n8n. "
                    "No webhook was called."
                )

            elif action == "already_applied":
                connection.execute(
                    """
                    UPDATE jobs
                    SET
                        status = 'already_applied',
                        already_applied = 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (job_id,),
                )

                message = (
                    "Job marked as already applied."
                )

            elif action == "reject_similar":
                connection.execute(
                    """
                    UPDATE jobs
                    SET
                        status = 'rejected_similar',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (job_id,),
                )

                message = (
                    "Job marked as rejected similar."
                )

            else:
                connection.execute(
                    """
                    UPDATE jobs
                    SET
                        status = 'found',
                        already_applied = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (job_id,),
                )

                message = (
                    "Job restored to found status."
                )

            connection.commit()
        finally:
            connection.close()

    elif action == "blacklist_company":
        targeting = get_setting(
            "targeting",
            {},
        )

        targeting["company_blacklist"] = (
            add_unique_company(
                targeting.get(
                    "company_blacklist",
                    [],
                ),
                company_name,
            )
        )

        targeting["company_watchlist"] = (
            remove_company(
                targeting.get(
                    "company_watchlist",
                    [],
                ),
                company_name,
            )
        )

        save_setting("targeting", targeting)

        affected = blacklist_existing_company_jobs(
            company_name
        )

        message = (
            f"{company_name} blacklisted. "
            f"{affected} stored job(s) rejected."
        )

    else:
        targeting = get_setting(
            "targeting",
            {},
        )

        targeting["company_watchlist"] = (
            add_unique_company(
                targeting.get(
                    "company_watchlist",
                    [],
                ),
                company_name,
            )
        )

        targeting["company_blacklist"] = (
            remove_company(
                targeting.get(
                    "company_blacklist",
                    [],
                ),
                company_name,
            )
        )

        save_setting("targeting", targeting)

        affected = boost_existing_company_jobs(
            company_name
        )

        message = (
            f"{company_name} boosted. "
            f"{affected} stored job(s) rescored."
        )

    record_event(
        job_id=job_id,
        event_type=f"job_action_{action}",
        actor=actor,
        payload={
            "job_id": job_id,
            "company_name": company_name,
            "title": title,
            "action": action,
            "message": message,
        },
    )

    return True, message

# BIDIRECTIONAL_TELEGRAM_SYNC_WRAPPER
def apply_job_action(*args, **kwargs):
    """
    Run the original local action, then synchronize the latest
    Telegram card when the action originated outside Telegram.
    """
    result = _apply_job_action_local(
        *args,
        **kwargs,
    )

    success, message = result

    job_id = kwargs.get("job_id")

    if job_id is None and len(args) >= 1:
        job_id = args[0]

    actor = kwargs.get("actor")

    if actor is None and len(args) >= 3:
        actor = args[2]

    normalized_actor = str(
        actor or "system"
    ).strip().lower()

    if (
        success
        and job_id is not None
        and normalized_actor != "telegram"
    ):
        try:
            from app.telegram_sync import (
                sync_latest_job_card,
            )

            sync_result = sync_latest_job_card(
                int(job_id),
                notice=message,
                actor=(
                    f"{normalized_actor}_action"
                ),
            )

            if not sync_result.get("success"):
                print(
                    "Telegram synchronization skipped "
                    "or failed:",
                    sync_result,
                )

        except Exception as error:
            # A Telegram problem must never undo a valid
            # local dashboard/database action.
            print(
                "Telegram synchronization error:",
                error,
            )

    return result

# AADIL_STORED_JOB_APPROVE_AND_RUN_N8N_V1
import os as _aadil_stored_os

_aadil_original_apply_job_action_v1 = apply_job_action


def apply_job_action(*args, **kwargs):
    success, message = _aadil_original_apply_job_action_v1(
        *args,
        **kwargs,
    )

    job_id = kwargs.get("job_id")
    action = kwargs.get("action")
    actor = kwargs.get("actor")

    if job_id is None and len(args) >= 1:
        job_id = args[0]
    if action is None and len(args) >= 2:
        action = args[1]
    if actor is None and len(args) >= 3:
        actor = args[2]
    actor_name = str(actor or "").strip().casefold()

    should_start = (
        success
        and str(action or "") == "approve_for_n8n"
        and actor_name in {
            "telegram",
            "aadil",
            "telegram_stored",
            "telegram_control_center",
        }
        and str(
            _aadil_stored_os.getenv("AADIL_SKIP_STORED_JOB_AUTO_DISPATCH")
            or ""
        ).strip().casefold()
        not in {"1", "true", "yes", "on"}
    )

    if not should_start:
        return success, message

    try:
        from app.stored_job_n8n_worker import start_stored_job_run

        started = start_stored_job_run(
            int(job_id),
            actor=str(actor or "telegram"),
        )
    except Exception as error:
        return True, (
            "Job was approved locally, but the guarded n8n worker "
            f"could not start: {error}"
        )

    return True, str(
        started.get("message")
        or "Stored-job n8n action processed."
    )
