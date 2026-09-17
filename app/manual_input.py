from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from app.database import ROOT_DIR, get_connection
from app.job_detail import build_manual_job_text, enrich_job_details
from app.job_store import save_job

MAX_CAPTURE_CHARACTERS = 120_000
WORKER_MODULE = "app.manual_input_worker"

HEADING_ALIASES: dict[str, str] = {
    "job title": "title",
    "title": "title",
    "company name": "company_name",
    "company": "company_name",
    "location": "location_raw",
    "remote hybrid onsite": "remote_type",
    "work arrangement": "remote_type",
    "internship part time full time": "employment_type",
    "employment type": "employment_type",
    "pay wage salary": "salary_raw",
    "salary": "salary_raw",
    "posted date": "date_posted",
    "date posted": "date_posted",
    "apply deadline": "apply_deadline",
    "application deadline": "apply_deadline",
    "start date": "start_date",
    "end date": "end_date",
    "hours per week": "hours_per_week",
    "duration": "duration",
    "job description": "description_raw",
    "description": "description_raw",
    "responsibilities": "responsibilities",
    "qualifications": "qualifications",
    "preferred": "preferred_qualifications",
    "preferred qualifications": "preferred_qualifications",
    "preferred skills": "preferred_skills",
    "skills keywords": "skills_keywords",
    "skills and keywords": "skills_keywords",
    "skills": "skills_keywords",
    "benefits": "benefits",
    "work authorization": "work_authorization",
    "work authorization sponsorship": "work_authorization",
    "application": "apply_url",
    "application link": "apply_url",
    "apply link": "apply_url",
    "job link": "apply_url",
    "recruiter": "recruiter",
    "recruiter email": "recruiter_email",
    "industry": "industry",
    "company size": "company_size",
    "employer description": "employer_description",
}

REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "Job Title"),
    ("company_name", "Company Name"),
    ("location_raw", "Location"),
    ("description_raw", "Job Description"),
    ("apply_url", "Application"),
)


def ensure_schema(connection=None) -> None:
    owns_connection = connection is None
    if connection is None:
        connection = get_connection()

    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS telegram_manual_sessions (
                chat_id INTEGER PRIMARY KEY,
                session_status TEXT NOT NULL DEFAULT 'capturing',
                buffer_text TEXT NOT NULL DEFAULT '',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS telegram_manual_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                job_id INTEGER,
                queue_id INTEGER,
                execution_id INTEGER,
                worker_pid INTEGER,
                run_status TEXT NOT NULL DEFAULT 'created',
                status_message_id INTEGER,
                raw_text TEXT NOT NULL,
                parsed_json TEXT NOT NULL DEFAULT '{}',
                last_progress_text TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_telegram_manual_runs_chat
            ON telegram_manual_runs(chat_id, id DESC);

            CREATE INDEX IF NOT EXISTS idx_telegram_manual_runs_status
            ON telegram_manual_runs(run_status, updated_at);
            """
        )
        connection.commit()
    finally:
        if owns_connection:
            connection.close()


def normalize_heading(value: str) -> str:
    text = value.casefold().replace("&", " and ")
    text = re.sub(r"[/|\\_\-–—]+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_manual_command(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return stripped

    first_line, _, remainder = stripped.partition("\n")
    first_word = first_line.split(maxsplit=1)[0]
    command = first_word.split("@", 1)[0].casefold()

    if command in {"/manual", "/manual_input", "/manualinput"}:
        inline = first_line[len(first_word):].strip()
        return "\n".join(part for part in (inline, remainder.strip()) if part).strip()

    return stripped


SCALAR_MANUAL_FIELDS = {
    "title", "company_name", "location_raw", "remote_type",
    "employment_type", "salary_raw", "date_posted", "apply_deadline",
    "start_date", "end_date", "hours_per_week", "apply_url",
    "industry", "company_size", "employer_description",
}


def _append_value(target: dict[str, str], key: str, value: str) -> None:
    cleaned = value.strip()
    if not cleaned:
        return
    existing = target.get(key, "").strip()
    if key in SCALAR_MANUAL_FIELDS:
        if not existing:
            target[key] = cleaned
        elif existing.casefold() == cleaned.casefold():
            return
        return
    target[key] = existing + "\n" + cleaned if existing else cleaned


def parse_manual_job_text(raw_text: str) -> dict[str, Any]:
    text = strip_manual_command(raw_text)
    parsed: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_lines
        if current_key is not None:
            _append_value(parsed, current_key, "\n".join(current_lines))
        current_key = None
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        match = re.match(r"^\s*([^:\n]{1,100})\s*:\s*(.*)$", line)

        if match:
            mapped = HEADING_ALIASES.get(normalize_heading(match.group(1)))
            if mapped:
                flush()
                current_key = mapped
                inline_value = match.group(2).strip()
                if inline_value:
                    current_lines.append(inline_value)
                continue

        if current_key is not None:
            current_lines.append(line)

    flush()

    for key, value in list(parsed.items()):
        lines = [line.rstrip() for line in value.splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        parsed[key] = "\n".join(lines).strip()

    preferred_parts = [
        parsed.get("preferred_qualifications", "").strip(),
        parsed.get("preferred_skills", "").strip(),
    ]
    combined_preferred = "\n".join(part for part in preferred_parts if part)
    if combined_preferred:
        parsed["preferred_skills"] = combined_preferred

    duration = parsed.pop("duration", "").strip()
    if duration:
        parsed["description_raw"] = (
            (parsed.get("description_raw") or "").rstrip()
            + "\n\nDuration:\n"
            + duration
        ).strip()

    application = parsed.get("apply_url", "").strip()
    if application:
        url_match = re.search(r"https?://\S+", application)
        if url_match:
            application = url_match.group(0).rstrip(".,);]")
        parsed["apply_url"] = application

    location = parsed.get("location_raw", "").strip()
    city = None
    state = None
    location_match = re.search(r"^\s*([^,\n]+),\s*([A-Za-z]{2})(?:\b|,)", location)
    if location_match:
        city = location_match.group(1).strip()
        state = location_match.group(2).upper()

    salary = parsed.get("salary_raw", "").strip()
    hourly_min = None
    hourly_max = None
    if salary:
        numbers = [
            float(value.replace(",", ""))
            for value in re.findall(
                r"\$?\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)",
                salary,
            )
        ]
        lowered = salary.casefold()
        if numbers and any(token in lowered for token in ("/hour", "per hour", "hourly", "/hr")):
            hourly_min = min(numbers)
            hourly_max = max(numbers)

    description_sections = [
        parsed.get("description_raw", "").strip(),
    ]
    for label, key in (
        ("Responsibilities", "responsibilities"),
        ("Qualifications", "qualifications"),
        ("Preferred Qualifications", "preferred_qualifications"),
        ("Skills & Keywords", "skills_keywords"),
        ("Benefits", "benefits"),
        ("Work Authorization", "work_authorization"),
    ):
        value = parsed.get(key, "").strip()
        if value:
            description_sections.append(f"{label}:\n{value}")

    full_description = "\n\n".join(part for part in description_sections if part)

    job: dict[str, Any] = {
        "source": "Telegram Manual Input",
        "source_board": "Telegram Manual",
        "source_tier": 0,
        "ats_job_id": None,
        "company_name": parsed.get("company_name") or "Unknown Company",
        "company": parsed.get("company_name") or "Unknown Company",
        "title": parsed.get("title") or "Unknown Position",
        "job_title": parsed.get("title") or "Unknown Position",
        "location_raw": location or "Not specified",
        "location": location or "Not specified",
        "city": city,
        "state": state,
        "country": "US",
        "remote_type": parsed.get("remote_type") or "Not specified",
        "workplace_type": parsed.get("remote_type") or "Not specified",
        "employment_type": parsed.get("employment_type") or "Not specified",
        "job_type": parsed.get("employment_type") or "Not specified",
        "job_url": application or None,
        "apply_url": application or None,
        "description_raw": full_description or "Not specified",
        "description": full_description or "Not specified",
        "job_description": full_description or "Not specified",
        "salary_raw": salary or None,
        "salary": salary or None,
        "salary_min": hourly_min,
        "salary_max": hourly_max,
        "normalized_hourly_min": hourly_min,
        "normalized_hourly_max": hourly_max,
        "salary_currency": "USD" if hourly_min is not None else None,
        "pay_period": "hour" if hourly_min is not None else None,
        "salary_confidence": "high" if hourly_min is not None else "unknown",
        "date_posted": parsed.get("date_posted"),
        "posted_date": parsed.get("date_posted"),
        "apply_deadline": parsed.get("apply_deadline"),
        "start_date": parsed.get("start_date"),
        "end_date": parsed.get("end_date"),
        "hours_per_week": parsed.get("hours_per_week"),
        "responsibilities": parsed.get("responsibilities"),
        "qualifications": parsed.get("qualifications"),
        "preferred_qualifications": parsed.get("preferred_qualifications"),
        "preferred_skills": parsed.get("preferred_skills"),
        "skills_keywords": parsed.get("skills_keywords"),
        "skills": parsed.get("skills_keywords") or parsed.get("preferred_skills"),
        "work_authorization": parsed.get("work_authorization"),
        "benefits": parsed.get("benefits"),
        "recruiter": parsed.get("recruiter"),
        "recruiter_email": parsed.get("recruiter_email"),
        "company_size": parsed.get("company_size"),
        "industry": parsed.get("industry"),
        "employer_description": parsed.get("employer_description"),
    }

    job = enrich_job_details(job)
    job["manual_job_text"] = build_manual_job_text(job)
    return {"job": job, "fields": parsed, "raw_text": text}


def missing_required_fields(parsed: dict[str, Any]) -> list[str]:
    job = parsed.get("job") if isinstance(parsed, dict) else {}
    if not isinstance(job, dict):
        job = {}

    missing = []
    for key, label in REQUIRED_FIELDS:
        value = str(job.get(key) or "").strip()
        if not value or value.casefold() in {
            "not specified",
            "unknown",
            "unknown company",
            "unknown position",
        }:
            missing.append(label)
    return missing


def persist_manual_job(raw_text: str, *, actor: str = "product_manual_input") -> dict[str, Any]:
    """Persist a confirmed manual posting through the canonical job store.

    This is deliberately synchronous and queues nothing.  Preparation remains a
    separate, explicit action through ``start_stored_job_run``.
    """
    parsed = parse_manual_job_text(raw_text)
    missing = missing_required_fields(parsed)
    if missing:
        return {"success": False, "missing_fields": missing, "parsed": parsed}
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        result = save_job(connection, parsed["job"], actor=actor)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "success": True,
        "job_id": int(result["job_id"]),
        "inserted": bool(result.get("inserted")),
        "duplicate_reason": result.get("duplicate_reason"),
        "parsed": parsed,
    }


def _spawn_worker(run_id: int) -> dict[str, Any]:
    # AADIL_MANUAL_WORKER_VENV_START_V1
    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"telegram_manual_input_run_{run_id}.log"

    python_path = ROOT_DIR / ".venv" / "bin" / "python"
    if not python_path.exists():
        python_path = Path(sys.executable)

    with log_path.open("ab", buffering=0) as log_handle:
        process = subprocess.Popen(
            [
                str(python_path),
                "-u",
                "-m",
                WORKER_MODULE,
                "--run-id",
                str(run_id),
            ],
            cwd=ROOT_DIR,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute(
            """
            UPDATE telegram_manual_runs
            SET worker_pid = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (process.pid, run_id),
        )
        connection.commit()
    finally:
        connection.close()

    # Detect import/startup failures immediately instead of leaving the run
    # permanently stuck at "created".
    time.sleep(1.25)
    return_code = process.poll()
    if return_code is not None:
        try:
            tail = "\n".join(
                log_path.read_text(errors="replace").splitlines()[-30:]
            )
        except Exception:
            tail = ""
        error_text = (
            f"Manual worker exited during startup with code {return_code}."
            + (f"\n\nWorker log:\n{tail}" if tail else "")
        )[:4000]

        connection = get_connection()
        try:
            ensure_schema(connection)
            connection.execute(
                """
                UPDATE telegram_manual_runs
                SET
                    run_status = 'failed_worker_start',
                    error_message = ?,
                    completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error_text, run_id),
            )
            connection.commit()
        finally:
            connection.close()

        raise RuntimeError(error_text)

    return {
        "pid": process.pid,
        "log_path": str(log_path),
        "python_path": str(python_path),
    }


def start_capture(chat_id: int) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO telegram_manual_sessions (
                chat_id, session_status, buffer_text, chunk_count,
                started_at, updated_at
            )
            VALUES (?, 'capturing', '', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                session_status = 'capturing',
                buffer_text = '',
                chunk_count = 0,
                started_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id,),
        )
        connection.commit()
    finally:
        connection.close()

    return {
        "success": True,
        "message": (
            "Manual job capture started.\n\n"
            "Paste the formatted job in one or more messages. "
            "When finished, send /manual_done.\n\n"
            "Use /manual_cancel to discard it."
        ),
    }


def append_capture(chat_id: int, text: str) -> dict[str, Any]:
    content = str(text or "").strip()
    if not content:
        return {"capturing": False}

    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute(
            """
            SELECT *
            FROM telegram_manual_sessions
            WHERE chat_id = ? AND session_status = 'capturing'
            """,
            (chat_id,),
        ).fetchone()

        if row is None:
            return {"capturing": False}

        current = str(row["buffer_text"] or "")
        combined = (current.rstrip() + "\n\n" + content).strip() if current else content

        if len(combined) > MAX_CAPTURE_CHARACTERS:
            return {
                "capturing": True,
                "success": False,
                "message": (
                    "This manual job is too large. The capture limit is "
                    f"{MAX_CAPTURE_CHARACTERS:,} characters."
                ),
            }

        connection.execute(
            """
            UPDATE telegram_manual_sessions
            SET
                buffer_text = ?,
                chunk_count = chunk_count + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ?
            """,
            (combined, chat_id),
        )
        connection.commit()
        updated = connection.execute(
            """
            SELECT chunk_count, length(buffer_text) AS character_count
            FROM telegram_manual_sessions
            WHERE chat_id = ?
            """,
            (chat_id,),
        ).fetchone()
    finally:
        connection.close()

    return {
        "capturing": True,
        "success": True,
        "message": (
            f"Added part {int(updated['chunk_count'])}. "
            f"Captured {int(updated['character_count']):,} characters. "
            "Send /manual_done when finished."
        ),
    }


def _create_run(chat_id: int, raw_text: str) -> dict[str, Any]:
    parsed = parse_manual_job_text(raw_text)
    missing = missing_required_fields(parsed)

    if missing:
        return {
            "success": False,
            "missing_fields": missing,
            "message": (
                "I could not start the n8n run because these required fields "
                "are missing or not recognized:\n• "
                + "\n• ".join(missing)
                + "\n\nAdd them to the capture, then send /manual_done again."
            ),
        }

    connection = get_connection()
    try:
        ensure_schema(connection)
        cursor = connection.execute(
            """
            INSERT INTO telegram_manual_runs (
                chat_id, run_status, raw_text, parsed_json,
                created_at, updated_at
            )
            VALUES (?, 'created', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                chat_id,
                parsed["raw_text"],
                json.dumps(parsed, ensure_ascii=False, default=str),
            ),
        )
        run_id = int(cursor.lastrowid)
        connection.commit()
    finally:
        connection.close()

    try:
        worker = _spawn_worker(run_id)
    except Exception as error:
        return {
            "success": False,
            "run_id": run_id,
            "message": (
                f"Manual run {run_id} was created, but its worker could not start. "
                f"No n8n call was made. Reason: {error}"
            )[:4000],
        }

    return {
        "success": True,
        "run_id": run_id,
        "pid": worker["pid"],
        "log_path": worker["log_path"],
        "message": (
            f"Manual job accepted as run {run_id}. It is being stored and sent "
            "through the full n8n workflow. I will keep one Telegram status "
            "message updated as it progresses."
        ),
    }


def finish_capture(chat_id: int) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute(
            """
            SELECT *
            FROM telegram_manual_sessions
            WHERE chat_id = ? AND session_status = 'capturing'
            """,
            (chat_id,),
        ).fetchone()
        if row is None:
            return {
                "success": False,
                "message": "No manual capture is active. Send /manual_input first.",
            }
        raw_text = str(row["buffer_text"] or "").strip()
        if not raw_text:
            return {"success": False, "message": "Nothing has been pasted yet."}
    finally:
        connection.close()

    result = _create_run(chat_id, raw_text)

    if result.get("success"):
        connection = get_connection()
        try:
            connection.execute(
                """
                UPDATE telegram_manual_sessions
                SET session_status = 'submitted', updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = ?
                """,
                (chat_id,),
            )
            connection.commit()
        finally:
            connection.close()

    return result


def create_inline_run(chat_id: int, raw_text: str) -> dict[str, Any]:
    text = strip_manual_command(raw_text)
    if not text:
        return {
            "success": False,
            "message": (
                "No job text followed /manual. For a long posting, use "
                "/manual_input, paste the job in parts, then /manual_done."
            ),
        }
    return _create_run(chat_id, text)


def cancel_capture(chat_id: int) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute(
            "SELECT session_status FROM telegram_manual_sessions WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if row is None or str(row["session_status"]) != "capturing":
            return {"success": False, "message": "No active manual capture was found."}

        connection.execute(
            """
            UPDATE telegram_manual_sessions
            SET session_status = 'cancelled', buffer_text = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        connection.commit()
    finally:
        connection.close()

    return {"success": True, "message": "Manual job capture cancelled."}


def manual_status_text(chat_id: int) -> str:
    connection = get_connection()
    try:
        ensure_schema(connection)
        run = connection.execute(
            """
            SELECT * FROM telegram_manual_runs
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (chat_id,),
        ).fetchone()
        session = connection.execute(
            "SELECT * FROM telegram_manual_sessions WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()

        if run is None:
            if session is not None and str(session["session_status"]) == "capturing":
                return (
                    "Manual capture is active.\n"
                    f"Parts: {int(session['chunk_count'] or 0)}\n"
                    f"Characters: {len(str(session['buffer_text'] or '')):,}\n"
                    "Send /manual_done when finished."
                )
            return "No Telegram manual-input run has been created yet."

        lines = [
            f"Manual run: {run['id']}",
            f"Status: {run['run_status']}",
        ]
        for label, column in (
            ("Job", "job_id"),
            ("Queue", "queue_id"),
            ("n8n execution", "execution_id"),
        ):
            if run[column] not in (None, ""):
                lines.append(f"{label}: {run[column]}")

        if run["last_progress_text"]:
            lines.extend(["", str(run["last_progress_text"])])
        if run["error_message"]:
            lines.extend(["", "Error: " + str(run["error_message"])])
        return "\n".join(lines)
    finally:
        connection.close()

# AADIL_TG_MANUAL_RUNTIME_REPAIR_V1
from app.tg_n8n_runtime_repair_v1 import (
    guarded_call as _aadil_tg_guarded_call_v1,
)

if "create_inline_run" in globals():
    _aadil_original_create_inline_run_v1 = (
        create_inline_run
    )

    def create_inline_run(*args, **kwargs):
        return _aadil_tg_guarded_call_v1(
            "telegram_manual_create_inline_run",
            _aadil_original_create_inline_run_v1,
            *args,
            **kwargs,
        )

if "finish_capture" in globals():
    _aadil_original_finish_capture_v1 = finish_capture

    def finish_capture(*args, **kwargs):
        return _aadil_tg_guarded_call_v1(
            "telegram_manual_finish_capture",
            _aadil_original_finish_capture_v1,
            *args,
            **kwargs,
        )
