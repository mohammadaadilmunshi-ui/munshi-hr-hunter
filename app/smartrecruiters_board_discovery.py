from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from typing import Any
from urllib.parse import unquote, urlparse

from app.database import get_setting
from app.sources.smartrecruiters import (
    fetch_all_postings,
)


SOURCE_NAME = "SmartRecruiters"


def canonical_company(value: Any) -> str:
    text = unicodedata.normalize(
        "NFKD",
        str(value or ""),
    )

    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )

    words = re.findall(
        r"[a-z0-9]+",
        text.casefold(),
    )

    suffixes = {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "company",
        "co",
        "plc",
        "usa",
    }

    while words and words[-1] in suffixes:
        words.pop()

    return "".join(words)


def token_variants(
    company_name: str,
) -> list[str]:
    original_words = re.findall(
        r"[A-Za-z0-9]+",
        company_name,
    )

    lower_words = [
        word.casefold()
        for word in original_words
    ]

    candidates = [
        "".join(original_words),
        "".join(lower_words),
        "-".join(lower_words),
    ]

    output: list[str] = []
    seen: set[str] = set()

    for candidate in candidates:
        candidate = candidate.strip("-")

        if (
            len(candidate) < 2
            or candidate.casefold() in seen
        ):
            continue

        seen.add(candidate.casefold())
        output.append(candidate)

    return output


def direct_token(value: Any) -> str | None:
    text = str(value or "").strip()

    if not text:
        return None

    try:
        parsed = urlparse(text)
    except ValueError:
        return None

    if (
        parsed.netloc.casefold()
        != "careers.smartrecruiters.com"
    ):
        return None

    pieces = [
        unquote(piece)
        for piece in parsed.path.split("/")
        if piece
    ]

    return pieces[0] if pieces else None


def load_candidates() -> list[
    dict[str, str]
]:
    targeting = get_setting(
        "targeting",
        {},
    ) or {}

    company_names = list(
        targeting.get(
            "company_watchlist"
        )
        or []
    )

    connection = sqlite3.connect(
        "data/hunter.db"
    )
    connection.row_factory = sqlite3.Row

    direct_candidates: list[
        dict[str, str]
    ] = []

    try:
        rows = connection.execute(
            """
            SELECT
                company_name,
                ats_type,
                careers_url
            FROM ats_company_registry
            WHERE company_name NOT LIKE
                '%Adapter Test%'
            """
        ).fetchall()
    finally:
        connection.close()

    for row in rows:
        company_name = str(
            row["company_name"]
            or ""
        ).strip()

        if company_name:
            company_names.append(
                company_name
            )

        token = direct_token(
            row["careers_url"]
        )

        if token and company_name:
            direct_candidates.append(
                {
                    "company": company_name,
                    "token": token,
                    "method": "direct_url",
                }
            )

    candidates = list(
        direct_candidates
    )

    seen_companies: set[str] = set()

    for company_name in company_names:
        company_name = str(
            company_name or ""
        ).strip()

        company_key = canonical_company(
            company_name
        )

        if (
            not company_key
            or company_key in seen_companies
        ):
            continue

        seen_companies.add(company_key)

        for token in token_variants(
            company_name
        ):
            candidates.append(
                {
                    "company": company_name,
                    "token": token,
                    "method": "dashboard_probe",
                }
            )

    deduplicated: dict[
        tuple[str, str],
        dict[str, str],
    ] = {}

    for candidate in candidates:
        key = (
            canonical_company(
                candidate["company"]
            ),
            candidate["token"].casefold(),
        )

        if key not in deduplicated:
            deduplicated[key] = candidate

    return list(
        deduplicated.values()
    )[:150]


def probe(
    candidate: dict[str, str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        **candidate,
        "valid": False,
        "identity_verified": False,
        "jobs_found": 0,
        "returned_company_names": [],
        "error": None,
    }

    try:
        fetched = fetch_all_postings(
            candidate["token"],
            max_pages=1,
            timeout_seconds=12,
        )

        postings = fetched[
            "postings"
        ]

        returned_names = sorted(
            {
                str(
                    (
                        posting.get(
                            "company"
                        )
                        or {}
                    ).get("name")
                    or ""
                ).strip()
                for posting in postings
                if isinstance(
                    posting,
                    dict,
                )
            }
            - {""}
        )

        expected_key = canonical_company(
            candidate["company"]
        )

        returned_keys = {
            canonical_company(name)
            for name in returned_names
        }

        identity_verified = (
            expected_key in returned_keys
        )

        result.update(
            {
                "valid": True,
                "identity_verified": (
                    identity_verified
                ),
                "jobs_found": len(
                    postings
                ),
                "returned_company_names": (
                    returned_names
                ),
            }
        )

    except Exception as error:
        result["error"] = str(error)[:500]

    return result


def main() -> None:
    candidates = load_candidates()
    results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(
        max_workers=6
    ) as executor:
        future_map = {
            executor.submit(
                probe,
                candidate,
            ): candidate
            for candidate in candidates
        }

        for future in as_completed(
            future_map
        ):
            results.append(
                future.result()
            )

    verified = [
        result
        for result in results
        if (
            result["valid"]
            and result[
                "identity_verified"
            ]
            and result[
                "jobs_found"
            ] > 0
        )
    ]

    best_by_company: dict[
        str,
        dict[str, Any],
    ] = {}

    for result in sorted(
        verified,
        key=lambda item: (
            item["method"]
            == "direct_url",
            item["jobs_found"],
        ),
        reverse=True,
    ):
        company_key = canonical_company(
            result["company"]
        )

        best_by_company.setdefault(
            company_key,
            result,
        )

    selected = list(
        best_by_company.values()
    )

    connection = sqlite3.connect(
        "data/hunter.db"
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        inserted_or_updated = 0

        for board in selected:
            before_changes = (
                connection.total_changes
            )

            connection.execute(
                """
                INSERT INTO
                    ats_company_registry (
                        company_name,
                        ats_type,
                        board_token,
                        careers_url,
                        enabled,
                        priority_weight,
                        notes,
                        last_success_at,
                        last_run_at,
                        consecutive_failures,
                        jobs_found_last_run,
                        health_status,
                        last_error
                    )
                VALUES (
                    ?,
                    'smartrecruiters',
                    ?,
                    ?,
                    1,
                    80,
                    ?,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    0,
                    ?,
                    'healthy',
                    NULL
                )
                ON CONFLICT(company_name)
                DO UPDATE SET
                    ats_type =
                        excluded.ats_type,
                    board_token =
                        excluded.board_token,
                    careers_url =
                        excluded.careers_url,
                    enabled = 1,
                    priority_weight =
                        excluded.priority_weight,
                    notes =
                        excluded.notes,
                    last_success_at =
                        CURRENT_TIMESTAMP,
                    last_run_at =
                        CURRENT_TIMESTAMP,
                    consecutive_failures = 0,
                    jobs_found_last_run =
                        excluded.jobs_found_last_run,
                    health_status = 'healthy',
                    last_error = NULL,
                    updated_at =
                        CURRENT_TIMESTAMP
                WHERE lower(
                    ats_company_registry.ats_type
                ) IN (
                    'unknown',
                    'smartrecruiters'
                )
                """,
                (
                    board["company"],
                    board["token"],
                    (
                        "https://careers."
                        "smartrecruiters.com/"
                        + board["token"]
                    ),
                    (
                        "Identity-verified "
                        "SmartRecruiters board; "
                        f"method={board['method']}"
                    ),
                    int(
                        board["jobs_found"]
                    ),
                ),
            )

            if (
                connection.total_changes
                > before_changes
            ):
                inserted_or_updated += 1

        connection.execute(
            """
            UPDATE source_health
            SET
                enabled = 1,
                cadence_minutes =
                    CASE
                        WHEN cadence_minutes < 120
                        THEN 120
                        ELSE cadence_minutes
                    END,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE source_name = ?
            """,
            (SOURCE_NAME,),
        )

        payload = {
            "success": True,
            "candidate_probes": len(
                candidates
            ),
            "verified_boards": len(
                selected
            ),
            "boards_inserted_or_updated": (
                inserted_or_updated
            ),
            "job_database_writes": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
            "boards": selected,
        }

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
                NULL,
                'smartrecruiters_board_discovery',
                'terminal_patch',
                'completed',
                ?
            )
            """,
            (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    print(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
