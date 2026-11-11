#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import time
import urllib.request
from typing import Any, Callable

from app.database import DB_PATH, ROOT_DIR
from app.runtime_config import downstream_int, n8n_database_path, service_endpoint

PROJECT = ROOT_DIR
LOG_DIR = PROJECT / "logs"
LOG_PATH = LOG_DIR / "tg_n8n_runtime_repair_v1.log"
LOCK_ROOTS = (
    PROJECT / "data",
    PROJECT / "locks",
    PROJECT / "runtime",
    PROJECT / ".runtime",
)

OPEN_QUEUE_STATES = {
    "pending",
    "dispatching",
    "accepted",
    "processing",
    "running",
}
OPEN_RUN_STATES = {
    "pending",
    "processing",
    "dispatching",
    "accepted",
    "running",
    "started",
}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _log(event: str, payload: dict[str, Any] | None = None) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": _utc_now(),
        "event": event,
        "payload": payload or {},
    }
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def load_project_env() -> dict[str, str]:
    env_path = PROJECT / ".env"
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for raw_line in env_path.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _http_status(url: str) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            return int(response.status)
    except Exception as exc:
        return getattr(exc, "code", None)


def runtime_preflight() -> dict[str, Any]:
    load_project_env()
    n8n_host, n8n_port = service_endpoint("n8n")
    api_host, api_port = service_endpoint("fastapi")
    state = {
        "n8n_port": _port_open(n8n_port, n8n_host),
        "n8n_health": _http_status(f"http://{n8n_host}:{n8n_port}/healthz"),
        "fastapi_port": _port_open(api_port, api_host),
        "fastapi_health": _http_status(f"http://{api_host}:{api_port}/health"),
    }
    state["healthy"] = (
        state["n8n_port"]
        and state["n8n_health"] == 200
        and state["fastapi_port"]
        and state["fastapi_health"] == 200
    )
    _log("runtime_preflight", state)
    return state


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _lock_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(errors="replace").strip()
    except Exception:
        return None

    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            for key in ("pid", "process_id", "worker_pid"):
                if key in value:
                    return int(value[key])
        if isinstance(value, int):
            return value
    except Exception:
        pass

    match = re.search(
        r"\bpid\D{0,5}(\d+)\b",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        return int(match.group(1))
    if raw.isdigit():
        return int(raw)
    return None


def remove_dead_stale_locks() -> list[dict[str, Any]]:
    removed: list[dict[str, Any]] = []
    now = time.time()
    stale_seconds = downstream_int("runtime_repair_stale_seconds", minimum=1)
    candidates: list[Path] = []

    for root in LOCK_ROOTS:
        if not root.exists():
            continue
        try:
            candidates.extend(root.rglob("*.lock"))
        except Exception:
            continue

    unique = {
        str(path.resolve()): path
        for path in candidates
    }
    for path in unique.values():
        try:
            age = now - path.stat().st_mtime
        except Exception:
            continue
        pid = _lock_pid(path)
        dead = pid is None or not _pid_alive(pid)
        if age < stale_seconds or not dead:
            continue
        try:
            path.unlink()
            removed.append({
                "path": str(path),
                "age_seconds": round(age, 2),
                "pid": pid,
            })
        except FileNotFoundError:
            pass

    if removed:
        _log("dead_stale_locks_removed", {"locks": removed})
    return removed


def _qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _columns(
    connection: sqlite3.Connection,
    table: str,
) -> list[str]:
    return [
        row[1]
        for row in connection.execute(
            f"PRAGMA table_info({_qident(table)})"
        )
    ]


def _tables(connection: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    ]


def _parse_time(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _stale_from_row(row: dict[str, Any]) -> bool:
    now = dt.datetime.now(dt.timezone.utc)
    for key in (
        "updated_at",
        "updatedAt",
        "started_at",
        "startedAt",
        "created_at",
        "createdAt",
        "accepted_at",
    ):
        parsed = _parse_time(row.get(key))
        if parsed is not None:
            return (
                now - parsed
            ).total_seconds() >= STALE_SECONDS
    return False


def reconcile_stale_database_state() -> dict[str, Any]:
    result: dict[str, Any] = {
        "database_exists": DB_PATH.exists(),
        "queue_rows_failed": [],
        "manual_rows_failed": [],
    }
    if not DB_PATH.exists():
        _log("database_missing", {"path": str(DB_PATH)})
        return result

    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")

    try:
        tables = _tables(connection)

        if "n8n_dispatch_queue" in tables:
            cols = _columns(
                connection,
                "n8n_dispatch_queue",
            )
            if "queue_status" in cols and "id" in cols:
                placeholders = ",".join(
                    "?" for _ in OPEN_QUEUE_STATES
                )
                rows = connection.execute(
                    "SELECT * FROM n8n_dispatch_queue "
                    f"WHERE lower(queue_status) IN ({placeholders})",
                    tuple(sorted(OPEN_QUEUE_STATES)),
                ).fetchall()

                for source_row in rows:
                    row = dict(source_row)
                    if not _stale_from_row(row):
                        continue
                    assignments = ["queue_status='failed'"]
                    params: list[Any] = []
                    if "last_error" in cols:
                        assignments.append("last_error=?")
                        params.append(
                            "Recovered stale dispatch after "
                            "n8n/Telegram runtime interruption."
                        )
                    elif "error_message" in cols:
                        assignments.append("error_message=?")
                        params.append(
                            "Recovered stale dispatch after "
                            "n8n/Telegram runtime interruption."
                        )
                    if "updated_at" in cols:
                        assignments.append(
                            "updated_at=CURRENT_TIMESTAMP"
                        )
                    params.append(row["id"])
                    connection.execute(
                        "UPDATE n8n_dispatch_queue SET "
                        + ", ".join(assignments)
                        + " WHERE id=?",
                        tuple(params),
                    )
                    result["queue_rows_failed"].append(
                        row["id"]
                    )

        for table in tables:
            if (
                "manual" not in table.lower()
                or table == "n8n_dispatch_queue"
            ):
                continue

            cols = _columns(connection, table)
            id_col = "id" if "id" in cols else None
            status_col = next(
                (
                    name
                    for name in (
                        "run_status",
                        "status",
                        "state",
                    )
                    if name in cols
                ),
                None,
            )
            if not id_col or not status_col:
                continue

            placeholders = ",".join(
                "?" for _ in OPEN_RUN_STATES
            )
            try:
                rows = connection.execute(
                    f"SELECT * FROM {_qident(table)} "
                    f"WHERE lower({_qident(status_col)}) "
                    f"IN ({placeholders})",
                    tuple(sorted(OPEN_RUN_STATES)),
                ).fetchall()
            except sqlite3.Error:
                continue

            for source_row in rows:
                row = dict(source_row)
                if not _stale_from_row(row):
                    continue
                assignments = [
                    f"{_qident(status_col)}='failed'"
                ]
                params = []
                error_col = next(
                    (
                        name
                        for name in (
                            "last_error",
                            "error_message",
                            "error",
                        )
                        if name in cols
                    ),
                    None,
                )
                if error_col:
                    assignments.append(
                        f"{_qident(error_col)}=?"
                    )
                    params.append(
                        "Recovered stale Telegram manual run "
                        "after runtime interruption."
                    )
                if "updated_at" in cols:
                    assignments.append(
                        "updated_at=CURRENT_TIMESTAMP"
                    )
                params.append(row[id_col])
                connection.execute(
                    f"UPDATE {_qident(table)} SET "
                    + ", ".join(assignments)
                    + f" WHERE {_qident(id_col)}=?",
                    tuple(params),
                )
                result["manual_rows_failed"].append({
                    "table": table,
                    "id": row[id_col],
                })

        connection.commit()
    finally:
        connection.close()

    _log("stale_database_reconciliation", result)
    return result


def prepare_runtime() -> dict[str, Any]:
    return {
        "env_loaded": sorted(
            load_project_env().keys()
        ),
        "preflight": runtime_preflight(),
        "locks": remove_dead_stale_locks(),
        "database": reconcile_stale_database_state(),
    }


def _result_failed(value: Any) -> bool:
    if value is None or value is False:
        return True
    if isinstance(value, dict):
        if value.get("success") is False:
            return True
        if value.get("blocked") is True:
            return True
        text = json.dumps(
            value,
            default=str,
        ).lower()
    else:
        text = str(value).lower()

    failure_tokens = (
        "unfinished n8n dispatch queue",
        "already active",
        "already being started",
        "production dispatch was blocked",
        "expected exactly one",
        "failed to start",
        "database is locked",
        "lock exists",
    )
    return any(
        token in text
        for token in failure_tokens
    )


def guarded_call(
    operation: str,
    original: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    preparation = prepare_runtime()
    if not preparation["preflight"].get("healthy"):
        raise RuntimeError(
            "Aadil HR Hunter runtime is not healthy. "
            "n8n and FastAPI must both be online before "
            "Telegram dispatch."
        )

    first_error: Exception | None = None
    first_result: Any = None

    try:
        first_result = original(*args, **kwargs)
        if not _result_failed(first_result):
            _log(
                operation + "_success_first_attempt",
                {"result": first_result},
            )
            return first_result
    except Exception as exc:
        first_error = exc
        _log(
            operation + "_first_attempt_exception",
            {"error": repr(exc)},
        )

    retry_repair = {
        "locks": remove_dead_stale_locks(),
        "database": reconcile_stale_database_state(),
        "preflight": runtime_preflight(),
    }
    if not retry_repair["preflight"].get("healthy"):
        if first_error is not None:
            raise first_error
        return first_result

    try:
        retry_result = original(*args, **kwargs)
        _log(
            operation + "_retry_result",
            {"result": retry_result},
        )
        return retry_result
    except Exception as retry_error:
        _log(
            operation + "_retry_exception",
            {
                "first_error": repr(first_error),
                "retry_error": repr(retry_error),
            },
        )
        raise

# AADIL_FAILED_QUEUE_RETRY_REPAIR_V2
# Terminal failed/cancelled queues without callback results are archived
# before the normal manual/stored-job dispatch function runs.

def _aadil_v2_qident(name):
    return '"' + str(name).replace('"', '""') + '"'


def _aadil_v2_tables(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _aadil_v2_columns(connection, table):
    return [
        row[1]
        for row in connection.execute(
            f"PRAGMA table_info({_aadil_v2_qident(table)})"
        )
    ]


def _aadil_v2_result_count(connection, job_id):
    available = _aadil_v2_tables(connection)
    if "n8n_results" not in available:
        return 0
    if "job_id" not in _aadil_v2_columns(connection, "n8n_results"):
        return 0
    row = connection.execute(
        "SELECT COUNT(*) FROM n8n_results WHERE job_id=?",
        (int(job_id),),
    ).fetchone()
    return int(row[0] or 0)


def _aadil_v2_reset_job_flags(connection, job_id):
    if "jobs" not in _aadil_v2_tables(connection):
        return []
    job_columns = _aadil_v2_columns(connection, "jobs")
    assignments = []
    changed = []

    for name in (
        "sent_to_n8n",
        "n8n_sent",
        "is_sent_to_n8n",
    ):
        if name in job_columns:
            assignments.append(
                f"{_aadil_v2_qident(name)}=0"
            )
            changed.append(name)

    for name in (
        "n8n_send_mode",
        "n8n_request_id",
        "n8n_queue_id",
        "n8n_execution_id",
        "n8n_last_error",
        "last_n8n_error",
    ):
        if name in job_columns:
            assignments.append(
                f"{_aadil_v2_qident(name)}=NULL"
            )
            changed.append(name)

    if "updated_at" in job_columns:
        assignments.append(
            "updated_at=CURRENT_TIMESTAMP"
        )

    if assignments:
        connection.execute(
            "UPDATE jobs SET "
            + ", ".join(assignments)
            + " WHERE id=?",
            (int(job_id),),
        )
    return changed


def archive_retryable_failed_queues_v2():
    result = {
        "database_exists": DB_PATH.exists(),
        "archived": [],
        "preserved_with_results": [],
    }
    if not DB_PATH.exists():
        return result

    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")

    try:
        available = _aadil_v2_tables(connection)
        if "n8n_dispatch_queue" not in available:
            return result

        queue_columns = _aadil_v2_columns(
            connection,
            "n8n_dispatch_queue",
        )
        if not {
            "id",
            "job_id",
            "queue_status",
        }.issubset(queue_columns):
            return result

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
                n8n_failed_queue_retry_archive (
                    archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_queue_id INTEGER NOT NULL UNIQUE,
                    job_id INTEGER,
                    original_status TEXT NOT NULL,
                    dispatch_mode TEXT,
                    request_id TEXT,
                    row_json TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP,
                    archive_reason TEXT NOT NULL
                )
            """
        )

        rows = connection.execute(
            """
            SELECT *
            FROM n8n_dispatch_queue
            WHERE lower(queue_status) IN (
                'failed',
                'cancelled',
                'canceled'
            )
            ORDER BY id ASC
            """
        ).fetchall()

        for source_row in rows:
            row = dict(source_row)
            queue_id = int(row["id"])
            job_id = int(row["job_id"])
            result_count = _aadil_v2_result_count(
                connection,
                job_id,
            )

            if result_count > 0:
                result["preserved_with_results"].append({
                    "queue_id": queue_id,
                    "job_id": job_id,
                    "result_count": result_count,
                })
                continue

            connection.execute(
                """
                INSERT OR IGNORE INTO
                    n8n_failed_queue_retry_archive (
                        original_queue_id,
                        job_id,
                        original_status,
                        dispatch_mode,
                        request_id,
                        row_json,
                        archive_reason
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    queue_id,
                    job_id,
                    str(row["queue_status"]),
                    row.get("dispatch_mode"),
                    row.get("request_id"),
                    json.dumps(
                        row,
                        default=str,
                        ensure_ascii=False,
                    ),
                    (
                        "Terminal failed/cancelled queue "
                        "without n8n_results callback; "
                        "archived to allow one safe retry."
                    ),
                ),
            )

            connection.execute(
                """
                DELETE FROM n8n_dispatch_queue
                WHERE id=?
                  AND lower(queue_status) IN (
                      'failed',
                      'cancelled',
                      'canceled'
                  )
                """,
                (queue_id,),
            )

            changed_flags = _aadil_v2_reset_job_flags(
                connection,
                job_id,
            )

            if "events" in available:
                event_columns = set(
                    _aadil_v2_columns(connection, "events")
                )
                required = {
                    "job_id",
                    "event_type",
                    "actor",
                    "event_status",
                    "payload_json",
                }
                if required.issubset(event_columns):
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
                            "failed_queue_archived_for_retry",
                            "tg_n8n_runtime_repair_v2",
                            "completed",
                            json.dumps({
                                "queue_id": queue_id,
                                "original_status": row[
                                    "queue_status"
                                ],
                            }),
                        ),
                    )

            result["archived"].append({
                "queue_id": queue_id,
                "job_id": job_id,
                "original_status": str(
                    row["queue_status"]
                ),
                "reset_job_flags": changed_flags,
            })

        connection.commit()
        return result
    finally:
        connection.close()


_aadil_original_guarded_call_v2 = guarded_call


def guarded_call(operation, original, *args, **kwargs):
    archive_result = archive_retryable_failed_queues_v2()
    try:
        _log(
            "failed_queue_retry_preflight_v2",
            archive_result,
        )
    except Exception:
        pass
    return _aadil_original_guarded_call_v2(
        operation,
        original,
        *args,
        **kwargs,
    )

# AADIL_TERMINAL_EXECUTION_QUEUE_RECONCILIATION_V1
# Reconciles live accepted/running Hunter queues whose linked n8n
# execution has already ended in a terminal error.

def _aadil_terminal_qident(name):
    return '"' + str(name).replace('"', '""') + '"'


def _aadil_terminal_tables(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _aadil_terminal_columns(connection, table):
    return [
        row[1]
        for row in connection.execute(
            f"PRAGMA table_info({_aadil_terminal_qident(table)})"
        )
    ]


def _aadil_terminal_execution_id_from_queue(
    hunter_connection,
    queue_row,
):
    for name in (
        "n8n_execution_id",
        "execution_id",
        "n8n_execution",
        "executionId",
    ):
        value = queue_row.get(name)
        if value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                pass

    queue_id = int(queue_row["id"])
    job_id = int(queue_row["job_id"])
    available = _aadil_terminal_tables(
        hunter_connection
    )

    execution_columns = (
        "n8n_execution_id",
        "execution_id",
        "n8n_execution",
        "executionId",
    )

    for table in sorted(available):
        if table == "n8n_dispatch_queue":
            continue

        table_columns = _aadil_terminal_columns(
            hunter_connection,
            table,
        )
        execution_column = next(
            (
                name
                for name in execution_columns
                if name in table_columns
            ),
            None,
        )
        if not execution_column:
            continue

        filters = []
        parameters = []

        if "queue_id" in table_columns:
            filters.append(
                f"{_aadil_terminal_qident('queue_id')}=?"
            )
            parameters.append(queue_id)

        if "job_id" in table_columns:
            filters.append(
                f"{_aadil_terminal_qident('job_id')}=?"
            )
            parameters.append(job_id)

        if not filters:
            continue

        order_column = next(
            (
                name
                for name in (
                    "id",
                    "updated_at",
                    "updatedAt",
                    "created_at",
                    "createdAt",
                )
                if name in table_columns
            ),
            execution_column,
        )

        try:
            row = hunter_connection.execute(
                f"SELECT "
                f"{_aadil_terminal_qident(execution_column)} "
                f"FROM {_aadil_terminal_qident(table)} "
                f"WHERE {' OR '.join(filters)} "
                f"AND "
                f"{_aadil_terminal_qident(execution_column)} "
                f"IS NOT NULL "
                f"ORDER BY "
                f"{_aadil_terminal_qident(order_column)} DESC "
                f"LIMIT 1",
                tuple(parameters),
            ).fetchone()
        except sqlite3.Error:
            continue

        if row and row[0] not in (None, ""):
            try:
                return int(row[0])
            except (TypeError, ValueError):
                continue

    return None


def _aadil_terminal_has_callback_result(
    hunter_connection,
    queue_row,
    execution_id,
):
    available = _aadil_terminal_tables(
        hunter_connection
    )
    queue_id = int(queue_row["id"])
    job_id = int(queue_row["job_id"])

    candidate_tables = [
        table
        for table in available
        if any(
            token in table.lower()
            for token in (
                "result",
                "callback",
                "completion",
            )
        )
    ]

    for table in candidate_tables:
        table_columns = _aadil_terminal_columns(
            hunter_connection,
            table,
        )
        clauses = []
        parameters = []

        if "queue_id" in table_columns:
            clauses.append(
                f"{_aadil_terminal_qident('queue_id')}=?"
            )
            parameters.append(queue_id)

        if "job_id" in table_columns:
            clauses.append(
                f"{_aadil_terminal_qident('job_id')}=?"
            )
            parameters.append(job_id)

        execution_column = next(
            (
                name
                for name in (
                    "n8n_execution_id",
                    "execution_id",
                    "n8n_execution",
                    "executionId",
                )
                if name in table_columns
            ),
            None,
        )
        if execution_column:
            clauses.append(
                f"{_aadil_terminal_qident(execution_column)}=?"
            )
            parameters.append(int(execution_id))

        if not clauses:
            continue

        try:
            row = hunter_connection.execute(
                f"SELECT COUNT(*) "
                f"FROM {_aadil_terminal_qident(table)} "
                f"WHERE {' OR '.join(clauses)}",
                tuple(parameters),
            ).fetchone()
        except sqlite3.Error:
            continue

        if row and int(row[0] or 0) > 0:
            return True

    return False


def _aadil_terminal_reset_job_flags(
    hunter_connection,
    job_id,
):
    if "jobs" not in _aadil_terminal_tables(
        hunter_connection
    ):
        return []

    job_columns = _aadil_terminal_columns(
        hunter_connection,
        "jobs",
    )
    assignments = []
    changed = []

    for name in (
        "sent_to_n8n",
        "n8n_sent",
        "is_sent_to_n8n",
    ):
        if name in job_columns:
            assignments.append(
                f"{_aadil_terminal_qident(name)}=0"
            )
            changed.append(name)

    for name in (
        "n8n_send_mode",
        "n8n_request_id",
        "n8n_queue_id",
        "n8n_execution_id",
        "n8n_last_error",
        "last_n8n_error",
    ):
        if name in job_columns:
            assignments.append(
                f"{_aadil_terminal_qident(name)}=NULL"
            )
            changed.append(name)

    if "updated_at" in job_columns:
        assignments.append(
            "updated_at=CURRENT_TIMESTAMP"
        )

    if assignments:
        hunter_connection.execute(
            "UPDATE jobs SET "
            + ", ".join(assignments)
            + " WHERE id=?",
            (int(job_id),),
        )

    return changed


def reconcile_terminal_n8n_queues_v1():
    n8n_database = n8n_database_path()
    result = {
        "hunter_database_exists": DB_PATH.exists(),
        "n8n_database_exists": n8n_database.exists(),
        "archived": [],
        "skipped_without_execution_id": [],
        "preserved_with_callback": [],
    }

    if (
        not result["hunter_database_exists"]
        or not result["n8n_database_exists"]
    ):
        return result

    hunter_connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )
    hunter_connection.row_factory = sqlite3.Row
    hunter_connection.execute(
        "PRAGMA busy_timeout=30000"
    )

    n8n_connection = sqlite3.connect(
        f"file:{n8n_database}?mode=ro",
        uri=True,
        timeout=30,
    )
    n8n_connection.row_factory = sqlite3.Row
    n8n_connection.execute(
        "PRAGMA busy_timeout=30000"
    )
    n8n_connection.execute(
        "PRAGMA query_only=ON"
    )

    try:
        available = _aadil_terminal_tables(
            hunter_connection
        )
        if "n8n_dispatch_queue" not in available:
            return result

        queue_columns = _aadil_terminal_columns(
            hunter_connection,
            "n8n_dispatch_queue",
        )
        if not {
            "id",
            "job_id",
            "queue_status",
        }.issubset(queue_columns):
            return result

        hunter_connection.execute(
            "CREATE TABLE IF NOT EXISTS "
            "n8n_terminal_execution_queue_archive ("
            "archive_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "original_queue_id INTEGER NOT NULL UNIQUE,"
            "job_id INTEGER NOT NULL,"
            "n8n_execution_id INTEGER NOT NULL,"
            "original_status TEXT NOT NULL,"
            "execution_status TEXT NOT NULL,"
            "row_json TEXT NOT NULL,"
            "archived_at TEXT NOT NULL "
            "DEFAULT CURRENT_TIMESTAMP,"
            "archive_reason TEXT NOT NULL"
            ")"
        )

        rows = hunter_connection.execute(
            "SELECT * FROM n8n_dispatch_queue "
            "WHERE lower(queue_status) IN ("
            "'accepted','dispatching','processing',"
            "'running','started','queued'"
            ") ORDER BY id ASC"
        ).fetchall()

        for source_row in rows:
            queue_row = dict(source_row)
            queue_id = int(queue_row["id"])
            job_id = int(queue_row["job_id"])

            execution_id = (
                _aadil_terminal_execution_id_from_queue(
                    hunter_connection,
                    queue_row,
                )
            )

            if execution_id is None:
                result[
                    "skipped_without_execution_id"
                ].append({
                    "queue_id": queue_id,
                    "job_id": job_id,
                })
                continue

            execution = n8n_connection.execute(
                "SELECT * FROM execution_entity "
                "WHERE id=? LIMIT 1",
                (int(execution_id),),
            ).fetchone()

            if not execution:
                continue

            execution_row = dict(execution)
            execution_status = str(
                execution_row.get("status") or ""
            ).lower()

            if execution_status not in {
                "error",
                "failed",
                "crashed",
                "canceled",
                "cancelled",
            }:
                continue

            if _aadil_terminal_has_callback_result(
                hunter_connection,
                queue_row,
                execution_id,
            ):
                result[
                    "preserved_with_callback"
                ].append({
                    "queue_id": queue_id,
                    "job_id": job_id,
                    "execution_id": execution_id,
                    "execution_status": (
                        execution_status
                    ),
                })
                continue

            hunter_connection.execute(
                "INSERT OR IGNORE INTO "
                "n8n_terminal_execution_queue_archive ("
                "original_queue_id,job_id,"
                "n8n_execution_id,original_status,"
                "execution_status,row_json,"
                "archive_reason"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    queue_id,
                    job_id,
                    int(execution_id),
                    str(queue_row["queue_status"]),
                    execution_status,
                    json.dumps(
                        queue_row,
                        default=str,
                        ensure_ascii=False,
                    ),
                    (
                        "Live Hunter queue was linked "
                        "to a terminal n8n error and "
                        "had no callback/result row."
                    ),
                ),
            )

            hunter_connection.execute(
                "DELETE FROM n8n_dispatch_queue "
                "WHERE id=? AND lower(queue_status) IN ("
                "'accepted','dispatching','processing',"
                "'running','started','queued'"
                ")",
                (queue_id,),
            )

            changed_flags = (
                _aadil_terminal_reset_job_flags(
                    hunter_connection,
                    job_id,
                )
            )

            result["archived"].append({
                "queue_id": queue_id,
                "job_id": job_id,
                "execution_id": execution_id,
                "execution_status": execution_status,
                "reset_job_flags": changed_flags,
            })

        hunter_connection.commit()
        return result
    finally:
        n8n_connection.close()
        hunter_connection.close()


_aadil_previous_guarded_call_terminal_v1 = guarded_call


def guarded_call(operation, original, *args, **kwargs):
    terminal_result = (
        reconcile_terminal_n8n_queues_v1()
    )
    try:
        _log(
            "terminal_execution_queue_"
            "reconciliation_v1",
            terminal_result,
        )
    except Exception:
        pass

    return _aadil_previous_guarded_call_terminal_v1(
        operation,
        original,
        *args,
        **kwargs,
    )
