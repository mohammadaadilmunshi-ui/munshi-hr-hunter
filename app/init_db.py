from app.database import get_connection, get_setting, initialize_database


def main() -> None:
    database_path = initialize_database()
    connection = get_connection()

    try:
        table_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            """
        ).fetchone()[0]

        location_count = connection.execute(
            "SELECT COUNT(*) FROM location_rules"
        ).fetchone()[0]

        source_count = connection.execute(
            "SELECT COUNT(*) FROM source_health"
        ).fetchone()[0]

        journal_mode = connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0]

        busy_timeout = connection.execute(
            "PRAGMA busy_timeout"
        ).fetchone()[0]
    finally:
        connection.close()

    scoring = get_setting("scoring", {})
    authorization = get_setting("authorization", {})
    runtime = get_setting("runtime", {})

    print(f"Database created: {database_path}")
    print(f"Application tables: {table_count}")
    print(f"Location rules seeded: {location_count}")
    print(f"Sources seeded: {source_count}")
    print(f"Journal mode: {journal_mode}")
    print(f"Busy timeout: {busy_timeout} ms")
    print()
    print(f"CPT start date: {authorization.get('cpt_start_date')}")
    print(f"CPT end date: {authorization.get('cpt_end_date')}")
    print(f"Auto n8n threshold: {scoring.get('auto_n8n_threshold')}")
    print(f"Default automatic limit: {scoring.get('daily_auto_n8n_limit')}")
    print(f"Aggressive automatic limit: {scoring.get('aggressive_auto_n8n_limit')}")
    print(f"Manual limit: {scoring.get('daily_manual_n8n_limit')}")
    print(f"n8n enabled: {runtime.get('n8n_enabled')}")
    print(f"Telegram enabled: {runtime.get('telegram_enabled')}")
    print("Database initialization: OK")


if __name__ == "__main__":
    main()
