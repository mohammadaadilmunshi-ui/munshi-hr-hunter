from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st


MARKER = "AADIL_DASHBOARD_JOB_BOARDS_EASY_MODE_V2_7_4"
ROOT_DIR = Path(
    os.environ.get(
        "AADIL_HR_HUNTER_PROJECT",
        str(Path(__file__).resolve().parent.parent),
    )
).expanduser().resolve()
DB_PATH = ROOT_DIR / "data/hunter.db"
EASTERN = ZoneInfo("America/New_York")
PROVIDERS = ("Personio", "Pinpoint", "Comeet", "Recruitee")
PROVIDER_ORDER = {
    provider: index
    for index, provider in enumerate(PROVIDERS)
}


def inject_dashboard_readability_css() -> None:
    st.markdown(
        """
        <style>
        /* AADIL_DASHBOARD_JOB_BOARDS_EASY_CSS_V2_7_4 */

        [data-testid="stMetric"] {
            min-height: 138px !important;
            height: auto !important;
            padding: 1rem !important;
            overflow: visible !important;
        }

        [data-testid="stMetricLabel"],
        [data-testid="stMetricLabel"] > div,
        [data-testid="stMetricLabel"] p {
            min-height: 2.4rem !important;
            height: auto !important;
            overflow: visible !important;
            text-overflow: clip !important;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
            line-height: 1.22 !important;
            font-size: 0.98rem !important;
        }

        [data-testid="stMetricValue"] {
            overflow: visible !important;
            white-space: normal !important;
            line-height: 1.05 !important;
            font-size: clamp(1.7rem, 2.15vw, 2.45rem) !important;
        }

        [data-testid="stMetricDelta"],
        [data-testid="stMetricDelta"] > div {
            max-width: 100% !important;
            overflow: visible !important;
            text-overflow: clip !important;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
            line-height: 1.2 !important;
        }

        div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) {
            flex-wrap: wrap !important;
            align-items: stretch !important;
            gap: 0.9rem !important;
        }

        div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"])
        > div[data-testid="column"] {
            min-width: 220px !important;
            flex: 1 1 220px !important;
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 0.55rem !important;
            overflow-x: auto !important;
            scrollbar-width: thin !important;
        }

        .stTabs [data-baseweb="tab"] {
            flex: 0 0 auto !important;
            padding-left: 0.9rem !important;
            padding-right: 0.9rem !important;
        }

        .stTabs [data-baseweb="tab"] p {
            white-space: nowrap !important;
        }

        [data-testid="stDataFrame"] {
            width: 100% !important;
        }

        .aadil-easy-note,
        .aadil-legend,
        .aadil-flow-card,
        .aadil-provider-card {
            border: 1px solid rgba(148, 163, 184, 0.28);
            border-radius: 16px;
            background: rgba(30, 41, 59, 0.34);
        }

        .aadil-easy-note {
            padding: 1rem 1.1rem;
            margin: 0.25rem 0 1rem 0;
            line-height: 1.5;
        }

        .aadil-legend {
            padding: 0.85rem 1rem;
            margin: 0.45rem 0 1rem 0;
            line-height: 1.65;
        }

        .aadil-flow-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(165px, 1fr));
            gap: 0.75rem;
            margin: 0.5rem 0 1.15rem 0;
        }

        .aadil-flow-card {
            padding: 0.9rem;
            min-height: 124px;
        }

        .aadil-flow-number {
            font-size: 1.35rem;
            font-weight: 800;
            margin-bottom: 0.25rem;
        }

        .aadil-flow-title {
            font-size: 1rem;
            font-weight: 750;
            margin-bottom: 0.2rem;
        }

        .aadil-flow-text {
            color: rgba(226, 232, 240, 0.82);
            line-height: 1.4;
            font-size: 0.92rem;
        }

        .aadil-provider-card {
            padding: 1rem;
            min-height: 238px;
            margin-bottom: 0.75rem;
        }

        .aadil-provider-title {
            font-size: 1.15rem;
            font-weight: 800;
            margin-bottom: 0.35rem;
        }

        .aadil-provider-state {
            font-weight: 750;
            margin-bottom: 0.65rem;
        }

        .aadil-provider-line {
            margin: 0.18rem 0;
            line-height: 1.35;
        }

        .aadil-small-muted {
            color: rgba(226, 232, 240, 0.72);
            font-size: 0.88rem;
        }

        @media (max-width: 1050px) {
            .aadil-flow-grid {
                grid-template-columns: repeat(2, minmax(180px, 1fr));
            }
        }

        @media (max-width: 700px) {
            .aadil-flow-grid {
                grid-template-columns: 1fr;
            }

            div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"])
            > div[data-testid="column"] {
                min-width: min(100%, 250px) !important;
                flex-basis: 250px !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro",
        uri=True,
        timeout=20,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    return (
        connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table' AND name=?
            LIMIT 1
            """,
            (table_name,),
        ).fetchone()
        is not None
    )


def _columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()

    return {
        str(row[1])
        for row in connection.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    }


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None

    text = str(value).strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None

    if parsed is None:
        for pattern in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue

    if parsed is None:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(EASTERN)


def _time_text(value: Any) -> str:
    parsed = _parse_time(value)

    if parsed is None:
        return "Not recorded"

    return parsed.strftime("%b %-d, %Y · %-I:%M %p ET")


def _safe_url(value: Any) -> str:
    raw = str(value or "").strip()

    if not raw:
        return ""

    try:
        parsed = urlsplit(raw)
        return urlunsplit(
            (
                parsed.scheme or "https",
                parsed.netloc,
                parsed.path or "/",
                "",
                "",
            )
        )
    except Exception:
        return raw.split("?", 1)[0]


def _registered_counts(
    connection: sqlite3.Connection,
) -> dict[str, int]:
    counts = {
        provider: 0
        for provider in PROVIDERS
    }

    if _table_exists(connection, "public_adapter_boards"):
        columns = _columns(
            connection,
            "public_adapter_boards",
        )
        enabled_filter = (
            "WHERE COALESCE(enabled,1)=1"
            if "enabled" in columns
            else ""
        )
        rows = connection.execute(
            f"""
            SELECT
                lower(source_name) AS source_name,
                COUNT(*) AS count_value
            FROM public_adapter_boards
            {enabled_filter}
            GROUP BY lower(source_name)
            """
        ).fetchall()

        for row in rows:
            source_name = str(
                row["source_name"] or ""
            )

            for provider in (
                "Personio",
                "Pinpoint",
                "Comeet",
            ):
                if provider.casefold() in source_name:
                    counts[provider] += int(
                        row["count_value"] or 0
                    )

    if _table_exists(connection, "market_public_boards"):
        columns = _columns(
            connection,
            "market_public_boards",
        )
        conditions = [
            "lower(COALESCE(provider,''))='recruitee'"
        ]

        if "enabled" in columns:
            conditions.append(
                "COALESCE(enabled,1)=1"
            )

        counts["Recruitee"] = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM market_public_boards
                WHERE {" AND ".join(conditions)}
                """
            ).fetchone()[0]
        )

    return counts


def _runtime_urls(
    connection: sqlite3.Connection,
) -> set[str]:
    urls: set[str] = set()

    for table_name in (
        "public_adapter_boards",
        "market_public_boards",
    ):
        if not _table_exists(
            connection,
            table_name,
        ):
            continue

        columns = _columns(
            connection,
            table_name,
        )
        enabled_filter = (
            "WHERE COALESCE(enabled,1)=1"
            if "enabled" in columns
            else ""
        )

        urls.update(
            str(row[0] or "").casefold()
            for row in connection.execute(
                f"""
                SELECT board_url
                FROM {table_name}
                {enabled_filter}
                """
            ).fetchall()
        )

    return urls


def _source_map(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    if not _table_exists(connection, "source_health"):
        return {}

    columns = _columns(connection, "source_health")
    desired = [
        "source_name",
        "enabled",
        "cadence_minutes",
        "health_status",
        "raw_jobs_last_run",
        "jobs_found_last_run",
        "eligible_jobs_last_run",
        "inserted_jobs_last_run",
        "duplicate_jobs_last_run",
        "rejected_jobs_last_run",
        "consecutive_failures",
        "last_run_at",
        "last_success_at",
        "last_error",
        "provider_used_last_run",
    ]
    selected = [
        column
        for column in desired
        if column in columns
    ]

    if "source_name" not in selected:
        return {}

    result: dict[str, dict[str, Any]] = {}

    for row in connection.execute(
        f"""
        SELECT {", ".join(selected)}
        FROM source_health
        """
    ).fetchall():
        data = dict(row)
        source_name = str(
            data.get("source_name") or ""
        ).casefold()

        for provider in PROVIDERS:
            if provider.casefold() in source_name:
                result[provider] = data
                break

    return result


def _schedule_map(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    if not _table_exists(
        connection,
        "source_random_schedule",
    ):
        return {}

    columns = _columns(
        connection,
        "source_random_schedule",
    )
    desired = [
        "source_name",
        "next_run_at",
        "schedule_state",
        "base_cadence_minutes",
    ]
    selected = [
        column
        for column in desired
        if column in columns
    ]

    if "source_name" not in selected:
        return {}

    result: dict[str, dict[str, Any]] = {}

    for row in connection.execute(
        f"""
        SELECT {", ".join(selected)}
        FROM source_random_schedule
        """
    ).fetchall():
        data = dict(row)
        source_name = str(
            data.get("source_name") or ""
        ).casefold()

        for provider in PROVIDERS:
            if provider.casefold() in source_name:
                result[provider] = data
                break

    return result


def _provider_plain_state(
    technical_status: str,
    raw_jobs: int,
    last_run_value: Any,
    failures: int,
    latest_error: str,
) -> tuple[str, str]:
    status = str(
        technical_status or ""
    ).casefold()

    if failures > 0 or status in {
        "failed",
        "unhealthy",
        "degraded",
        "runtime_failure",
        "blocked",
    }:
        return (
            "❌",
            "Needs attention",
        )

    if raw_jobs > 0 and _parse_time(last_run_value):
        return (
            "✅",
            "Job finder ran successfully",
        )

    if status == "healthy":
        return (
            "✅",
            "Ready and healthy",
        )

    if status in {
        "enabled_pending_first_run",
        "pending_first_run",
        "installed_pending_first_run",
        "not_tested",
    }:
        return (
            "⏳",
            "Waiting for its first completed scan",
        )

    if latest_error.strip():
        return (
            "⚠️",
            "Finished with a warning",
        )

    return (
        "⚠️",
        "Status needs review",
    )


def _board_plain_state(
    technical_status: str,
    runtime_registered: bool,
) -> tuple[str, str, int]:
    status = str(
        technical_status or ""
    ).casefold()

    if status == "valid" and runtime_registered:
        return (
            "✅",
            "Working and connected",
            0,
        )

    if status == "valid":
        return (
            "🟦",
            "Checked, but not connected",
            1,
        )

    if status in {
        "pending",
        "retry",
    }:
        return (
            "⏳",
            "Waiting to be checked",
            2,
        )

    if status == "unresolved":
        return (
            "⚠️",
            "Needs a closer look",
            3,
        )

    if status == "invalid":
        return (
            "❌",
            "Broken or unavailable",
            4,
        )

    return (
        "▫️",
        "Unknown state",
        5,
    )


@st.cache_data(
    ttl=15,
    show_spinner=False,
)
def load_data() -> dict[str, Any]:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Hunter database not found: {DB_PATH}"
        )

    connection = _connect()

    try:
        integrity = str(
            connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
        )
        registered = _registered_counts(
            connection
        )
        runtime_urls = _runtime_urls(
            connection
        )
        source_health = _source_map(
            connection
        )
        schedules = _schedule_map(
            connection
        )

        raw_board_rows: list[
            dict[str, Any]
        ] = []

        if _table_exists(
            connection,
            "employer_board_discovery_candidates",
        ):
            columns = _columns(
                connection,
                "employer_board_discovery_candidates",
            )
            desired = [
                "id",
                "provider",
                "company_name",
                "board_locator",
                "board_url",
                "validation_status",
                "validation_error",
                "last_http_status",
                "visible_jobs",
                "enabled",
                "discovery_source",
                "first_seen_at",
                "last_seen_at",
                "last_validated_at",
            ]
            selected = [
                column
                for column in desired
                if column in columns
            ]

            for row in connection.execute(
                f"""
                SELECT {", ".join(selected)}
                FROM employer_board_discovery_candidates
                WHERE lower(
                    COALESCE(provider,'')
                ) IN (
                    'personio',
                    'pinpoint',
                    'comeet',
                    'recruitee'
                )
                """
            ).fetchall():
                data = dict(row)
                provider = str(
                    data.get("provider") or ""
                ).title()
                technical_status = str(
                    data.get(
                        "validation_status"
                    )
                    or "pending"
                )
                raw_endpoint = str(
                    data.get("board_url") or ""
                )
                endpoint = _safe_url(
                    raw_endpoint
                )
                runtime_registered = (
                    raw_endpoint.casefold()
                    in runtime_urls
                )
                state_icon, plain_state, sort_rank = (
                    _board_plain_state(
                        technical_status,
                        runtime_registered,
                    )
                )

                raw_board_rows.append(
                    {
                        "Candidate ID": data.get("id"),
                        "State": state_icon,
                        "Plain state": plain_state,
                        "Provider": provider,
                        "Employer": str(
                            data.get("company_name")
                            or data.get(
                                "board_locator"
                            )
                            or "Unknown employer"
                        ),
                        "Connected to job finder?": (
                            "Yes"
                            if runtime_registered
                            else "No"
                        ),
                        "Jobs seen": int(
                            data.get(
                                "visible_jobs"
                            )
                            or 0
                        ),
                        "Last checked": _time_text(
                            data.get(
                                "last_validated_at"
                            )
                        ),
                        "Keep checking it?": (
                            "Yes"
                            if bool(
                                int(
                                    data.get(
                                        "enabled"
                                    )
                                    or 0
                                )
                            )
                            else "No"
                        ),
                        "HTTP": data.get(
                            "last_http_status"
                        ),
                        "Board link": endpoint,
                        "What went wrong": str(
                            data.get(
                                "validation_error"
                            )
                            or ""
                        ),
                        "Technical status": (
                            technical_status
                        ),
                        "Board locator": str(
                            data.get(
                                "board_locator"
                            )
                            or ""
                        ),
                        "Discovery source": str(
                            data.get(
                                "discovery_source"
                            )
                            or ""
                        ),
                        "First seen": _time_text(
                            data.get(
                                "first_seen_at"
                            )
                        ),
                        "Last seen": _time_text(
                            data.get(
                                "last_seen_at"
                            )
                        ),
                        "_Sort rank": sort_rank,
                        "_Provider order": (
                            PROVIDER_ORDER.get(
                                provider,
                                99,
                            )
                        ),
                    }
                )

        raw_board_df = pd.DataFrame(
            raw_board_rows
        )

        raw_columns = [
            "Candidate ID",
            "State",
            "Plain state",
            "Provider",
            "Employer",
            "Connected to job finder?",
            "Jobs seen",
            "Last checked",
            "Keep checking it?",
            "HTTP",
            "Board link",
            "What went wrong",
            "Technical status",
            "Board locator",
            "Discovery source",
            "First seen",
            "Last seen",
            "_Sort rank",
            "_Provider order",
        ]

        if raw_board_df.empty:
            raw_board_df = pd.DataFrame(
                columns=raw_columns
            )

        duplicate_key_columns = [
            "Provider",
            "Employer",
            "Board link",
            "Technical status",
        ]
        duplicate_mask = raw_board_df.duplicated(
            subset=duplicate_key_columns,
            keep="first",
        )
        duplicate_rows = int(
            duplicate_mask.sum()
        )

        board_df = (
            raw_board_df.loc[
                ~duplicate_mask
            ]
            .copy()
            .sort_values(
                by=[
                    "_Sort rank",
                    "Connected to job finder?",
                    "Jobs seen",
                    "_Provider order",
                    "Employer",
                ],
                ascending=[
                    True,
                    False,
                    False,
                    True,
                    True,
                ],
                kind="stable",
            )
            .reset_index(drop=True)
        )

        provider_rows: list[
            dict[str, Any]
        ] = []

        for provider in PROVIDERS:
            subset = raw_board_df[
                raw_board_df[
                    "Provider"
                ].str.casefold()
                == provider.casefold()
            ]
            technical_counts = (
                subset[
                    "Technical status"
                ]
                .str.casefold()
                .value_counts()
                .to_dict()
                if not subset.empty
                else {}
            )
            source = source_health.get(
                provider,
                {},
            )
            schedule = schedules.get(
                provider,
                {},
            )
            raw_jobs = int(
                source.get(
                    "raw_jobs_last_run"
                )
                or source.get(
                    "jobs_found_last_run"
                )
                or 0
            )
            eligible_jobs = int(
                source.get(
                    "eligible_jobs_last_run"
                )
                or 0
            )
            new_jobs = int(
                source.get(
                    "inserted_jobs_last_run"
                )
                or 0
            )
            duplicates = int(
                source.get(
                    "duplicate_jobs_last_run"
                )
                or 0
            )
            rejected = int(
                source.get(
                    "rejected_jobs_last_run"
                )
                or 0
            )
            failures = int(
                source.get(
                    "consecutive_failures"
                )
                or 0
            )
            technical_status = str(
                source.get(
                    "health_status"
                )
                or "not recorded"
            )
            latest_error = str(
                source.get(
                    "last_error"
                )
                or ""
            )
            state_icon, plain_state = (
                _provider_plain_state(
                    technical_status,
                    raw_jobs,
                    source.get(
                        "last_run_at"
                    ),
                    failures,
                    latest_error,
                )
            )
            waiting_count = int(
                technical_counts.get(
                    "pending",
                    0,
                )
                + technical_counts.get(
                    "retry",
                    0,
                )
            )
            problem_count = int(
                technical_counts.get(
                    "invalid",
                    0,
                )
                + technical_counts.get(
                    "unresolved",
                    0,
                )
            )

            provider_rows.append(
                {
                    "State": state_icon,
                    "Provider": provider,
                    "Plain runtime state": (
                        plain_state
                    ),
                    "Connected boards": int(
                        registered[provider]
                    ),
                    "Checked and valid": int(
                        technical_counts.get(
                            "valid",
                            0,
                        )
                    ),
                    "Waiting board candidates": (
                        waiting_count
                    ),
                    "Problem board candidates": (
                        problem_count
                    ),
                    "Invalid": int(
                        technical_counts.get(
                            "invalid",
                            0,
                        )
                    ),
                    "Unresolved": int(
                        technical_counts.get(
                            "unresolved",
                            0,
                        )
                    ),
                    "Jobs visible at validation": int(
                        subset[
                            "Jobs seen"
                        ].sum()
                        if not subset.empty
                        else 0
                    ),
                    "Jobs checked last scan": (
                        raw_jobs
                    ),
                    "Jobs matching my rules": (
                        eligible_jobs
                    ),
                    "New jobs saved": new_jobs,
                    "Duplicate jobs": duplicates,
                    "Rejected by my rules": (
                        rejected
                    ),
                    "Last scan": _time_text(
                        source.get(
                            "last_run_at"
                        )
                    ),
                    "Next scan": _time_text(
                        schedule.get(
                            "next_run_at"
                        )
                    ),
                    "Technical runtime status": (
                        technical_status
                    ),
                    "Consecutive failures": (
                        failures
                    ),
                    "Latest technical error": (
                        latest_error
                    ),
                    "Cadence minutes": (
                        source.get(
                            "cadence_minutes"
                        )
                    ),
                    "Schedule state": (
                        schedule.get(
                            "schedule_state"
                        )
                    ),
                    "Provider used last run": (
                        source.get(
                            "provider_used_last_run"
                        )
                    ),
                    "Last success": _time_text(
                        source.get(
                            "last_success_at"
                        )
                    ),
                }
            )

        provider_df = pd.DataFrame(
            provider_rows
        )

        run_df = pd.DataFrame()

        if _table_exists(
            connection,
            "employer_board_discovery_runs",
        ):
            columns = _columns(
                connection,
                "employer_board_discovery_runs",
            )
            selected = [
                column
                for column in (
                    "id",
                    "started_at",
                    "completed_at",
                    "success",
                    "network_request_made",
                    "local_scanned",
                    "commoncrawl_urls_seen",
                    "validation_valid",
                    "validation_invalid",
                    "runtime_inserted",
                    "runtime_updated",
                    "error",
                )
                if column in columns
            ]

            if selected:
                run_df = pd.read_sql_query(
                    f"""
                    SELECT {", ".join(selected)}
                    FROM employer_board_discovery_runs
                    ORDER BY id DESC
                    LIMIT 100
                    """,
                    connection,
                )

        totals = {
            "discovered_rows": int(
                len(raw_board_df)
            ),
            "unique_board_rows": int(
                len(board_df)
            ),
            "duplicate_rows_hidden": (
                duplicate_rows
            ),
            "connected_boards": int(
                provider_df[
                    "Connected boards"
                ].sum()
            ),
            "valid_boards": int(
                provider_df[
                    "Checked and valid"
                ].sum()
            ),
            "waiting_candidates": int(
                provider_df[
                    "Waiting board candidates"
                ].sum()
            ),
            "problem_candidates": int(
                provider_df[
                    "Problem board candidates"
                ].sum()
            ),
            "jobs_visible_validation": int(
                provider_df[
                    "Jobs visible at validation"
                ].sum()
            ),
            "jobs_checked_last_scan": int(
                provider_df[
                    "Jobs checked last scan"
                ].sum()
            ),
            "jobs_matching_rules": int(
                provider_df[
                    "Jobs matching my rules"
                ].sum()
            ),
            "new_jobs_saved": int(
                provider_df[
                    "New jobs saved"
                ].sum()
            ),
        }

        return {
            "integrity": integrity,
            "provider_df": provider_df,
            "board_df": board_df,
            "raw_board_df": raw_board_df,
            "run_df": run_df,
            "totals": totals,
        }

    finally:
        connection.close()


def _provider_card_html(
    row: dict[str, Any],
) -> str:
    return f"""
    <div class="aadil-provider-card">
      <div class="aadil-provider-title">
        {row["State"]} {row["Provider"]}
      </div>
      <div class="aadil-provider-state">
        {row["Plain runtime state"]}
      </div>
      <div class="aadil-provider-line">
        🔌 <b>{int(row["Connected boards"]):,}</b>
        connected boards
      </div>
      <div class="aadil-provider-line">
        ⏳ <b>{int(row["Waiting board candidates"]):,}</b>
        possible boards waiting to be checked
      </div>
      <div class="aadil-provider-line">
        ⚠️ <b>{int(row["Problem board candidates"]):,}</b>
        board candidates need attention
      </div>
      <div class="aadil-provider-line">
        🔎 <b>{int(row["Jobs checked last scan"]):,}</b>
        jobs checked in the last scan
      </div>
      <div class="aadil-provider-line">
        🎯 <b>{int(row["Jobs matching my rules"]):,}</b>
        matched your targeting rules
      </div>
      <div class="aadil-provider-line">
        💾 <b>{int(row["New jobs saved"]):,}</b>
        new jobs saved
      </div>
      <div class="aadil-small-muted">
        Last scan: {row["Last scan"]}<br>
        Next scan: {row["Next scan"]}
      </div>
    </div>
    """


def _csv_bytes(
    dataframe: pd.DataFrame,
) -> bytes:
    return dataframe.to_csv(
        index=False
    ).encode("utf-8")


def render_job_boards_dashboard() -> None:
    inject_dashboard_readability_css()

    st.subheader(
        "Employer Job Boards"
    )
    st.caption(
        "A simple view first, with every technical detail "
        "still available underneath."
    )

    st.markdown(
        """
        <div class="aadil-easy-note">
          <b>Think of this page like a library:</b><br>
          A <b>job board</b> is a shelf that may contain many jobs.
          “New boards” means new shelves were found.
          A Telegram job card appears only after a provider scans the shelf,
          finds a job, and that job passes your targeting rules.
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button(
        "↻ Refresh job-board data",
        key="aadil_easy_refresh_v2_7_4",
    ):
        load_data.clear()
        st.rerun()

    try:
        data = load_data()
    except Exception as error:
        st.error(
            f"Unable to load job-board data: {error}"
        )
        return

    providers: pd.DataFrame = data[
        "provider_df"
    ]
    boards: pd.DataFrame = data[
        "board_df"
    ]
    raw_boards: pd.DataFrame = data[
        "raw_board_df"
    ]
    runs: pd.DataFrame = data[
        "run_df"
    ]
    totals: dict[str, int] = data[
        "totals"
    ]

    easy_tab, boards_tab, technical_tab = st.tabs(
        [
            "👶 Easy Overview",
            "🔎 Browse Boards",
            "🧰 Every Technical Detail",
        ]
    )

    with easy_tab:
        st.markdown(
            """
            <div class="aadil-flow-grid">
              <div class="aadil-flow-card">
                <div class="aadil-flow-number">1️⃣</div>
                <div class="aadil-flow-title">Find possible boards</div>
                <div class="aadil-flow-text">
                  Discovery looks for employer career pages.
                </div>
              </div>
              <div class="aadil-flow-card">
                <div class="aadil-flow-number">2️⃣</div>
                <div class="aadil-flow-title">Check the board</div>
                <div class="aadil-flow-text">
                  Validation confirms the link works and has jobs.
                </div>
              </div>
              <div class="aadil-flow-card">
                <div class="aadil-flow-number">3️⃣</div>
                <div class="aadil-flow-title">Scan the jobs</div>
                <div class="aadil-flow-text">
                  Personio, Pinpoint, Comeet, or Recruitee reads the jobs.
                </div>
              </div>
              <div class="aadil-flow-card">
                <div class="aadil-flow-number">4️⃣</div>
                <div class="aadil-flow-title">Send good matches</div>
                <div class="aadil-flow-text">
                  Only jobs passing your rules can become Telegram cards.
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        metric_row = st.columns(4)

        metric_row[0].metric(
            "Boards connected to the job finder",
            f"{totals['connected_boards']:,}",
            help=(
                "These boards are registered for live job scanning."
            ),
        )
        metric_row[1].metric(
            "Boards checked and working",
            f"{totals['valid_boards']:,}",
            help=(
                "These possible boards passed endpoint validation."
            ),
        )
        metric_row[2].metric(
            "Possible boards waiting to be checked",
            f"{totals['waiting_candidates']:,}",
            help=(
                "These are discovery candidates, not active boards "
                "and not job postings."
            ),
        )
        metric_row[3].metric(
            "Board candidates needing attention",
            f"{totals['problem_candidates']:,}",
            help=(
                "Invalid and unresolved candidates. They are not "
                "automatically treated as working boards."
            ),
        )

        metric_row = st.columns(4)

        metric_row[0].metric(
            "Jobs checked in the latest provider scans",
            f"{totals['jobs_checked_last_scan']:,}",
        )
        metric_row[1].metric(
            "Jobs matching your targeting rules",
            f"{totals['jobs_matching_rules']:,}",
        )
        metric_row[2].metric(
            "New jobs saved in the latest scans",
            f"{totals['new_jobs_saved']:,}",
        )
        metric_row[3].metric(
            "Database safety check",
            str(data["integrity"]).upper(),
        )

        st.markdown(
            """
            <div class="aadil-legend">
              <b>What the colors mean</b><br>
              ✅ <b>Working:</b> checked and connected to the job finder.<br>
              🟦 <b>Checked:</b> the link works, but it is not connected yet.<br>
              ⏳ <b>Waiting:</b> a possible board has not finished validation.<br>
              ⚠️ <b>Needs review:</b> the system could not decide automatically.<br>
              ❌ <b>Broken/unavailable:</b> the endpoint failed validation.
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            "### Four job-finder providers"
        )

        provider_records = providers.to_dict(
            "records"
        )

        for start_index in range(
            0,
            len(provider_records),
            2,
        ):
            columns = st.columns(2)

            for offset, column in enumerate(
                columns
            ):
                record_index = (
                    start_index + offset
                )

                if record_index >= len(
                    provider_records
                ):
                    continue

                with column:
                    st.markdown(
                        _provider_card_html(
                            provider_records[
                                record_index
                            ]
                        ),
                        unsafe_allow_html=True,
                    )

        with st.expander(
            "Why can a provider say “pending” even after jobs were scanned?",
            expanded=False,
        ):
            st.write(
                "The raw database field can remain "
                "`enabled_pending_first_run` even when a scan already "
                "recorded jobs. This page therefore shows a plain-language "
                "runtime state derived from the last scan and raw-job count. "
                "The original technical field is preserved in the "
                "Technical Detail tab."
            )

        st.info(
            f"The simple board list hides "
            f"{totals['duplicate_rows_hidden']:,} exact duplicate "
            "candidate row(s). Nothing is deleted; every raw row remains "
            "available in the Technical Detail tab."
        )

    with boards_tab:
        st.markdown(
            "### Choose what you want to see"
        )

        view_option = st.selectbox(
            "Board view",
            options=[
                "✅ Working and connected",
                "🟦 Checked but not connected",
                "⏳ Waiting to be checked",
                "⚠️ Needs a closer look",
                "❌ Broken or unavailable",
                "📚 Everything",
            ],
            index=0,
            key="aadil_easy_board_view_v2_7_4",
            help=(
                "The default view shows working boards first instead "
                "of filling the screen with red errors."
            ),
        )

        filter_columns = st.columns(
            [1.2, 2.8]
        )

        provider_filter = (
            filter_columns[0].selectbox(
                "Provider",
                options=[
                    "All providers",
                    *PROVIDERS,
                ],
                key="aadil_easy_provider_filter_v2_7_4",
            )
        )
        search_text = (
            filter_columns[1].text_input(
                "Search employer, board link, or error",
                placeholder=(
                    "Example: company name, 404, timeout…"
                ),
                key="aadil_easy_search_v2_7_4",
            )
            .strip()
            .casefold()
        )

        filtered = boards.copy()

        plain_state_filter = {
            "✅ Working and connected": (
                "Working and connected"
            ),
            "🟦 Checked but not connected": (
                "Checked, but not connected"
            ),
            "⏳ Waiting to be checked": (
                "Waiting to be checked"
            ),
            "⚠️ Needs a closer look": (
                "Needs a closer look"
            ),
            "❌ Broken or unavailable": (
                "Broken or unavailable"
            ),
        }.get(view_option)

        if plain_state_filter:
            filtered = filtered[
                filtered[
                    "Plain state"
                ]
                == plain_state_filter
            ]

        if provider_filter != "All providers":
            filtered = filtered[
                filtered[
                    "Provider"
                ]
                == provider_filter
            ]

        if search_text:
            searchable = (
                filtered[
                    [
                        "Provider",
                        "Employer",
                        "Plain state",
                        "Board link",
                        "What went wrong",
                    ]
                ]
                .fillna("")
                .astype(str)
                .agg(
                    " ".join,
                    axis=1,
                )
                .str.casefold()
            )
            filtered = filtered[
                searchable.str.contains(
                    search_text,
                    regex=False,
                )
            ]

        st.caption(
            f"Showing {len(filtered):,} easy-to-read unique board "
            f"row(s). Raw candidate rows: "
            f"{totals['discovered_rows']:,}."
        )

        simple_columns = [
            "State",
            "Provider",
            "Employer",
            "Plain state",
            "Connected to job finder?",
            "Jobs seen",
            "Last checked",
            "Keep checking it?",
            "HTTP",
            "Board link",
            "What went wrong",
        ]

        st.dataframe(
            filtered[
                simple_columns
            ],
            use_container_width=True,
            hide_index=True,
            height=580,
            column_config={
                "Board link": st.column_config.LinkColumn(
                    display_text="Open board",
                    width="small",
                ),
                "What went wrong": st.column_config.TextColumn(
                    width="large",
                ),
                "Employer": st.column_config.TextColumn(
                    width="medium",
                ),
                "Last checked": st.column_config.TextColumn(
                    width="medium",
                ),
            },
        )

        st.download_button(
            "Download this filtered board view as CSV",
            data=_csv_bytes(
                filtered[
                    simple_columns
                ]
            ),
            file_name=(
                "aadil_job_boards_easy_view.csv"
            ),
            mime="text/csv",
            key="aadil_easy_download_v2_7_4",
        )

    with technical_tab:
        st.warning(
            "This tab keeps every raw status, error, schedule, "
            "candidate row, and discovery-run field. It is intentionally "
            "more technical."
        )

        st.markdown(
            "### Complete provider runtime details"
        )
        st.dataframe(
            providers,
            use_container_width=True,
            hide_index=True,
            height=330,
            column_config={
                "Latest technical error": st.column_config.TextColumn(
                    width="large",
                ),
                "Last scan": st.column_config.TextColumn(
                    width="medium",
                ),
                "Next scan": st.column_config.TextColumn(
                    width="medium",
                ),
            },
        )
        st.download_button(
            "Download provider details as CSV",
            data=_csv_bytes(
                providers
            ),
            file_name=(
                "aadil_provider_health_all_details.csv"
            ),
            mime="text/csv",
            key="aadil_technical_provider_download_v2_7_4",
        )

        st.markdown(
            "### Every raw employer-board candidate row"
        )
        technical_board_columns = [
            column
            for column in raw_boards.columns
            if not column.startswith("_")
        ]
        st.dataframe(
            raw_boards[
                technical_board_columns
            ],
            use_container_width=True,
            hide_index=True,
            height=620,
            column_config={
                "Board link": st.column_config.LinkColumn(
                    display_text="Open board",
                    width="small",
                ),
                "What went wrong": st.column_config.TextColumn(
                    width="large",
                ),
            },
        )
        st.download_button(
            "Download every raw board row as CSV",
            data=_csv_bytes(
                raw_boards[
                    technical_board_columns
                ]
            ),
            file_name=(
                "aadil_job_boards_every_raw_detail.csv"
            ),
            mime="text/csv",
            key="aadil_technical_board_download_v2_7_4",
        )

        with st.expander(
            "Employer Board Discovery run history",
            expanded=False,
        ):
            if runs.empty:
                st.info(
                    "No compatible discovery-run history was found."
                )
            else:
                st.dataframe(
                    runs,
                    use_container_width=True,
                    hide_index=True,
                    height=360,
                )
                st.download_button(
                    "Download discovery run history as CSV",
                    data=_csv_bytes(
                        runs
                    ),
                    file_name=(
                        "aadil_board_discovery_run_history.csv"
                    ),
                    mime="text/csv",
                    key="aadil_run_history_download_v2_7_4",
                )
