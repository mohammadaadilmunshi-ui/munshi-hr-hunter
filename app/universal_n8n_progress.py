from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from app.database import ROOT_DIR, get_connection
from app.runtime_config import downstream_int, n8n_database_path, n8n_workflow_id

LOCK_DIR = ROOT_DIR / "data"

RESULT_EXTRA_COLUMNS: dict[str, str] = {
    "resume_docx_url": "TEXT",
    "cover_letter_pdf_url": "TEXT",
    "google_sheet_row_url": "TEXT",
    "contacts_sheet_url": "TEXT",
    "outreach_sheet_url": "TEXT",
    "recruiter_names_json": "TEXT",
    "recruiter_linkedin_urls_json": "TEXT",
    "recruiter_contacts_json": "TEXT",
    "execution_id": "INTEGER",
    "extra_outputs_json": "TEXT",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    if connection is None:
        connection = get_connection()

    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS telegram_n8n_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                queue_id INTEGER NOT NULL UNIQUE,
                dispatch_mode TEXT NOT NULL,
                chat_id INTEGER,
                status_message_id INTEGER,
                execution_id INTEGER,
                run_status TEXT NOT NULL DEFAULT 'created',
                last_progress_text TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_telegram_n8n_progress_job
            ON telegram_n8n_progress(job_id, id DESC);

            CREATE INDEX IF NOT EXISTS idx_telegram_n8n_progress_status
            ON telegram_n8n_progress(run_status, updated_at);
            """
        )

        result_columns = _table_columns(connection, "n8n_results")
        for name, sql_type in RESULT_EXTRA_COLUMNS.items():
            if name not in result_columns:
                connection.execute(
                    f'ALTER TABLE n8n_results ADD COLUMN "{name}" {sql_type}'
                )

        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _json_value(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def persist_extended_result(
    connection: sqlite3.Connection,
    job_id: int,
    payload: dict[str, Any],
    completed_at: str | None = None,
) -> int | None:
    """Persist optional callback details on the newest result row.

    This is intentionally additive. Existing n8n callback payloads continue
    to work unchanged; new recruiter/contact/output fields are stored when
    the workflow sends them.
    """
    ensure_schema(connection)

    row = connection.execute(
        """
        SELECT id
        FROM n8n_results
        WHERE job_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(job_id),),
    ).fetchone()
    if row is None:
        return None

    result_id = int(row["id"])
    values = {
        "resume_docx_url": payload.get("resume_docx_url")
        or payload.get("resume_word_url"),
        "cover_letter_pdf_url": payload.get("cover_letter_pdf_url"),
        "google_sheet_row_url": payload.get("google_sheet_row_url"),
        "contacts_sheet_url": payload.get("contacts_sheet_url"),
        "outreach_sheet_url": payload.get("outreach_sheet_url"),
        "recruiter_names_json": _json_value(payload.get("recruiter_names")),
        "recruiter_linkedin_urls_json": _json_value(
            payload.get("recruiter_linkedin_urls")
        ),
        "recruiter_contacts_json": _json_value(payload.get("recruiter_contacts")),
        "execution_id": payload.get("execution_id"),
        "extra_outputs_json": _json_value(payload.get("extra_outputs")),
    }

    assignments = [f'"{name}" = ?' for name in values]
    connection.execute(
        f"""
        UPDATE n8n_results
        SET {", ".join(assignments)}
        WHERE id = ?
        """,
        [*values.values(), result_id],
    )
    return result_id


def _first(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def _decode_json(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return value


def _as_list(value: Any) -> list[Any]:
    decoded = _decode_json(value)
    if decoded in (None, ""):
        return []
    if isinstance(decoded, list):
        return decoded
    if isinstance(decoded, tuple):
        return list(decoded)
    if isinstance(decoded, str):
        pieces = [
            item.strip()
            for item in re.split(r"[\n,;]+", decoded)
            if item.strip()
        ]
        return pieces
    return [decoded]


def _extra_outputs(result: dict[str, Any]) -> dict[str, Any]:
    value = _decode_json(result.get("extra_outputs_json"))
    return value if isinstance(value, dict) else {}


def _link_value(result: dict[str, Any], extra: dict[str, Any], *names: str) -> str:
    value = _first(result, *names)
    if value in (None, ""):
        value = _first(extra, *names)
    return str(value or "").strip()


def _contact_rows(result: dict[str, Any], extra: dict[str, Any]) -> list[dict[str, str]]:
    contacts_raw = _decode_json(result.get("recruiter_contacts_json"))
    if contacts_raw in (None, ""):
        contacts_raw = _first(
            extra,
            "recruiter_contacts",
            "contacts",
            "recruiters",
        )

    contacts: list[dict[str, str]] = []
    if isinstance(contacts_raw, dict):
        contacts_raw = [contacts_raw]
    if isinstance(contacts_raw, list):
        for item in contacts_raw:
            if not isinstance(item, dict):
                continue
            contacts.append(
                {
                    "name": str(
                        _first(item, "name", "full_name", "recruiter_name") or ""
                    ).strip(),
                    "linkedin": str(
                        _first(
                            item,
                            "linkedin",
                            "linkedin_url",
                            "linkedin_profile",
                            "profile_url",
                        )
                        or ""
                    ).strip(),
                    "email": str(_first(item, "email", "recruiter_email") or "").strip(),
                    "title": str(
                        _first(item, "title", "job_title", "role") or ""
                    ).strip(),
                }
            )

    names = _as_list(
        _first(result, "recruiter_names_json")
        or _first(extra, "recruiter_names")
    )
    urls = _as_list(
        _first(result, "recruiter_linkedin_urls_json")
        or _first(extra, "recruiter_linkedin_urls", "linkedin_urls")
    )

    max_len = max(len(names), len(urls))
    for index in range(max_len):
        name = str(names[index] if index < len(names) else "").strip()
        url = str(urls[index] if index < len(urls) else "").strip()
        if not name and not url:
            continue
        candidate = {"name": name, "linkedin": url, "email": "", "title": ""}
        if candidate not in contacts:
            contacts.append(candidate)

    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for contact in contacts:
        key = (
            contact.get("name", "").casefold(),
            contact.get("linkedin", "").casefold(),
            contact.get("email", "").casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(contact)
    return deduped


def _safe(value: Any, html_mode: bool) -> str:
    text = str(value or "")
    return html.escape(text) if html_mode else text


def _link_line(label: str, url: str, html_mode: bool) -> str:
    if html_mode:
        return f'🔗 <a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
    return f"{label}: {url}"


def _truth_text(value: Any) -> str:
    if value in (None, ""):
        return "Not reported"
    try:
        return "Yes" if int(value) == 1 else "No"
    except Exception:
        return "Yes" if str(value).strip().casefold() in {
            "true",
            "yes",
            "y",
            "1",
        } else "No"


def render_final_message(
    job: dict[str, Any],
    result: dict[str, Any],
    queue: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
    *,
    html_mode: bool = False,
    heading: str = "✅ N8N APPLICATION PACKAGE COMPLETED",
) -> str:
    """Render the same final result content for every dispatch entry path."""
    queue = queue or {}
    execution = execution or {}
    extra = _extra_outputs(result)

    title = _first(job, "title", "job_title") or "Unknown position"
    company = _first(job, "company_name", "company") or "Unknown company"
    status = _first(result, "n8n_status", "status") or job.get("status") or "completed"
    dispatch_mode = _first(queue, "dispatch_mode") or result.get("send_mode") or "unknown"

    lines = [
        _safe(heading, html_mode),
        "",
        f"🏢 {_safe(company, html_mode)}",
        f"💼 {_safe(title, html_mode)}",
        f"📌 Status: {_safe(status, html_mode)}",
        f"🚦 Entry path: {_safe(dispatch_mode, html_mode)}",
    ]

    hunter_score = job.get("hunter_score")
    if hunter_score not in (None, ""):
        lines.append(f"📊 Hunter score: {_safe(hunter_score, html_mode)}")

    ats_score = _first(result, "final_ats_score", "ats_resume_score")
    if ats_score not in (None, ""):
        lines.append(f"🎯 ATS score: {_safe(ats_score, html_mode)}")

    queue_id = _first(queue, "id", "queue_id")
    if queue_id not in (None, ""):
        lines.append(f"📥 Queue ID: {_safe(queue_id, html_mode)}")

    execution_id = _first(
        execution,
        "id",
        "execution_id",
    ) or result.get("execution_id")
    if execution_id not in (None, ""):
        lines.append(f"🧩 n8n execution: {_safe(execution_id, html_mode)}")

    links = [
        (
            "Job posting",
            str(_first(job, "apply_url", "job_url") or "").strip(),
        ),
        (
            "Resume Word / DOCX",
            _link_value(
                result,
                extra,
                "resume_docx_url",
                "resume_word_url",
                "resume_download_url",
            ),
        ),
        (
            "Resume Google Doc",
            _link_value(result, extra, "resume_doc_url", "google_doc_url"),
        ),
        (
            "Resume PDF",
            _link_value(result, extra, "resume_pdf_url", "google_pdf_url"),
        ),
        (
            "Cover Letter Doc",
            _link_value(
                result,
                extra,
                "cover_letter_doc_url",
                "cover_letter_url",
            ),
        ),
        (
            "Cover Letter PDF",
            _link_value(result, extra, "cover_letter_pdf_url"),
        ),
        (
            "Job sheet row",
            _link_value(
                result,
                extra,
                "google_sheet_row_url",
                "job_sheet_row_url",
            ),
        ),
        (
            "Main Google Sheet",
            _link_value(result, extra, "google_sheet_url", "sheet_url"),
        ),
        (
            "Contacts Sheet",
            _link_value(result, extra, "contacts_sheet_url"),
        ),
        (
            "Outreach Sheet",
            _link_value(result, extra, "outreach_sheet_url"),
        ),
    ]

    visible_links = [(label, url) for label, url in links if url]
    if visible_links:
        lines.extend(["", "📄 OUTPUTS"])
        lines.extend(_link_line(label, url, html_mode) for label, url in visible_links)

    contacts = _contact_rows(result, extra)
    recruiter_found = result.get("recruiter_found")
    outreach_created = result.get("outreach_draft_created")

    lines.extend(
        [
            "",
            f"👥 Recruiters found: {_safe(_truth_text(recruiter_found), html_mode)}",
            f"✉️ Outreach draft created: {_safe(_truth_text(outreach_created), html_mode)}",
        ]
    )

    if contacts:
        lines.append("")
        lines.append("👤 RECRUITER CONTACTS")
        for contact in contacts[:10]:
            name = contact.get("name") or "Recruiter"
            title_text = contact.get("title") or ""
            linkedin = contact.get("linkedin") or ""
            email = contact.get("email") or ""
            display = name + (f" — {title_text}" if title_text else "")
            if linkedin:
                if html_mode:
                    lines.append(
                        f'• <a href="{html.escape(linkedin, quote=True)}">'
                        f"{html.escape(display)}</a>"
                    )
                else:
                    lines.append(f"• {display}: {linkedin}")
            else:
                lines.append(f"• {_safe(display, html_mode)}")
            if email:
                lines.append(f"  Email: {_safe(email, html_mode)}")
    elif _truth_text(recruiter_found) == "Yes":
        lines.append(
            "Recruiter details were not included in the localhost callback. "
            "Open the Contacts Sheet link when available."
        )

    error = str(result.get("error_message") or "").strip()
    if error:
        lines.extend(["", f"⚠️ {_safe(error, html_mode)}"])

    text = "\n".join(lines)
    if len(text) > 3950:
        text = text[:3920].rstrip() + "\n…"
    return text


def _load_job(job_id: int) -> dict[str, Any]:
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
        if row is None:
            raise RuntimeError(f"Job {job_id} was not found.")
        return dict(row)
    finally:
        connection.close()


def _load_queue(queue_id: int) -> dict[str, Any]:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM n8n_dispatch_queue WHERE id = ?",
            (int(queue_id),),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Queue {queue_id} was not found.")
        return dict(row)
    finally:
        connection.close()


def _latest_result(job_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute(
            """
            SELECT *
            FROM n8n_results
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(job_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text.split(".")[0], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _execution_for_queue(queue: dict[str, Any]) -> dict[str, Any] | None:
    database_path = n8n_database_path()
    if not database_path.exists():
        return None

    accepted = _parse_time(queue.get("accepted_at") or queue.get("reserved_at"))
    floor = accepted - timedelta(seconds=45) if accepted else None

    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
        timeout=15,
    )
    connection.row_factory = sqlite3.Row
    try:
        columns = _table_columns(connection, "execution_entity")
        selected = [
            name
            for name in (
                "id",
                "workflowId",
                "status",
                "startedAt",
                "stoppedAt",
                "finished",
            )
            if name in columns
        ]
        if "id" not in selected:
            return None
        rows = connection.execute(
            f"""
            SELECT {", ".join(selected)}
            FROM execution_entity
            WHERE workflowId = ?
            ORDER BY CAST(id AS INTEGER) DESC
            LIMIT 25
            """,
            (n8n_workflow_id(),),
        ).fetchall()

        candidates: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            started = _parse_time(item.get("startedAt"))
            if floor is not None and started is not None and started < floor:
                continue
            candidates.append(item)

        if not candidates:
            return None

        # Queue guards allow only one production execution at a time.
        # The earliest execution after acceptance is the best correlation.
        candidates.sort(key=lambda item: int(item.get("id") or 0))
        return candidates[0]
    finally:
        connection.close()


def _telegram_request(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    from app.telegram_client import telegram_request

    return telegram_request(method, payload)


def _configured_chat_id() -> int:
    from app.telegram_client import CHAT_ID

    return int(str(CHAT_ID or "0").strip() or 0)


def _send_message(chat_id: int, text: str) -> int:
    response = _telegram_request(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
    )
    return int(response["result"]["message_id"])


def _edit_message(chat_id: int, message_id: int, text: str) -> None:
    try:
        _telegram_request(
            "editMessageText",
            {
                "chat_id": str(chat_id),
                "message_id": str(message_id),
                "text": text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )
    except Exception as error:
        if "message is not modified" not in str(error).casefold():
            raise


def _progress_row(queue_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute(
            "SELECT * FROM telegram_n8n_progress WHERE queue_id = ?",
            (int(queue_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def register_progress(
    *,
    job_id: int,
    queue_id: int,
    dispatch_mode: str,
    chat_id: int | None = None,
    status_message_id: int | None = None,
) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO telegram_n8n_progress (
                job_id,
                queue_id,
                dispatch_mode,
                chat_id,
                status_message_id,
                run_status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'registered', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(queue_id) DO UPDATE SET
                job_id = excluded.job_id,
                dispatch_mode = excluded.dispatch_mode,
                chat_id = COALESCE(excluded.chat_id, telegram_n8n_progress.chat_id),
                status_message_id = COALESCE(
                    excluded.status_message_id,
                    telegram_n8n_progress.status_message_id
                ),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(job_id),
                int(queue_id),
                str(dispatch_mode),
                int(chat_id) if chat_id not in (None, "") else None,
                int(status_message_id)
                if status_message_id not in (None, "")
                else None,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM telegram_n8n_progress WHERE queue_id = ?",
            (int(queue_id),),
        ).fetchone()
        return dict(row)
    finally:
        connection.close()


def _update_progress_row(queue_id: int, **values: Any) -> None:
    allowed = {
        "chat_id",
        "status_message_id",
        "execution_id",
        "run_status",
        "last_progress_text",
        "error_message",
        "completed_at",
    }
    clean = {key: value for key, value in values.items() if key in allowed}
    if not clean:
        return
    assignments = [f'"{name}" = ?' for name in clean]
    assignments.append("updated_at = CURRENT_TIMESTAMP")
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute(
            f"""
            UPDATE telegram_n8n_progress
            SET {", ".join(assignments)}
            WHERE queue_id = ?
            """,
            [*clean.values(), int(queue_id)],
        )
        connection.commit()
    finally:
        connection.close()


def _monitor_lock(queue_id: int) -> Path:
    return LOCK_DIR / f"universal_n8n_monitor_{int(queue_id)}.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def start_monitor(
    *,
    job_id: int,
    queue_id: int,
    dispatch_mode: str,
    chat_id: int | None = None,
    status_message_id: int | None = None,
) -> dict[str, Any]:
    """Start a monitor only for paths without their own foreground worker."""
    register_progress(
        job_id=job_id,
        queue_id=queue_id,
        dispatch_mode=dispatch_mode,
        chat_id=chat_id,
        status_message_id=status_message_id,
    )

    if str(dispatch_mode).casefold() == "telegram_manual":
        return {
            "success": True,
            "started": False,
            "reason": "manual_or_stored_worker_tracks_progress",
        }

    lock_path = _monitor_lock(queue_id)
    if lock_path.exists():
        try:
            payload = json.loads(lock_path.read_text())
        except Exception:
            payload = {}
        pid = int(payload.get("pid") or 0)
        if _pid_alive(pid):
            return {
                "success": True,
                "started": False,
                "reason": "monitor_already_running",
                "pid": pid,
            }
        lock_path.unlink(missing_ok=True)

    python_path = ROOT_DIR / ".venv" / "bin" / "python"
    if not python_path.exists():
        python_path = Path(sys.executable)

    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (
        f"universal_n8n_monitor_queue_{int(queue_id)}_"
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
                "app.universal_n8n_progress",
                "--monitor-queue",
                str(int(queue_id)),
            ],
            cwd=ROOT_DIR,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        handle.close()

    lock_path.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "job_id": int(job_id),
                "queue_id": int(queue_id),
                "dispatch_mode": str(dispatch_mode),
                "log_path": str(log_path),
                "started_at": utc_now(),
            },
            indent=2,
        )
        + "\n"
    )
    return {
        "success": True,
        "started": True,
        "pid": process.pid,
        "log_path": str(log_path),
    }


def _render_progress(
    job: dict[str, Any],
    queue: dict[str, Any],
    execution: dict[str, Any] | None,
    result: dict[str, Any] | None,
) -> str:
    execution = execution or {}
    queue_status = str(queue.get("queue_status") or "unknown")
    execution_id = execution.get("id")
    execution_status = str(execution.get("status") or "waiting_for_start")
    callback_status = (
        str(result.get("n8n_status") or "received")
        if result
        else "waiting"
    )
    dispatch_mode = str(queue.get("dispatch_mode") or "unknown")
    return "\n".join(
        [
            "⚙️ <b>n8n application workflow in progress</b>",
            "",
            f"🏢 <b>{html.escape(str(job.get('company_name') or 'Unknown company'))}</b>",
            f"💼 {html.escape(str(job.get('title') or 'Unknown position'))}",
            f"🚦 Entry path: <b>{html.escape(dispatch_mode)}</b>",
            f"📥 Queue {int(queue.get('id') or 0)}: <b>{html.escape(queue_status)}</b>",
            (
                f"🧩 Execution {int(execution_id)}: "
                f"<b>{html.escape(execution_status)}</b>"
                if execution_id
                else "🧩 Execution: <b>waiting to start</b>"
            ),
            f"📡 Localhost callback: <b>{html.escape(callback_status)}</b>",
            "",
            "This message will update until the final files and scores are ready.",
        ]
    )[:4000]


def monitor_queue(queue_id: int) -> dict[str, Any]:
    queue_id = int(queue_id)
    lock_path = _monitor_lock(queue_id)
    queue = _load_queue(queue_id)
    job_id = int(queue["job_id"])
    job = _load_job(job_id)
    progress = register_progress(
        job_id=job_id,
        queue_id=queue_id,
        dispatch_mode=str(queue.get("dispatch_mode") or "unknown"),
    )
    chat_id = int(progress.get("chat_id") or _configured_chat_id() or 0)
    if not chat_id:
        raise RuntimeError("Telegram chat ID is not configured.")

    message_id = int(progress.get("status_message_id") or 0)
    if not message_id:
        message_id = _send_message(
            chat_id,
            _render_progress(job, queue, None, None),
        )
        _update_progress_row(
            queue_id,
            chat_id=chat_id,
            status_message_id=message_id,
            run_status="monitoring",
        )

    started = time.monotonic()
    poll_seconds = downstream_int("n8n_progress_poll_seconds", minimum=1)
    max_monitor_seconds = downstream_int("n8n_progress_max_monitor_seconds", minimum=1)
    callback_grace_seconds = downstream_int("n8n_callback_grace_seconds", minimum=1)
    last_text = ""
    terminal_seen_at: float | None = None

    try:
        while time.monotonic() - started < max_monitor_seconds:
            queue = _load_queue(queue_id)
            result = _latest_result(job_id)
            execution = _execution_for_queue(queue)

            queue_status = str(queue.get("queue_status") or "unknown").casefold()
            execution_status = str(
                (execution or {}).get("status")
                or (
                    "success"
                    if (execution or {}).get("finished")
                    else "waiting_for_start"
                )
            ).casefold()

            text = _render_progress(job, queue, execution, result)
            if text != last_text:
                _edit_message(chat_id, message_id, text)
                last_text = text
                _update_progress_row(
                    queue_id,
                    execution_id=(execution or {}).get("id"),
                    run_status=f"n8n_{execution_status}",
                    last_progress_text=text,
                )

            if execution_status in {
                "success",
                "error",
                "failed",
                "crashed",
                "cancelled",
                "canceled",
            }:
                if terminal_seen_at is None:
                    terminal_seen_at = time.monotonic()

            if result is not None and execution_status == "success":
                # Let any final callback iteration land, then select newest row.
                if terminal_seen_at is not None and time.monotonic() - terminal_seen_at < 8:
                    time.sleep(poll_seconds)
                    continue
                result = _latest_result(job_id) or result
                final_text = render_final_message(
                    _load_job(job_id),
                    result,
                    queue,
                    execution,
                    html_mode=True,
                )
                _edit_message(chat_id, message_id, final_text)
                _update_progress_row(
                    queue_id,
                    execution_id=(execution or {}).get("id"),
                    run_status="completed",
                    last_progress_text=final_text,
                    error_message=None,
                    completed_at=utc_now(),
                )
                return {
                    "success": True,
                    "job_id": job_id,
                    "queue_id": queue_id,
                    "execution_id": (execution or {}).get("id"),
                    "result_id": result.get("id"),
                }

            if queue_status in {"failed", "cancelled", "canceled"}:
                raise RuntimeError(
                    str(queue.get("last_error") or f"Queue ended as {queue_status}.")
                )

            if execution_status in {
                "error",
                "failed",
                "crashed",
                "cancelled",
                "canceled",
            }:
                raise RuntimeError(
                    f"n8n execution {(execution or {}).get('id')} ended as "
                    f"{execution_status}."
                )

            if (
                terminal_seen_at is not None
                and execution_status == "success"
                and result is None
                and time.monotonic() - terminal_seen_at > callback_grace_seconds
            ):
                raise RuntimeError(
                    "n8n finished successfully, but no localhost callback result "
                    f"arrived within {callback_grace_seconds} seconds."
                )

            time.sleep(poll_seconds)

        raise TimeoutError(
            f"Timed out after {max_monitor_seconds} seconds waiting for n8n completion."
        )
    except Exception as error:
        error_text = str(error)
        failure = "\n".join(
            [
                "❌ <b>n8n application workflow stopped</b>",
                "",
                f"🏢 <b>{html.escape(str(job.get('company_name') or 'Unknown company'))}</b>",
                f"💼 {html.escape(str(job.get('title') or 'Unknown position'))}",
                f"Reason: {html.escape(error_text)}",
                "",
                "No automatic retry was started.",
            ]
        )[:4000]
        try:
            _edit_message(chat_id, message_id, failure)
        finally:
            _update_progress_row(
                queue_id,
                run_status="failed",
                last_progress_text=failure,
                error_message=error_text[:4000],
                completed_at=utc_now(),
            )
        raise
    finally:
        if lock_path.exists():
            try:
                payload = json.loads(lock_path.read_text())
            except Exception:
                payload = {}
            if int(payload.get("pid") or 0) in {0, os.getpid()}:
                lock_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitor-queue", type=int)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        sample_job = {
            "company_name": "Example Company",
            "title": "HR Intern",
            "hunter_score": 95,
            "job_url": "https://example.com/job",
        }
        sample_result = {
            "n8n_status": "application_ready",
            "final_ats_score": 94,
            "resume_doc_url": "https://example.com/resume-doc",
            "resume_pdf_url": "https://example.com/resume.pdf",
            "recruiter_found": 1,
            "outreach_draft_created": 1,
            "recruiter_names_json": json.dumps(["Recruiter One"]),
            "recruiter_linkedin_urls_json": json.dumps(
                ["https://linkedin.com/in/example"]
            ),
        }
        text = render_final_message(
            sample_job,
            sample_result,
            {"id": 1, "dispatch_mode": "auto_top_match"},
            {"id": 2, "status": "success"},
            html_mode=False,
        )
        print(
            json.dumps(
                {
                    "success": True,
                    "network_request_made": False,
                    "rendered_characters": len(text),
                    "contains_ats": "ATS score" in text,
                    "contains_recruiter": "Recruiter One" in text,
                },
                indent=2,
            )
        )
        return

    if args.monitor_queue is None:
        parser.error("--monitor-queue is required unless --self-test is used")
    result = monitor_queue(args.monitor_queue)
    print(json.dumps(result, indent=2, default=str))




# AADIL_OPT_US_NATIONWIDE_INTEGRITY_V1
from app.opt_us_nationwide_integrity_v1 import (
    find_webhook_execution_for_queue as _aadil_find_webhook_execution_v1,
    reconcile_n8n_queue as _aadil_reconcile_n8n_queue_v1,
)

_aadil_previous_execution_for_queue_v1 = _execution_for_queue
_aadil_previous_monitor_queue_v1 = monitor_queue


def _execution_for_queue(queue):
    matched = _aadil_find_webhook_execution_v1(
        queue,
        n8n_db=n8n_database_path(),
        workflow_id=n8n_workflow_id(),
    )
    return matched


def monitor_queue(queue_id):
    try:
        return _aadil_previous_monitor_queue_v1(queue_id)
    finally:
        try:
            _aadil_reconcile_n8n_queue_v1(
                n8n_db=n8n_database_path(),
            )
        except Exception:
            pass

if __name__ == "__main__":
    main()
