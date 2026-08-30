from app.database import get_connection


COLUMNS = {
    "score_breakdown_json": (
        "TEXT NOT NULL DEFAULT '{}'"
    ),
    "scoring_version": "TEXT",
    "last_scored_at": "TEXT",
}


def main() -> None:
    connection = get_connection()

    try:
        existing_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(jobs)"
            ).fetchall()
        }

        added = []

        for column_name, definition in COLUMNS.items():
            if column_name in existing_columns:
                continue

            connection.execute(
                f"""
                ALTER TABLE jobs
                ADD COLUMN {column_name} {definition}
                """
            )

            added.append(column_name)

        connection.commit()
    finally:
        connection.close()

    print(
        "Scoring columns added: "
        + (
            ", ".join(added)
            if added
            else "none, already present"
        )
    )

    print("Scoring migration: OK")


if __name__ == "__main__":
    main()
