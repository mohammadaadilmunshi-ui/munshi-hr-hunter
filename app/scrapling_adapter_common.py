from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from app.database import DB_PATH, ROOT_DIR, get_connection
from app.job_store import save_job
from app.runtime_config import n8n_database_path, n8n_workflow_id

CONFIG_PATH = ROOT_DIR / "config" / "scrapling_sources.json"
TOOL_PYTHON = ROOT_DIR / "tools" / "venvs" / "scrapling" / "bin" / "python"
TOOL_DIR = ROOT_DIR / "tools" / "scrapling_jobs"
LOCK_DIR = ROOT_DIR / "data" / "scrapling_locks"
LOG_DIR = ROOT_DIR / "logs"
HUNTER_DB = DB_PATH

STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scrapling_source_state (
    source_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    external_enabled INTEGER NOT NULL DEFAULT 0,
    next_run_at TEXT,
    blocked_until TEXT,
    daily_date TEXT,
    requests_today INTEGER NOT NULL DEFAULT 0,
    raw_jobs_today INTEGER NOT NULL DEFAULT 0,
    consecutive_blocks INTEGER NOT NULL DEFAULT 0,
    last_started_at TEXT,
    last_finished_at TEXT,
    last_result_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def now_local() -> datetime:
    return datetime.now().replace(microsecond=0)


def iso(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ") if value else None


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            result = datetime.fromisoformat(candidate)
            if result.tzinfo is not None:
                result = result.astimezone().replace(tzinfo=None)
            return result.replace(microsecond=0)
        except ValueError:
            pass
    return None


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def ensure_state_schema(connection: sqlite3.Connection) -> None:
    connection.execute(STATE_TABLE_SQL)


def next_phase_time(phase_minute: int, *, after: datetime | None = None) -> datetime:
    point = (after or now_local()) + timedelta(seconds=1)
    base = point.replace(hour=0, minute=0, second=0, microsecond=0)
    for day_offset in range(0, 3):
        day = base + timedelta(days=day_offset)
        for hour in range(0, 24, 3):
            candidate = day.replace(hour=hour, minute=phase_minute, second=0)
            if candidate >= point:
                return candidate
    raise RuntimeError("Could not calculate the next three-hour phase.")


def active_work_reason() -> str:
    # Process-level guard.
    try:
        completed = subprocess.run(
            ["ps", "-axo", "command="],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        text = completed.stdout.lower()
        patterns = (
            "app.manual_input_worker",
            "app.stored_job_n8n_worker",
            "app.n8n_dispatch",
            "manual_input_worker.py",
            "stored_job_n8n_worker.py",
        )
        for pattern in patterns:
            if pattern in text:
                return f"active process detected: {pattern}"
    except Exception:
        pass

    # Hunter queue/manual-run guard.
    if HUNTER_DB.exists():
        connection = sqlite3.connect(HUNTER_DB, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

            for table in ("n8n_dispatch_queue", "dispatch_queue", "n8n_queue"):
                if table not in tables:
                    continue
                columns = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if "status" in columns:
                    placeholders = ",".join("?" for _ in range(8))
                    active = connection.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM {table}
                        WHERE lower(COALESCE(status, '')) IN ({placeholders})
                        """,
                        (
                            "pending", "queued", "accepted", "dispatching",
                            "dispatched", "running", "waiting", "processing",
                        ),
                    ).fetchone()[0]
                    if active:
                        return f"{active} open item(s) in {table}"

            if "telegram_manual_runs" in tables:
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(telegram_manual_runs)"
                    )
                }
                if "status" in columns:
                    active = connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM telegram_manual_runs
                        WHERE lower(COALESCE(status, '')) IN (
                            'collecting', 'processing', 'dispatching',
                            'running', 'waiting_callback', 'accepted'
                        )
                        """
                    ).fetchone()[0]
                    if active:
                        return f"{active} active Telegram manual run(s)"
        finally:
            connection.close()

    # Active production n8n execution guard.
    n8n_database = n8n_database_path()
    if n8n_database.exists():
        connection = sqlite3.connect(
            f"file:{n8n_database}?mode=ro", uri=True, timeout=10
        )
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            table = "execution_entity" if "execution_entity" in tables else None
            if table:
                columns = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                status_column = "status" if "status" in columns else None
                workflow_column = (
                    "workflowId" if "workflowId" in columns
                    else "workflow_id" if "workflow_id" in columns
                    else None
                )
                if status_column:
                    sql = (
                        f"SELECT COUNT(*) FROM {table} "
                        f"WHERE lower(COALESCE({status_column}, '')) IN "
                        "('new','running','waiting')"
                    )
                    params: tuple[Any, ...] = ()
                    if workflow_column:
                        sql += f" AND {workflow_column} = ?"
                        params = (n8n_workflow_id(),)
                    active = connection.execute(sql, params).fetchone()[0]
                    if active:
                        return f"{active} active production n8n execution(s)"
        finally:
            connection.close()

    return ""


@contextlib.contextmanager
def source_lock(source_key: str) -> Iterator[bool]:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_DIR / f"{source_key}.lock"
    handle = lock_path.open("a+")
    acquired = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def source_row(connection: sqlite3.Connection, source_key: str) -> sqlite3.Row:
    ensure_state_schema(connection)
    row = connection.execute(
        "SELECT * FROM scrapling_source_state WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Missing Scrapling source state: {source_key}")
    return row


def reset_daily_if_needed(
    connection: sqlite3.Connection,
    source_key: str,
) -> sqlite3.Row:
    row = source_row(connection, source_key)
    today = now_local().date().isoformat()
    if str(row["daily_date"] or "") != today:
        connection.execute(
            """
            UPDATE scrapling_source_state
            SET daily_date = ?,
                requests_today = 0,
                raw_jobs_today = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE source_key = ?
            """,
            (today, source_key),
        )
        connection.commit()
        row = source_row(connection, source_key)
    return row


def update_source_health(
    connection: sqlite3.Connection,
    display_name: str,
    *,
    health_status: str,
    jobs_found: int = 0,
    error: str = "",
    http_status: int | None = None,
    successful: bool = False,
) -> None:
    now_text = iso(now_local())
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(source_health)")
    }
    assignments: dict[str, Any] = {
        "health_status": health_status,
        "jobs_found_last_run": int(jobs_found),
        "last_error": error[:2000],
        "last_http_status": http_status,
        "last_run_at": now_text,
        "updated_at": now_text,
    }
    if successful:
        assignments["last_success_at"] = now_text
        assignments["consecutive_failures"] = 0
    else:
        assignments["last_failure_at"] = now_text

    assignments = {
        key: value for key, value in assignments.items()
        if key in columns
    }
    if not assignments:
        return
    sql = ", ".join(f'"{key}" = ?' for key in assignments)
    values = list(assignments.values()) + [display_name]
    connection.execute(
        f'UPDATE source_health SET {sql} WHERE source_name = ?',
        values,
    )


def _tool_script(source_key: str) -> Path:
    mapping = {
        "google_jobs": TOOL_DIR / "google_jobs_fetch.py",
        "indeed": TOOL_DIR / "indeed_fetch.py",
    }
    try:
        return mapping[source_key]
    except KeyError as exc:
        raise RuntimeError(f"Unknown source key: {source_key}") from exc


def invoke_fetcher(
    source_key: str,
    *,
    max_jobs: int,
    max_pages: int,
    max_detail_fetches: int,
    hours_old: int,
    delay_min: float,
    delay_max: float,
    self_test: bool = False,
) -> dict[str, Any]:
    if not TOOL_PYTHON.exists():
        raise RuntimeError(
            f"Scrapling tool Python was not found: {TOOL_PYTHON}"
        )
    command = [str(TOOL_PYTHON), str(_tool_script(source_key))]
    if self_test:
        command.append("--self-test")
    else:
        config = load_config()
        command.extend([
            "--max-jobs", str(max_jobs),
            "--max-pages", str(max_pages),
            "--max-detail-fetches", str(max_detail_fetches),
            "--hours-old", str(hours_old),
            "--delay-min", str(delay_min),
            "--delay-max", str(delay_max),
            "--search-terms-json", json.dumps(config["search_terms"]),
            "--locations-json", json.dumps(config["locations"]),
        ])
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        timeout=1800,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Fetcher process failed.\n"
            f"Command: {' '.join(command)}\n"
            f"stdout:\n{completed.stdout[-5000:]}\n"
            f"stderr:\n{completed.stderr[-5000:]}"
        )
    try:
        result = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Fetcher did not return valid JSON.\n"
            f"stdout:\n{completed.stdout[-5000:]}\n"
            f"stderr:\n{completed.stderr[-5000:]}"
        ) from exc
    result["fetcher_stderr_tail"] = completed.stderr[-2000:]
    return result


def _to_raw_job(source_key: str, item: dict[str, Any]) -> dict[str, Any]:
    source = (
        "GoogleJobs/Scrapling"
        if source_key == "google_jobs"
        else "Indeed/Scrapling"
    )
    company = str(item.get("company") or "").strip()
    title = str(item.get("title") or "").strip()
    location = str(item.get("location") or "").strip()
    description = str(item.get("description") or "").strip()
    url = str(item.get("job_url") or item.get("url") or "").strip()
    apply_url = str(item.get("apply_url") or url).strip()
    salary = str(item.get("salary") or "").strip()
    date_posted = str(item.get("date_posted") or "").strip()

    return {
        "source": source,
        "source_tier": 2,
        "company_name": company,
        "company": company,
        "title": title,
        "location_raw": location,
        "location": location,
        "job_url": url,
        "url": url,
        "apply_url": apply_url,
        "description_raw": description,
        "description": description,
        "salary_raw": salary,
        "salary": salary,
        "date_posted": date_posted,
        "remote_type": item.get("remote_type") or "",
        "ats_job_id": item.get("ats_job_id") or "",
        "scrapling_metadata": item.get("metadata") or {},
    }


def store_jobs(
    source_key: str,
    actor: str,
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    connection = get_connection()
    inserted = 0
    duplicates = 0
    rejected = 0
    job_ids: list[int] = []
    errors: list[str] = []
    try:
        for item in jobs:
            raw_job = _to_raw_job(source_key, item)
            if not raw_job["company_name"] or not raw_job["title"]:
                rejected += 1
                continue
            try:
                result = save_job(connection, raw_job, actor=actor)
                if isinstance(result, dict):
                    job_id = result.get("job_id") or result.get("id")
                    if job_id is not None:
                        try:
                            job_ids.append(int(job_id))
                        except (TypeError, ValueError):
                            pass
                    duplicate_reason = result.get("duplicate_reason")
                    is_duplicate = bool(duplicate_reason)
                    if result.get("created") is False or result.get("was_new") is False:
                        is_duplicate = True
                    if is_duplicate:
                        duplicates += 1
                    else:
                        inserted += 1
                else:
                    inserted += 1
            except Exception as error:
                errors.append(str(error))
        connection.commit()
    finally:
        connection.close()
    return {
        "inserted_count": inserted,
        "duplicate_count": duplicates,
        "rejected_count": rejected,
        "job_ids": job_ids,
        "storage_errors": errors,
    }


def maybe_emit_summary(payload: dict[str, Any]) -> None:
    try:
        from app.source_run_notifier import emit_source_run_result
        emit_source_run_result(payload)
    except Exception:
        # Notification failure must never turn a completed scrape into a failure.
        pass


def run_source(
    source_key: str,
    *,
    no_store: bool = False,
    force: bool = False,
    smoke: bool = False,
) -> dict[str, Any]:
    config = load_config()
    source_config = config["sources"][source_key]
    common = config["common"]
    display_name = source_config["display_name"]

    if not force:
        reason = active_work_reason()
        if reason:
            return {
                "status": "skipped_active_work",
                "source": display_name,
                "reason": reason,
                "no_store": no_store,
            }

    with source_lock(source_key) as acquired:
        if not acquired:
            return {
                "status": "skipped_locked",
                "source": display_name,
                "reason": "A previous run for this source is still active.",
                "no_store": no_store,
            }

        max_jobs = 10 if smoke else int(source_config["max_raw_jobs"])
        max_pages = 1 if smoke else int(source_config["max_pages"])
        max_details = 0 if smoke else int(source_config["max_detail_fetches"])
        hours_old = int(source_config["hours_old"])

        state_connection = sqlite3.connect(HUNTER_DB, timeout=30)
        state_connection.row_factory = sqlite3.Row
        try:
            ensure_state_schema(state_connection)
            state = reset_daily_if_needed(state_connection, source_key)
            daily_ceiling = int(source_config["daily_request_ceiling"])
            if not smoke and not no_store and int(state["requests_today"]) >= daily_ceiling:
                return {
                    "status": "skipped_daily_ceiling",
                    "source": display_name,
                    "requests_today": int(state["requests_today"]),
                    "daily_request_ceiling": daily_ceiling,
                }

            started = now_local()
            if not no_store:
                state_connection.execute(
                    """
                    UPDATE scrapling_source_state
                    SET last_started_at = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE source_key = ?
                    """,
                    (iso(started), source_key),
                )
                state_connection.commit()

            result = invoke_fetcher(
                source_key,
                max_jobs=max_jobs,
                max_pages=max_pages,
                max_detail_fetches=max_details,
                hours_old=hours_old,
                delay_min=float(common["delay_min_seconds"]),
                delay_max=float(common["delay_max_seconds"]),
            )

            result.setdefault("source", display_name)
            result["no_store"] = no_store
            result["production_cap"] = int(source_config["max_raw_jobs"])
            result["started_at"] = iso(started)
            result["finished_at"] = iso(now_local())

            if no_store:
                return result

            status = str(result.get("status") or "error")
            jobs = result.get("jobs") if isinstance(result.get("jobs"), list) else []
            request_count = int(result.get("request_count") or 0)
            raw_count = int(result.get("raw_count") or len(jobs))
            http_status = result.get("http_status")
            errors = result.get("errors") if isinstance(result.get("errors"), list) else []

            storage = {
                "inserted_count": 0,
                "duplicate_count": 0,
                "rejected_count": 0,
                "job_ids": [],
                "storage_errors": [],
            }
            if status == "success":
                storage = store_jobs(
                    source_key,
                    str(source_config["actor"]),
                    jobs[: int(source_config["max_raw_jobs"])],
                )
                result.update(storage)

            current_state = reset_daily_if_needed(state_connection, source_key)
            consecutive_blocks = int(current_state["consecutive_blocks"] or 0)
            blocked_until = None
            next_run = None

            blocked = status in {
                "blocked", "rate_limited", "captcha", "access_denied"
            } or int(http_status or 0) in {403, 429}

            if blocked:
                consecutive_blocks += 1
                backoffs = list(common["blocked_backoff_minutes"])
                backoff = int(backoffs[min(consecutive_blocks - 1, len(backoffs) - 1)])
                blocked_until = now_local() + timedelta(minutes=backoff)
                next_run = blocked_until
                health = (
                    "blocked_paused"
                    if consecutive_blocks >= int(
                        common["stop_after_consecutive_blocks_in_run"]
                    )
                    else "blocked_backoff"
                )
                error_text = "; ".join(str(value) for value in errors)[:2000]
                update_source_health(
                    state_connection,
                    display_name,
                    health_status=health,
                    jobs_found=0,
                    error=error_text or status,
                    http_status=int(http_status) if http_status else None,
                    successful=False,
                )
            elif status == "success":
                consecutive_blocks = 0
                next_run = next_phase_time(
                    int(source_config["phase_minute"]),
                    after=now_local(),
                )
                update_source_health(
                    state_connection,
                    display_name,
                    health_status="healthy",
                    jobs_found=int(storage["inserted_count"]),
                    error="",
                    http_status=int(http_status) if http_status else None,
                    successful=True,
                )
            else:
                consecutive_blocks = 0
                next_run = next_phase_time(
                    int(source_config["phase_minute"]),
                    after=now_local(),
                )
                error_text = "; ".join(str(value) for value in errors)[:2000]
                update_source_health(
                    state_connection,
                    display_name,
                    health_status="error",
                    jobs_found=0,
                    error=error_text or status,
                    http_status=int(http_status) if http_status else None,
                    successful=False,
                )

            state_connection.execute(
                """
                UPDATE scrapling_source_state
                SET next_run_at = ?,
                    blocked_until = ?,
                    requests_today = requests_today + ?,
                    raw_jobs_today = raw_jobs_today + ?,
                    consecutive_blocks = ?,
                    last_finished_at = ?,
                    last_result_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE source_key = ?
                """,
                (
                    iso(next_run),
                    iso(blocked_until),
                    request_count,
                    raw_count,
                    consecutive_blocks,
                    iso(now_local()),
                    json.dumps(result, default=str)[:100000],
                    source_key,
                ),
            )
            state_connection.commit()

            result["next_run_at"] = iso(next_run)
            result["blocked_until"] = iso(blocked_until)

            maybe_emit_summary({
                "source_name": display_name,
                "status": status,
                "raw_count": raw_count,
                "inserted_count": int(storage["inserted_count"]),
                "duplicate_count": int(storage["duplicate_count"]),
                "errors": errors + list(storage["storage_errors"]),
                "job_ids": storage["job_ids"],
                "network_request_made": request_count > 0,
                "n8n_called": False,
            })

            return result
        finally:
            state_connection.close()


def set_external_enabled(enabled: bool) -> list[dict[str, Any]]:
    config = load_config()
    connection = sqlite3.connect(HUNTER_DB, timeout=30)
    connection.row_factory = sqlite3.Row
    output = []
    try:
        ensure_state_schema(connection)
        for source_key, source_config in config["sources"].items():
            next_run = (
                next_phase_time(int(source_config["phase_minute"]))
                if enabled else None
            )
            connection.execute(
                """
                UPDATE scrapling_source_state
                SET external_enabled = ?,
                    next_run_at = ?,
                    blocked_until = NULL,
                    consecutive_blocks = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE source_key = ?
                """,
                (1 if enabled else 0, iso(next_run), source_key),
            )
            health = "configured_external_scheduler" if enabled else "installed_disabled"
            connection.execute(
                """
                UPDATE source_health
                SET enabled = 0,
                    cadence_minutes = 180,
                    health_status = ?,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE source_name = ?
                """,
                (
                    health,
                    (
                        "Managed by com.aadil.hr-hunter.scrapling-sources; "
                        "use bundle enable/disable scripts."
                    ),
                    source_config["display_name"],
                ),
            )
            output.append({
                "source_key": source_key,
                "display_name": source_config["display_name"],
                "external_enabled": enabled,
                "next_run_at": iso(next_run),
                "max_raw_jobs": source_config["max_raw_jobs"],
            })

        if enabled:
            # Avoid duplicate Google/Indeed collection through the older
            # combined JobSpy source. This can be re-enabled later if that
            # worker is restricted to ZipRecruiter only.
            connection.execute(
                """
                UPDATE source_health
                SET enabled = 0,
                    health_status = CASE
                        WHEN lower(source_name) = 'jobspy'
                        THEN 'disabled_replaced_by_scrapling'
                        ELSE health_status
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE lower(source_name) = 'jobspy'
                """
            )
        connection.commit()
    finally:
        connection.close()
    return output


def external_status() -> list[dict[str, Any]]:
    config = load_config()
    connection = sqlite3.connect(HUNTER_DB, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        ensure_state_schema(connection)
        rows = connection.execute(
            """
            SELECT *
            FROM scrapling_source_state
            ORDER BY source_key
            """
        ).fetchall()
        return [
            {
                "source_key": row["source_key"],
                "display_name": row["display_name"],
                "external_enabled": bool(row["external_enabled"]),
                "next_run_at": row["next_run_at"],
                "blocked_until": row["blocked_until"],
                "requests_today": row["requests_today"],
                "raw_jobs_today": row["raw_jobs_today"],
                "consecutive_blocks": row["consecutive_blocks"],
                "last_started_at": row["last_started_at"],
                "last_finished_at": row["last_finished_at"],
                "max_raw_jobs": config["sources"][row["source_key"]]["max_raw_jobs"],
            }
            for row in rows
        ]
    finally:
        connection.close()
