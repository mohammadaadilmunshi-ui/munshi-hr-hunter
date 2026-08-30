from __future__ import annotations

import hashlib
import inspect
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MARKER = "AADIL_FORCE_RERUN_CHILD_JOB_V1"
PROJECT = Path(
    os.getenv("AADIL_HR_HUNTER_PROJECT")
    or Path(__file__).resolve().parents[1]
)
DB_PATH = Path(
    os.getenv("AADIL_HUNTER_DB")
    or PROJECT / "data" / "hunter.db"
)
TABLE = "force_rerun_runs_v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def _columns(connection: sqlite3.Connection, name: str) -> list[str]:
    if not _table_exists(connection, name):
        return []
    return [
        str(row[1])
        for row in connection.execute(
            f'PRAGMA table_info("{name}")'
        ).fetchall()
    ]


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_job_id INTEGER NOT NULL,
            child_job_id INTEGER UNIQUE,
            rerun_part INTEGER NOT NULL,
            queue_id INTEGER UNIQUE,
            chat_id INTEGER,
            run_status TEXT NOT NULL DEFAULT 'created',
            request_id TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(parent_job_id, rerun_part),
            FOREIGN KEY(parent_job_id) REFERENCES jobs(id) ON DELETE CASCADE,
            FOREIGN KEY(child_job_id) REFERENCES jobs(id) ON DELETE SET NULL
        )
        """
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_parent ON {TABLE}(parent_job_id, rerun_part)"
    )


def canonical_parent_job_id(job_id: int) -> int:
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute(
            f"SELECT parent_job_id FROM {TABLE} WHERE child_job_id=? ORDER BY id DESC LIMIT 1",
            (int(job_id),),
        ).fetchone()
        return int(row[0]) if row else int(job_id)
    finally:
        connection.close()


def _next_rerun_part_in_connection(
    connection: sqlite3.Connection,
    parent_job_id: int,
) -> int:
    ensure_schema(connection)
    row = connection.execute(
        f"SELECT COALESCE(MAX(rerun_part), 1) FROM {TABLE} WHERE parent_job_id=?",
        (int(parent_job_id),),
    ).fetchone()
    return max(2, int(row[0] or 1) + 1)


def next_rerun_part(job_id: int) -> int:
    parent_job_id = canonical_parent_job_id(int(job_id))
    connection = get_connection()
    try:
        return _next_rerun_part_in_connection(connection, parent_job_id)
    finally:
        connection.close()


def _hard_block_reason(job: dict[str, Any]) -> str | None:
    status = str(job.get("status") or "").strip().casefold()
    reason = str(job.get("hard_rejection_reason") or "").strip()
    work_auth = str(job.get("work_authorization") or "").strip()
    combined = f"{reason} {work_auth}".casefold()

    if status == "blacklisted" or "company_blacklist" in combined:
        return "The company/job is blacklisted. Force rerun does not bypass the blacklist."

    explicit_blocks = (
        "citizens only",
        "citizen only",
        "u.s. citizen only",
        "us citizen only",
        "green card only",
        "permanent residents only",
        "security clearance required",
        "active clearance required",
    )
    if any(token in combined for token in explicit_blocks):
        return "The job has an explicit citizenship, permanent-residency, or clearance restriction."

    return None


def _copy_job_as_child(
    connection: sqlite3.Connection,
    parent: dict[str, Any],
    rerun_part: int,
) -> tuple[int, str]:
    columns = _columns(connection, "jobs")
    if not columns or "id" not in columns or "job_fingerprint" not in columns:
        raise RuntimeError("The jobs table contract is unavailable.")

    token = uuid.uuid4().hex
    parent_fingerprint = str(parent.get("job_fingerprint") or "").strip()
    if not parent_fingerprint:
        raise RuntimeError("The parent job has no fingerprint.")

    child_fingerprint = hashlib.sha256(
        f"{MARKER}|{parent_fingerprint}|part={rerun_part}|{token}".encode("utf-8")
    ).hexdigest()

    values: dict[str, Any] = {
        column: parent.get(column)
        for column in columns
        if column != "id"
    }
    values["job_fingerprint"] = child_fingerprint

    overrides: dict[str, Any] = {
        "status": "approved_for_n8n",
        "sent_to_n8n": 0,
        # A force-rerun child is a downstream execution record, not a newly
        # delivered Telegram card. Inheriting the parent's sent flag makes
        # card accounting claim a message exists when no message was sent.
        "telegram_sent": 0,
        "telegram_notified": 0,
        "hard_rejection_reason": None,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "added_at": utc_now(),
    }
    for key, value in overrides.items():
        if key in values:
            values[key] = value

    for key in (
        "n8n_execution_id",
        "n8n_status",
        "resume_doc_url",
        "resume_pdf_url",
        "cover_letter_doc_url",
        "google_sheet_row_url",
    ):
        if key in values:
            values[key] = None

    if "source" in values:
        base_source = str(values.get("source") or "Stored Job").strip()
        values["source"] = f"{base_source} / Force Rerun Part {rerun_part}"

    if "source_job_id" in values:
        base_source_id = str(values.get("source_job_id") or parent.get("id") or "job")
        values["source_job_id"] = f"{base_source_id}:force-rerun:{rerun_part}:{token[:10]}"

    insert_columns = list(values)
    quoted = ", ".join(f'"{column}"' for column in insert_columns)
    placeholders = ", ".join("?" for _ in insert_columns)
    cursor = connection.execute(
        f"INSERT INTO jobs ({quoted}) VALUES ({placeholders})",
        [values[column] for column in insert_columns],
    )
    child_job_id = int(cursor.lastrowid)
    return child_job_id, child_fingerprint


def _record_event(
    connection: sqlite3.Connection,
    job_id: int,
    event_type: str,
    status: str,
    payload: dict[str, Any],
) -> None:
    if not _table_exists(connection, "events"):
        return
    columns = set(_columns(connection, "events"))
    required = {"job_id", "event_type", "actor", "event_status", "payload_json"}
    if not required.issubset(columns):
        return
    connection.execute(
        """
        INSERT INTO events (job_id, event_type, actor, event_status, payload_json)
        VALUES (?, ?, 'force_rerun_v1', ?, ?)
        """,
        (
            int(job_id),
            event_type,
            status,
            json.dumps(payload, ensure_ascii=False, default=str),
        ),
    )


def request_force_rerun(job_id: int, chat_id: int | None = None) -> dict[str, Any]:
    requested_job_id = int(job_id)
    parent_job_id = canonical_parent_job_id(requested_job_id)
    connection = get_connection()
    child_job_id: int | None = None
    run_id: int | None = None

    try:
        ensure_schema(connection)
        parent_row = connection.execute(
            "SELECT * FROM jobs WHERE id=?",
            (parent_job_id,),
        ).fetchone()
        if parent_row is None:
            return {
                "success": False,
                "started": False,
                "message": f"Stored job {parent_job_id} was not found.",
            }
        parent = dict(parent_row)
        block_reason = _hard_block_reason(parent)
        if block_reason:
            return {
                "success": False,
                "started": False,
                "message": block_reason,
                "parent_job_id": parent_job_id,
            }

        open_queue = connection.execute(
            """
            SELECT id, job_id, queue_status
            FROM n8n_dispatch_queue
            WHERE lower(queue_status) IN ('pending', 'dispatching', 'accepted')
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
        if open_queue is not None:
            return {
                "success": False,
                "started": False,
                "message": (
                    "Another n8n queue item is still open "
                    f"(queue {open_queue['id']}, job {open_queue['job_id']}). "
                    "No second production run was started."
                ),
            }

        connection.execute("BEGIN IMMEDIATE")
        rerun_part = _next_rerun_part_in_connection(
            connection,
            parent_job_id,
        )
        child_job_id, child_fingerprint = _copy_job_as_child(
            connection,
            parent,
            rerun_part,
        )
        cursor = connection.execute(
            f"""
            INSERT INTO {TABLE} (
                parent_job_id,
                child_job_id,
                rerun_part,
                chat_id,
                run_status
            ) VALUES (?, ?, ?, ?, 'child_created')
            """,
            (
                parent_job_id,
                child_job_id,
                rerun_part,
                int(chat_id) if chat_id is not None else None,
            ),
        )
        run_id = int(cursor.lastrowid)
        _record_event(
            connection,
            child_job_id,
            "force_rerun_child_created",
            "completed",
            {
                "force_rerun_run_id": run_id,
                "parent_job_id": parent_job_id,
                "child_job_id": child_job_id,
                "rerun_part": rerun_part,
                "child_fingerprint": child_fingerprint,
            },
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    try:
        from app.stored_job_n8n_worker import start_stored_job_run

        parameters = inspect.signature(start_stored_job_run).parameters
        accepts_var_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        kwargs: dict[str, Any] = {}
        if "actor" in parameters or accepts_var_kwargs:
            kwargs["actor"] = f"telegram_force_rerun_part_{rerun_part}"
        # Only pass chat_id when it is an explicit named parameter.  A legacy
        # *args/**kwargs wrapper forwards unknown keywords to the original
        # worker, whose production signature does not accept chat_id.
        if "chat_id" in parameters and chat_id is not None:
            kwargs["chat_id"] = int(chat_id)
        start_result = start_stored_job_run(
            int(child_job_id),
            **kwargs,
        )
        if not isinstance(start_result, dict):
            start_result = {
                "success": bool(start_result),
                "started": bool(start_result),
                "message": "Stored-job worker returned a non-dictionary result.",
            }
    except Exception as error:
        start_result = {
            "success": False,
            "started": False,
            "message": str(error),
        }

    connection = get_connection()
    try:
        ensure_schema(connection)
        queue_row = connection.execute(
            "SELECT * FROM n8n_dispatch_queue WHERE job_id=? ORDER BY id DESC LIMIT 1",
            (int(child_job_id),),
        ).fetchone()
        queue_id = int(queue_row["id"]) if queue_row else None
        request_id = str(queue_row["request_id"]) if queue_row else None
        started = bool(
            start_result.get("success")
            and (
                start_result.get("started")
                or queue_row is not None
            )
        )
        run_status = "queued" if started else "start_blocked"
        connection.execute(
            f"""
            UPDATE {TABLE}
            SET queue_id=?, request_id=?, run_status=?, error_message=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                queue_id,
                request_id,
                run_status,
                None if started else str(start_result.get("message") or "Run did not start."),
                int(run_id),
            ),
        )
        _record_event(
            connection,
            int(child_job_id),
            "force_rerun_start_requested",
            "completed" if started else "blocked",
            {
                "force_rerun_run_id": run_id,
                "parent_job_id": parent_job_id,
                "child_job_id": child_job_id,
                "rerun_part": rerun_part,
                "queue_id": queue_id,
                "request_id": request_id,
                "start_result": start_result,
            },
        )
        connection.commit()
    finally:
        connection.close()

    if not started:
        return {
            "success": False,
            "started": False,
            "parent_job_id": parent_job_id,
            "child_job_id": child_job_id,
            "rerun_part": rerun_part,
            "queue_id": queue_id,
            "message": str(start_result.get("message") or "Force rerun could not start."),
        }

    return {
        "success": True,
        "started": True,
        "parent_job_id": parent_job_id,
        "child_job_id": child_job_id,
        "rerun_part": rerun_part,
        "queue_id": queue_id,
        "request_id": request_id,
        "message": (
            f"Force Rerun Part {rerun_part} started as child job {child_job_id}. "
            f"Queue: {queue_id}. The original job and original application result were preserved."
        ),
    }


def self_test() -> dict[str, Any]:
    return {
        "success": True,
        "marker": MARKER,
        "database_writes": 0,
        "provider_calls": 0,
        "telegram_calls": 0,
        "n8n_calls": 0,
    }
