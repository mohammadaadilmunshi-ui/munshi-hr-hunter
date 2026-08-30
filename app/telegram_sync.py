from __future__ import annotations

import json
from typing import Any

from app.database import get_connection
from app.telegram_client import edit_job_card


def get_latest_job_card(
    job_id: int,
) -> dict[str, int] | None:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT payload_json
            FROM events
            WHERE job_id = ?
              AND event_type = 'telegram_job_card_sent'
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    payload = json.loads(
        row["payload_json"] or "{}"
    )

    chat_id = payload.get("chat_id")
    message_id = payload.get("message_id")

    if not chat_id or not message_id:
        return None

    return {
        "chat_id": int(chat_id),
        "message_id": int(message_id),
    }


def record_sync_event(
    *,
    job_id: int,
    status: str,
    actor: str,
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
            VALUES (
                ?,
                'telegram_card_sync',
                ?,
                ?,
                ?
            )
            """,
            (
                job_id,
                actor,
                status,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()
    finally:
        connection.close()


def sync_latest_job_card(
    job_id: int,
    *,
    notice: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    card = get_latest_job_card(job_id)

    if card is None:
        result = {
            "success": False,
            "reason": "no_telegram_card",
            "job_id": job_id,
        }

        record_sync_event(
            job_id=job_id,
            status="skipped",
            actor=actor,
            payload=result,
        )

        return result

    try:
        changed = edit_job_card(
            chat_id=card["chat_id"],
            message_id=card["message_id"],
            job_id=job_id,
            notice=notice,
        )

        result = {
            "success": True,
            "changed": changed,
            "job_id": job_id,
            "chat_id": card["chat_id"],
            "message_id": card["message_id"],
        }

        record_sync_event(
            job_id=job_id,
            status="completed",
            actor=actor,
            payload=result,
        )

        return result

    except Exception as error:
        result = {
            "success": False,
            "reason": "telegram_api_error",
            "job_id": job_id,
            "error": str(error),
        }

        record_sync_event(
            job_id=job_id,
            status="failed",
            actor=actor,
            payload=result,
        )

        return result
