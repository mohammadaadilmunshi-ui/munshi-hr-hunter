from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.database import get_connection


MARKER = "AADIL_HR_HUNTER_BROAD_MARKET_COVERAGE_V2_6"

DIRECT_PROVIDER_HOSTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "jobs.personio.",
    "pinpointhq.com",
    "comeet.co",
)


def classify_url(url: str) -> dict[str, str]:
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.casefold().split(":", 1)[0]
    path = parsed.path or ""
    lower_url = str(url or "").casefold()

    if host.endswith(".recruitee.com"):
        return {
            "provider": "recruitee",
            "source_kind": "api",
            "board_locator": host.split(".", 1)[0],
        }
    if "workable.com" in host:
        return {
            "provider": "workable",
            "source_kind": "career_page",
            "board_locator": path.strip("/").split("/", 1)[0],
        }
    if any(
        token in host
        for token in (
            "teamtailor.com",
            "bamboohr.com",
            "jobvite.com",
            "icims.com",
            "myworkdayjobs.com",
            "oraclecloud.com",
            "successfactors.com",
            "ultipro.com",
            "ukg.com",
            "adp.com",
            "jazz.co",
            "breezy.hr",
            "freshteam.com",
            "zohorecruit.com",
            "rippling.com",
            "taleo.net",
            "avature.net",
        )
    ):
        return {
            "provider": "generic_ats",
            "source_kind": "career_page",
            "board_locator": host,
        }
    if re.search(r"\.(rss|atom|xml)(?:$|\?)", lower_url) or any(
        token in lower_url
        for token in ("/rss", "/feed", "offers.xml", "jobs.xml")
    ):
        return {
            "provider": "generic_feed",
            "source_kind": "rss",
            "board_locator": host,
        }
    return {
        "provider": "generic_career_page",
        "source_kind": "career_page",
        "board_locator": host,
    }


def initialize_market_tables(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS market_public_boards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'generic_career_page',
            source_kind TEXT NOT NULL DEFAULT 'career_page',
            board_locator TEXT,
            board_url TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            priority_weight INTEGER NOT NULL DEFAULT 10,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(company_name, board_url)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_public_boards_enabled
        ON market_public_boards(enabled, source_kind, provider)
        """
    )


def import_existing_career_urls() -> dict[str, Any]:
    connection = get_connection()
    scanned = 0
    inserted = 0
    updated = 0
    skipped_direct = 0
    detected: dict[str, int] = {}

    try:
        initialize_market_tables(connection)
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "ats_company_registry" not in tables:
            connection.commit()
            return {
                "success": True,
                "scanned": 0,
                "inserted": 0,
                "updated": 0,
                "skipped_direct": 0,
                "detected": {},
            }

        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(ats_company_registry)"
            ).fetchall()
        }
        company_col = next(
            (
                name
                for name in ("company_name", "name", "company")
                if name in columns
            ),
            None,
        )
        url_col = next(
            (
                name
                for name in ("careers_url", "career_url", "board_url", "url")
                if name in columns
            ),
            None,
        )
        if not company_col or not url_col:
            connection.commit()
            return {
                "success": True,
                "scanned": 0,
                "inserted": 0,
                "updated": 0,
                "skipped_direct": 0,
                "detected": {},
                "reason": "unsupported_registry_columns",
            }

        rows = connection.execute(
            f"""
            SELECT "{company_col}" AS company_name,
                   "{url_col}" AS careers_url
            FROM ats_company_registry
            WHERE trim(COALESCE("{url_col}",'')) != ''
            """
        ).fetchall()

        for row in rows:
            scanned += 1
            company = str(row["company_name"] or "").strip()
            url = str(row["careers_url"] or "").strip()
            host = urlparse(url).netloc.casefold()
            if not company or not url:
                continue
            if any(token in host for token in DIRECT_PROVIDER_HOSTS):
                skipped_direct += 1
                continue

            classified = classify_url(url)
            detected[classified["provider"]] = (
                detected.get(classified["provider"], 0) + 1
            )
            existing = connection.execute(
                """
                SELECT id
                FROM market_public_boards
                WHERE lower(company_name)=lower(?)
                  AND lower(board_url)=lower(?)
                """,
                (company, url),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE market_public_boards
                    SET provider=?, source_kind=?, board_locator=?,
                        enabled=1, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        classified["provider"],
                        classified["source_kind"],
                        classified["board_locator"],
                        existing["id"],
                    ),
                )
                updated += 1
            else:
                connection.execute(
                    """
                    INSERT INTO market_public_boards(
                        company_name,provider,source_kind,board_locator,
                        board_url,enabled,priority_weight,notes
                    ) VALUES(?,?,?,?,?,1,10,?)
                    """,
                    (
                        company,
                        classified["provider"],
                        classified["source_kind"],
                        classified["board_locator"],
                        url,
                        "Imported from ats_company_registry by V2.6",
                    ),
                )
                inserted += 1

        connection.commit()
        return {
            "success": True,
            "scanned": scanned,
            "inserted": inserted,
            "updated": updated,
            "skipped_direct": skipped_direct,
            "detected": detected,
            "network_request_made": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
