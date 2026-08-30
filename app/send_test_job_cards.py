from app.database import get_connection
from app.telegram_client import send_job_card


def main() -> None:
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT id, company_name, title
            FROM jobs
            WHERE source = 'Fake Worker'
              AND telegram_sent = 0
            ORDER BY hunter_score DESC
            """
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        print("No unsent fake jobs were found.")
        return

    for row in rows:
        message_id = send_job_card(
            int(row["id"])
        )

        print(
            f"Sent job {row['id']}: "
            f"{row['company_name']} | "
            f"{row['title']} | "
            f"message_id={message_id}"
        )

    print("Telegram job-card test: OK")


if __name__ == "__main__":
    main()
