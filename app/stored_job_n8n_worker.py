from __future__ import annotations

import argparse
import html
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database import ROOT_DIR, get_connection
from app.runtime_config import downstream_int, n8n_database_path, n8n_workflow_id

LOCK_DIR = ROOT_DIR / "data"
GLOBAL_LOCK = LOCK_DIR / "production_dispatch_active.lock"
TERMINAL_QUEUE_STATES = {"completed", "failed", "cancelled"}
OPEN_QUEUE_STATES = {"pending", "dispatching", "accepted"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _job_lock(job_id: int) -> Path:
    return LOCK_DIR / f"stored_job_n8n_{job_id}.lock"


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_lock(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n")
    temp.replace(path)


def _clear_stale_lock(path: Path) -> None:
    if not path.exists():
        return
    payload = _read_lock(path)
    pid = int(payload.get("pid") or 0)
    if not _pid_alive(pid):
        path.unlink(missing_ok=True)


def _port_open(port: int) -> bool:
    sock = socket.socket()
    sock.settimeout(0.5)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def _get_chat_id() -> int:
    from app.telegram_client import CHAT_ID

    if CHAT_ID in (None, ""):
        raise RuntimeError("Telegram CHAT_ID is not configured.")
    return int(CHAT_ID)


def _telegram_request(method: str, payload: dict[str, Any]) -> Any:
    from app.telegram_client import telegram_request

    return telegram_request(method, payload)


def _send_progress(chat_id: int, text: str) -> int:
    # AADIL_TELEGRAM_TRANSPORT_CALLBACK_RESILIENCE_V3
    try:
        response = _telegram_request(
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )
        return int(response["result"]["message_id"])
    except Exception as error:
        # Telegram is presentation only. Queue creation and production
        # n8n dispatch must continue when Telegram is temporarily down.
        print(
            "Initial Telegram progress message unavailable; "
            "continuing n8n dispatch: "
            f"{error!r}",
            flush=True,
        )
        return 0


def _edit_progress(
    chat_id: int,
    message_id: int,
    text: str,
) -> None:
    # AADIL_TELEGRAM_TRANSPORT_CALLBACK_RESILIENCE_V3
    if int(message_id or 0) <= 0:
        return

    try:
        _telegram_request(
            "editMessageText",
            {
                "chat_id": str(chat_id),
                "message_id": str(message_id),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )
    except Exception as error:
        if "message is not modified" not in str(error).lower():
            print(
                "Progress edit failed; n8n continues: "
                f"{error}",
                flush=True,
            )


def _safe(value: Any) -> str:
    return html.escape(str(value or ""))


def _get_job(job_id: int) -> dict[str, Any]:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Stored job {job_id} was not found.")
        return dict(row)
    finally:
        connection.close()


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(
            f'PRAGMA table_info("{table}")'
        ).fetchall()
    }


def _queue_rows_for_job(job_id: int) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        from app.n8n_dispatch import ensure_schema

        ensure_schema(connection)
        rows = connection.execute(
            """
            SELECT *
            FROM n8n_dispatch_queue
            WHERE job_id = ?
            ORDER BY id DESC
            """,
            (job_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _open_queue_rows(excluding_job_id: int | None = None) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        from app.n8n_dispatch import ensure_schema

        ensure_schema(connection)
        if excluding_job_id is None:
            rows = connection.execute(
                """
                SELECT *
                FROM n8n_dispatch_queue
                WHERE queue_status IN ('pending', 'dispatching', 'accepted')
                ORDER BY id
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT *
                FROM n8n_dispatch_queue
                WHERE queue_status IN ('pending', 'dispatching', 'accepted')
                  AND job_id <> ?
                ORDER BY id
                """,
                (excluding_job_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _daily_manual_limit() -> tuple[int, int]:
    connection = get_connection()
    try:
        limit = 25
        row = connection.execute(
            """
            SELECT value_json
            FROM settings
            WHERE setting_key = 'scoring'
            """
        ).fetchone()
        if row is not None:
            try:
                value = json.loads(row["value_json"])
                if isinstance(value, dict):
                    limit = max(
                        1,
                        int(value.get("daily_manual_n8n_limit") or 25),
                    )
            except Exception:
                pass

        from app.n8n_dispatch import ensure_schema

        ensure_schema(connection)
        columns = _table_columns(connection, "n8n_dispatch_queue")
        time_column = next(
            (
                name
                for name in ("accepted_at", "queued_at", "created_at")
                if name in columns
            ),
            None,
        )
        if time_column is None:
            return limit, 0

        count = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM n8n_dispatch_queue
                WHERE dispatch_mode = 'telegram_manual'
                  AND datetime(COALESCE({time_column}, CURRENT_TIMESTAMP))
                      >= datetime('now', '-24 hours')
                """
            ).fetchone()[0]
        )
        return limit, count
    finally:
        connection.close()


def _active_production_execution() -> dict[str, Any] | None:
    database_path = n8n_database_path()
    if not database_path.exists():
        return None

    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    try:
        columns = _table_columns(connection, "execution_entity")
        if not columns:
            return None

        clauses = ["workflowId = ?"]
        parameters: list[Any] = [n8n_workflow_id()]

        if "status" in columns:
            clauses.append(
                "lower(COALESCE(status, '')) IN "
                "('new', 'running', 'waiting', 'unknown')"
            )
        elif "stoppedAt" in columns:
            clauses.append("stoppedAt IS NULL")
        else:
            return None

        row = connection.execute(
            f"""
            SELECT *
            FROM execution_entity
            WHERE {' AND '.join(clauses)}
            ORDER BY id DESC
            LIMIT 1
            """,
            parameters,
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def _n8n_execution_state(after_id: int = 0) -> dict[str, Any] | None:
    database_path = n8n_database_path()
    if not database_path.exists():
        return None
    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    try:
        columns = _table_columns(connection, "execution_entity")
        select = ["id"]
        for name in ("status", "startedAt", "stoppedAt", "finished"):
            if name in columns:
                select.append(name)
        row = connection.execute(
            f"""
            SELECT {', '.join(select)}
            FROM execution_entity
            WHERE workflowId = ?
              AND CAST(id AS INTEGER) > ?
            ORDER BY CAST(id AS INTEGER) DESC
            LIMIT 1
            """,
            (n8n_workflow_id(), int(after_id)),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def _latest_execution_id() -> int:
    database_path = n8n_database_path()
    if not database_path.exists():
        return 0
    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
        timeout=10,
    )
    try:
        value = connection.execute(
            """
            SELECT COALESCE(MAX(CAST(id AS INTEGER)), 0)
            FROM execution_entity
            WHERE workflowId = ?
            """,
            (n8n_workflow_id(),),
        ).fetchone()[0]
        return int(value or 0)
    finally:
        connection.close()


def _latest_result(job_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        columns = _table_columns(connection, "n8n_results")
        if not columns:
            return None

        where = ""
        parameters: list[Any] = []
        if "job_id" in columns:
            where = "job_id = ?"
            parameters = [job_id]
        elif "row_id" in columns:
            where = "row_id = ?"
            parameters = [job_id]
        else:
            job = _get_job(job_id)
            fingerprint = str(job.get("job_fingerprint") or "")
            if not fingerprint or "job_fingerprint" not in columns:
                return None
            where = "job_fingerprint = ?"
            parameters = [fingerprint]

        order_column = next(
            (
                name
                for name in ("id", "completed_at", "created_at", "updated_at")
                if name in columns
            ),
            None,
        )
        order = f"{order_column} DESC" if order_column else "rowid DESC"
        row = connection.execute(
            f"""
            SELECT *
            FROM n8n_results
            WHERE {where}
            ORDER BY {order}
            LIMIT 1
            """,
            parameters,
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def _queue_row(queue_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM n8n_dispatch_queue WHERE id = ?",
            (queue_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def _record_event(
    job_id: int,
    event_type: str,
    status: str,
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
            VALUES (?, ?, 'telegram', ?, ?)
            """,
            (
                job_id,
                event_type,
                status,
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
    finally:
        connection.close()


def _reset_failed_queue(queue_id: int) -> None:
    connection = get_connection()
    try:
        columns = _table_columns(connection, "n8n_dispatch_queue")
        assignments = ["queue_status = 'pending'", "updated_at = CURRENT_TIMESTAMP"]
        for name in (
            "http_status",
            "response_text",
            "last_error",
            "reserved_at",
            "accepted_at",
            "completed_at",
        ):
            if name in columns:
                assignments.append(f"{name} = NULL")
        connection.execute(
            f"""
            UPDATE n8n_dispatch_queue
            SET {', '.join(assignments)}
            WHERE id = ?
              AND queue_status = 'failed'
            """,
            (queue_id,),
        )
        connection.commit()
    finally:
        connection.close()


def _create_or_reuse_queue(job: dict[str, Any]) -> tuple[int, str]:
    from app.n8n_dispatch import (
        ensure_schema,
        insert_queue_item,
    )

    connection = get_connection()
    try:
        ensure_schema(connection)
        existing = connection.execute(
            """
            SELECT *
            FROM n8n_dispatch_queue
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(job["id"]),),
        ).fetchone()

        if existing is not None:
            row = dict(existing)
            status = str(row.get("queue_status") or "").lower()
            if status == "failed":
                connection.close()
                _reset_failed_queue(int(row["id"]))
                return int(row["id"]), "retry_failed_queue"
            return int(row["id"]), f"reuse_{status or 'existing'}"

        connection.execute("BEGIN IMMEDIATE")
        queue = insert_queue_item(
            connection,
            job,
            "telegram_manual",
            "production",
        )
        connection.commit()
        return int(queue["id"]), "created"
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _dispatch_one(queue_id: int, job_id: int) -> dict[str, Any]:
    from app.n8n_dispatch import dispatch_pending

    result = dispatch_pending(
        webhook_mode="production",
        dry_run=False,
        allow_disabled=False,
        limit=1,
    )
    if result.get("blocked"):
        raise RuntimeError(
            "Production dispatch was blocked: "
            + json.dumps(result, ensure_ascii=False, default=str)
        )
    if int(result.get("n8n_calls", 0) or 0) != 1:
        raise RuntimeError(
            "Expected exactly one production webhook call, received: "
            + json.dumps(result, ensure_ascii=False, default=str)
        )
    dispatched = result.get("dispatched") or []
    if len(dispatched) != 1:
        raise RuntimeError("Expected exactly one dispatched queue item.")
    item = dispatched[0]
    if int(item.get("job_id") or 0) != job_id:
        raise RuntimeError("Dispatcher selected a different job.")
    if int(item.get("queue_id") or 0) != queue_id:
        raise RuntimeError("Dispatcher selected a different queue row.")
    return result


def _render_final(job: dict[str, Any], result: dict[str, Any]) -> str:
    # AADIL_UNIVERSAL_N8N_PROGRESS_V1
    from app.universal_n8n_progress import render_final_message

    return render_final_message(
        job,
        result,
        html_mode=True,
        heading="✅ n8n application package finished",
    )


def start_stored_job_run(
    job_id: int,
    actor: str = "telegram",
) -> dict[str, Any]:
    job_id = int(job_id)
    job = _get_job(job_id)
    lock_path = _job_lock(job_id)
    _clear_stale_lock(lock_path)

    if lock_path.exists():
        payload = _read_lock(lock_path)
        return {
            "success": True,
            "started": False,
            "message": (
                "n8n processing is already active for this job "
                f"(PID {payload.get('pid') or 'unknown'})."
            ),
        }

    result = _latest_result(job_id)
    if int(job.get("sent_to_n8n") or 0) == 1 and result:
        return {
            "success": True,
            "started": False,
            "message": "This job already has an n8n result. No duplicate run was started.",
        }

    other_open = _open_queue_rows(excluding_job_id=job_id)
    if other_open:
        return {
            "success": False,
            "started": False,
            "message": (
                "Another n8n queue item is still open. "
                "Use /queue and wait for it to finish."
            ),
        }

    active = _active_production_execution()
    if active:
        return {
            "success": False,
            "started": False,
            "message": (
                "The production n8n workflow is already running. "
                "No second execution was started."
            ),
        }

    limit, used = _daily_manual_limit()
    if used >= limit:
        return {
            "success": False,
            "started": False,
            "message": (
                f"Daily manual n8n limit reached ({used}/{limit}). "
                "No webhook was called."
            ),
        }

    if not _port_open(5678):
        return {
            "success": False,
            "started": False,
            "message": "n8n is offline on port 5678. No webhook was called.",
        }
    if not _port_open(8000):
        return {
            "success": False,
            "started": False,
            "message": "FastAPI is offline on port 8000. No webhook was called.",
        }

    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return {
            "success": True,
            "started": False,
            "message": "n8n processing is already being started for this job.",
        }

    with os.fdopen(descriptor, "w") as handle:
        json.dump(
            {
                "pid": os.getpid(),
                "job_id": job_id,
                "actor": actor,
                "reserved": True,
                "started_at": utc_now(),
            },
            handle,
        )

    python_path = ROOT_DIR / ".venv" / "bin" / "python"
    if not python_path.exists():
        python_path = Path(sys.executable)

    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (
        f"stored_job_n8n_{job_id}_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".log"
    )
    handle = log_path.open("ab", buffering=0)
    try:
        process = subprocess.Popen(
            [
                str(python_path),
                "-u",
                "-m",
                "app.stored_job_n8n_worker",
                "--job-id",
                str(job_id),
            ],
            cwd=ROOT_DIR,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        lock_path.unlink(missing_ok=True)
        raise
    finally:
        handle.close()

    _write_lock(
        lock_path,
        {
            "pid": process.pid,
            "job_id": job_id,
            "actor": actor,
            "started_at": utc_now(),
            "log_path": str(log_path),
        },
    )
    _record_event(
        job_id,
        "stored_job_n8n_worker_started",
        "started",
        {
            "pid": process.pid,
            "actor": actor,
            "log_path": str(log_path),
        },
    )
    return {
        "success": True,
        "started": True,
        "pid": process.pid,
        "log_path": str(log_path),
        "message": (
            "n8n production run started. "
            "Telegram progress and final output links will follow."
        ),
    }


def run(job_id: int, chat_id: int | None = None) -> dict[str, Any]:
    job_id = int(job_id)
    chat_id = int(chat_id or _get_chat_id())
    job = _get_job(job_id)
    lock_path = _job_lock(job_id)
    current_pid = os.getpid()
    existing = _read_lock(lock_path)
    existing_pid = int(existing.get("pid") or 0)
    if existing_pid and existing_pid != current_pid and _pid_alive(existing_pid):
        raise RuntimeError(
            f"Another stored-job worker is already active: PID {existing_pid}"
        )
    _write_lock(
        lock_path,
        {
            **existing,
            "pid": current_pid,
            "job_id": job_id,
            "started_at": existing.get("started_at") or utc_now(),
        },
    )
    _write_lock(
        GLOBAL_LOCK,
        {
            "pid": current_pid,
            "job_id": job_id,
            "kind": "stored_job_n8n",
            "started_at": utc_now(),
        },
    )

    message_id = _send_progress(
        chat_id,
        "\n".join(
            [
                "🚀 <b>Starting stored-job n8n run</b>",
                "",
                f"🏢 <b>{_safe(job.get('company_name'))}</b>",
                f"💼 {_safe(job.get('title'))}",
                "🛡 Running preflight and duplicate guards…",
            ]
        ),
    )

    try:
        result = _latest_result(job_id)
        if int(job.get("sent_to_n8n") or 0) == 1 and result:
            final_text = _render_final(job, result)
            _edit_progress(chat_id, message_id, final_text)
            return {
                "success": True,
                "already_completed": True,
                "result": result,
            }

        other_open = _open_queue_rows(excluding_job_id=job_id)
        if other_open:
            raise RuntimeError(
                "Another queue item is open. No production call was made."
            )
        active = _active_production_execution()
        if active:
            raise RuntimeError(
                "The production workflow is already running. "
                "No second execution was started."
            )
        limit, used = _daily_manual_limit()
        if used >= limit:
            raise RuntimeError(
                f"Daily manual n8n limit reached ({used}/{limit})."
            )
        if not _port_open(5678):
            raise RuntimeError("n8n is offline on port 5678.")
        if not _port_open(8000):
            raise RuntimeError("FastAPI is offline on port 8000.")

        _edit_progress(
            chat_id,
            message_id,
            "\n".join(
                [
                    "✅ <b>Preflight passed</b>",
                    "",
                    f"🏢 <b>{_safe(job.get('company_name'))}</b>",
                    f"💼 {_safe(job.get('title'))}",
                    f"📊 Manual usage: {used}/{limit}",
                    "📥 Creating or reusing exactly one queue item…",
                ]
            ),
        )

        start_execution_id = _latest_execution_id()
        queue_id, queue_mode = _create_or_reuse_queue(job)
        queue = _queue_row(queue_id) or {}
        # AADIL_STORED_REGISTER_UNIVERSAL_PROGRESS_V1
        try:
            from app.universal_n8n_progress import register_progress

            register_progress(
                job_id=job_id,
                queue_id=queue_id,
                dispatch_mode=str(queue.get("dispatch_mode") or "telegram_manual"),
                chat_id=chat_id,
                status_message_id=message_id,
            )
        except Exception as progress_error:
            print(
                f"Universal progress registration failed: {progress_error}",
                flush=True,
            )
        queue_status = str(queue.get("queue_status") or "").lower()

        _record_event(
            job_id,
            "stored_job_n8n_queue_ready",
            "recorded",
            {
                "queue_id": queue_id,
                "queue_mode": queue_mode,
                "queue_status": queue_status,
            },
        )

        if queue_status in {"pending", "failed", ""}:
            _edit_progress(
                chat_id,
                message_id,
                "\n".join(
                    [
                        "📤 <b>Queue ready</b>",
                        "",
                        f"Queue ID: <code>{queue_id}</code>",
                        "Calling the production webhook exactly once…",
                    ]
                ),
            )
            dispatch_result = _dispatch_one(queue_id, job_id)
            _record_event(
                job_id,
                "stored_job_n8n_dispatched",
                "accepted",
                {
                    "queue_id": queue_id,
                    "dispatch_result": dispatch_result,
                },
            )
        else:
            dispatch_result = {
                "reused_queue": True,
                "queue_status": queue_status,
                "n8n_calls": 0,
            }

        started_at = time.monotonic()
        last_text = ""
        execution_id = 0

        callback_timeout = downstream_int("stored_job_callback_timeout_seconds", minimum=1)
        poll_seconds = downstream_int("n8n_progress_poll_seconds", minimum=1)
        while time.monotonic() - started_at < callback_timeout:
            queue = _queue_row(queue_id) or {}
            queue_status = str(queue.get("queue_status") or "unknown")
            execution = _n8n_execution_state(start_execution_id)
            if execution:
                execution_id = int(execution.get("id") or 0)
                execution_status = str(
                    execution.get("status")
                    or (
                        "success"
                        if execution.get("finished")
                        else "running"
                    )
                )
            else:
                execution_status = "waiting_for_start"

            result = _latest_result(job_id)
            callback_status = (
                str(result.get("n8n_status") or "received")
                if result
                else "waiting"
            )

            text = "\n".join(
                [
                    "⚙️ <b>n8n production run in progress</b>",
                    "",
                    f"🏢 <b>{_safe(job.get('company_name'))}</b>",
                    f"💼 {_safe(job.get('title'))}",
                    f"📥 Queue {queue_id}: <b>{_safe(queue_status)}</b>",
                    (
                        f"🧩 Execution {execution_id}: "
                        f"<b>{_safe(execution_status)}</b>"
                        if execution_id
                        else "🧩 Execution: <b>waiting to start</b>"
                    ),
                    f"📡 Localhost callback: <b>{_safe(callback_status)}</b>",
                ]
            )
            if text != last_text:
                _edit_progress(chat_id, message_id, text)
                last_text = text

            if result:
                from app.universal_n8n_progress import render_final_message

                final_text = render_final_message(
                    _get_job(job_id),
                    result,
                    queue,
                    execution,
                    html_mode=True,
                    heading="✅ n8n application package finished",
                )
                _edit_progress(chat_id, message_id, final_text)
                try:
                    from app.telegram_sync import sync_latest_job_card

                    sync_latest_job_card(
                        job_id,
                        notice="n8n result received from stored-job run.",
                        actor="stored_job_n8n_worker",
                    )
                except Exception as error:
                    print(f"Final card sync failed: {error}", flush=True)

                _record_event(
                    job_id,
                    "stored_job_n8n_completed",
                    "completed",
                    {
                        "queue_id": queue_id,
                        "execution_id": execution_id,
                        "result": result,
                    },
                )
                return {
                    "success": True,
                    "job_id": job_id,
                    "queue_id": queue_id,
                    "execution_id": execution_id,
                    "result": result,
                }

            if queue_status.lower() == "failed":
                error = str(queue.get("last_error") or "Queue failed.")
                raise RuntimeError(error)

            if execution_status.lower() in {"error", "failed", "crashed", "canceled"}:
                raise RuntimeError(
                    f"n8n execution {execution_id} ended as {execution_status}."
                )

            time.sleep(poll_seconds)

        raise TimeoutError(
            "Timed out waiting for the localhost callback. "
            "The queue/execution may still need inspection before retrying."
        )

    except Exception as error:
        error_text = str(error)
        _edit_progress(
            chat_id,
            message_id,
            "\n".join(
                [
                    "❌ <b>Stored-job n8n run stopped</b>",
                    "",
                    f"🏢 <b>{_safe(job.get('company_name'))}</b>",
                    f"💼 {_safe(job.get('title'))}",
                    f"Reason: {_safe(error_text)}",
                    "",
                    "No automatic retry was started.",
                ]
            )[:4000],
        )
        _record_event(
            job_id,
            "stored_job_n8n_failed",
            "failed",
            {"error": error_text},
        )
        raise
    finally:
        lock_path.unlink(missing_ok=True)
        global_payload = _read_lock(GLOBAL_LOCK)
        if int(global_payload.get("pid") or 0) == current_pid:
            GLOBAL_LOCK.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--chat-id", type=int)
    args = parser.parse_args()
    result = run(args.job_id, args.chat_id)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

# AADIL_TG_STORED_JOB_RUNTIME_REPAIR_V1
from app.tg_n8n_runtime_repair_v1 import (
    guarded_call as _aadil_tg_guarded_call_v1,
)

if "start_stored_job_run" in globals():
    _aadil_original_start_stored_job_run_v1 = (
        start_stored_job_run
    )

    def start_stored_job_run(*args, **kwargs):
        return _aadil_tg_guarded_call_v1(
            "telegram_scored_jobcard_start_stored_job_run",
            _aadil_original_start_stored_job_run_v1,
            *args,
            **kwargs,
        )

if __name__ == "__main__":
    main()
