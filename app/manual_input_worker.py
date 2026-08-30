from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any

from app.database import ROOT_DIR, get_connection, get_setting
from app.job_store import save_job
from app.manual_input import ensure_schema
from app.n8n_dispatch import (
    dispatch_pending,
    ensure_schema as ensure_dispatch_schema,
    insert_queue_item,
)
from app.telegram_client import CHAT_ID, telegram_request
from app.runtime_config import (
    downstream_int,
    launch_agent_plist,
    n8n_database_path,
    n8n_workflow_id,
    service_endpoint,
    is_macos,
)

LOCK_PATH = ROOT_DIR / "data" / "telegram_manual_n8n.lock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Store a Telegram manual job and run the full n8n workflow."
    )
    parser.add_argument("--run-id", type=int, required=True)
    return parser.parse_args()


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.75)
        return sock.connect_ex((host, port)) == 0


def n8n_connection() -> sqlite3.Connection:
    database_path = n8n_database_path()
    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
        timeout=15,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection


def acquire_lock(run_id: int) -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "pid": os.getpid(),
            "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )

    for attempt in range(2):
        try:
            descriptor = os.open(
                LOCK_PATH,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload + "\n")
            return
        except FileExistsError:
            try:
                existing = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
                pid = int(existing.get("pid") or 0)
                if pid > 0:
                    os.kill(pid, 0)
                    raise RuntimeError(
                        "Another Telegram manual n8n run is already active "
                        f"(PID {pid}, run {existing.get('run_id')})."
                    )
            except ProcessLookupError:
                LOCK_PATH.unlink(missing_ok=True)
                if attempt == 0:
                    continue
            except json.JSONDecodeError:
                LOCK_PATH.unlink(missing_ok=True)
                if attempt == 0:
                    continue
            raise RuntimeError(
                "The Telegram manual n8n lock is already present. "
                f"Inspect {LOCK_PATH}."
            )


def release_lock() -> None:
    try:
        if LOCK_PATH.exists():
            payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            if int(payload.get("pid") or 0) == os.getpid():
                LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def service_loaded(label: str) -> bool:
    if not is_macos():
        return False
    completed = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def pause_scraper_scheduler() -> bool:
    if not is_macos():
        return False
    orchestration = get_setting("orchestration", {}) or {}
    label = str(orchestration.get("source_worker_launch_agent") or "").strip()
    if not label:
        raise RuntimeError("The canonical source worker launch agent is not configured.")
    plist = launch_agent_plist(label)
    was_loaded = service_loaded(label)
    if not was_loaded:
        return False

    subprocess.run(
        [
            "launchctl",
            "bootout",
            f"gui/{os.getuid()}",
            str(plist),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    time.sleep(1)
    if service_loaded(label):
        raise RuntimeError("Could not pause the smart auto-scraper scheduler.")
    return True


def restore_scraper_scheduler(was_loaded: bool) -> None:
    if not is_macos():
        return
    orchestration = get_setting("orchestration", {}) or {}
    label = str(orchestration.get("source_worker_launch_agent") or "").strip()
    plist = launch_agent_plist(label) if label else Path()
    if not was_loaded or not label or not plist.exists():
        return
    if service_loaded(label):
        return
    subprocess.run(
        [
            "launchctl",
            "bootstrap",
            f"gui/{os.getuid()}",
            str(plist),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def active_source_workers() -> list[str]:
    completed = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        text=True,
        capture_output=True,
        check=True,
    )
    patterns = (
        "app.telegram_source_runner",
        "app.hourly_worker",
        "app.greenhouse_worker",
        "app.lever_worker",
        "app.ashby_worker",
        "app.smartrecruiters_worker",
        "app.dice_worker",
        "app.unified_hourly_coordinator",
    )
    found = []
    for line in completed.stdout.splitlines():
        if any(pattern in line for pattern in patterns):
            found.append(line.strip())
    return found


def wait_for_source_workers(timeout_seconds: int = 180) -> list[str]:
    """Give an already-running source lane time to finish instead of failing manual input.

    The randomized source runner detects this manual worker process and defers
    new starts. The unified coordinator is also patched to defer while a manual
    or stored-job worker is active.
    """
    deadline = time.monotonic() + max(0, int(timeout_seconds))
    workers = active_source_workers()
    while workers and time.monotonic() < deadline:
        time.sleep(2)
        workers = active_source_workers()
    return workers


def load_run(run_id: int) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute(
            "SELECT * FROM telegram_manual_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Manual run {run_id} was not found.")
        return dict(row)
    finally:
        connection.close()


def update_run(run_id: int, **values: Any) -> None:
    if not values:
        return

    allowed = {
        "job_id",
        "queue_id",
        "execution_id",
        "worker_pid",
        "run_status",
        "status_message_id",
        "last_progress_text",
        "error_message",
    }
    clean = {key: value for key, value in values.items() if key in allowed}
    if not clean:
        return

    assignments = [f"{key} = ?" for key in clean]
    assignments.append("updated_at = CURRENT_TIMESTAMP")

    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute(
            f"""
            UPDATE telegram_manual_runs
            SET {', '.join(assignments)}
            WHERE id = ?
            """,
            [*clean.values(), run_id],
        )
        connection.commit()
    finally:
        connection.close()


def complete_run(run_id: int, status: str, error: str | None = None) -> None:
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute(
            """
            UPDATE telegram_manual_runs
            SET
                run_status = ?,
                error_message = ?,
                completed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, error, run_id),
        )
        connection.commit()
    finally:
        connection.close()


def send_or_edit_status(run_id: int, text: str) -> None:
    run = load_run(run_id)
    chat_id = int(run.get("chat_id") or CHAT_ID or 0)
    if not chat_id:
        return

    safe_text = text[:4000]
    message_id = run.get("status_message_id")
    payload = {
        "chat_id": str(chat_id),
        "text": safe_text,
        "disable_web_page_preview": "true",
    }

    if message_id:
        try:
            response = telegram_request(
                "editMessageText",
                {**payload, "message_id": str(message_id)},
            )
            if response.get("ok"):
                update_run(run_id, last_progress_text=safe_text)
                return
        except Exception as error:
            if "message is not modified" in str(error).casefold():
                update_run(run_id, last_progress_text=safe_text)
                return

    response = telegram_request("sendMessage", payload)
    new_message_id = int(response["result"]["message_id"])
    update_run(
        run_id,
        status_message_id=new_message_id,
        last_progress_text=safe_text,
    )


def add_event(
    job_id: int | None,
    event_type: str,
    status: str,
    payload: dict[str, Any],
) -> None:
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO events (
                job_id, event_type, actor, event_status, payload_json
            )
            VALUES (?, ?, 'telegram_manual_input', ?, ?)
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


def open_queue_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM n8n_dispatch_queue
        WHERE lower(queue_status) IN ('pending', 'dispatching', 'accepted')
        ORDER BY id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def running_primary_executions() -> list[dict[str, Any]]:
    if not n8n_database_path().exists():
        return []
    connection = n8n_connection()
    try:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(execution_entity)"
            ).fetchall()
        }
        if "status" not in columns:
            return []
        rows = connection.execute(
            """
            SELECT id, status, startedAt
            FROM execution_entity
            WHERE workflowId = ?
              AND lower(COALESCE(status, '')) IN ('new', 'running', 'waiting')
            ORDER BY id
            """,
            (n8n_workflow_id(),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def setting_dict(connection: sqlite3.Connection, setting_key: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT value_json FROM settings WHERE setting_key = ?",
        (setting_key,),
    ).fetchone()
    if row is None:
        return {}
    try:
        value = json.loads(row["value_json"])
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def manual_daily_limit(connection: sqlite3.Connection) -> tuple[int, int]:
    scoring = setting_dict(connection, "scoring")
    try:
        limit = int(scoring["daily_manual_n8n_limit"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(
            "Canonical scoring.daily_manual_n8n_limit is missing or invalid."
        ) from None
    used = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM n8n_dispatch_queue
            WHERE dispatch_mode = 'telegram_manual'
              AND lower(queue_status) IN (
                  'pending', 'dispatching', 'accepted', 'completed'
              )
              AND date(queued_at, 'localtime') = date('now', 'localtime')
            """
        ).fetchone()[0]
    )
    return limit, used


def capture_baseline() -> dict[str, int]:
    hunter = get_connection()
    try:
        result_id = int(
            hunter.execute(
                "SELECT COALESCE(MAX(id), 0) FROM n8n_results"
            ).fetchone()[0]
        )
    finally:
        hunter.close()

    n8n = n8n_connection()
    try:
        execution_id = int(
            n8n.execute(
                """
                SELECT COALESCE(MAX(id), 0)
                FROM execution_entity
                WHERE workflowId = ?
                """,
                (n8n_workflow_id(),),
            ).fetchone()[0]
        )
    finally:
        n8n.close()

    return {"result_id": result_id, "execution_id": execution_id}


def latest_execution_after(baseline_id: int) -> dict[str, Any] | None:
    connection = n8n_connection()
    try:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(execution_entity)"
            ).fetchall()
        }
        selected = [
            name
            for name in (
                "id",
                "workflowId",
                "mode",
                "status",
                "startedAt",
                "stoppedAt",
                "finished",
            )
            if name in columns
        ]
        row = connection.execute(
            f"""
            SELECT {', '.join(selected)}
            FROM execution_entity
            WHERE workflowId = ? AND id > ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (n8n_workflow_id(), baseline_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


# AADIL_MANUAL_LATEST_RESULT_V1
def result_for_job(job_id: int, baseline_result_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT *
            FROM n8n_results
            WHERE job_id = ? AND id > ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id, baseline_result_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def queue_for_job(job_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT *
            FROM n8n_dispatch_queue
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def first_value(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def format_outputs(result: dict[str, Any] | None) -> str:
    if not result:
        return ""

    lines: list[str] = []
    final_ats = first_value(result, "final_ats_score", "ats_resume_score")
    if final_ats not in (None, ""):
        lines.append(f"Final ATS score: {final_ats}")

    for label, names in (
        ("Resume Doc", ("resume_doc_url", "google_doc_url")),
        ("Resume PDF", ("resume_pdf_url", "google_pdf_url")),
        ("Cover Letter", ("cover_letter_doc_url", "cover_letter_url")),
        ("Google Sheet", ("google_sheet_url", "sheet_url")),
    ):
        value = first_value(result, *names)
        if value:
            lines.append(f"{label}: {value}")

    if result.get("error_message"):
        lines.append("Workflow error: " + str(result["error_message"]))
    return "\n".join(lines)


def stage_text(
    run_id: int,
    job_id: int,
    queue: dict[str, Any] | None,
    execution: dict[str, Any] | None,
    result: dict[str, Any] | None,
) -> str:
    queue_id = queue.get("id") if queue else None
    queue_status = str(queue.get("queue_status") if queue else "pending").lower()
    execution_id = execution.get("id") if execution else None
    execution_status = str(execution.get("status") if execution else "waiting").lower()

    if result:
        stage = "Callback received; final outputs are being confirmed."
    elif execution_status == "success":
        stage = "n8n finished successfully; waiting for the localhost callback."
    elif execution_status in {"running", "waiting", "new"}:
        stage = f"n8n execution is {execution_status}."
    elif queue_status == "accepted":
        stage = "Production webhook accepted; waiting for n8n to start."
    elif queue_status == "dispatching":
        stage = "Calling the production n8n webhook."
    else:
        stage = f"Queue is {queue_status}."

    lines = [
        "⏳ TELEGRAM MANUAL → N8N",
        "",
        f"Manual run: {run_id}",
        f"Job ID: {job_id}",
    ]
    if queue_id:
        lines.append(f"Queue: {queue_id} ({queue_status})")
    if execution_id:
        lines.append(f"n8n execution: {execution_id} ({execution_status})")
    lines.extend(["", stage])
    return "\n".join(lines)


def completion_text(
    run_id: int,
    job_id: int,
    queue: dict[str, Any],
    execution: dict[str, Any],
    result: dict[str, Any],
) -> str:
    # AADIL_UNIVERSAL_N8N_PROGRESS_V1
    from app.universal_n8n_progress import render_final_message

    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
        job = dict(row) if row is not None else {"id": job_id}
    finally:
        connection.close()

    return render_final_message(
        job,
        result,
        queue,
        execution,
        html_mode=False,
        heading="✅ TELEGRAM MANUAL FULL LOOP COMPLETED",
    )


def run_manual(run_id: int) -> None:
    run = load_run(run_id)
    parsed = json.loads(run["parsed_json"])
    raw_job = parsed["job"]

    acquire_lock(run_id)
    scheduler_was_loaded = False

    try:
        update_run(run_id, run_status="preflight", worker_pid=os.getpid())
        send_or_edit_status(
            run_id,
            (
                "🔎 TELEGRAM MANUAL PREFLIGHT\n\n"
                f"Manual run: {run_id}\n"
                "Checking n8n, FastAPI, queue safety, and the daily manual limit."
            ),
        )

        scheduler_was_loaded = pause_scraper_scheduler()

        workers = wait_for_source_workers()
        if workers:
            raise RuntimeError(
                "A source scraper remained active after the manual-priority wait:\n"
                + "\n".join(workers[:8])
            )

        n8n_host, n8n_port = service_endpoint("n8n")
        api_host, api_port = service_endpoint("fastapi")
        if not port_open(n8n_port, n8n_host):
            raise RuntimeError(f"n8n is offline on configured port {n8n_port}.")
        if not port_open(api_port, api_host):
            raise RuntimeError(f"FastAPI is offline on configured port {api_port}.")
        if not n8n_database_path().exists():
            raise RuntimeError("The configured n8n database is missing.")

        running = running_primary_executions()
        if running:
            raise RuntimeError(
                "The primary n8n workflow already has a running/waiting execution. "
                "Let it finish before submitting another manual job."
            )

        connection = get_connection()
        try:
            ensure_dispatch_schema(connection)
            open_rows = open_queue_rows(connection)
            if open_rows:
                raise RuntimeError(
                    "An unfinished n8n dispatch queue already exists. "
                    "Let the current job finish before submitting another one."
                )
            limit, used = manual_daily_limit(connection)
            if used >= limit:
                raise RuntimeError(
                    f"The Telegram manual n8n daily limit is reached ({used}/{limit})."
                )
        finally:
            connection.close()

        baseline = capture_baseline()

        update_run(run_id, run_status="storing")
        send_or_edit_status(
            run_id,
            (
                "💾 STORING MANUAL JOB\n\n"
                f"Manual run: {run_id}\n"
                f"{raw_job.get('company_name')} — {raw_job.get('title')}\n\n"
                "Normalizing, scoring, fingerprinting, and deduplicating."
            ),
        )

        connection = get_connection()
        try:
            ensure_dispatch_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            save_result = save_job(
                connection,
                raw_job,
                actor="telegram_manual_input",
            )
            connection.commit()
            job_id = int(save_result.get("job_id") or save_result.get("id") or 0)
            if not job_id:
                raise RuntimeError(
                    "The job store did not return a job ID: "
                    + json.dumps(save_result, default=str)
                )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        update_run(run_id, job_id=job_id, run_status="stored")
        add_event(
            job_id,
            "telegram_manual_input_stored",
            "completed",
            {
                "run_id": run_id,
                "inserted": bool(save_result.get("inserted")),
                "duplicate_reason": save_result.get("duplicate_reason"),
            },
        )

        existing_queue = queue_for_job(job_id)
        connection = get_connection()
        try:
            job_row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if job_row is None:
                raise RuntimeError("The stored job disappeared.")
            job = dict(job_row)
        finally:
            connection.close()

        if existing_queue or int(job.get("sent_to_n8n") or 0) == 1:
            lines = [
                "ℹ️ MANUAL JOB ALREADY STORED",
                "",
                f"Manual run: {run_id}",
                f"Job ID: {job_id}",
            ]
            if existing_queue:
                lines.append(
                    f"Existing queue: {existing_queue.get('id')} "
                    f"({existing_queue.get('queue_status')})"
                )
            lines.extend(
                [
                    "",
                    "No second n8n execution was started because this job already "
                    "has a queue or send record.",
                ]
            )
            complete_run(run_id, "duplicate_not_dispatched")
            send_or_edit_status(run_id, "\n".join(lines))
            return

        connection = get_connection()
        try:
            ensure_dispatch_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE jobs
                SET status = 'approved_for_n8n', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
            approved = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if approved is None:
                raise RuntimeError("Could not reload the approved job.")

            queue = insert_queue_item(
                connection,
                dict(approved),
                "telegram_manual",
                "production",
            )
            queue_id = int(queue["id"])
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        update_run(run_id, queue_id=queue_id, run_status="queued")
        # AADIL_MANUAL_REGISTER_UNIVERSAL_PROGRESS_V1
        try:
            from app.universal_n8n_progress import register_progress

            run_state = load_run(run_id)
            register_progress(
                job_id=job_id,
                queue_id=queue_id,
                dispatch_mode="telegram_manual",
                chat_id=int(run_state.get("chat_id") or CHAT_ID or 0) or None,
                status_message_id=run_state.get("status_message_id"),
            )
        except Exception as progress_error:
            print(
                f"Universal progress registration failed: {progress_error}",
                flush=True,
            )
        add_event(
            job_id,
            "telegram_manual_input_queued",
            "completed",
            {
                "run_id": run_id,
                "queue_id": queue_id,
                "dispatch_mode": "telegram_manual",
                "webhook_mode": "production",
            },
        )

        send_or_edit_status(
            run_id,
            (
                "📨 MANUAL JOB QUEUED\n\n"
                f"Manual run: {run_id}\n"
                f"Job ID: {job_id}\n"
                f"Queue: {queue_id}\n\n"
                "Dispatching exactly one production request to n8n."
            ),
        )

        dispatch_result = dispatch_pending(
            webhook_mode="production",
            dry_run=False,
            allow_disabled=False,
            limit=1,
        )

        if dispatch_result.get("blocked"):
            raise RuntimeError(
                "Production dispatch was blocked: "
                + json.dumps(dispatch_result, default=str)
            )

        dispatched = dispatch_result.get("dispatched") or []
        matched = [
            item
            for item in dispatched
            if int(item.get("job_id") or 0) == job_id
            and int(item.get("queue_id") or 0) == queue_id
        ]

        if int(dispatch_result.get("n8n_calls") or 0) != 1 or len(matched) != 1:
            raise RuntimeError(
                "Expected exactly one accepted n8n dispatch for this job: "
                + json.dumps(dispatch_result, default=str)
            )

        if (
            str(matched[0].get("result")) != "accepted"
            or str(matched[0].get("execution_scope")) != "full"
        ):
            raise RuntimeError(
                "The production webhook was not accepted with full scope: "
                + json.dumps(matched[0], default=str)
            )

        update_run(run_id, run_status="dispatched")
        add_event(
            job_id,
            "telegram_manual_input_dispatched",
            "completed",
            {
                "run_id": run_id,
                "queue_id": queue_id,
                "dispatch_result": matched[0],
            },
        )

        max_run_seconds = downstream_int("manual_worker_max_run_seconds", minimum=1)
        callback_grace_seconds = downstream_int("n8n_callback_grace_seconds", minimum=1)
        poll_seconds = downstream_int("manual_worker_poll_seconds", minimum=1)
        deadline = time.time() + max_run_seconds
        success_seen_at: float | None = None
        last_stage: str | None = None

        while time.time() < deadline:
            queue_state = queue_for_job(job_id)
            result = result_for_job(job_id, baseline["result_id"])
            execution = latest_execution_after(baseline["execution_id"])

            if execution and int(execution.get("id") or 0):
                update_run(
                    run_id,
                    execution_id=int(execution["id"]),
                    run_status="n8n_" + str(execution.get("status") or "running"),
                )

            stage = stage_text(
                run_id,
                job_id,
                queue_state,
                execution,
                result,
            )
            if stage != last_stage:
                send_or_edit_status(run_id, stage)
                last_stage = stage

            queue_status = str(
                queue_state.get("queue_status") if queue_state else "missing"
            ).casefold()
            execution_status = str(
                execution.get("status") if execution else "waiting"
            ).casefold()

            if execution_status == "success" and success_seen_at is None:
                success_seen_at = time.time()

            if (
                queue_status == "completed"
                and execution_status == "success"
                and result is not None
            ):
                if str(result.get("n8n_status") or "").casefold() in {
                    "failed",
                    "error",
                    "crashed",
                    "cancelled",
                    "canceled",
                }:
                    raise RuntimeError(
                        "The callback reported workflow failure: "
                        + str(result.get("error_message") or result.get("n8n_status"))
                    )

                text = completion_text(
                    run_id,
                    job_id,
                    queue_state,
                    execution,
                    result,
                )
                complete_run(run_id, "completed")
                add_event(
                    job_id,
                    "telegram_manual_input_completed",
                    "completed",
                    {
                        "run_id": run_id,
                        "queue_id": queue_id,
                        "execution_id": execution["id"],
                        "result_id": result.get("id"),
                    },
                )
                send_or_edit_status(run_id, text)
                return

            if execution_status in {"error", "crashed", "cancelled", "canceled"}:
                raise RuntimeError(
                    f"n8n execution {execution.get('id')} ended as {execution_status}."
                )

            if queue_status in {"failed", "cancelled", "canceled"}:
                raise RuntimeError(
                    "The n8n queue failed: "
                    + str(queue_state.get("last_error") or queue_status)
                )

            if (
                success_seen_at is not None
                and time.time() - success_seen_at > callback_grace_seconds
                and queue_status != "completed"
            ):
                raise RuntimeError(
                    "n8n finished successfully, but the localhost callback did "
                    f"not complete within {callback_grace_seconds} seconds."
                )

            time.sleep(poll_seconds)

        raise TimeoutError(
            f"Timed out after {max_run_seconds} seconds waiting for the manual n8n workflow."
        )

    finally:
        restore_scraper_scheduler(scheduler_was_loaded)
        release_lock()


def main() -> None:
    args = parse_args()
    run_id = int(args.run_id)

    try:
        run_manual(run_id)
    except Exception as error:
        error_text = str(error)
        try:
            run = load_run(run_id)
            job_id = run.get("job_id")
            complete_run(run_id, "failed", error_text[:4000])
            add_event(
                int(job_id) if job_id else None,
                "telegram_manual_input_failed",
                "failed",
                {
                    "run_id": run_id,
                    "error": error_text,
                    "traceback": traceback.format_exc()[-6000:],
                },
            )
            send_or_edit_status(
                run_id,
                (
                    "❌ TELEGRAM MANUAL FULL LOOP FAILED\n\n"
                    f"Manual run: {run_id}\n"
                    + (f"Job ID: {job_id}\n" if job_id else "")
                    + "\n"
                    + error_text
                    + "\n\nUse /manual_status to review the stored state. "
                    "Do not submit the same job again until the queue is checked."
                ),
            )
        finally:
            print(traceback.format_exc(), flush=True)
        raise


if __name__ == "__main__":
    main()
