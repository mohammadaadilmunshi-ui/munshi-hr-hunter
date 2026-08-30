from __future__ import annotations

import html
import json
import os
import sqlite3
import socket
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.database import ROOT_DIR, get_connection
from app.runtime_config import downstream_int

EASTERN = ZoneInfo("America/New_York")
_OBSERVATIONS: list[dict[str, Any]] = []
_OBSERVATIONS_LOCK = threading.Lock()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_nonempty(mapping: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None

    if parsed is None:
        for pattern in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%m/%d/%Y %H:%M:%S",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue

    if parsed is None:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(EASTERN)


def format_job_added_at(job: dict[str, Any]) -> str:
    raw_value = _first_nonempty(
        job,
        [
            "added_at",
            "created_at",
            "first_seen_at",
            "date_found",
            "updated_at",
            "last_scored_at",
        ],
    )

    parsed = _parse_datetime(raw_value)
    if parsed is None:
        return "Not recorded"

    return parsed.strftime("%b %-d, %Y · %-I:%M %p ET")


def format_dashboard_added_at(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "Not recorded"

    return parsed.strftime("%Y-%m-%d %-I:%M %p ET")


def record_job_observation(
    result: dict[str, Any],
    raw_job: dict[str, Any],
    actor: str,
) -> None:
    job_id = result.get("job_id") or result.get("id")
    if not job_id:
        return

    observation = {
        "job_id": int(job_id),
        "inserted": bool(result.get("inserted")),
        "duplicate_reason": result.get("duplicate_reason"),
        "source": str(
            raw_job.get("source")
            or raw_job.get("source_name")
            or actor
            or "Unknown"
        ),
        "actor": actor,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }

    with _OBSERVATIONS_LOCK:
        _OBSERVATIONS.append(observation)


def _consume_observations(source_name: str) -> list[dict[str, Any]]:
    source_lower = source_name.strip().lower()

    with _OBSERVATIONS_LOCK:
        if not _OBSERVATIONS:
            return []

        selected: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []

        for observation in _OBSERVATIONS:
            observed_source = str(observation.get("source") or "").lower()

            if (
                not source_lower
                or source_lower in observed_source
                or observed_source in source_lower
            ):
                selected.append(observation)
            else:
                remaining.append(observation)

        _OBSERVATIONS[:] = remaining

    return selected


def _extract_message_id(value: Any) -> int | None:
    if isinstance(value, dict):
        direct = value.get("message_id")
        if direct not in (None, ""):
            try:
                return int(direct)
            except (TypeError, ValueError):
                pass

        for child in value.values():
            found = _extract_message_id(child)
            if found:
                return found

    if isinstance(value, list):
        for child in value:
            found = _extract_message_id(child)
            if found:
                return found

    return None


def _notification_was_sent(value: Any) -> bool:
    if _extract_message_id(value):
        return True

    if isinstance(value, dict):
        if value.get("sent") is True:
            return True

        nested = value.get("source_run_notification")
        if isinstance(nested, dict) and nested.get("sent") is True:
            return True

    return False


def _count_from_payload(payload: dict[str, Any], names: list[str]) -> int:
    for name in names:
        value = payload.get(name)
        if value in (None, ""):
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _fallback_recent_duplicate_ids(
    source_name: str,
    count: int,
) -> list[int]:
    if count <= 0:
        return []

    connection = get_connection()

    try:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(jobs)"
            ).fetchall()
        }

        order_candidates = [
            name
            for name in (
                "updated_at",
                "added_at",
                "created_at",
                "id",
            )
            if name in columns
        ]
        order_expression = ", ".join(
            f"{name} DESC" for name in order_candidates
        ) or "id DESC"

        rows = connection.execute(
            f"""
            SELECT id
            FROM jobs
            WHERE lower(COALESCE(source, '')) LIKE ?
            ORDER BY {order_expression}
            LIMIT ?
            """,
            (
                f"%{source_name.lower()}%",
                min(count, downstream_int("telegram_max_batch_size", minimum=1)),
            ),
        ).fetchall()

        return [int(row["id"]) for row in rows]

    finally:
        connection.close()


def _ensure_control_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS telegram_source_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            summary_message_id INTEGER,
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS telegram_source_run_jobs (
            run_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            relationship TEXT NOT NULL DEFAULT 'already_stored',
            observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id, job_id),
            FOREIGN KEY(run_id)
                REFERENCES telegram_source_runs(id)
                ON DELETE CASCADE,
            FOREIGN KEY(job_id)
                REFERENCES jobs(id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS
            idx_telegram_source_run_jobs_run
        ON telegram_source_run_jobs(run_id, job_id);
        """
    )


def _keyboard(run_id: int, stored_count: int) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []

    if stored_count > 0:
        rows.append(
            [
                {
                    "text": f"📂 View {stored_count} stored job(s)",
                    "callback_data": f"cc:stored:{run_id}:0",
                }
            ]
        )

    rows.append(
        [
            {
                "text": "⏱ Run ready adapters",
                "callback_data": "cc:run:all",
            }
        ]
    )

    return {"inline_keyboard": rows}


def post_source_run_controls(
    payload: dict[str, Any],
    notifier_result: Any,
) -> dict[str, Any]:
    if not _notification_was_sent(notifier_result):
        return {
            "attached": False,
            "reason": "source_summary_not_sent",
        }

    source_name = str(
        payload.get("source")
        or payload.get("source_name")
        or "Source"
    ).strip()

    duplicate_count = _count_from_payload(
        payload,
        [
            "database_duplicates",
            "already_stored",
            "already_stored_count",
            "duplicate_count",
            "jobs_already_stored",
        ],
    )

    observations = _consume_observations(source_name)
    stored_ids = sorted(
        {
            int(item["job_id"])
            for item in observations
            if not bool(item.get("inserted"))
        }
    )

    if duplicate_count and not stored_ids:
        stored_ids = _fallback_recent_duplicate_ids(
            source_name,
            duplicate_count,
        )

    if stored_ids and duplicate_count <= 0:
        duplicate_count = len(stored_ids)

    summary_message_id = _extract_message_id(notifier_result)

    connection = get_connection()

    try:
        _ensure_control_tables(connection)
        connection.execute("BEGIN IMMEDIATE")

        cursor = connection.execute(
            """
            INSERT INTO telegram_source_runs (
                source_name,
                summary_message_id,
                duplicate_count,
                payload_json
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                source_name,
                summary_message_id,
                duplicate_count,
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )

        run_id = int(cursor.lastrowid)

        for job_id in stored_ids:
            connection.execute(
                """
                INSERT OR IGNORE INTO telegram_source_run_jobs (
                    run_id,
                    job_id,
                    relationship,
                    observed_at
                )
                VALUES (?, ?, 'already_stored', CURRENT_TIMESTAMP)
                """,
                (run_id, job_id),
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    from app.telegram_client import CHAT_ID, telegram_request

    reply_markup = json.dumps(
        _keyboard(run_id, len(stored_ids)),
        ensure_ascii=False,
    )

    if summary_message_id and CHAT_ID:
        try:
            telegram_request(
                "editMessageReplyMarkup",
                {
                    "chat_id": str(CHAT_ID),
                    "message_id": str(summary_message_id),
                    "reply_markup": reply_markup,
                },
            )

            return {
                "attached": True,
                "mode": "summary_message_buttons",
                "run_id": run_id,
                "stored_job_ids": stored_ids,
                "message_id": summary_message_id,
            }

        except Exception as error:
            attachment_error = str(error)
    else:
        attachment_error = "summary message ID unavailable"

    response = telegram_request(
        "sendMessage",
        {
            "chat_id": str(CHAT_ID),
            "text": (
                f"🎛 <b>{html.escape(source_name)} controls</b>\n"
                f"🕒 {html.escape(datetime.now(EASTERN).strftime('%b %-d, %Y · %-I:%M %p ET'))}"
            ),
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
        },
    )

    return {
        "attached": True,
        "mode": "separate_control_message",
        "run_id": run_id,
        "stored_job_ids": stored_ids,
        "message_id": int(response["result"]["message_id"]),
        "summary_attachment_error": attachment_error,
    }


def _load_run_jobs(
    run_id: int,
    offset: int,
    page_size: int | None = None,
) -> tuple[list[int], int]:
    resolved_page_size = (
        max(1, int(page_size))
        if page_size is not None
        else downstream_int("telegram_application_run_page_size", minimum=1)
    )
    connection = get_connection()

    try:
        _ensure_control_tables(connection)

        total = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM telegram_source_run_jobs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()[0]
        )

        rows = connection.execute(
            """
            SELECT job_id
            FROM telegram_source_run_jobs
            WHERE run_id = ?
            ORDER BY job_id
            LIMIT ? OFFSET ?
            """,
            (run_id, resolved_page_size, max(0, offset)),
        ).fetchall()

        return [int(row["job_id"]) for row in rows], total

    finally:
        connection.close()


def _send_fresh_job_card(
    job_id: int,
    chat_id: int,
) -> int:
    """
    Send a brand-new visible copy of a stored job card.

    This bypasses send_job_card(), because send_job_card() may edit or
    reuse the job's original Telegram message. This preview path does
    not update telegram_sent, telegram_message_id, status, or n8n state.
    """
    from app.telegram_client import (
        build_keyboard,
        format_job_card,
        get_job,
        telegram_request,
    )

    job = get_job(int(job_id))

    if not job:
        raise RuntimeError(
            f"Stored job {int(job_id)} was not found."
        )

    response = telegram_request(
        "sendMessage",
        {
            "chat_id": str(int(chat_id)),
            "text": format_job_card(job),
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
            "reply_markup": json.dumps(
                build_keyboard(job),
                ensure_ascii=False,
            ),
        },
    )

    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError(
            "Telegram did not accept the fresh stored-job card: "
            + json.dumps(response, ensure_ascii=False, default=str)
        )

    result = response.get("result") or {}
    message_id = result.get("message_id")

    if message_id in (None, ""):
        raise RuntimeError(
            "Telegram accepted the request but returned no message_id."
        )

    return int(message_id)

def send_stored_job_cards(
    run_id: int,
    offset: int = 0,
    chat_id: int | None = None,
) -> dict[str, Any]:
    job_ids, total = _load_run_jobs(run_id, offset)

    if not job_ids:
        return {
            "success": False,
            "message": "No stored jobs are linked to this source run.",
            "sent": [],
            "failures": [],
        }

    from app.telegram_client import CHAT_ID, telegram_request

    target_chat_id = int(chat_id or CHAT_ID or 0)

    if target_chat_id <= 0:
        return {
            "success": False,
            "message": "Telegram chat ID is not configured.",
            "sent": [],
            "failures": [],
        }

    telegram_request(
        "sendMessage",
        {
            "chat_id": str(target_chat_id),
            "text": (
                "📂 <b>Already-stored jobs</b>\n"
                f"Showing {offset + 1}-{offset + len(job_ids)} of {total}\n"
                f"🕒 {html.escape(datetime.now(EASTERN).strftime('%b %-d, %Y · %-I:%M %p ET'))}"
            ),
            "parse_mode": "HTML",
        },
    )

    sent: list[dict[str, int]] = []
    failures: list[dict[str, Any]] = []

    for job_id in job_ids:
        try:
            message_id = _send_fresh_job_card(
                int(job_id),
                target_chat_id,
            )
            sent.append(
                {
                    "job_id": int(job_id),
                    "message_id": int(message_id),
                }
            )
        except Exception as error:
            failures.append(
                {
                    "job_id": int(job_id),
                    "error": str(error),
                }
            )

    next_offset = offset + len(job_ids)

    if next_offset < total:
        telegram_request(
            "sendMessage",
            {
                "chat_id": str(target_chat_id),
                "text": "More stored jobs are available.",
                "reply_markup": json.dumps(
                    {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "➡️ Show next stored jobs",
                                    "callback_data": (
                                        f"cc:stored:{run_id}:{next_offset}"
                                    ),
                                }
                            ]
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
        )

    if failures:
        telegram_request(
            "sendMessage",
            {
                "chat_id": str(target_chat_id),
                "text": (
                    "⚠️ Some stored cards could not be opened.\n"
                    + "\n".join(
                        f"Job {item['job_id']}: {item['error'][:300]}"
                        for item in failures
                    )
                ),
            },
        )

    return {
        "success": bool(sent),
        "message": (
            f"Opened {len(sent)} stored job card(s)."
            + (
                f" {len(failures)} failed."
                if failures
                else ""
            )
        ),
        "sent": sent,
        "failures": failures,
        "total": total,
        "next_offset": (
            next_offset if next_offset < total else None
        ),
    }


def _runner_lock_path() -> Path:
    return ROOT_DIR / "data" / "enabled_sources_runner.lock"


def start_enabled_sources(chat_id: int) -> dict[str, Any]:
    lock_path = _runner_lock_path()

    if lock_path.exists():
        try:
            lock_data = json.loads(lock_path.read_text())
            pid = int(lock_data.get("pid") or 0)
            if pid > 0:
                os.kill(pid, 0)
                return {
                    "success": False,
                    "message": (
                        "An enabled-adapter run is already in progress."
                    ),
                }
        except (OSError, ValueError, json.JSONDecodeError):
            lock_path.unlink(missing_ok=True)

    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (
        "telegram_enabled_sources_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".log"
    )

    log_handle = log_path.open("ab")

    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "app.telegram_source_runner",
            "--run-now",
            "--chat-id",
            str(chat_id),
        ],
        cwd=ROOT_DIR,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    return {
        "success": True,
        "message": (
            "Enabled adapters started in the background. "
            "Telegram will receive a completion report."
        ),
        "pid": process.pid,
        "log_path": str(log_path),
    }


def handle_control_callback(
    callback_data: str,
    chat_id: int,
) -> tuple[bool, str, bool]:
    parts = callback_data.split(":")

    if parts[:2] == ["cc", "run"] and parts[2:] == ["all"]:
        result = start_enabled_sources(chat_id)
        return (
            bool(result.get("success")),
            str(result.get("message") or "Run request processed."),
            not bool(result.get("success")),
        )

    if len(parts) in (3, 4) and parts[:2] == ["cc", "stored"]:
        try:
            run_id = int(parts[2])
            offset = int(parts[3]) if len(parts) == 4 else 0
        except ValueError:
            return False, "Invalid stored-job request.", True

        result = send_stored_job_cards(run_id, offset)
        return (
            bool(result.get("success")),
            str(result.get("message") or "Stored jobs processed."),
            not bool(result.get("success")),
        )

    if len(parts) == 3 and parts[:2] == ["cc", "recent"]:
        try:
            limit = int(parts[2])
        except ValueError:
            limit = 5

        result = send_recent_job_cards(
            chat_id,
            limit,
        )
        return (
            bool(result.get("success")),
            str(result.get("message") or "Recent jobs processed."),
            not bool(result.get("success")),
        )

    if parts == ["cc", "sources"]:
        from app.telegram_client import telegram_request

        telegram_request(
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": source_status_text(),
                "parse_mode": "HTML",
            },
        )
        return True, "Source status sent.", False

    if parts == ["cc", "status"]:
        from app.telegram_client import telegram_request

        telegram_request(
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": system_status_text(),
                "parse_mode": "HTML",
            },
        )
        return True, "System status sent.", False

    if parts == ["cc", "queue"]:
        from app.telegram_client import telegram_request

        telegram_request(
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": queue_status_text(),
                "parse_mode": "HTML",
            },
        )
        return True, "Queue status sent.", False

    return False, "Unsupported control action.", True


def install_streamlit_timestamp_wrapper(st_module: Any) -> None:
    if getattr(st_module, "_aadil_timestamp_wrapper_installed", False):
        return

    original_dataframe = st_module.dataframe
    original_data_editor = getattr(st_module, "data_editor", None)

    def enhance(data: Any) -> Any:
        try:
            import pandas as pd

            if not isinstance(data, pd.DataFrame):
                return data

            frame = data.copy()
            columns_by_lower = {
                str(column).strip().lower(): column
                for column in frame.columns
            }

            id_column = columns_by_lower.get("id")
            company_column = (
                columns_by_lower.get("company")
                or columns_by_lower.get("company_name")
            )
            title_column = columns_by_lower.get("title")

            if not (id_column and company_column and title_column):
                return data

            timestamp_column = None
            for candidate in (
                "added at",
                "added_at",
                "created_at",
                "first_seen_at",
                "date_found",
            ):
                if candidate in columns_by_lower:
                    timestamp_column = columns_by_lower[candidate]
                    break

            if timestamp_column is None:
                ids = [
                    int(value)
                    for value in frame[id_column].tolist()
                    if str(value).strip().isdigit()
                ]

                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    connection = get_connection()
                    try:
                        job_columns = {
                            str(row[1])
                            for row in connection.execute(
                                "PRAGMA table_info(jobs)"
                            ).fetchall()
                        }
                        source_column = next(
                            (
                                name
                                for name in (
                                    "added_at",
                                    "created_at",
                                    "first_seen_at",
                                    "updated_at",
                                )
                                if name in job_columns
                            ),
                            None,
                        )

                        if source_column:
                            rows = connection.execute(
                                f"""
                                SELECT id, {source_column} AS timestamp_value
                                FROM jobs
                                WHERE id IN ({placeholders})
                                """,
                                ids,
                            ).fetchall()

                            mapping = {
                                int(row["id"]): row["timestamp_value"]
                                for row in rows
                            }

                            insert_at = list(frame.columns).index(id_column) + 1
                            frame.insert(
                                insert_at,
                                "Added at",
                                [
                                    format_dashboard_added_at(
                                        mapping.get(int(value))
                                    )
                                    if str(value).strip().isdigit()
                                    else "Not recorded"
                                    for value in frame[id_column]
                                ],
                            )
                    finally:
                        connection.close()

            else:
                formatted = frame[timestamp_column].map(
                    format_dashboard_added_at
                )

                if str(timestamp_column) != "Added at":
                    frame = frame.drop(columns=[timestamp_column])
                    insert_at = list(frame.columns).index(id_column) + 1
                    frame.insert(insert_at, "Added at", formatted)
                else:
                    frame[timestamp_column] = formatted

            return frame

        except Exception:
            return data

    def dataframe_wrapper(data: Any = None, *args: Any, **kwargs: Any) -> Any:
        return original_dataframe(enhance(data), *args, **kwargs)

    st_module.dataframe = dataframe_wrapper

    if callable(original_data_editor):
        def data_editor_wrapper(
            data: Any = None,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            return original_data_editor(enhance(data), *args, **kwargs)

        st_module.data_editor = data_editor_wrapper

    st_module._aadil_timestamp_wrapper_installed = True

# AADIL_TELEGRAM_COMMAND_CONTROL_V3
def _latest_stored_run() -> tuple[int | None, int]:
    connection = get_connection()

    try:
        _ensure_control_tables(connection)

        row = connection.execute(
            """
            SELECT
                r.id,
                COUNT(j.job_id) AS stored_count
            FROM telegram_source_runs AS r
            JOIN telegram_source_run_jobs AS j
              ON j.run_id = r.id
            GROUP BY r.id
            HAVING COUNT(j.job_id) > 0
            ORDER BY r.id DESC
            LIMIT 1
            """
        ).fetchone()

        if row is None:
            return None, 0

        return int(row["id"]), int(row["stored_count"] or 0)

    finally:
        connection.close()



def _table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def _job_columns(
    connection: sqlite3.Connection,
) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(jobs)"
        ).fetchall()
    }


def _job_order_expression(
    columns: set[str],
) -> str:
    preferred = [
        name
        for name in (
            "added_at",
            "created_at",
            "first_seen_at",
            "date_found",
            "updated_at",
            "id",
        )
        if name in columns
    ]

    if not preferred:
        return "id DESC"

    return ", ".join(
        f"{name} DESC"
        for name in preferred
    )


def _send_job_ids(
    job_ids: list[int],
    chat_id: int,
    heading: str,
) -> dict[str, Any]:
    from app.telegram_client import telegram_request

    if not job_ids:
        return {
            "success": False,
            "message": "No matching stored jobs were found.",
            "sent": [],
            "failures": [],
        }

    telegram_request(
        "sendMessage",
        {
            "chat_id": str(int(chat_id)),
            "text": (
                f"{heading}\n"
                f"Jobs: <b>{len(job_ids)}</b>\n"
                f"🕒 {html.escape(datetime.now(EASTERN).strftime('%b %-d, %Y · %-I:%M %p ET'))}"
            ),
            "parse_mode": "HTML",
        },
    )

    sent: list[dict[str, int]] = []
    failures: list[dict[str, Any]] = []

    for job_id in job_ids:
        try:
            message_id = _send_fresh_job_card(
                int(job_id),
                int(chat_id),
            )
            sent.append(
                {
                    "job_id": int(job_id),
                    "message_id": int(message_id),
                }
            )
        except Exception as error:
            failures.append(
                {
                    "job_id": int(job_id),
                    "error": str(error),
                }
            )

    if failures:
        telegram_request(
            "sendMessage",
            {
                "chat_id": str(int(chat_id)),
                "text": (
                    "⚠️ Some requested job cards could not be opened.\n"
                    + "\n".join(
                        f"Job {item['job_id']}: {item['error'][:300]}"
                        for item in failures
                    )
                ),
            },
        )

    return {
        "success": bool(sent),
        "message": (
            f"Opened {len(sent)} fresh job card(s)."
            + (
                f" {len(failures)} failed."
                if failures
                else ""
            )
        ),
        "sent": sent,
        "failures": failures,
    }


def send_recent_job_cards(
    chat_id: int,
    limit: int = 5,
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 5), 10))
    connection = get_connection()

    try:
        columns = _job_columns(connection)
        order_expression = _job_order_expression(columns)

        rows = connection.execute(
            f"""
            SELECT id
            FROM jobs
            ORDER BY {order_expression}
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        job_ids = [int(row["id"]) for row in rows]
    finally:
        connection.close()

    return _send_job_ids(
        job_ids,
        chat_id,
        "🆕 <b>Most recently stored jobs</b>",
    )


def send_job_by_id(
    chat_id: int,
    job_id: int,
) -> dict[str, Any]:
    connection = get_connection()

    try:
        row = connection.execute(
            "SELECT id FROM jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return {
            "success": False,
            "message": f"Job ID {job_id} was not found.",
        }

    return _send_job_ids(
        [int(job_id)],
        chat_id,
        f"🔎 <b>Stored job #{int(job_id)}</b>",
    )


def find_stored_job_cards(
    chat_id: int,
    query: str,
    limit: int = 5,
) -> dict[str, Any]:
    search_text = str(query or "").strip()

    if not search_text:
        return {
            "success": False,
            "message": (
                "Add a company or title after /find. "
                "Example: /find Tenex"
            ),
        }

    limit = max(1, min(int(limit or 5), 10))
    connection = get_connection()

    try:
        columns = _job_columns(connection)

        company_column = next(
            (
                name
                for name in ("company_name", "company")
                if name in columns
            ),
            None,
        )
        title_column = next(
            (
                name
                for name in ("title", "job_title")
                if name in columns
            ),
            None,
        )

        if company_column is None and title_column is None:
            return {
                "success": False,
                "message": (
                    "The jobs table has no searchable company/title columns."
                ),
            }

        search_parts: list[str] = []
        parameters: list[Any] = []

        for column in (company_column, title_column):
            if column:
                search_parts.append(
                    f"lower(COALESCE({column}, '')) LIKE ?"
                )
                parameters.append(
                    f"%{search_text.lower()}%"
                )

        order_expression = _job_order_expression(columns)
        parameters.append(limit)

        rows = connection.execute(
            f"""
            SELECT id
            FROM jobs
            WHERE {" OR ".join(search_parts)}
            ORDER BY {order_expression}
            LIMIT ?
            """,
            parameters,
        ).fetchall()

        job_ids = [int(row["id"]) for row in rows]

    finally:
        connection.close()

    return _send_job_ids(
        job_ids,
        chat_id,
        (
            "🔍 <b>Stored-job search</b>\n"
            f"Query: <code>{html.escape(search_text)}</code>"
        ),
    )


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(
            ("127.0.0.1", int(port)),
            timeout=0.6,
        ):
            return True
    except OSError:
        return False


def _source_runner_state() -> str:
    lock_path = _runner_lock_path()

    if not lock_path.exists():
        return "idle"

    try:
        data = json.loads(lock_path.read_text())
        pid = int(data.get("pid") or 0)

        if pid > 0:
            os.kill(pid, 0)
            return f"running (PID {pid})"
    except (OSError, ValueError, json.JSONDecodeError):
        lock_path.unlink(missing_ok=True)

    return "idle"


def queue_status_text() -> str:
    connection = get_connection()

    try:
        if not _table_exists(
            connection,
            "n8n_dispatch_queue",
        ):
            return (
                "📨 <b>n8n queue</b>\n\n"
                "Queue table is not installed."
            )

        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(n8n_dispatch_queue)"
            ).fetchall()
        }

        if "queue_status" not in columns:
            return (
                "📨 <b>n8n queue</b>\n\n"
                "The queue_status column is missing."
            )

        counts = connection.execute(
            """
            SELECT
                lower(COALESCE(queue_status, 'unknown')) AS status,
                COUNT(*) AS total
            FROM n8n_dispatch_queue
            GROUP BY lower(COALESCE(queue_status, 'unknown'))
            ORDER BY total DESC, status
            """
        ).fetchall()

        open_rows = connection.execute(
            """
            SELECT *
            FROM n8n_dispatch_queue
            WHERE lower(COALESCE(queue_status, '')) IN (
                'pending',
                'dispatching',
                'accepted'
            )
            ORDER BY id DESC
            LIMIT 5
            """
        ).fetchall()

    finally:
        connection.close()

    lines = [
        "📨 <b>n8n dispatch queue</b>",
        "",
    ]

    if counts:
        for row in counts:
            lines.append(
                f"• {html.escape(str(row['status']))}: "
                f"<b>{int(row['total'] or 0)}</b>"
            )
    else:
        lines.append("No queue records.")

    lines.extend(
        [
            "",
            f"Open items: <b>{len(open_rows)}</b>",
        ]
    )

    for row in open_rows:
        item = dict(row)
        details = [
            f"#{item.get('id')}",
            f"job {item.get('job_id')}",
            str(item.get("queue_status") or "unknown"),
        ]
        lines.append(
            "• " + html.escape(" · ".join(details))
        )

    return "\n".join(lines)


def system_status_text() -> str:
    import html as _html
    import socket as _socket
    from datetime import datetime as _datetime
    from datetime import timezone as _timezone
    from zoneinfo import ZoneInfo as _ZoneInfo

    from app.database import get_connection as _get_connection

    _eastern = _ZoneInfo("America/New_York")

    def _port_open(port: int) -> bool:
        try:
            with _socket.create_connection(
                ("127.0.0.1", port),
                timeout=0.8,
            ):
                return True
        except OSError:
            return False

    def _format_time(value):
        if value in (None, ""):
            return None

        text = str(value).strip().replace("Z", "+00:00")
        parsed = None

        try:
            parsed = _datetime.fromisoformat(text)
        except ValueError:
            for pattern in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    parsed = _datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue

        if parsed is None:
            return str(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_timezone.utc)

        return parsed.astimezone(_eastern).strftime(
            "%b %-d, %Y · %-I:%M %p ET"
        )

    def _category(row):
        status = str(
            row.get("health_status") or "not_tested"
        ).strip().lower()
        failures = int(row.get("consecutive_failures") or 0)
        error = str(row.get("last_error") or "").strip()
        combined = f"{status} {error}".lower()

        setup_states = {
            "configuration_required",
            "needs_credentials",
            "source_not_configured",
            "board_configuration_required",
        }
        pending_states = {
            "enabled_pending_first_run",
            "pending_first_run",
            "not_tested",
            "not_tested_direct",
            "scheduled_not_live_tested",
            "installed_pending_first_run",
            "ready",
        }
        failure_terms = (
            "failed",
            "unhealthy",
            "degraded",
            "blocked",
            "rate_limited",
            "rate limited",
            "worker_missing",
            "worker missing",
            "exception",
            "traceback",
            "timeout",
            "timed out",
            "captcha",
        )

        if status in setup_states:
            return "setup"

        if status in pending_states:
            return "pending"

        if failures > 0 or any(
            term in combined
            for term in failure_terms
        ):
            return "error"

        if (
            status.startswith("healthy")
            or status.startswith("completed")
            or status.startswith("success")
            or status
            in {
                "cooldown",
                "zero_yield",
                "zero_eligible",
                "duplicate_only",
                "enabled",
                "idle",
            }
        ):
            return "healthy"

        return "notice"

    connection = _get_connection()

    try:
        source_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(source_health)"
            ).fetchall()
        }
        desired = [
            "source_name",
            "enabled",
            "health_status",
            "last_run_at",
            "last_success_at",
            "last_error",
            "consecutive_failures",
            "last_http_status",
            "raw_jobs_last_run",
            "eligible_jobs_last_run",
            "inserted_jobs_last_run",
            "provider_used_last_run",
        ]
        selected = [
            name for name in desired
            if name in source_columns
        ]
        order_expression = (
            "source_tier, source_name"
            if "source_tier" in source_columns
            else "source_name"
        )

        source_rows = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT {", ".join(selected)}
                FROM source_health
                ORDER BY {order_expression}
                """
            ).fetchall()
        ]

        enabled_rows = [
            row
            for row in source_rows
            if int(row.get("enabled") or 0) == 1
        ]

        categories = {
            "healthy": [],
            "pending": [],
            "setup": [],
            "error": [],
            "notice": [],
        }

        for row in enabled_rows:
            categories[_category(row)].append(row)

        job_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(jobs)"
            ).fetchall()
        }
        timestamp_column = next(
            (
                name
                for name in (
                    "added_at",
                    "created_at",
                    "first_seen_at",
                    "date_found",
                )
                if name in job_columns
            ),
            None,
        )

        total_jobs = int(
            connection.execute(
                "SELECT COUNT(*) FROM jobs"
            ).fetchone()[0]
        )
        jobs_today = 0
        last_job_time = None

        if timestamp_column:
            jobs_today = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM jobs
                    WHERE date(
                        {timestamp_column},
                        'localtime'
                    ) = date('now', 'localtime')
                    """
                ).fetchone()[0]
            )

            last_row = connection.execute(
                f"""
                SELECT
                    {timestamp_column} AS timestamp_value
                FROM jobs
                WHERE trim(
                    COALESCE({timestamp_column}, '')
                ) != ''
                ORDER BY {timestamp_column} DESC
                LIMIT 1
                """
            ).fetchone()

            if last_row:
                last_job_time = last_row["timestamp_value"]

        tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            ).fetchall()
        }
        open_queue = 0

        if "n8n_dispatch_queue" in tables:
            queue_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(n8n_dispatch_queue)"
                ).fetchall()
            }

            if "queue_status" in queue_columns:
                open_queue = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM n8n_dispatch_queue
                        WHERE lower(
                            COALESCE(queue_status, '')
                        ) IN (
                            'pending',
                            'dispatching',
                            'accepted'
                        )
                        """
                    ).fetchone()[0]
                )

    finally:
        connection.close()

    runner_function = globals().get("_source_runner_state")

    try:
        runner_state = (
            str(runner_function())
            if callable(runner_function)
            else "unknown"
        )
    except Exception:
        runner_state = "unknown"

    lines = [
        "📊 <b>Aadil HR Hunter status</b>",
        "",
        (
            "✅" if _port_open(8000) else "❌"
        )
        + " FastAPI: "
        + (
            "online" if _port_open(8000) else "offline"
        ),
        (
            "✅" if _port_open(5678) else "❌"
        )
        + " n8n: "
        + (
            "online" if _port_open(5678) else "offline"
        ),
        f"🤖 Source runner: {runner_state}",
        (
            "🧭 Enabled adapters: "
            f"<b>{len(enabled_rows)}/{len(source_rows)}</b>"
        ),
        f"📦 Stored jobs: <b>{total_jobs}</b>",
        f"🆕 Added today: <b>{jobs_today}</b>",
        f"📨 Open n8n queue: <b>{open_queue}</b>",
    ]

    if last_job_time:
        lines.append(
            "🕒 Latest job: "
            + _html.escape(
                _format_time(last_job_time) or ""
            )
        )

    if categories["error"]:
        lines.extend(
            [
                "",
                "❌ <b>Runtime failures</b>",
            ]
        )

        for row in categories["error"][:8]:
            status = _html.escape(
                str(
                    row.get("health_status")
                    or "unknown"
                )
            )
            failures = int(
                row.get("consecutive_failures") or 0
            )
            error = str(
                row.get("last_error") or ""
            ).strip()
            detail = (
                _html.escape(
                    str(row.get("source_name") or "")
                )
                + " · "
                + status
            )

            if failures:
                detail += f" · failures {failures}"

            if error:
                detail += (
                    " · "
                    + _html.escape(error[:180])
                )

            lines.append("• " + detail)

    else:
        lines.extend(
            [
                "",
                "✅ Adapter runtime failures: <b>none</b>",
            ]
        )

    if categories["pending"]:
        lines.extend(
            [
                "",
                "⏳ <b>Awaiting first live run</b>",
                _html.escape(
                    ", ".join(
                        str(row.get("source_name"))
                        for row in categories["pending"]
                    )
                ),
            ]
        )

    if categories["setup"]:
        lines.extend(
            [
                "",
                "⚙️ <b>Setup required</b>",
                _html.escape(
                    ", ".join(
                        str(row.get("source_name"))
                        for row in categories["setup"]
                    )
                ),
            ]
        )

    if categories["notice"]:
        lines.extend(
            [
                "",
                "ℹ️ <b>Other source states</b>",
                _html.escape(
                    ", ".join(
                        (
                            f"{row.get('source_name')} "
                            f"({row.get('health_status') or 'unknown'})"
                        )
                        for row in categories["notice"]
                    )
                ),
            ]
        )

    lines.extend(
        [
            "",
            (
                "Use /sources for per-adapter metrics "
                "and exact errors."
            ),
        ]
    )

    return "\n".join(lines)



def command_help_text() -> str:
    return (
        "🎛 <b>Aadil HR Hunter commands</b>\n\n"
        "/run - run enabled adapters that are ready\n"
        "/stored - reopen stored jobs from the latest source run\n"
        "/recent [1-10] - show newest stored jobs\n"
        "/job &lt;id&gt; - open one stored job card\n"
        "/find &lt;company or title&gt; - search stored jobs\n"
        "/sources - show enabled and disabled adapters\n"
        "/status - show system and scraper health\n"
        "/queue - show n8n dispatch queue\n"
        "/health_job_boards [provider] - show employer-board health\n"
        # AADIL_JOB_BOARD_HEALTH_HELP_TEXT_V2_7_2
        "/menu - open the button control panel"
    )


def source_status_text() -> str:
    import html as _html
    from datetime import datetime as _datetime
    from datetime import timezone as _timezone
    from zoneinfo import ZoneInfo as _ZoneInfo

    from app.database import get_connection as _get_connection
    from app.source_runtime_state_v1 import classify_row as _classify_runtime_row

    _eastern = _ZoneInfo("America/New_York")
    _now_utc = _datetime.now(_timezone.utc)

    def _parse_time(value):
        if value in (None, ""):
            return None

        text = str(value).strip().replace("Z", "+00:00")
        parsed = None

        try:
            parsed = _datetime.fromisoformat(text)
        except ValueError:
            for pattern in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
            ):
                try:
                    parsed = _datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue

        if parsed is None:
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_timezone.utc)

        return parsed.astimezone(_timezone.utc)

    def _format_et(value):
        parsed = _parse_time(value)

        if parsed is None:
            return "not scheduled"

        return parsed.astimezone(_eastern).strftime(
            "%b %-d, %Y · %-I:%M %p ET"
        )

    def _remaining(value):
        parsed = _parse_time(value)

        if parsed is None:
            return None

        seconds = int(
            (parsed - _now_utc).total_seconds()
        )

        if seconds <= 0:
            return "Ready now"

        minutes = (seconds + 59) // 60
        hours, minute_remainder = divmod(
            minutes,
            60,
        )
        days, hour_remainder = divmod(
            hours,
            24,
        )

        parts = []

        if days:
            parts.append(f"{days}d")

        if hour_remainder:
            parts.append(f"{hour_remainder}h")

        if minute_remainder or not parts:
            parts.append(f"{minute_remainder}m")

        return " ".join(parts) + " remaining"

    def _category(row):
        # AADIL_TELEGRAM_CANONICAL_RUNTIME_STATE_V16
        runtime_state = str(row.get("_runtime_state") or "").strip().lower()
        if runtime_state:
            if runtime_state == "setup_required":
                return "setup"
            if runtime_state == "failed_waiting_retry":
                return "error"
            if runtime_state == "running":
                return "running"
            if runtime_state == "awaiting_first_live_run":
                return "pending"
            return "normal"

        status = str(row.get("health_status") or "not_tested").strip().lower()
        if status in {
            "configuration_required", "needs_credentials",
            "source_not_configured", "board_configuration_required",
        }:
            return "setup"
        if status in {"failed", "error", "unhealthy"}:
            return "error"
        if status in {
            "enabled_pending_first_run", "pending_first_run", "not_tested",
            "not_tested_direct", "scheduled_not_live_tested",
            "installed_pending_first_run", "ready",
        }:
            return "pending"
        return "normal"

    connection = _get_connection()

    try:
        health_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(source_health)"
            ).fetchall()
        }
        desired = [
            "source_name",
            "enabled",
            "cadence_minutes",
            "health_status",
            "last_error",
            "consecutive_failures",
            "last_success_at",
            "last_failure_at",
            "last_run_at",
            "raw_jobs_last_run",
            "jobs_found_last_run",
            "eligible_jobs_last_run",
            "inserted_jobs_last_run",
            "provider_used_last_run",
        ]
        selected = [
            name
            for name in desired
            if name in health_columns
        ]
        order_expression = (
            "source_tier, source_name"
            if "source_tier" in health_columns
            else "source_name"
        )
        rows = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT {", ".join(selected)}
                FROM source_health
                ORDER BY {order_expression}
                """
            ).fetchall()
        ]

        tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                """
            ).fetchall()
        }
        schedule_map = {}

        if "source_random_schedule" in tables:
            schedule_columns = {
                str(row[1])
                for row in connection.execute(
                    """
                    PRAGMA table_info(
                        source_random_schedule
                    )
                    """
                ).fetchall()
            }
            schedule_desired = [
                "source_name",
                "next_run_at",
                "schedule_state",
                "schedule_reason",
                "last_started_at",
                "last_completed_at",
                "last_worker_status",
                "last_worker_returncode",
                "consecutive_scheduler_failures",
            ]
            schedule_selected = [
                name
                for name in schedule_desired
                if name in schedule_columns
            ]

            if (
                "source_name" in schedule_selected
                and len(schedule_selected) > 1
            ):
                schedule_map = {
                    str(row["source_name"]): dict(row)
                    for row in connection.execute(
                        f"""
                        SELECT
                            {", ".join(schedule_selected)}
                        FROM source_random_schedule
                        """
                    ).fetchall()
                }

    finally:
        connection.close()

    enabled = [
        row
        for row in rows
        if int(row.get("enabled") or 0) == 1
    ]
    disabled = [
        row
        for row in rows
        if int(row.get("enabled") or 0) != 1
    ]

    ready_count = 0
    cooling_count = 0
    running_count = 0
    setup_count = 0
    failure_count = 0

    rendered_rows = []

    for row in enabled:
        source_name = str(
            row.get("source_name") or "Unknown"
        )
        cadence = int(
            row.get("cadence_minutes") or 0
        )
        schedule = schedule_map.get(
            source_name,
            {},
        )
        runtime_row = dict(row)
        runtime_row.update(schedule)
        classified = _classify_runtime_row(runtime_row, now=_now_utc)
        row["_runtime_state"] = classified.get("runtime_state")
        category = _category(row)
        next_run = schedule.get("next_run_at")
        remaining = _remaining(next_run)

        raw = int(
            row.get("raw_jobs_last_run")
            or row.get("jobs_found_last_run")
            or 0
        )
        eligible = int(
            row.get("eligible_jobs_last_run") or 0
        )
        inserted = int(
            row.get("inserted_jobs_last_run") or 0
        )
        status = str(
            row.get("health_status") or "not_tested"
        )
        error = str(
            row.get("last_error") or ""
        ).strip()
        provider = str(
            row.get("provider_used_last_run") or ""
        ).strip()

        if category == "setup":
            setup_count += 1
            icon = "⚙️"
            timer_label = "Setup required"
        elif category == "error":
            failure_count += 1
            icon = "❌"
            timer_label = "Runtime failure"
        elif category == "running":
            running_count += 1
            icon = "🔄"
            timer_label = "Running now"
        elif remaining == "Ready now":
            ready_count += 1
            icon = "🟢"
            timer_label = "Ready now"
        else:
            cooling_count += 1
            icon = "⏳"
            timer_label = (
                remaining
                or "Schedule unavailable"
            )

        lines = [
            (
                f"{icon} "
                + _html.escape(source_name)
                + " · "
                + _html.escape(timer_label)
                + f" · every {cadence} min"
            )
        ]

        if next_run:
            lines.append(
                "   Next: "
                + _html.escape(
                    _format_et(next_run)
                )
            )

        lines.append(
            (
                "   "
                + _html.escape(status)
                + f" · raw {raw}"
                + f" → eligible {eligible}"
                + f" → new {inserted}"
            )
        )

        if provider:
            lines.append(
                "   Provider: "
                + _html.escape(provider[:90])
            )

        if category in ("setup", "error") and error:
            label = (
                "Setup"
                if category == "setup"
                else "Error"
            )
            lines.append(
                f"   {label}: "
                + _html.escape(error[:180])
            )

        rendered_rows.append("\n".join(lines))

    lines = [
        "⏱ <b>Source health timers</b>",
        "",
        f"🟢 Ready now: <b>{ready_count}</b>",
        f"⏳ Cooling down: <b>{cooling_count}</b>",
        f"🔄 Running now: <b>{running_count}</b>",
        f"⚙️ Setup required: <b>{setup_count}</b>",
        f"❌ Runtime failures: <b>{failure_count}</b>",
        f"⏸ Disabled: <b>{len(disabled)}</b>",
        "",
        "<b>Enabled sources</b>",
        *rendered_rows,
    ]

    if disabled:
        lines.extend(
            [
                "",
                "<b>Disabled sources</b>",
                "\n".join(
                    "▫️ "
                    + _html.escape(
                        str(
                            row.get("source_name")
                            or "Unknown"
                        )
                    )
                    for row in disabled
                ),
            ]
        )

    lines.extend(
        [
            "",
            (
                "🛡 Repeated /run presses cannot "
                "bypass these timers."
            ),
        ]
    )

    text = "\n\n".join(lines)

    if len(text) <= 4090:
        return text

    compact_rows = []

    for row in enabled:
        source_name = str(
            row.get("source_name") or "Unknown"
        )
        cadence = int(
            row.get("cadence_minutes") or 0
        )
        schedule = schedule_map.get(
            source_name,
            {},
        )
        runtime_row = dict(row)
        runtime_row.update(schedule)
        classified = _classify_runtime_row(runtime_row, now=_now_utc)
        row["_runtime_state"] = classified.get("runtime_state")
        category = _category(row)
        remaining = _remaining(
            schedule.get("next_run_at")
        )

        if category == "setup":
            icon = "⚙️"
            timer_label = "Setup required"
        elif category == "error":
            icon = "❌"
            timer_label = "Runtime failure"
        elif category == "running":
            icon = "🔄"
            timer_label = "Running now"
        elif remaining == "Ready now":
            icon = "🟢"
            timer_label = "Ready now"
        else:
            icon = "⏳"
            timer_label = (
                remaining
                or "Schedule unavailable"
            )

        compact_rows.append(
            (
                f"{icon} "
                + _html.escape(source_name)
                + " · "
                + _html.escape(timer_label)
                + f" · every {cadence} min"
            )
        )

    compact = [
        "⏱ <b>Source health timers</b>",
        "",
        f"🟢 Ready now: <b>{ready_count}</b>",
        f"⏳ Cooling down: <b>{cooling_count}</b>",
        f"🔄 Running now: <b>{running_count}</b>",
        f"⚙️ Setup required: <b>{setup_count}</b>",
        f"❌ Runtime failures: <b>{failure_count}</b>",
        f"⏸ Disabled: <b>{len(disabled)}</b>",
        "",
        "<b>Enabled sources</b>",
        *compact_rows,
        "",
        (
            "Open Operations → Source Health in the dashboard "
            "for raw/eligible/new counts and exact health evidence."
        ),
        "",
        (
            "🛡 Repeated /run presses cannot "
            "bypass these timers."
        ),
    ]

    return "\n".join(compact)[:4090]




def send_control_panel(chat_id: int) -> dict[str, Any]:
    from app.telegram_client import telegram_request

    latest_run_id, stored_count = _latest_stored_run()

    keyboard_rows: list[list[dict[str, str]]] = [
        [
            {
                "text": "⏱ Run ready adapters",
                "callback_data": "cc:run:all",
            }
        ],
    ]

    if latest_run_id is not None and stored_count > 0:
        keyboard_rows.append(
            [
                {
                    "text": (
                        f"📂 Latest {stored_count} duplicate job(s)"
                    ),
                    "callback_data": (
                        f"cc:stored:{latest_run_id}:0"
                    ),
                }
            ]
        )

    keyboard_rows.extend(
        [
            [
                {
                    "text": "🆕 Recent jobs",
                    "callback_data": "cc:recent:5",
                },
                {
                    "text": "📊 Status",
                    "callback_data": "cc:status",
                },
            ],
            [
                {
                    "text": "⏱ Timers",
                    "callback_data": "cc:sources",
                },
                {
                    "text": "📨 Queue",
                    "callback_data": "cc:queue",
                },
            ],
        ]
    )

    # AADIL_FORCE_RERUN_CONTROL_PANEL_V1_1
    keyboard_rows.append(
        [
            {
                "text": "🔁 Force rerun stored job",
                "callback_data": "cc:rerun:help",
            },
            {
                "text": "🧭 Runtime state",
                "callback_data": "cc:runtime",
            },
        ]
    )

    # AADIL_JOB_BOARD_HEALTH_MENU_BUTTON_V2_7_2
    keyboard_rows.append(
        [
            {
                "text": "🩺 Job board health",
                "callback_data": "cc:jbh:s",
            }
        ]
    )
    response = telegram_request(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": command_help_text(),
            "parse_mode": "HTML",
            "reply_markup": json.dumps(
                {"inline_keyboard": keyboard_rows},
                ensure_ascii=False,
            ),
        },
    )

    return {
        "success": True,
        "message": "Control panel sent.",
        "message_id": int(
            response["result"]["message_id"]
        ),
        "latest_run_id": latest_run_id,
        "stored_count": stored_count,
    }


def send_latest_stored_jobs() -> dict[str, Any]:
    latest_run_id, stored_count = _latest_stored_run()

    if latest_run_id is None or stored_count <= 0:
        return {
            "success": False,
            "message": (
                "No stored-job source run is available yet. "
                "Run the enabled adapters once; future source summaries "
                "will include the stored-job button."
            ),
        }

    return send_stored_job_cards(latest_run_id, 0)


def configure_bot_commands(
    chat_id: int | None = None,
) -> dict[str, Any]:
    from app.telegram_client import CHAT_ID, telegram_request

    commands = [
        {
            "command": "start",
            "description": "Open HR Hunter controls",
        },
        {
            "command": "menu",
            "description": "Open the button control panel",
        },
        {
            "command": "run",
            "description": "Run all enabled source adapters",
        },
        {
            "command": "stored",
            "description": "Show latest duplicate stored jobs",
        },
        {
            "command": "recent",
            "description": "Show newest stored jobs",
        },
        {
            "command": "job",
            "description": "Open a stored job by ID",
        },
        {
            "command": "find",
            "description": "Search jobs by company or title",
        },
        {
            "command": "sources",
            "description": "Show adapter settings and health",
        },
        {
            "command": "status",
            "description": "Show system and scraper status",
        },
        {
            "command": "queue",
            "description": "Show the n8n dispatch queue",
        },
        # AADIL_TELEGRAM_MANUAL_INPUT_BOT_COMMANDS_V1
        {
            "command": "manual_input",
            "description": "Start a multi-message manual job",
        },
        {
            "command": "manual",
            "description": "Run a one-message manual job",
        },
        {
            "command": "manual_done",
            "description": "Submit the captured manual job",
        },
        {
            "command": "manual_cancel",
            "description": "Cancel manual job capture",
        },
        {
            "command": "manual_status",
            "description": "Show the latest manual n8n run",
        },
        # AADIL_JOB_BOARD_HEALTH_BOT_COMMAND_V2_7_2
        {
            "command": "health_job_boards",
            "description": "Show every employer board health detail",
        },
    ]

    command_response = telegram_request(
        "setMyCommands",
        {
            "commands": json.dumps(
                commands,
                ensure_ascii=False,
            )
        },
    )

    target_chat = int(chat_id or CHAT_ID or 0)
    panel_result = None

    if target_chat:
        panel_result = send_control_panel(target_chat)

    return {
        "success": bool(command_response.get("ok")),
        "commands": commands,
        "command_response": command_response,
        "control_panel": panel_result,
    }

# AADIL_SOURCE_HEALTH_REASON_VISIBILITY_V1

# AADIL_OBSERVABILITY_TARGET_AUDIT_V2_6_2_1

# AADIL_SOURCE_TIMER_RESTORE_V2_6_2_2

# AADIL_RERUN_RUNTIME_BOT_COMMANDS_V1_1
_aadil_handle_control_callback_before_rerun_runtime_v1_1 = handle_control_callback
_aadil_configure_bot_commands_before_rerun_runtime_v1_1 = configure_bot_commands


def handle_control_callback(
    callback_data: str,
    chat_id: int,
) -> tuple[bool, str, bool]:
    data = str(callback_data or "")

    if data == "cc:rerun:help":
        result = send_recent_job_cards(
            int(chat_id),
            5,
        )
        success = bool(result.get("success"))
        return (
            success,
            (
                "Recent stored-job cards sent. Press Force Rerun on the job you want."
                if success
                else str(result.get("message") or "No recent stored jobs were found.")
            ),
            not success,
        )

    if data == "cc:runtime":
        from app.telegram_client import telegram_request
        from app.source_runtime_state_v1 import telegram_runtime_section

        telegram_request(
            "sendMessage",
            {
                "chat_id": str(int(chat_id)),
                "text": telegram_runtime_section(),
                "parse_mode": "HTML",
            },
        )
        return True, "Runtime state sent.", False

    return _aadil_handle_control_callback_before_rerun_runtime_v1_1(
        callback_data,
        chat_id,
    )


def configure_bot_commands(
    chat_id: int | None = None,
) -> dict[str, Any]:
    from app.telegram_client import telegram_request

    result = dict(
        _aadil_configure_bot_commands_before_rerun_runtime_v1_1(
            chat_id
        )
        or {}
    )
    commands = [
        dict(item)
        for item in (
            result.get("commands")
            or []
        )
        if isinstance(item, dict)
    ]
    names = {
        str(item.get("command") or "")
        for item in commands
    }

    additions = (
        {
            "command": "rerun",
            "description": "Force rerun a stored job as Part 2/3",
        },
        {
            "command": "runtime",
            "description": "Show current source execution state",
        },
    )
    for item in additions:
        if item["command"] not in names:
            commands.append(item)

    command_response = telegram_request(
        "setMyCommands",
        {
            "commands": json.dumps(
                commands,
                ensure_ascii=False,
            )
        },
    )
    result["success"] = bool(command_response.get("ok"))
    result["commands"] = commands
    result["command_response"] = command_response
    return result


# AADIL_TELEGRAM_COMPLETE_SIDE_MENU_V1
_AADIL_COMPLETE_SIDE_MENU_COMMANDS_V1 = [
    {"command": "start", "description": "Open HR Hunter controls"},
    {"command": "menu", "description": "Open the button control panel"},
    {"command": "run", "description": "Run enabled sources that are ready"},
    {"command": "stored", "description": "Reopen latest stored jobs"},
    {"command": "recent", "description": "Show newest stored jobs"},
    {"command": "job", "description": "Open one stored job by ID"},
    {"command": "find", "description": "Search jobs by company or title"},
    {"command": "sources", "description": "Show source timers and health"},
    {"command": "status", "description": "Show core system status"},
    {"command": "queue", "description": "Show the n8n dispatch queue"},
    {"command": "forceremove", "description": "Safely clear stuck queue state"},
    {"command": "manual_input", "description": "Start a multi-message manual job"},
    {"command": "manual", "description": "Run a one-message manual job"},
    {"command": "manual_done", "description": "Submit the captured manual job"},
    {"command": "manual_cancel", "description": "Cancel manual job capture"},
    {"command": "manual_status", "description": "Show the latest manual n8n run"},
    {"command": "health_job_boards", "description": "Show employer-board health details"},
    {"command": "rerun", "description": "Force rerun a stored job as Part N"},
    {"command": "runtime", "description": "Show current source execution state"},
]


def configure_bot_commands(
    chat_id: int | None = None,
) -> dict[str, Any]:
    """Publish every primary Telegram slash command without dropping older entries."""
    import json as _json
    from app.telegram_client import CHAT_ID, telegram_request

    commands = [
        dict(item)
        for item in _AADIL_COMPLETE_SIDE_MENU_COMMANDS_V1
    ]

    response = telegram_request(
        "setMyCommands",
        {
            "commands": _json.dumps(
                commands,
                ensure_ascii=False,
            )
        },
    )

    target_chat = int(chat_id or CHAT_ID or 0)
    panel_result = None
    if target_chat:
        panel_result = send_control_panel(target_chat)

    return {
        "success": bool(response.get("ok")),
        "commands": commands,
        "command_response": response,
        "control_panel": panel_result,
        "marker": "AADIL_TELEGRAM_COMPLETE_SIDE_MENU_V1",
    }
# AADIL_TELEGRAM_SCORECARDS_V1 — BEGIN
_aadil_scorecards_original_handle_control_callback_v1 = handle_control_callback
_aadil_scorecards_original_configure_bot_commands_v1 = configure_bot_commands
_aadil_scorecards_original_send_control_panel_v1 = send_control_panel

def _aadil_scorecards_inject_panel_button_v1(payload):
    if not isinstance(payload, dict):
        return payload
    updated = dict(payload)
    raw_markup = updated.get("reply_markup")
    if raw_markup in (None, ""):
        return updated

    was_text = isinstance(raw_markup, str)
    try:
        markup = (
            json.loads(raw_markup)
            if was_text
            else dict(raw_markup)
        )
    except Exception:
        return updated

    rows = markup.get("inline_keyboard")
    if not isinstance(rows, list):
        return updated

    callback_data = "cc:sc:menu"
    exists = any(
        isinstance(button, dict)
        and button.get("callback_data") == callback_data
        for row in rows
        if isinstance(row, list)
        for button in row
    )
    if not exists:
        rows.append(
            [
                {
                    "text": "📊 Past 24h Jobs by Hunter Score",
                    "callback_data": callback_data,
                }
            ]
        )

    updated["reply_markup"] = (
        json.dumps(markup, ensure_ascii=False)
        if was_text
        else markup
    )
    return updated


def send_control_panel(chat_id):
    import app.telegram_client as _aadil_scorecards_client_v1

    original_client_request = _aadil_scorecards_client_v1.telegram_request
    original_global_request = globals().get("telegram_request")

    def proxy(method, payload):
        outgoing = payload
        if str(method) == "sendMessage":
            outgoing = _aadil_scorecards_inject_panel_button_v1(payload)
        return original_client_request(method, outgoing)

    _aadil_scorecards_client_v1.telegram_request = proxy
    if original_global_request is not None:
        globals()["telegram_request"] = proxy

    try:
        return _aadil_scorecards_original_send_control_panel_v1(chat_id)
    finally:
        _aadil_scorecards_client_v1.telegram_request = original_client_request
        if original_global_request is not None:
            globals()["telegram_request"] = original_global_request


def handle_control_callback(callback_data, chat_id):
    data = str(callback_data or "")
    if data == "cc:scorecards" or data.startswith("cc:sc:"):
        from app.telegram_scorecards_v1 import (
            handle_scorecard_callback,
        )
        return handle_scorecard_callback(data, int(chat_id))
    return _aadil_scorecards_original_handle_control_callback_v1(
        callback_data,
        chat_id,
    )


def configure_bot_commands(chat_id=None):
    from app.telegram_client import telegram_request

    result = dict(
        _aadil_scorecards_original_configure_bot_commands_v1(
            chat_id
        )
        or {}
    )
    commands = [
        dict(item)
        for item in (result.get("commands") or [])
        if isinstance(item, dict)
    ]
    names = {
        str(item.get("command") or "")
        for item in commands
    }
    additions = (
        {
            "command": "scorecards",
            "description": "Browse past 24h jobs by Hunter score",
        },
        {
            "command": "last24h",
            "description": "Open the 24-hour scorecard browser",
        },
    )
    for item in additions:
        if item["command"] not in names:
            commands.append(dict(item))

    command_response = telegram_request(
        "setMyCommands",
        {
            "commands": json.dumps(
                commands,
                ensure_ascii=False,
            )
        },
    )
    menu_response = telegram_request(
        "setChatMenuButton",
        {
            "menu_button": json.dumps(
                {"type": "commands"},
                ensure_ascii=False,
            )
        },
    )

    result["success"] = bool(
        command_response.get("ok")
        and menu_response.get("ok")
    )
    result["commands"] = commands
    result["command_response"] = command_response
    result["menu_response"] = menu_response
    result["scorecards_marker"] = "AADIL_TELEGRAM_SCORECARDS_V1"
    return result


if "commands_help_text" in globals():
    _aadil_scorecards_original_commands_help_text_v1 = commands_help_text

    def commands_help_text(*args, **kwargs):
        text = _aadil_scorecards_original_commands_help_text_v1(
            *args,
            **kwargs,
        )
        if "/scorecards" not in text:
            text = (
                text.rstrip()
                + "\n/scorecards - browse past 24h Telegram jobs by Hunter score"
                + "\n/last24h - open the same 24-hour scorecard browser"
            )
        return text
# AADIL_TELEGRAM_SCORECARDS_V1 — END
# AADIL_TELEGRAM_SIDE_MENU_SCORECARDS_V1_2 — BEGIN
_aadil_side_menu_original_configure_bot_commands_v1_2 = configure_bot_commands

def configure_bot_commands(chat_id=None):
    import json as _aadil_side_menu_json_v1_2

    from app.telegram_client import (
        telegram_request as _aadil_side_menu_telegram_request_v1_2,
    )
    from app.telegram_side_menu_scorecards_v1_2 import (
        side_menu_command_specs as _aadil_side_menu_specs_v1_2,
    )

    result = dict(
        _aadil_side_menu_original_configure_bot_commands_v1_2(
            chat_id
        )
        or {}
    )

    desired = _aadil_side_menu_specs_v1_2()
    managed_names = {
        str(item.get("command") or "")
        for item in desired
    }

    existing = [
        dict(item)
        for item in (result.get("commands") or [])
        if isinstance(item, dict)
    ]

    ordered = []
    seen = set()

    for item in desired + existing:
        name = str(item.get("command") or "").strip()
        description = str(item.get("description") or "").strip()

        if not name or name in seen:
            continue

        if name in managed_names:
            preferred = next(
                (
                    dict(candidate)
                    for candidate in desired
                    if candidate.get("command") == name
                ),
                None,
            )
            if preferred is not None:
                item = preferred

        ordered.append(
            {
                "command": name,
                "description": description,
            }
            if name not in managed_names
            else dict(item)
        )
        seen.add(name)

    command_response = _aadil_side_menu_telegram_request_v1_2(
        "setMyCommands",
        {
            "commands": _aadil_side_menu_json_v1_2.dumps(
                ordered,
                ensure_ascii=False,
            )
        },
    )

    menu_response = _aadil_side_menu_telegram_request_v1_2(
        "setChatMenuButton",
        {
            "menu_button": _aadil_side_menu_json_v1_2.dumps(
                {"type": "commands"},
                ensure_ascii=False,
            )
        },
    )

    result["success"] = bool(
        command_response.get("ok")
        and menu_response.get("ok")
    )
    result["commands"] = ordered
    result["command_response"] = command_response
    result["menu_response"] = menu_response
    result["side_menu_marker"] = (
        "AADIL_TELEGRAM_SIDE_MENU_SCORECARDS_V1_2"
    )

    return result
# AADIL_TELEGRAM_SIDE_MENU_SCORECARDS_V1_2 — END
