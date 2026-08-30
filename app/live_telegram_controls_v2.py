from __future__ import annotations

import sqlite3
from typing import Any

from app.database import get_connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _send_job_cards(chat_id: int, job_ids: list[int], heading: str) -> dict[str, Any]:
    from app.telegram_client import send_job_card, telegram_request

    if not job_ids:
        return {"success": False, "message": "No matching stored jobs were found."}

    telegram_request(
        "sendMessage",
        {
            "chat_id": str(int(chat_id)),
            "text": heading,
            "parse_mode": "HTML",
        },
    )
    sent: list[int] = []
    failures: list[dict[str, Any]] = []
    for job_id in job_ids:
        try:
            send_job_card(int(job_id))
            sent.append(int(job_id))
        except Exception as error:
            failures.append({"job_id": int(job_id), "error": str(error)})
    return {
        "success": bool(sent),
        "message": f"Sent {len(sent)} job card(s)." + (
            f" {len(failures)} failed." if failures else ""
        ),
        "sent": sent,
        "failures": failures,
    }


def _duplicate_job_ids(run_id: int, offset: int) -> tuple[list[int], int]:
    connection = get_connection()
    connection.row_factory = sqlite3.Row
    try:
        if (
            _table_exists(connection, "telegram_source_run_jobs")
            and _table_exists(connection, "telegram_source_runs")
        ):
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM telegram_source_run_jobs WHERE run_id=?",
                    (int(run_id),),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT job_id
                FROM telegram_source_run_jobs
                WHERE run_id=?
                ORDER BY job_id DESC
                LIMIT 10 OFFSET ?
                """,
                (int(run_id), max(0, int(offset))),
            ).fetchall()
            ids = [int(row[0]) for row in rows]
            if ids:
                return ids, total

            source_row = connection.execute(
                """
                SELECT source_name, duplicate_count
                FROM telegram_source_runs
                WHERE id=?
                """,
                (int(run_id),),
            ).fetchone()
            if source_row:
                source_name = str(source_row["source_name"] or "")
                count = max(1, int(source_row["duplicate_count"] or 1))
                rows = connection.execute(
                    """
                    SELECT id
                    FROM jobs
                    WHERE lower(COALESCE(source, '')) LIKE ?
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (
                        f"%{source_name.casefold()}%",
                        min(10, count),
                        max(0, int(offset)),
                    ),
                ).fetchall()
                return [int(row[0]) for row in rows], len(rows)
        return [], 0
    finally:
        connection.close()


def _recent_job_ids(limit: int) -> list[int]:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT id
            FROM jobs
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 10)),),
        ).fetchall()
        return [int(row[0]) for row in rows]
    finally:
        connection.close()


def handle_live_callback(
    callback_data: str,
    chat_id: int,
) -> tuple[bool, str, bool]:
    data = str(callback_data or "")

    if data.startswith("app:"):
        from app.application_runs_v1 import handle_application_callback

        return handle_application_callback(data, int(chat_id))

    parts = data.split(":")
    if len(parts) >= 3 and parts[:2] == ["cc", "stored"]:
        try:
            run_id = int(parts[2])
            offset = int(parts[3]) if len(parts) > 3 else 0
        except ValueError:
            return False, "Invalid duplicate-job request.", True
        ids, total = _duplicate_job_ids(run_id, offset)
        result = _send_job_cards(
            int(chat_id),
            ids,
            (
                "📂 <b>Duplicate/already-stored jobs from this run</b>\n"
                f"Showing {len(ids)} of {total}"
            ),
        )
        return (
            bool(result.get("success")),
            str(result.get("message") or "Duplicate jobs processed."),
            not bool(result.get("success")),
        )

    if len(parts) >= 3 and parts[:2] == ["cc", "recent"]:
        try:
            limit = int(parts[2])
        except ValueError:
            limit = 5
        result = _send_job_cards(
            int(chat_id),
            _recent_job_ids(limit),
            "🆕 <b>Most recently stored jobs</b>",
        )
        return (
            bool(result.get("success")),
            str(result.get("message") or "Recent jobs processed."),
            not bool(result.get("success")),
        )

    if data == "cc:applied":
        from app.application_runs_v1 import send_applied_jobs

        send_applied_jobs(int(chat_id), 0)
        return True, "Applied-jobs dashboard opened.", False

    if data == "cc:runs":
        from app.application_runs_v1 import send_application_runs

        send_application_runs(int(chat_id), 0)
        return True, "Application runs opened.", False

    try:
        from app.telegram_control_center import handle_control_callback

        return handle_control_callback(data, int(chat_id))
    except Exception as error:
        return False, f"Control action failed: {error}", True
