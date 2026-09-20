from __future__ import annotations

from app import database, greenhouse_worker
from app.ats_registry import initialize_registry


def test_greenhouse_rotates_least_recently_run_boards_first(hunter_db) -> None:
    initialize_registry()
    connection = database.get_connection()
    try:
        connection.execute("DELETE FROM ats_company_registry WHERE lower(ats_type)='greenhouse'")
        rows = [
            ("Never Run", "greenhouse", "neverrun", 1, 10, None),
            ("Old Run", "greenhouse", "oldrun", 1, 1, "2026-09-01 00:00:00"),
            ("Recent High Priority", "greenhouse", "recent", 1, 999, "2026-09-20 00:00:00"),
        ]
        connection.executemany(
            """
            INSERT INTO ats_company_registry(
                company_name,ats_type,board_token,enabled,priority_weight,last_run_at
            ) VALUES(?,?,?,?,?,?)
            """,
            rows,
        )
        connection.commit()
    finally:
        connection.close()

    selected = greenhouse_worker.load_enabled_companies(2)

    assert [row["company_name"] for row in selected] == [
        "Never Run",
        "Old Run",
    ]
