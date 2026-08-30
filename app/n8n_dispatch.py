from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.job_detail import build_manual_job_text as build_structured_manual_job_text
from app.database import DB_PATH, get_connection, get_setting as get_canonical_setting
from app.application_runs_v1 import enhance_dispatch_payload, hard_work_authorization_block
from app.targeting import evaluate_job, load_rules


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"


def _downstream_contract() -> dict[str, Any]:
    return dict(get_canonical_setting("downstream_contract", {}) or {})


def _required_scoring_value(scoring: dict[str, Any], key: str, value_type: type) -> Any:
    try:
        value = value_type(scoring[key])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(f"Required canonical scoring setting is missing or invalid: {key}") from None
    return value


def _required_contract_number(key: str, value_type: type) -> Any:
    contract = _downstream_contract()
    try:
        value = value_type(contract[key])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(f"Required downstream contract setting is missing or invalid: {key}") from None
    return value


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def load_dotenv_value(
    key: str,
) -> str:
    environment_value = str(
        os.getenv(key)
        or ""
    ).strip()

    if environment_value:
        return environment_value

    if not ENV_PATH.exists():
        return ""

    for raw_line in ENV_PATH.read_text().splitlines():
        line = raw_line.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        candidate_key, value = line.split(
            "=",
            1,
        )

        if candidate_key.strip() != key:
            continue

        return value.strip().strip(
            "\"'"
        )

    return ""


def load_setting(
    connection: sqlite3.Connection,
    setting_key: str,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT value_json
        FROM settings
        WHERE setting_key = ?
        """,
        (setting_key,),
    ).fetchone()

    if row is None:
        return dict(fallback)

    try:
        value = json.loads(
            row["value_json"]
        )
    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return dict(fallback)

    if not isinstance(value, dict):
        return dict(fallback)

    return value


def ensure_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS
            n8n_dispatch_queue (
                id INTEGER PRIMARY KEY
                    AUTOINCREMENT,

                job_id INTEGER NOT NULL
                    UNIQUE,

                job_fingerprint TEXT NOT NULL,

                dispatch_mode TEXT NOT NULL,

                idempotency_key TEXT NOT NULL
                    UNIQUE,

                request_id TEXT NOT NULL
                    UNIQUE,

                queue_status TEXT NOT NULL
                    DEFAULT 'pending',

                webhook_mode TEXT NOT NULL
                    DEFAULT 'production',

                attempt_count INTEGER NOT NULL
                    DEFAULT 0,

                http_status INTEGER,

                response_text TEXT,

                last_error TEXT,

                queued_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                reserved_at TEXT,

                accepted_at TEXT,

                completed_at TEXT,

                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(job_id)
                    REFERENCES jobs(id)
                    ON DELETE CASCADE
            );

        CREATE INDEX IF NOT EXISTS
            idx_n8n_dispatch_queue_status
        ON n8n_dispatch_queue (
            queue_status,
            queued_at
        );

        CREATE INDEX IF NOT EXISTS
            idx_n8n_dispatch_queue_mode
        ON n8n_dispatch_queue (
            dispatch_mode,
            queued_at
        );
        """
    )


def normalize_status(
    value: Any,
) -> str:
    return str(
        value or "found"
    ).strip().casefold()


def is_base_eligible(
    job: dict[str, Any],
) -> bool:
    blocked_statuses = {
        normalize_status(value)
        for value in (_downstream_contract().get("blocked_job_statuses") or [])
    }
    if normalize_status(
        job.get("status")
    ) in blocked_statuses:
        return False

    if str(
        job.get(
            "hard_rejection_reason"
        )
        or ""
    ).strip():
        return False

    if int(
        job.get("sent_to_n8n")
        or 0
    ) == 1:
        return False

    if int(
        job.get("already_applied")
        or 0
    ) == 1:
        return False

    return True


def is_manual_eligible(
    job: dict[str, Any],
) -> bool:
    blocked, _reason = hard_work_authorization_block(job)
    return bool(
        not blocked
        and is_base_eligible(job)
        and normalize_status(
            job.get("status")
        )
        == "approved_for_n8n"
    )


def is_auto_eligible(
    job: dict[str, Any],
    threshold: float,
    *,
    targeting_rules: Any | None = None,
) -> bool:
    source = str(
        job.get("source")
        or ""
    ).strip().casefold()

    excluded_sources = {
        str(value).strip().casefold()
        for value in (_downstream_contract().get("excluded_auto_sources") or [])
    }
    if source in excluded_sources:
        return False

    blocked, _reason = hard_work_authorization_block(job)
    if (
        blocked
        or not is_base_eligible(job)
        or normalize_status(job.get("status")) != "found"
        or float(job.get("hunter_score") or 0) < threshold
        or int(job.get("cpt_trapdoor") or 0) != 0
    ):
        return False

    # Historical rows can retain a high score and status='found' after the
    # canonical targeting policy changes. Automatic dispatch must therefore
    # re-evaluate current policy at the final producer boundary. Explicit
    # telegram/manual approvals remain a separate human-controlled path.
    decision = evaluate_job(
        job,
        targeting_rules or load_rules(),
    )
    return bool(
        decision.get("accepted")
        and decision.get("primary_category") == "ELIGIBLE"
    )


def safe_text(
    value: Any,
    fallback: str = "Not specified",
) -> str:
    text = str(
        value or ""
    ).strip()

    return text or fallback


def build_manual_job_text(
    job: dict[str, Any],
) -> str:
    return build_structured_manual_job_text(job)


def create_idempotency_key(
    job: dict[str, Any],
) -> str:
    fingerprint = str(
        job.get("job_fingerprint")
        or ""
    ).strip()

    if not fingerprint:
        raise ValueError(
            "Job fingerprint is required."
        )

    queue_version = str(_downstream_contract().get("queue_version") or "")
    if not queue_version:
        raise ValueError("The downstream queue version is not configured.")
    material = f"{queue_version}|{fingerprint}"

    return hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()


def create_request_id(
    job_id: int,
    dispatch_mode: str,
) -> str:
    return (
        f"hunter_{job_id}_"
        f"{dispatch_mode}_"
        f"{uuid.uuid4().hex}"
    )


def build_payload(
    job: dict[str, Any],
    queue: dict[str, Any],
    *,
    webhook_mode: str,
) -> dict[str, Any]:
    test_mode = (
        webhook_mode == "test"
    )

    contract = _downstream_contract()
    callback_url = str(contract.get("callback_url") or "")
    execution_scope = str(
        contract.get("test_execution_scope" if test_mode else "production_execution_scope") or ""
    )
    payload = {
        "source_system": str(contract.get("source_system") or ""),
        "schema_version": str(contract.get("payload_schema_version") or ""),
        "workflow_target": str(contract.get("workflow_target") or ""),

        "row_id": int(job["id"]),
        "hunter_row_id": int(
            job["id"]
        ),

        "job_fingerprint": str(
            job["job_fingerprint"]
        ),
        "idempotency_key": str(
            queue["idempotency_key"]
        ),

        "hunter_score": float(
            job.get("hunter_score")
            or 0
        ),
        "match_label": str(
            job.get("match_label")
            or ""
        ),
        "target_track": str(
            job.get("target_track")
            or "General HR"
        ),

        "manual_job_text": (
            build_manual_job_text(job)
        ),

        "dispatch_mode": str(
            queue["dispatch_mode"]
        ),
        "request_id": str(
            queue["request_id"]
        ),

        "execution_scope": execution_scope,
        "localhost_execution_scope": execution_scope,

        "test_mode": test_mode,

        "callback_enabled": True,
        "callback_url": callback_url,
        "localhost_callback_url": callback_url,

        "queue_version": str(contract.get("queue_version") or ""),
    }
    required = ("source_system", "schema_version", "workflow_target", "callback_url", "queue_version")
    missing = [key for key in required if not str(payload.get(key) or "").strip()]
    if missing:
        raise ValueError(f"Downstream contract is missing required fields: {missing}")
    return enhance_dispatch_payload(payload, job, queue)


def count_today(
    connection: sqlite3.Connection,
    dispatch_mode: str,
) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM n8n_dispatch_queue
        WHERE
            dispatch_mode = ?
            AND queue_status IN (
                'pending',
                'dispatching',
                'accepted',
                'completed'
            )
            AND date(
                queued_at,
                'localtime'
            ) = date(
                'now',
                'localtime'
            )
        """,
        (dispatch_mode,),
    ).fetchone()

    return int(
        row["total"]
        if row is not None
        else 0
    )


def load_unqueued_jobs(
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    completed_statuses = [
        normalize_status(value)
        for value in (_downstream_contract().get("completed_result_statuses") or [])
        if normalize_status(value)
    ]
    if not completed_statuses:
        raise RuntimeError("completed_result_statuses is not configured.")
    result_placeholders = ",".join("?" for _ in completed_statuses)
    rows = connection.execute(
        f"""
        SELECT j.*
        FROM jobs AS j
        LEFT JOIN n8n_dispatch_queue AS q
            ON q.job_id = j.id
        WHERE
            q.id IS NULL
            AND COALESCE(
                j.sent_to_n8n,
                0
            ) = 0
            AND NOT EXISTS (
                SELECT 1
                FROM n8n_results AS result
                WHERE result.job_id = j.id
                  AND lower(COALESCE(result.n8n_status, ''))
                      IN ({result_placeholders})
            )
        ORDER BY
            j.hunter_score DESC,
            j.id ASC
        """,
        completed_statuses,
    ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def has_completed_result(
    connection: sqlite3.Connection,
    job_id: int,
) -> bool:
    completed_statuses = [
        normalize_status(value)
        for value in (_downstream_contract().get("completed_result_statuses") or [])
        if normalize_status(value)
    ]
    if not completed_statuses:
        raise RuntimeError("completed_result_statuses is not configured.")
    placeholders = ",".join("?" for _ in completed_statuses)
    row = connection.execute(
        f"""
        SELECT 1
        FROM n8n_results
        WHERE job_id = ?
          AND lower(COALESCE(n8n_status, '')) IN ({placeholders})
        LIMIT 1
        """,
        (int(job_id), *completed_statuses),
    ).fetchone()
    return row is not None


def plan_candidates(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    ensure_schema(connection)
    scoring = load_setting(
        connection,
        "scoring",
        {},
    )

    threshold = _required_scoring_value(scoring, "auto_n8n_threshold", float)
    auto_limit = _required_scoring_value(scoring, "daily_auto_n8n_limit", int)
    manual_limit = _required_scoring_value(scoring, "daily_manual_n8n_limit", int)

    auto_used = count_today(
        connection,
        "auto_top_match",
    )

    manual_used = count_today(
        connection,
        "telegram_manual",
    )

    jobs = load_unqueued_jobs(
        connection
    )

    manual_candidates = [
        job
        for job in jobs
        if is_manual_eligible(job)
    ][
        : max(
            0,
            manual_limit - manual_used,
        )
    ]

    auto_candidates: list[
        dict[str, Any]
    ] = []

    if auto_used < auto_limit:
        targeting_rules = load_rules()
        auto_candidates = [
            job
            for job in jobs
            if is_auto_eligible(
                job,
                threshold,
                targeting_rules=targeting_rules,
            )
        ][:1]

    return {
        "threshold": threshold,
        "auto_limit": auto_limit,
        "manual_limit": (
            manual_limit
        ),
        "auto_used_today": auto_used,
        "manual_used_today": (
            manual_used
        ),
        "manual_candidates": (
            manual_candidates
        ),
        "auto_candidates": (
            auto_candidates
        ),
    }


def insert_queue_item(
    connection: sqlite3.Connection,
    job: dict[str, Any],
    dispatch_mode: str,
    webhook_mode: str,
) -> dict[str, Any]:
    # Keep the queue contract self-initializing for isolated workers, tests,
    # and restored databases.  Production callers normally initialize this
    # schema during worker startup, but insertion must not depend on that
    # ordering.
    ensure_schema(connection)

    idempotency_key = (
        create_idempotency_key(job)
    )

    request_id = create_request_id(
        int(job["id"]),
        dispatch_mode,
    )

    connection.execute(
        """
        INSERT OR IGNORE INTO
            n8n_dispatch_queue (
                job_id,
                job_fingerprint,
                dispatch_mode,
                idempotency_key,
                request_id,
                queue_status,
                webhook_mode
            )
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            int(job["id"]),
            str(
                job["job_fingerprint"]
            ),
            dispatch_mode,
            idempotency_key,
            request_id,
            webhook_mode,
        ),
    )

    row = connection.execute(
        """
        SELECT *
        FROM n8n_dispatch_queue
        WHERE job_id = ?
        """,
        (int(job["id"]),),
    ).fetchone()

    if row is None:
        raise RuntimeError(
            "Queue insertion failed."
        )

    return dict(row)


def queue_candidates(
    *,
    webhook_mode: str,
    dry_run: bool,
) -> dict[str, Any]:
    connection = get_connection()

    try:
        ensure_schema(connection)

        plan = plan_candidates(
            connection
        )

        selected: list[
            tuple[
                dict[str, Any],
                str,
            ]
        ] = []

        selected.extend(
            (
                job,
                "telegram_manual",
            )
            for job in plan[
                "manual_candidates"
            ]
        )

        selected.extend(
            (
                job,
                "auto_top_match",
            )
            for job in plan[
                "auto_candidates"
            ]
        )

        queued = []

        if not dry_run:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            for job, dispatch_mode in selected:
                queue_row = insert_queue_item(
                    connection,
                    job,
                    dispatch_mode,
                    webhook_mode,
                )

                queued.append(
                    {
                        "job_id": int(
                            job["id"]
                        ),
                        "company": (
                            job[
                                "company_name"
                            ]
                        ),
                        "title": job[
                            "title"
                        ],
                        "hunter_score": (
                            job[
                                "hunter_score"
                            ]
                        ),
                        "dispatch_mode": (
                            dispatch_mode
                        ),
                        "queue_id": (
                            queue_row["id"]
                        ),
                        "queue_status": (
                            queue_row[
                                "queue_status"
                            ]
                        ),
                    }
                )

            connection.commit()

        else:
            queued = [
                {
                    "job_id": int(
                        job["id"]
                    ),
                    "company": (
                        job["company_name"]
                    ),
                    "title": job["title"],
                    "hunter_score": (
                        job["hunter_score"]
                    ),
                    "dispatch_mode": (
                        dispatch_mode
                    ),
                    "queue_id": None,
                    "queue_status": (
                        "dry_run"
                    ),
                }
                for job, dispatch_mode
                in selected
            ]

        return {
            "success": True,
            "dry_run": dry_run,
            "webhook_mode": (
                webhook_mode
            ),
            "threshold": plan[
                "threshold"
            ],
            "auto_limit": plan[
                "auto_limit"
            ],
            "manual_limit": plan[
                "manual_limit"
            ],
            "auto_used_today": plan[
                "auto_used_today"
            ],
            "manual_used_today": plan[
                "manual_used_today"
            ],
            "selected_count": len(
                selected
            ),
            "queued": queued,
            "n8n_calls": 0,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def webhook_url(
    webhook_mode: str,
) -> str:
    key = (
        "N8N_TEST_WEBHOOK_URL"
        if webhook_mode == "test"
        else (
            "N8N_PRODUCTION_WEBHOOK_URL"
        )
    )

    return load_dotenv_value(key)


def runtime_n8n_enabled(
    connection: sqlite3.Connection,
) -> bool:
    runtime = load_setting(
        connection,
        "runtime",
        {},
    )

    return bool(
        runtime.get(
            "n8n_enabled",
            False,
        )
    )


def record_event(
    connection: sqlite3.Connection,
    *,
    job_id: int | None,
    event_type: str,
    event_status: str,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO events (
            job_id,
            event_type,
            actor,
            event_status,
            payload_json
        )
        VALUES (?, ?, 'n8n_dispatcher', ?, ?)
        """,
        (
            job_id,
            event_type,
            event_status,
            json.dumps(
                payload,
                ensure_ascii=False,
            ),
        ),
    )


def revalidate_queue_item(
    job: dict[str, Any],
    queue: dict[str, Any],
    threshold: float,
    *,
    targeting_rules: Any | None = None,
) -> tuple[bool, str]:
    # AADIL_DISPATCH_TRUTH_AND_MANUAL_APPROVAL_V1
    dispatch_mode = str(queue["dispatch_mode"])

    # Queue creation is the explicit human approval boundary. Reapplying the
    # mutable job-status gate here previously cancelled accepted manual rows
    # with manual_job_no_longer_approved before the webhook was called.
    if dispatch_mode in {"telegram_manual", "telegram_force_rerun"}:
        return True, "explicit_manual_queue_approved"

    if dispatch_mode == "auto_top_match":
        if not is_auto_eligible(
            job,
            threshold,
            targeting_rules=targeting_rules,
        ):
            return False, "automatic_job_no_longer_eligible"
        return True, "automatic_eligible"

    return False, "unsupported_dispatch_mode"

def _dispatch_pending_core_all_issues_v1(
    *,
    webhook_mode: str,
    dry_run: bool,
    allow_disabled: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    connection = get_connection()

    try:
        ensure_schema(connection)

        scoring = load_setting(
            connection,
            "scoring",
            {},
        )

        threshold = _required_scoring_value(scoring, "auto_n8n_threshold", float)
        resolved_limit = (
            _required_contract_number("downstream_dispatch_batch_limit", int)
            if limit is None else int(limit)
        )
        webhook_timeout = _required_contract_number(
            "downstream_webhook_timeout_seconds", float
        )
        if resolved_limit < 1 or webhook_timeout <= 0:
            raise RuntimeError("Canonical downstream dispatch bounds are invalid.")

        enabled = runtime_n8n_enabled(
            connection
        )

        url = webhook_url(
            webhook_mode
        )

        if (
            not dry_run
            and not enabled
            and not allow_disabled
        ):
            return {
                "success": True,
                "blocked": True,
                "reason": (
                    "runtime_n8n_disabled"
                ),
                "webhook_mode": (
                    webhook_mode
                ),
                "n8n_calls": 0,
                "dispatched": [],
            }

        if not dry_run and not url:
            return {
                "success": True,
                "blocked": True,
                "reason": (
                    "webhook_url_missing"
                ),
                "webhook_mode": (
                    webhook_mode
                ),
                "n8n_calls": 0,
                "dispatched": [],
            }

        rows = connection.execute(
            """
            SELECT
                q.id AS id,
                q.job_id AS job_id,
                q.job_fingerprint
                    AS job_fingerprint,
                q.dispatch_mode
                    AS dispatch_mode,
                q.idempotency_key
                    AS idempotency_key,
                q.request_id
                    AS request_id,
                q.queue_status
                    AS queue_status,
                q.webhook_mode
                    AS webhook_mode,
                q.attempt_count
                    AS attempt_count
            FROM n8n_dispatch_queue AS q
            JOIN jobs AS j
                ON j.id = q.job_id
            WHERE
                q.queue_status =
                    'pending'
            ORDER BY
                CASE
                    WHEN q.dispatch_mode =
                        'telegram_manual'
                    THEN 0
                    ELSE 1
                END,
                j.hunter_score DESC,
                q.id ASC
            LIMIT ?
            """,
            (resolved_limit,),
        ).fetchall()

        targeting_rules = load_rules()

        dispatched = []
        suppressed = []
        errors = []
        n8n_calls = 0

        for raw_row in rows:
            combined = dict(raw_row)

            queue = {
                key: combined[key]
                for key in (
                    "id",
                    "job_id",
                    "job_fingerprint",
                    "dispatch_mode",
                    "idempotency_key",
                    "request_id",
                    "queue_status",
                    "webhook_mode",
                    "attempt_count",
                )
            }

            job_row = connection.execute(
                """
                SELECT *
                FROM jobs
                WHERE id = ?
                """,
                (
                    int(
                        queue["job_id"]
                    ),
                ),
            ).fetchone()

            if job_row is None:
                continue

            job = dict(job_row)

            if (
                str(queue["dispatch_mode"]) != "telegram_force_rerun"
                and has_completed_result(connection, int(job["id"]))
            ):
                if not dry_run:
                    connection.execute(
                        """
                        UPDATE n8n_dispatch_queue
                        SET queue_status = 'cancelled',
                            last_error = 'completed_result_already_exists',
                            completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND queue_status = 'pending'
                        """,
                        (int(queue["id"]),),
                    )
                    record_event(
                        connection,
                        job_id=int(job["id"]),
                        event_type="n8n_duplicate_dispatch_suppressed",
                        event_status="cancelled",
                        payload={
                            "queue_id": int(queue["id"]),
                            "dispatch_mode": str(queue["dispatch_mode"]),
                            "reason": "completed_result_already_exists",
                        },
                    )
                    connection.commit()
                suppressed.append(
                    {
                        "queue_id": int(queue["id"]),
                        "job_id": int(job["id"]),
                        "reason": "completed_result_already_exists",
                    }
                )
                continue

            valid, reason = (
                revalidate_queue_item(
                    job,
                    queue,
                    threshold,
                    targeting_rules=targeting_rules,
                )
            )

            if not valid:
                if not dry_run:
                    connection.execute(
                        """
                        UPDATE
                            n8n_dispatch_queue
                        SET
                            queue_status =
                                'cancelled',
                            last_error = ?,
                            updated_at =
                                CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            reason,
                            queue["id"],
                        ),
                    )

                    connection.commit()

                continue

            payload = build_payload(
                job,
                queue,
                webhook_mode=(
                    webhook_mode
                ),
            )

            preview = {
                "queue_id": queue["id"],
                "job_id": job["id"],
                "company": (
                    job["company_name"]
                ),
                "title": job["title"],
                "hunter_score": (
                    job["hunter_score"]
                ),
                "dispatch_mode": (
                    queue["dispatch_mode"]
                ),
                "execution_scope": (
                    payload[
                        "execution_scope"
                    ]
                ),
                "callback_url": (
                    payload[
                        "callback_url"
                    ]
                ),
            }

            if dry_run:
                dispatched.append(
                    {
                        **preview,
                        "result": "dry_run",
                    }
                )
                continue

            reservation_cursor = connection.execute(
                """
                UPDATE n8n_dispatch_queue
                SET
                    queue_status =
                        'dispatching',
                    attempt_count =
                        attempt_count + 1,
                    reserved_at =
                        CURRENT_TIMESTAMP,
                    webhook_mode = ?,
                    updated_at =
                        CURRENT_TIMESTAMP
                WHERE
                    id = ?
                    AND queue_status =
                        'pending'
                """,
                (
                    webhook_mode,
                    queue["id"],
                ),
            )

            if reservation_cursor.rowcount != 1:
                connection.rollback()
                continue

            connection.commit()

            try:
                headers = {
                    "Content-Type": (
                        "application/json"
                    ),
                }

                hunter_secret = (
                    load_dotenv_value(
                        "HUNTER_API_SECRET"
                    )
                )

                if hunter_secret:
                    headers[
                        "X-Hunter-Secret"
                    ] = hunter_secret

                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=webhook_timeout,
                )

                n8n_calls += 1

                response_text = str(
                    response.text
                    or ""
                )[:2000]

                if not (
                    200
                    <= response.status_code
                    < 300
                ):
                    raise RuntimeError(
                        "n8n returned HTTP "
                        f"{response.status_code}: "
                        f"{response_text}"
                    )

                connection.execute(
                    """
                    UPDATE n8n_dispatch_queue
                    SET
                        queue_status =
                            'accepted',
                        http_status = ?,
                        response_text = ?,
                        last_error = NULL,
                        accepted_at =
                            CURRENT_TIMESTAMP,
                        updated_at =
                            CURRENT_TIMESTAMP
                    WHERE id = ?
                        AND queue_status =
                            'dispatching'
                    """,
                    (
                        int(
                            response.status_code
                        ),
                        response_text,
                        queue["id"],
                    ),
                )

                record_event(
                    connection,
                    job_id=int(job["id"]),
                    event_type=(
                        "n8n_dispatch_accepted"
                    ),
                    event_status=(
                        "accepted"
                    ),
                    payload={
                        **preview,
                        "http_status": (
                            response.status_code
                        ),
                        "request_id": (
                            queue["request_id"]
                        ),
                        "idempotency_key": (
                            queue[
                                "idempotency_key"
                            ]
                        ),
                    },
                )

                connection.commit()

                dispatched.append(
                    {
                        **preview,
                        "result": "accepted",
                        "http_status": (
                            response.status_code
                        ),
                    }
                )

                # AADIL_AUTO_TOP_MATCH_PROGRESS_MONITOR_V1
                try:
                    from app.universal_n8n_progress import start_monitor

                    monitor_result = start_monitor(
                        job_id=int(job["id"]),
                        queue_id=int(queue["id"]),
                        dispatch_mode=str(queue["dispatch_mode"]),
                    )
                    record_event(
                        connection,
                        job_id=int(job["id"]),
                        event_type="universal_n8n_progress_monitor",
                        event_status=(
                            "started"
                            if monitor_result.get("started")
                            else "registered"
                        ),
                        payload=monitor_result,
                    )
                    connection.commit()
                except Exception as monitor_error:
                    # Progress reporting must never undo an accepted webhook.
                    record_event(
                        connection,
                        job_id=int(job["id"]),
                        event_type="universal_n8n_progress_monitor_failed",
                        event_status="failed",
                        payload={"error": str(monitor_error)},
                    )
                    connection.commit()
                    print(
                        f"Universal n8n progress monitor failed: {monitor_error}",
                        flush=True,
                    )

            except Exception as error:
                error_text = str(
                    error
                )[:2000]

                connection.execute(
                    """
                    UPDATE n8n_dispatch_queue
                    SET
                        queue_status =
                            'failed',
                        last_error = ?,
                        updated_at =
                            CURRENT_TIMESTAMP
                    WHERE id = ?
                        AND queue_status !=
                            'completed'
                    """,
                    (
                        error_text,
                        queue["id"],
                    ),
                )

                record_event(
                    connection,
                    job_id=int(job["id"]),
                    event_type=(
                        "n8n_dispatch_failed"
                    ),
                    event_status="failed",
                    payload={
                        **preview,
                        "error": error_text,
                    },
                )

                connection.commit()

                errors.append(
                    {
                        **preview,
                        "error": error_text,
                    }
                )

        return {
            "success": not bool(errors),
            "dry_run": dry_run,
            "blocked": False,
            "webhook_mode": (
                webhook_mode
            ),
            "runtime_n8n_enabled": (
                enabled
            ),
            "pending_examined": len(
                rows
            ),
            "dispatched": dispatched,
            "suppressed": suppressed,
            "errors": errors,
            "n8n_calls": n8n_calls,
        }

    finally:
        connection.close()

def dispatch_pending(
    *,
    webhook_mode: str,
    dry_run: bool,
    allow_disabled: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    # AADIL_DISPATCH_TRUTH_AND_MANUAL_APPROVAL_V1
    result = _dispatch_pending_core_all_issues_v1(
        webhook_mode=webhook_mode,
        dry_run=dry_run,
        allow_disabled=allow_disabled,
        limit=limit,
    )
    if not isinstance(result, dict):
        return {
            "success": False,
            "dispatch_status": "invalid_dispatcher_result",
            "errors": ["Dispatcher returned a non-dictionary result."],
            "dispatched": [],
            "n8n_calls": 0,
        }

    result = dict(result)
    dispatched = list(result.get("dispatched") or [])
    suppressed = list(result.get("suppressed") or [])
    errors = list(result.get("errors") or [])
    calls = int(result.get("n8n_calls") or 0)
    examined = int(result.get("pending_examined") or 0)

    if dry_run:
        result["success"] = True
        result["dispatch_status"] = "dry_run"
        return result

    if result.get("blocked"):
        result["success"] = False
        result["dispatch_status"] = "blocked"
        reason = str(result.get("reason") or "dispatch_blocked")
        if reason and reason not in errors:
            errors.append(reason)
        result["errors"] = errors
        return result

    if calls > 0 and calls == len(dispatched):
        result["success"] = True
        result["dispatch_status"] = "accepted"
        return result

    if examined == 0 and calls == 0 and not dispatched:
        result["success"] = True
        result["dispatch_status"] = "no_pending_items"
        return result

    if suppressed and calls == 0 and not dispatched and not errors:
        result["success"] = True
        result["dispatch_status"] = "duplicate_suppressed"
        return result

    if calls == 0 and not dispatched:
        result["success"] = False
        result["dispatch_status"] = "not_dispatched"
        if not errors:
            errors.append(
                "Pending queue rows were examined, but no n8n webhook was accepted."
            )
        result["errors"] = errors
        return result

    result["success"] = False
    result["dispatch_status"] = "dispatch_contract_violation"
    errors.append(
        "n8n_calls and dispatched rows did not match; the result was blocked as unsafe."
    )
    result["errors"] = errors
    return result



def queue_status() -> dict[str, Any]:
    connection = get_connection()

    try:
        ensure_schema(connection)

        rows = connection.execute(
            """
            SELECT
                queue_status,
                dispatch_mode,
                COUNT(*) AS total
            FROM n8n_dispatch_queue
            GROUP BY
                queue_status,
                dispatch_mode
            ORDER BY
                queue_status,
                dispatch_mode
            """
        ).fetchall()

        return {
            "queue_version": str(_downstream_contract().get("queue_version") or ""),
            "counts": [
                dict(row)
                for row in rows
            ],
        }

    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mode",
        choices=[
            "test",
            "production",
        ],
        default="production",
    )

    parser.add_argument(
        "--plan",
        action="store_true",
    )

    parser.add_argument(
        "--queue",
        action="store_true",
    )

    parser.add_argument(
        "--dispatch",
        action="store_true",
    )

    parser.add_argument(
        "--status",
        action="store_true",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    parser.add_argument(
        "--allow-disabled",
        action="store_true",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )

    args = parser.parse_args()

    connection = get_connection()

    try:
        ensure_schema(connection)
        connection.commit()
    finally:
        connection.close()

    output: dict[str, Any] = {
        "success": True,
        "queue_version": str(_downstream_contract().get("queue_version") or ""),
        "n8n_calls": 0,
    }

    if args.plan or args.queue:
        output["queue_result"] = (
            queue_candidates(
                webhook_mode=args.mode,
                dry_run=(
                    args.dry_run
                    or not args.queue
                ),
            )
        )

    if args.dispatch:
        output["dispatch_result"] = (
            dispatch_pending(
                webhook_mode=args.mode,
                dry_run=args.dry_run,
                allow_disabled=(
                    args.allow_disabled
                ),
                limit=args.limit,
            )
        )

        output["n8n_calls"] = (
            output[
                "dispatch_result"
            ]["n8n_calls"]
        )

    if args.status:
        output["status"] = (
            queue_status()
        )

    print(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
