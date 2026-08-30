from __future__ import annotations

import argparse
import json
import re
import unicodedata
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote, urlparse

import requests

from app.database import (
    get_connection,
    get_setting,
)
from app.discovery_config import (
    load_target_roles,
)
from app.relevance import (
    match_target_role,
)


SOURCE_NAMES = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
}

SOURCE_KIND_PRIORITY = {
    "direct_url": 3,
    "catalog": 2,
    "dashboard_company": 1,
}


CATALOG: list[dict[str, Any]] = [
    {
        "company": "OpenAI",
        "ats": "ashby",
        "tokens": ["OpenAI", "openai"],
    },
    {
        "company": "Anthropic",
        "ats": "ashby",
        "tokens": ["Anthropic", "anthropic"],
    },
    {
        "company": "Ramp",
        "ats": "ashby",
        "tokens": ["Ramp", "ramp"],
    },
    {
        "company": "Notion",
        "ats": "ashby",
        "tokens": ["Notion", "notion"],
    },
    {
        "company": "Linear",
        "ats": "ashby",
        "tokens": ["Linear", "linear"],
    },
    {
        "company": "Cursor",
        "ats": "ashby",
        "tokens": ["Cursor", "cursor"],
    },
    {
        "company": "Deel",
        "ats": "ashby",
        "tokens": ["Deel", "deel"],
    },
    {
        "company": "Vanta",
        "ats": "ashby",
        "tokens": ["Vanta", "vanta"],
    },
    {
        "company": "Harvey",
        "ats": "ashby",
        "tokens": ["Harvey", "harvey"],
    },
    {
        "company": "Perplexity",
        "ats": "ashby",
        "tokens": ["Perplexity", "perplexity"],
    },
    {
        "company": "Replit",
        "ats": "ashby",
        "tokens": ["Replit", "replit"],
    },
    {
        "company": "Retool",
        "ats": "ashby",
        "tokens": ["Retool", "retool"],
    },
    {
        "company": "Clay",
        "ats": "ashby",
        "tokens": ["Clay", "clay"],
    },
    {
        "company": "Mercury",
        "ats": "ashby",
        "tokens": ["Mercury", "mercury"],
    },
    {
        "company": "Hightouch",
        "ats": "ashby",
        "tokens": ["Hightouch", "hightouch"],
    },
    {
        "company": "ElevenLabs",
        "ats": "ashby",
        "tokens": ["ElevenLabs", "elevenlabs"],
    },
    {
        "company": "Hex",
        "ats": "ashby",
        "tokens": ["Hex", "hex"],
    },
    {
        "company": "Modal",
        "ats": "ashby",
        "tokens": ["Modal", "modal"],
    },
    {
        "company": "Stripe",
        "ats": "greenhouse",
        "tokens": ["stripe"],
    },
    {
        "company": "Datadog",
        "ats": "greenhouse",
        "tokens": ["datadog"],
    },
    {
        "company": "Cloudflare",
        "ats": "greenhouse",
        "tokens": ["cloudflare"],
    },
    {
        "company": "Reddit",
        "ats": "greenhouse",
        "tokens": ["reddit"],
    },
    {
        "company": "Figma",
        "ats": "greenhouse",
        "tokens": ["figma"],
    },
    {
        "company": "Coinbase",
        "ats": "greenhouse",
        "tokens": ["coinbase"],
    },
    {
        "company": "Robinhood",
        "ats": "greenhouse",
        "tokens": ["robinhood"],
    },
    {
        "company": "MongoDB",
        "ats": "greenhouse",
        "tokens": ["mongodb"],
    },
    {
        "company": "Samsara",
        "ats": "greenhouse",
        "tokens": ["samsara"],
    },
    {
        "company": "Gusto",
        "ats": "greenhouse",
        "tokens": ["gusto"],
    },
    {
        "company": "DoorDash",
        "ats": "greenhouse",
        "tokens": [
            "doordash",
            "doordashusa",
        ],
    },
    {
        "company": "Instacart",
        "ats": "greenhouse",
        "tokens": ["instacart"],
    },
    {
        "company": "Duolingo",
        "ats": "greenhouse",
        "tokens": ["duolingo"],
    },
    {
        "company": "Pinterest",
        "ats": "greenhouse",
        "tokens": ["pinterest"],
    },
    {
        "company": "Braze",
        "ats": "greenhouse",
        "tokens": ["braze"],
    },
    {
        "company": "Klaviyo",
        "ats": "greenhouse",
        "tokens": ["klaviyo"],
    },
    {
        "company": "Databricks",
        "ats": "greenhouse",
        "tokens": ["databricks"],
    },
    {
        "company": "Oscar Health",
        "ats": "greenhouse",
        "tokens": [
            "oscar",
            "oscarhealth",
        ],
    },
    {
        "company": "Flatiron Health",
        "ats": "greenhouse",
        "tokens": ["flatironhealth"],
    },
    {
        "company": "Plaid",
        "ats": "lever",
        "tokens": ["plaid"],
    },
    {
        "company": "Brex",
        "ats": "lever",
        "tokens": ["brex"],
    },
    {
        "company": "Rippling",
        "ats": "lever",
        "tokens": ["rippling"],
    },
    {
        "company": "Webflow",
        "ats": "lever",
        "tokens": ["webflow"],
    },
    {
        "company": "Flexport",
        "ats": "lever",
        "tokens": ["flexport"],
    },
    {
        "company": "Chime",
        "ats": "lever",
        "tokens": ["chime"],
    },
    {
        "company": "Discord",
        "ats": "lever",
        "tokens": ["discord"],
    },
    {
        "company": "Asana",
        "ats": "lever",
        "tokens": ["asana"],
    },
    {
        "company": "GitLab",
        "ats": "lever",
        "tokens": ["gitlab"],
    },
    {
        "company": "Carta",
        "ats": "lever",
        "tokens": ["carta"],
    },
    {
        "company": "Toast",
        "ats": "lever",
        "tokens": ["toast"],
    },
    {
        "company": "Spring Health",
        "ats": "lever",
        "tokens": ["springhealth"],
    },
    {
        "company": "Maven Clinic",
        "ats": "lever",
        "tokens": ["mavenclinic"],
    },
    {
        "company": "Zocdoc",
        "ats": "lever",
        "tokens": ["zocdoc"],
    },
    {
        "company": "Ro",
        "ats": "lever",
        "tokens": ["ro"],
    },
    {
        "company": "Included Health",
        "ats": "lever",
        "tokens": ["includedhealth"],
    },
    {
        "company": "Headspace",
        "ats": "lever",
        "tokens": ["headspace"],
    },
]


def clean_company_key(value: Any) -> str:
    text = unicodedata.normalize(
        "NFKD",
        str(value or ""),
    )

    text = "".join(
        character
        for character in text
        if not unicodedata.combining(
            character
        )
    )

    return re.sub(
        r"[^a-z0-9]",
        "",
        text.casefold(),
    )


def slug_variants(
    company_name: str,
) -> list[str]:
    text = unicodedata.normalize(
        "NFKD",
        company_name,
    )

    text = "".join(
        character
        for character in text
        if not unicodedata.combining(
            character
        )
    )

    words = re.findall(
        r"[a-z0-9]+",
        text.casefold(),
    )

    removable_suffixes = {
        "inc",
        "incorporated",
        "llc",
        "corp",
        "corporation",
        "company",
        "co",
        "group",
        "holdings",
        "plc",
        "ltd",
        "limited",
        "usa",
    }

    while (
        words
        and words[-1]
        in removable_suffixes
    ):
        words.pop()

    if not words:
        return []

    variants = [
        "".join(words),
        "-".join(words),
    ]

    if len(words) > 1:
        variants.append(words[0])

    output: list[str] = []
    seen: set[str] = set()

    for variant in variants:
        cleaned = variant.strip("-")

        if (
            not cleaned
            or cleaned in seen
            or len(cleaned) < 2
        ):
            continue

        seen.add(cleaned)
        output.append(cleaned)

    return output[:3]


def candidate_from_url(
    company_name: str,
    value: Any,
) -> dict[str, Any] | None:
    text = str(value or "").strip()

    if not text:
        return None

    try:
        parsed = urlparse(text)
    except ValueError:
        return None

    host = parsed.netloc.casefold()
    pieces = [
        unquote(piece)
        for piece in parsed.path.split("/")
        if piece
    ]

    if not pieces:
        return None

    if host in {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
    }:
        ats = "greenhouse"

    elif host in {
        "jobs.lever.co",
        "jobs.eu.lever.co",
    }:
        ats = "lever"

    elif host == "jobs.ashbyhq.com":
        ats = "ashby"

    else:
        return None

    return {
        "company": company_name,
        "ats": ats,
        "token": pieces[0],
        "source_kind": "direct_url",
    }


def load_direct_url_candidates() -> list[
    dict[str, Any]
]:
    connection = get_connection()
    candidates: list[dict[str, Any]] = []

    try:
        rows = connection.execute(
            """
            SELECT
                company_name,
                careers_url
            FROM ats_company_registry
            WHERE trim(
                COALESCE(careers_url, '')
            ) != ''
            """
        ).fetchall()

        for row in rows:
            candidate = candidate_from_url(
                row["company_name"],
                row["careers_url"],
            )

            if candidate:
                candidates.append(candidate)

        job_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(jobs)"
            ).fetchall()
        }

        url_columns = [
            column
            for column in (
                "job_url",
                "apply_url",
            )
            if column in job_columns
        ]

        if url_columns:
            query = (
                "SELECT company_name, "
                + ", ".join(url_columns)
                + " FROM jobs"
            )

            rows = connection.execute(
                query
            ).fetchall()

            for row in rows:
                for column in url_columns:
                    candidate = (
                        candidate_from_url(
                            row["company_name"],
                            row[column],
                        )
                    )

                    if candidate:
                        candidates.append(
                            candidate
                        )

    finally:
        connection.close()

    return candidates


def dashboard_company_names() -> list[str]:
    targeting = get_setting(
        "targeting",
        {},
    ) or {}

    names = list(
        targeting.get(
            "company_watchlist"
        )
        or []
    )

    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT company_name
            FROM ats_company_registry
            WHERE
                company_name NOT LIKE
                    '%Adapter Test%'
                AND company_name NOT LIKE
                    'Temporary %'
            """
        ).fetchall()
    finally:
        connection.close()

    names.extend(
        row["company_name"]
        for row in rows
    )

    output: list[str] = []
    seen: set[str] = set()

    for value in names:
        name = str(value or "").strip()
        key = clean_company_key(name)

        if not name or not key or key in seen:
            continue

        seen.add(key)
        output.append(name)

    return output


def build_candidates(
    max_probes: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    candidates.extend(
        load_direct_url_candidates()
    )

    for entry in CATALOG:
        for token in entry["tokens"]:
            candidates.append(
                {
                    "company": entry[
                        "company"
                    ],
                    "ats": entry["ats"],
                    "token": token,
                    "source_kind": (
                        "catalog"
                    ),
                }
            )

    for company_name in (
        dashboard_company_names()
    ):
        variants = slug_variants(
            company_name
        )

        for ats in SOURCE_NAMES:
            for token in variants[:2]:
                candidates.append(
                    {
                        "company": (
                            company_name
                        ),
                        "ats": ats,
                        "token": token,
                        "source_kind": (
                            "dashboard_company"
                        ),
                    }
                )

    deduplicated: dict[
        tuple[str, str, str],
        dict[str, Any],
    ] = {}

    for candidate in candidates:
        key = (
            clean_company_key(
                candidate["company"]
            ),
            candidate["ats"],
            candidate["token"].casefold(),
        )

        current = deduplicated.get(key)

        if (
            current is None
            or SOURCE_KIND_PRIORITY[
                candidate["source_kind"]
            ]
            > SOURCE_KIND_PRIORITY[
                current["source_kind"]
            ]
        ):
            deduplicated[key] = candidate

    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (
            -SOURCE_KIND_PRIORITY[
                item["source_kind"]
            ],
            item["company"].casefold(),
            item["ats"],
            item["token"].casefold(),
        ),
    )

    return ordered[
        : max(1, max_probes)
    ]


def count_target_hits(
    titles: list[str],
    target_roles: list[str],
) -> tuple[int, list[str]]:
    matched_titles: list[str] = []

    for title in titles:
        matched, _, _ = match_target_role(
            title,
            target_roles,
        )

        if matched:
            matched_titles.append(title)

    return (
        len(matched_titles),
        matched_titles[:10],
    )


def probe_candidate(
    candidate: dict[str, Any],
    target_roles: list[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    company = candidate["company"]
    ats = candidate["ats"]
    token = candidate["token"]

    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Aadil-HR-Hunter/1.0"
        ),
    }

    result = {
        **candidate,
        "valid": False,
        "http_status": None,
        "jobs_found": 0,
        "target_role_hits": 0,
        "matching_titles": [],
        "sample_titles": [],
        "api_instance": None,
        "careers_url": None,
        "error": None,
    }

    try:
        if ats == "greenhouse":
            endpoint = (
                "https://boards-api.greenhouse.io/"
                "v1/boards/"
                f"{quote(token, safe='')}/jobs"
            )

            response = requests.get(
                endpoint,
                timeout=timeout_seconds,
                headers=headers,
            )

            result["http_status"] = (
                response.status_code
            )

            response.raise_for_status()
            payload = response.json()

            jobs = payload.get("jobs")

            if not isinstance(jobs, list):
                raise ValueError(
                    "Invalid Greenhouse jobs payload."
                )

            titles = [
                str(job.get("title") or "")
                for job in jobs
                if isinstance(job, dict)
            ]

            result["careers_url"] = (
                "https://job-boards.greenhouse.io/"
                f"{token}"
            )

        elif ats == "ashby":
            endpoint = (
                "https://api.ashbyhq.com/"
                "posting-api/job-board/"
                f"{quote(token, safe='')}"
            )

            response = requests.get(
                endpoint,
                params={
                    "includeCompensation": (
                        "false"
                    )
                },
                timeout=timeout_seconds,
                headers=headers,
            )

            result["http_status"] = (
                response.status_code
            )

            response.raise_for_status()
            payload = response.json()

            jobs = payload.get("jobs")

            if not isinstance(jobs, list):
                raise ValueError(
                    "Invalid Ashby jobs payload."
                )

            titles = [
                str(job.get("title") or "")
                for job in jobs
                if isinstance(job, dict)
                and job.get("isListed") is not False
            ]

            result["careers_url"] = (
                "https://jobs.ashbyhq.com/"
                f"{token}"
            )

        elif ats == "lever":
            jobs = None
            titles: list[str] = []

            instances = [
                (
                    "global",
                    "https://api.lever.co",
                    "https://jobs.lever.co",
                ),
                (
                    "eu",
                    "https://api.eu.lever.co",
                    "https://jobs.eu.lever.co",
                ),
            ]

            last_error: Exception | None = None

            for (
                instance_name,
                api_root,
                careers_root,
            ) in instances:
                try:
                    endpoint = (
                        f"{api_root}/v0/postings/"
                        f"{quote(token, safe='')}"
                    )

                    response = requests.get(
                        endpoint,
                        params={
                            "mode": "json",
                            "limit": 100,
                        },
                        timeout=timeout_seconds,
                        headers=headers,
                    )

                    result["http_status"] = (
                        response.status_code
                    )

                    response.raise_for_status()
                    payload = response.json()

                    if not isinstance(
                        payload,
                        list,
                    ):
                        raise ValueError(
                            "Invalid Lever jobs payload."
                        )

                    jobs = payload
                    titles = [
                        str(
                            job.get("text")
                            or job.get("title")
                            or ""
                        )
                        for job in jobs
                        if isinstance(
                            job,
                            dict,
                        )
                    ]

                    result["api_instance"] = (
                        instance_name
                    )

                    result["careers_url"] = (
                        f"{careers_root}/{token}"
                    )

                    break

                except Exception as error:
                    last_error = error

            if jobs is None:
                raise (
                    last_error
                    or RuntimeError(
                        "Lever probe failed."
                    )
                )

        else:
            raise ValueError(
                f"Unsupported ATS: {ats}"
            )

        target_hit_count, matched_titles = (
            count_target_hits(
                titles,
                target_roles,
            )
        )

        result.update(
            {
                "valid": True,
                "jobs_found": len(titles),
                "target_role_hits": (
                    target_hit_count
                ),
                "matching_titles": (
                    matched_titles
                ),
                "sample_titles": (
                    titles[:5]
                ),
            }
        )

    except Exception as error:
        result["error"] = str(error)[:500]

    return result


def select_verified_boards(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    valid = [
        item
        for item in results
        if item["valid"]
        and (
            item["jobs_found"] > 0
            or item["source_kind"]
            == "direct_url"
        )
    ]

    selected: dict[
        str,
        dict[str, Any],
    ] = {}

    for item in sorted(
        valid,
        key=lambda value: (
            value["target_role_hits"],
            SOURCE_KIND_PRIORITY[
                value["source_kind"]
            ],
            value["jobs_found"],
        ),
        reverse=True,
    ):
        company_key = clean_company_key(
            item["company"]
        )

        if company_key not in selected:
            selected[company_key] = item

    return sorted(
        selected.values(),
        key=lambda item: (
            -item["target_role_hits"],
            item["company"].casefold(),
        ),
    )


def apply_verified_boards(
    boards: list[dict[str, Any]],
) -> dict[str, Any]:
    connection = get_connection()

    source_counts = {
        source_name: 0
        for source_name in SOURCE_NAMES.values()
    }

    try:
        connection.execute("BEGIN")

        connection.execute(
            """
            UPDATE ats_company_registry
            SET
                enabled = 0,
                notes = CASE
                    WHEN notes IS NULL
                    THEN 'Validation board disabled'
                    ELSE notes
                        || '; validation board disabled'
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE
                company_name LIKE
                    '%Adapter Test%'
                OR notes LIKE
                    'Temporary %'
            """
        )

        for board in boards:
            source_name = SOURCE_NAMES[
                board["ats"]
            ]

            source_counts[source_name] += 1

            base_priority = {
                "direct_url": 110,
                "dashboard_company": 100,
                "catalog": 70,
            }[board["source_kind"]]

            priority = (
                base_priority
                + min(
                    int(
                        board[
                            "target_role_hits"
                        ]
                    ),
                    20,
                )
            )

            notes = (
                "Auto-verified public "
                f"{source_name} board; "
                f"jobs={board['jobs_found']}; "
                "matching dashboard titles="
                f"{board['target_role_hits']}; "
                "discovery method="
                f"{board['source_kind']}"
            )

            connection.execute(
                """
                INSERT INTO ats_company_registry (
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
                    ?,
                    ?,
                    ?,
                    1,
                    ?,
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
                    notes = excluded.notes,
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
                """,
                (
                    board["company"],
                    board["ats"],
                    board["token"],
                    board["careers_url"],
                    priority,
                    notes,
                    board["jobs_found"],
                ),
            )

        for source_name in (
            SOURCE_NAMES.values()
        ):
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM ats_company_registry
                WHERE
                    enabled = 1
                    AND lower(ats_type) =
                        lower(?)
                    AND company_name NOT LIKE
                        '%Adapter Test%'
                """,
                (source_name,),
            ).fetchone()

            verified_count = int(
                row["count_value"]
            )

            source_counts[source_name] = (
                verified_count
            )

            # AADIL_SOURCE_ENABLEMENT_AUTHORITY_V16
            # ATS board discovery records evidence in its registries only.
            # It must never enable/disable a source or alter owner cadence.

        event_payload = {
            "verified_boards": len(
                boards
            ),
            "source_counts": (
                source_counts
            ),
            "telegram_messages": 0,
            "n8n_calls": 0,
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
                'ats_board_discovery_completed',
                'ats_board_discovery',
                'completed',
                ?
            )
            """,
            (
                json.dumps(
                    event_payload,
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

    return {
        "boards_upserted": len(boards),
        "source_counts": source_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=8,
    )

    parser.add_argument(
        "--max-probes",
        type=int,
        default=240,
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=12,
    )

    args = parser.parse_args()

    target_roles = load_target_roles()

    candidates = build_candidates(
        max_probes=max(
            1,
            min(
                args.max_probes,
                500,
            ),
        )
    )

    results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(
        max_workers=max(
            1,
            min(args.workers, 12),
        )
    ) as executor:
        future_map = {
            executor.submit(
                probe_candidate,
                candidate,
                target_roles,
                max(
                    5,
                    min(args.timeout, 30),
                ),
            ): candidate
            for candidate in candidates
        }

        for future in as_completed(
            future_map
        ):
            try:
                results.append(
                    future.result()
                )
            except Exception as error:
                candidate = future_map[
                    future
                ]

                results.append(
                    {
                        **candidate,
                        "valid": False,
                        "jobs_found": 0,
                        "target_role_hits": 0,
                        "error": str(error),
                    }
                )

    verified = select_verified_boards(
        results
    )

    apply_result = {
        "boards_upserted": 0,
        "source_counts": {},
    }

    if args.apply:
        apply_result = (
            apply_verified_boards(
                verified
            )
        )

    output = {
        "success": True,
        "mode": (
            "verified-ats-board-discovery"
        ),
        "completed_at": (
            datetime.now(timezone.utc)
            .isoformat()
        ),
        "apply_mode": bool(args.apply),
        "target_role_count": len(
            target_roles
        ),
        "candidate_probes": len(
            candidates
        ),
        "successful_public_endpoints": (
            sum(
                1
                for item in results
                if item.get("valid")
            )
        ),
        "verified_live_boards": len(
            verified
        ),
        "boards_with_current_target_hits": (
            sum(
                1
                for item in verified
                if item[
                    "target_role_hits"
                ] > 0
            )
        ),
        "database_boards_upserted": (
            apply_result[
                "boards_upserted"
            ]
        ),
        "source_counts": (
            apply_result[
                "source_counts"
            ]
        ),
        "verified_boards": verified,
        "failed_probe_count": sum(
            1
            for item in results
            if not item.get("valid")
        ),
        "network_requests_made": len(
            candidates
        ),
        "job_database_writes": 0,
        "telegram_messages": 0,
        "n8n_calls": 0,
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
