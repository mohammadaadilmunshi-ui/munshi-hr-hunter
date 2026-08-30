
from __future__ import annotations

import asyncio
import os
import signal
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.database import DB_PATH
from app.runtime_config import n8n_database_path, n8n_workflow_id

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HUNTER_DB = DB_PATH

OPEN_QUEUE_STATES = {
    "new", "open", "pending", "queued", "reserved", "dispatching",
    "accepted", "running", "processing", "waiting", "in_progress",
    "claimed", "locked", "retry", "retrying",
}
OPEN_MANUAL_STATES = OPEN_QUEUE_STATES | {
    "capturing", "submitted", "n8n_running", "n8n_success",
    "waiting_callback", "worker_started",
}
OPEN_PROGRESS_STATES = OPEN_QUEUE_STATES | {
    "registered", "n8n_running", "n8n_success", "waiting_callback",
}
OPEN_SESSION_STATES = {
    "active", "capturing", "collecting", "open", "waiting", "pending",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in con.execute(f"PRAGMA table_info({_q(table)})").fetchall()
    }


def _count_state_rows(
    con: sqlite3.Connection,
    table: str,
    status_column: str,
    states: set[str],
) -> int:
    if not _table_exists(con, table):
        return 0
    cols = _columns(con, table)
    if status_column not in cols:
        return 0
    placeholders = ",".join("?" for _ in states)
    row = con.execute(
        f"SELECT COUNT(*) FROM {_q(table)} "
        f"WHERE LOWER(COALESCE({_q(status_column)},'')) "
        f"IN ({placeholders})",
        tuple(sorted(states)),
    ).fetchone()
    return int(row[0] if row else 0)


def _count_true_open_progress(
    con: sqlite3.Connection,
) -> int:
    if not _table_exists(con, "telegram_n8n_progress"):
        return 0
    placeholders = ",".join("?" for _ in OPEN_PROGRESS_STATES)
    row = con.execute(
        f"""
        SELECT COUNT(*)
        FROM telegram_n8n_progress AS p
        LEFT JOIN n8n_dispatch_queue AS q
          ON q.id = p.queue_id
        WHERE LOWER(COALESCE(p.run_status,'')) IN ({placeholders})
          AND LOWER(COALESCE(q.queue_status,'')) != 'completed'
          AND NOT EXISTS (
              SELECT 1
              FROM n8n_results AS r
              WHERE r.job_id = p.job_id
                AND r.completed_at IS NOT NULL
          )
        """,
        tuple(sorted(OPEN_PROGRESS_STATES)),
    ).fetchone()
    return int(row[0] if row else 0)


def _reconcile_terminal_progress(
    con: sqlite3.Connection,
) -> int:
    """Repair only progress rows that have independent terminal evidence."""
    if not _table_exists(con, "telegram_n8n_progress"):
        return 0
    placeholders = ",".join("?" for _ in OPEN_PROGRESS_STATES)
    cursor = con.execute(
        f"""
        UPDATE telegram_n8n_progress
        SET
            run_status = 'completed',
            error_message = NULL,
            completed_at = COALESCE(
                completed_at,
                (
                    SELECT q.completed_at
                    FROM n8n_dispatch_queue AS q
                    WHERE q.id = telegram_n8n_progress.queue_id
                ),
                CURRENT_TIMESTAMP
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE LOWER(COALESCE(run_status,'')) IN ({placeholders})
          AND (
              EXISTS (
                  SELECT 1
                  FROM n8n_dispatch_queue AS q
                  WHERE q.id = telegram_n8n_progress.queue_id
                    AND LOWER(COALESCE(q.queue_status,'')) = 'completed'
              )
              OR EXISTS (
                  SELECT 1
                  FROM n8n_results AS r
                  WHERE r.job_id = telegram_n8n_progress.job_id
                    AND r.completed_at IS NOT NULL
              )
          )
        """,
        tuple(sorted(OPEN_PROGRESS_STATES)),
    )
    return int(cursor.rowcount if cursor.rowcount is not None else 0)


def _terminalize_true_open_progress(
    con: sqlite3.Connection,
    *,
    reason: str,
) -> int:
    if not _table_exists(con, "telegram_n8n_progress"):
        return 0
    placeholders = ",".join("?" for _ in OPEN_PROGRESS_STATES)
    now = _utc_now()
    cursor = con.execute(
        f"""
        UPDATE telegram_n8n_progress
        SET
            run_status = 'canceled',
            error_message = ?,
            completed_at = COALESCE(completed_at, ?),
            updated_at = CURRENT_TIMESTAMP
        WHERE LOWER(COALESCE(run_status,'')) IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1
              FROM n8n_dispatch_queue AS q
              WHERE q.id = telegram_n8n_progress.queue_id
                AND LOWER(COALESCE(q.queue_status,'')) = 'completed'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM n8n_results AS r
              WHERE r.job_id = telegram_n8n_progress.job_id
                AND r.completed_at IS NOT NULL
          )
        """,
        (reason, now, *tuple(sorted(OPEN_PROGRESS_STATES))),
    )
    return int(cursor.rowcount if cursor.rowcount is not None else 0)


def _active_n8n_executions() -> list[dict[str, Any]]:
    database_path = n8n_database_path()
    if not database_path.exists():
        return []
    con = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT id, status, startedAt, stoppedAt
            FROM execution_entity
            WHERE workflowId=?
              AND LOWER(COALESCE(status,'')) IN ('new','running','waiting')
            ORDER BY id DESC
            """,
            (n8n_workflow_id(),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def inspect_force_remove_state() -> dict[str, Any]:
    if not HUNTER_DB.exists():
        raise RuntimeError(f"Hunter database not found: {HUNTER_DB}")

    con = sqlite3.connect(HUNTER_DB)
    con.row_factory = sqlite3.Row
    try:
        queue_open = _count_state_rows(
            con, "n8n_dispatch_queue", "queue_status", OPEN_QUEUE_STATES
        )
        manual_open = _count_state_rows(
            con, "telegram_manual_runs", "run_status", OPEN_MANUAL_STATES
        )
        progress_open = _count_true_open_progress(con)
        sessions_open = _count_state_rows(
            con, "telegram_manual_sessions", "session_status", OPEN_SESSION_STATES
        )

        latest_queue = None
        if _table_exists(con, "n8n_dispatch_queue"):
            cols = _columns(con, "n8n_dispatch_queue")
            selected = [
                c for c in (
                    "id", "job_id", "queue_status", "request_id",
                    "accepted_at", "completed_at", "updated_at", "last_error"
                )
                if c in cols
            ]
            if selected:
                row = con.execute(
                    f"SELECT {', '.join(_q(c) for c in selected)} "
                    f"FROM {_q('n8n_dispatch_queue')} "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                latest_queue = dict(row) if row else None

        active = _active_n8n_executions()
        return {
            "queue_open": queue_open,
            "manual_open": manual_open,
            "progress_open": progress_open,
            "sessions_open": sessions_open,
            "open_total": queue_open + manual_open + progress_open + sessions_open,
            "active_n8n": active,
            "latest_queue": latest_queue,
        }
    finally:
        con.close()


def _backup_hunter_db() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PROJECT_ROOT / "backups" / f"telegram_force_remove_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / "hunter.db"

    source = sqlite3.connect(HUNTER_DB)
    destination = sqlite3.connect(backup_path)
    try:
        with destination:
            source.backup(destination)
    finally:
        destination.close()
        source.close()

    return backup_path


def _safe_kill_worker(pid: int) -> dict[str, Any]:
    result: dict[str, Any] = {"pid": pid, "action": "not_running"}
    if pid <= 1:
        return result
    try:
        command = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return result

    result["command"] = command
    allowed_markers = (
        "manual_input_worker",
        "stored_job_n8n_worker",
        "telegram_manual",
    )
    if not any(marker in command for marker in allowed_markers):
        result["action"] = "skipped_unrecognized_process"
        return result

    try:
        os.kill(pid, signal.SIGTERM)
        result["action"] = "sigterm_sent"
    except ProcessLookupError:
        result["action"] = "not_running"
    except PermissionError:
        result["action"] = "permission_denied"
    return result


def _terminalize(
    con: sqlite3.Connection,
    table: str,
    status_column: str,
    open_states: set[str],
    *,
    terminal_status: str = "canceled",
    reason: str,
) -> tuple[int, list[int]]:
    if not _table_exists(con, table):
        return 0, []

    cols = _columns(con, table)
    if status_column not in cols:
        return 0, []

    placeholders = ",".join("?" for _ in open_states)
    where_sql = (
        f"LOWER(COALESCE({_q(status_column)},'')) IN ({placeholders})"
    )
    where_params = tuple(sorted(open_states))

    worker_pids: list[int] = []
    if "worker_pid" in cols:
        rows = con.execute(
            f"SELECT worker_pid FROM {_q(table)} "
            f"WHERE {where_sql} AND worker_pid IS NOT NULL",
            where_params,
        ).fetchall()
        for row in rows:
            try:
                worker_pids.append(int(row[0]))
            except (TypeError, ValueError):
                pass

    assignments = [f"{_q(status_column)}=?"]
    values: list[Any] = [terminal_status]
    now = _utc_now()

    for column in ("completed_at", "stopped_at", "finished_at", "cancelled_at", "canceled_at"):
        if column in cols:
            assignments.append(f"{_q(column)}=COALESCE({_q(column)}, ?)")
            values.append(now)

    for column in ("updated_at", "updatedAt"):
        if column in cols:
            assignments.append(f"{_q(column)}=?")
            values.append(now)

    for column in ("finished", "completed", "done", "is_complete"):
        if column in cols:
            assignments.append(f"{_q(column)}=1")

    for column in ("is_running", "running", "in_progress", "active", "locked", "claimed"):
        if column in cols:
            assignments.append(f"{_q(column)}=0")

    for column in ("locked_by", "worker_id", "claim_token", "lease_owner", "processing_by"):
        if column in cols:
            assignments.append(f"{_q(column)}=NULL")

    for column in ("error_message", "last_error", "status_message"):
        if column in cols:
            assignments.append(f"{_q(column)}=?")
            values.append(reason)

    cursor = con.execute(
        f"UPDATE {_q(table)} SET {', '.join(assignments)} "
        f"WHERE {where_sql}",
        tuple(values) + where_params,
    )
    return int(cursor.rowcount if cursor.rowcount is not None else 0), worker_pids


def clear_stuck_state() -> dict[str, Any]:
    before = inspect_force_remove_state()
    if before["active_n8n"]:
        raise RuntimeError(
            "Refusing to clear local queue while a real n8n execution is active."
        )

    backup_path = _backup_hunter_db()
    reason = "Force-removed from Telegram /forceremove after no active n8n execution was detected."

    con = sqlite3.connect(HUNTER_DB)
    try:
        con.execute("BEGIN IMMEDIATE")
        queue_count, _ = _terminalize(
            con,
            "n8n_dispatch_queue",
            "queue_status",
            OPEN_QUEUE_STATES,
            reason=reason,
        )
        manual_count, worker_pids = _terminalize(
            con,
            "telegram_manual_runs",
            "run_status",
            OPEN_MANUAL_STATES,
            reason=reason,
        )
        reconciled_progress = _reconcile_terminal_progress(con)
        progress_count = _terminalize_true_open_progress(
            con,
            reason=reason,
        )
        session_count, _ = _terminalize(
            con,
            "telegram_manual_sessions",
            "session_status",
            OPEN_SESSION_STATES,
            terminal_status="cancelled",
            reason=reason,
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    worker_results = [_safe_kill_worker(pid) for pid in sorted(set(worker_pids))]
    after = inspect_force_remove_state()

    return {
        "backup": str(backup_path),
        "queue_rows_cleared": queue_count,
        "manual_runs_cleared": manual_count,
        "progress_rows_reconciled": reconciled_progress,
        "progress_rows_cleared": progress_count,
        "sessions_cleared": session_count,
        "workers": worker_results,
        "remaining_open": after["open_total"],
    }


def _authorized(update: Update) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False

    configured = (
        os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("AADIL_TELEGRAM_CHAT_ID")
        or os.getenv("TG_CHAT_ID")
        or ""
    ).strip()

    if not configured:
        return True
    return str(chat.id) == configured


def _preview_text(state: dict[str, Any]) -> str:
    active = state["active_n8n"]
    latest = state.get("latest_queue") or {}
    lines = [
        "⚠️ FORCE REMOVE STUCK QUEUE",
        "",
        f"📨 Open queue rows: {state['queue_open']}",
        f"🧵 Open manual runs: {state['manual_open']}",
        f"📡 Open progress rows: {state['progress_open']}",
        f"✍️ Open capture sessions: {state['sessions_open']}",
        f"⚙️ Active n8n executions: {len(active)}",
    ]

    if latest:
        lines.extend([
            "",
            f"Latest queue: {latest.get('id', '—')}",
            f"Latest job: {latest.get('job_id', '—')}",
            f"Latest status: {latest.get('queue_status', '—')}",
        ])

    lines.extend([
        "",
        "This removes only stuck items from the OPEN queue.",
        "It keeps stored jobs, completed results, resume links, PDFs, and n8n history.",
    ])

    if active:
        ids = ", ".join(str(row.get("id")) for row in active)
        lines.extend([
            "",
            f"⛔ Refusing to clear while n8n execution(s) {ids} are active.",
        ])

    return "\n".join(lines)


async def force_remove_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _authorized(update):
        await update.effective_message.reply_text("Unauthorized chat.")
        return

    state = await asyncio.to_thread(inspect_force_remove_state)
    if state["open_total"] == 0:
        await update.effective_message.reply_text(
            "✅ No stuck Telegram/n8n queue state is open."
        )
        return

    buttons = [[InlineKeyboardButton("Cancel", callback_data="force_remove:cancel")]]
    if not state["active_n8n"]:
        buttons[0].insert(
            0,
            InlineKeyboardButton(
                "🧹 Clear stuck state",
                callback_data="force_remove:confirm",
            ),
        )

    await update.effective_message.reply_text(
        _preview_text(state),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def maybe_handle_force_remove_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    query = update.callback_query
    if query is None:
        return False

    data = str(query.data or "")
    if not data.startswith("force_remove:"):
        return False

    try:
        await query.answer()
    except Exception:
        # A failed Telegram acknowledgement must not prevent local cleanup.
        pass

    if not _authorized(update):
        await query.edit_message_text("Unauthorized chat.")
        return True

    action = data.split(":", 1)[1]
    if action == "cancel":
        await query.edit_message_text("Force remove cancelled.")
        return True

    if action != "confirm":
        await query.edit_message_text("Unknown force-remove action.")
        return True

    state = await asyncio.to_thread(inspect_force_remove_state)
    if state["active_n8n"]:
        await query.edit_message_text(
            _preview_text(state) +
            "\n\nNothing was changed."
        )
        return True

    try:
        result = await asyncio.to_thread(clear_stuck_state)
    except Exception as exc:
        await query.edit_message_text(
            "❌ Force remove failed.\n\n"
            f"{type(exc).__name__}: {exc}"
        )
        return True

    await query.edit_message_text(
        "✅ STUCK QUEUE STATE CLEARED\n\n"
        f"📨 Queue rows: {result['queue_rows_cleared']}\n"
        f"🧵 Manual runs: {result['manual_runs_cleared']}\n"
        f"✅ Progress rows reconciled: {result['progress_rows_reconciled']}\n"
        f"📡 Truly stuck progress rows cleared: {result['progress_rows_cleared']}\n"
        f"✍️ Capture sessions: {result['sessions_cleared']}\n"
        f"📦 Remaining open state: {result['remaining_open']}\n\n"
        "Stored jobs, completed results, resume/PDF links, and n8n history were preserved."
    )
    return True

async def force_remove_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Dedicated CallbackQueryHandler entry point for force_remove:* buttons."""
    handled = await maybe_handle_force_remove_callback(update, context)
    if not handled and update.callback_query is not None:
        try:
            await update.callback_query.answer(
                "Force-remove callback was not recognized.",
                show_alert=True,
            )
        except Exception:
            pass
