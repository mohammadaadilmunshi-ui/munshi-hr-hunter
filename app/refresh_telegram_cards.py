from __future__ import annotations

import json

from app.database import get_connection
from app.telegram_client import edit_job_card


def main() -> None:
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                e.job_id,
                e.payload_json
            FROM events e
            INNER JOIN (
                SELECT
                    job_id,
                    MAX(id) AS latest_event_id
                FROM events
                WHERE event_type =
                    'telegram_job_card_sent'
                GROUP BY job_id
            ) latest
                ON latest.latest_event_id = e.id
            ORDER BY e.job_id
            """
        ).fetchall()
    finally:
        connection.close()

    refreshed = 0

    for row in rows:
        payload = json.loads(
            row["payload_json"] or "{}"
        )

        chat_id = payload.get("chat_id")
        message_id = payload.get("message_id")

        if not chat_id or not message_id:
            continue

        changed = edit_job_card(
            chat_id=int(chat_id),
            message_id=int(message_id),
            job_id=int(row["job_id"]),
            notice="Current decision and buttons refreshed.",
        )

        print(
            f"Job {row['job_id']}: "
            f"{'updated' if changed else 'already current'}"
        )

        refreshed += 1

    print(f"Cards processed: {refreshed}")


if __name__ == "__main__":
    main()
