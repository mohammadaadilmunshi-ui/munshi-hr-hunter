from __future__ import annotations

import argparse
import json
from typing import Any

from app.database import (
    get_connection,
    get_setting,
)


SUPPORTED_ATS_TYPES = {
    "unknown",
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "workable",
    "bamboohr",
    "teamtailor",
    "recruitee",
    "jobvite",
    "icims",
}


REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS ats_company_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    company_name TEXT NOT NULL UNIQUE,
    ats_type TEXT NOT NULL DEFAULT 'unknown',
    board_token TEXT,
    careers_url TEXT,

    enabled INTEGER NOT NULL DEFAULT 0,
    priority_weight INTEGER NOT NULL DEFAULT 0,
    notes TEXT,

    last_success_at TEXT,
    last_failure_at TEXT,
    last_run_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    jobs_found_last_run INTEGER NOT NULL DEFAULT 0,
    health_status TEXT NOT NULL DEFAULT 'not_configured',
    last_error TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ats_registry_enabled
ON ats_company_registry(enabled);

CREATE INDEX IF NOT EXISTS idx_ats_registry_type
ON ats_company_registry(ats_type);
"""


def initialize_registry() -> None:
    connection = get_connection()

    try:
        connection.executescript(
            REGISTRY_SQL
        )
        connection.commit()
    finally:
        connection.close()


def load_dashboard_watchlist() -> list[str]:
    targeting = get_setting(
        "targeting",
        {},
    ) or {}

    companies = targeting.get(
        "company_watchlist",
        [],
    )

    clean_companies: list[str] = []
    seen: set[str] = set()

    for company in companies:
        cleaned = str(company or "").strip()

        if not cleaned:
            continue

        key = cleaned.casefold()

        if key in seen:
            continue

        seen.add(key)
        clean_companies.append(cleaned)

    return clean_companies


def sync_dashboard_watchlist() -> dict[str, Any]:
    companies = load_dashboard_watchlist()

    connection = get_connection()

    try:
        before_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM ats_company_registry
            """
        ).fetchone()[0]

        for company in companies:
            connection.execute(
                """
                INSERT OR IGNORE INTO
                    ats_company_registry (
                        company_name,
                        ats_type,
                        enabled,
                        priority_weight,
                        health_status,
                        notes
                    )
                VALUES (
                    ?,
                    'unknown',
                    0,
                    10,
                    'not_configured',
                    'Imported from dashboard company watchlist'
                )
                """,
                (company,),
            )

        connection.commit()

        after_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM ats_company_registry
            """
        ).fetchone()[0]

    finally:
        connection.close()

    return {
        "watchlist_companies": len(companies),
        "registry_before": before_count,
        "registry_after": after_count,
        "new_registry_rows": (
            after_count - before_count
        ),
    }


def list_registry() -> list[dict[str, Any]]:
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                id,
                company_name,
                ats_type,
                board_token,
                careers_url,
                enabled,
                priority_weight,
                health_status,
                last_run_at,
                jobs_found_last_run
            FROM ats_company_registry
            ORDER BY
                priority_weight DESC,
                company_name COLLATE NOCASE
            """
        ).fetchall()
    finally:
        connection.close()

    return [
        dict(row)
        for row in rows
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--sync-watchlist",
        action="store_true",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    initialize_registry()

    sync_result = None

    if args.sync_watchlist:
        sync_result = (
            sync_dashboard_watchlist()
        )

    registry = list_registry()

    output = {
        "success": True,
        "configuration_source": (
            "SQLite dashboard company watchlist"
        ),
        "network_request_made": False,
        "telegram_messages": 0,
        "n8n_calls": 0,
        "supported_ats_types": sorted(
            SUPPORTED_ATS_TYPES
        ),
        "sync_result": sync_result,
        "registry_count": len(registry),
        "enabled_registry_count": sum(
            1
            for row in registry
            if bool(row["enabled"])
        ),
        "configured_registry_count": sum(
            1
            for row in registry
            if (
                row["ats_type"] != "unknown"
                and bool(row["board_token"])
            )
        ),
        "registry": registry,
    }

    print(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
