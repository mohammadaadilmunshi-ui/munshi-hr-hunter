from __future__ import annotations

import html
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from app.database import ROOT_DIR, get_connection


MARKER = "AADIL_TELEGRAM_JOB_BOARD_HEALTH_V2_7_2"
EASTERN = ZoneInfo("America/New_York")
PROVIDERS = ("personio", "pinpoint", "comeet", "recruitee")
NAMES = {
    "personio": "Personio",
    "pinpoint": "Pinpoint",
    "comeet": "Comeet",
    "recruitee": "Recruitee",
}
CODES = {
    "pe": "personio",
    "pi": "pinpoint",
    "co": "comeet",
    "re": "recruitee",
}
REVERSE_CODES = {value: key for key, value in CODES.items()}
PAGE_SIZE = 5
MAX_MESSAGE = 4096


def _table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table' AND name=?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


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


def _format_time(value: Any) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return "not recorded"
    return parsed.strftime("%b %-d, %Y · %-I:%M %p ET")


def _age(value: Any) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return "unknown age"
    seconds = max(
        0,
        int(
            (
                datetime.now(EASTERN)
                - parsed
            ).total_seconds()
        ),
    )
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _safe_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "not recorded"
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
        )[:240]
    except Exception:
        return raw.split("?", 1)[0][:240]


def _pid_state(path: Path) -> str:
    if not path.exists():
        return "not running"
    try:
        pid = int(path.read_text().strip())
        os.kill(pid, 0)
        return f"running · PID {pid}"
    except (OSError, ValueError):
        return "not running"


def _registered_counts(
    connection: sqlite3.Connection,
) -> dict[str, int]:
    counts = {provider: 0 for provider in PROVIDERS}

    if _table_exists(connection, "public_adapter_boards"):
        columns = _columns(
            connection,
            "public_adapter_boards",
        )
        enabled_clause = (
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
            {enabled_clause}
            GROUP BY lower(source_name)
            """
        ).fetchall()
        for row in rows:
            source = str(row["source_name"] or "")
            for provider in (
                "personio",
                "pinpoint",
                "comeet",
            ):
                if provider in source:
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
            conditions.append("COALESCE(enabled,1)=1")
        counts["recruitee"] = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM market_public_boards
                WHERE {" AND ".join(conditions)}
                """
            ).fetchone()[0]
        )

    return counts


def _candidate_summary(
    connection: sqlite3.Connection,
) -> tuple[
    dict[str, dict[str, int]],
    Any,
]:
    summary = {
        provider: {
            "total": 0,
            "valid": 0,
            "pending": 0,
            "retry": 0,
            "unresolved": 0,
            "invalid": 0,
            "visible_jobs": 0,
        }
        for provider in PROVIDERS
    }
    latest = None

    if not _table_exists(
        connection,
        "employer_board_discovery_candidates",
    ):
        return summary, latest

    rows = connection.execute(
        """
        SELECT
            lower(provider) AS provider,
            lower(validation_status) AS validation_status,
            COUNT(*) AS count_value,
            SUM(COALESCE(visible_jobs,0)) AS visible_jobs,
            MAX(last_validated_at) AS latest_validation
        FROM employer_board_discovery_candidates
        WHERE lower(provider) IN (
            'personio','pinpoint','comeet','recruitee'
        )
        GROUP BY lower(provider),lower(validation_status)
        """
    ).fetchall()

    for row in rows:
        provider = str(row["provider"] or "")
        if provider not in summary:
            continue
        status = str(
            row["validation_status"] or "pending"
        )
        count = int(row["count_value"] or 0)
        summary[provider]["total"] += count
        summary[provider]["visible_jobs"] += int(
            row["visible_jobs"] or 0
        )
        if status in summary[provider]:
            summary[provider][status] += count
        else:
            summary[provider]["pending"] += count
        value = row["latest_validation"]
        if value and (
            latest is None
            or str(value) > str(latest)
        ):
            latest = value

    return summary, latest


def _source_health(
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
    selected = [name for name in desired if name in columns]
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
            if provider in source_name:
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
    selected = [name for name in desired if name in columns]
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
            if provider in source_name:
                result[provider] = data
                break
    return result


def _source_condition(
    registered: int,
    valid: int,
    health: dict[str, Any],
) -> tuple[str, str]:
    status = str(
        health.get("health_status") or "not_tested"
    ).casefold()
    failures = int(
        health.get("consecutive_failures") or 0
    )
    if failures > 0 or status in {
        "failed",
        "unhealthy",
        "degraded",
        "runtime_failure",
        "blocked",
    }:
        return "❌", "runtime failure"
    if status in {
        "enabled_pending_first_run",
        "pending_first_run",
        "not_tested",
        "installed_pending_first_run",
    }:
        return "⏳", "awaiting ingestion run"
    if registered <= 0 or valid <= 0:
        return "⚙️", "no validated boards"
    if status == "healthy":
        return "✅", "working"
    return "⚠️", status or "unknown"


def build_summary() -> tuple[str, dict[str, Any]]:
    connection = get_connection()
    try:
        registered = _registered_counts(connection)
        candidates, latest_validation = (
            _candidate_summary(connection)
        )
        health = _source_health(connection)
        schedules = _schedule_map(connection)
        integrity = connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
    finally:
        connection.close()

    total_valid = sum(
        row["valid"]
        for row in candidates.values()
    )
    total_pending = sum(
        row["pending"] + row["retry"]
        for row in candidates.values()
    )
    total_unresolved = sum(
        row["unresolved"]
        for row in candidates.values()
    )
    total_invalid = sum(
        row["invalid"]
        for row in candidates.values()
    )
    total_visible = sum(
        row["visible_jobs"]
        for row in candidates.values()
    )

    lines = [
        "🩺 <b>Job board health center</b>",
        "",
        (
            "📡 Runtime-registered boards: "
            f"<b>{sum(registered.values())}</b>"
        ),
        f"✅ Live-validated: <b>{total_valid}</b>",
        f"⏳ Pending/retry: <b>{total_pending}</b>",
        f"⚠️ Unresolved: <b>{total_unresolved}</b>",
        f"❌ Invalid: <b>{total_invalid}</b>",
        (
            "💼 Jobs visible during validation: "
            f"<b>{total_visible}</b>"
        ),
        (
            "🗄 Database integrity: "
            f"<b>{html.escape(str(integrity))}</b>"
        ),
        "",
        (
            "🔎 Discovery controller: "
            + html.escape(
                _pid_state(
                    ROOT_DIR
                    / "data/employer_discovery_runner.pid"
                )
            )
        ),
        (
            "⚙️ First-run controller: "
            + html.escape(
                _pid_state(
                    ROOT_DIR
                    / "data/employer_source_first_runs.pid"
                )
            )
        ),
        "",
        "<b>Provider health</b>",
    ]

    for provider in PROVIDERS:
        candidate = candidates[provider]
        source = health.get(provider, {})
        schedule = schedules.get(provider, {})
        icon, condition = _source_condition(
            registered[provider],
            candidate["valid"],
            source,
        )
        raw = int(
            source.get("raw_jobs_last_run")
            or source.get("jobs_found_last_run")
            or 0
        )
        eligible = int(
            source.get("eligible_jobs_last_run") or 0
        )
        new = int(
            source.get("inserted_jobs_last_run") or 0
        )
        duplicates = int(
            source.get("duplicate_jobs_last_run") or 0
        )
        rejected = int(
            source.get("rejected_jobs_last_run") or 0
        )
        lines.extend(
            [
                "",
                (
                    f"{icon} <b>{NAMES[provider]}</b> · "
                    f"{html.escape(condition)}"
                ),
                (
                    f"   registered {registered[provider]} · "
                    f"valid {candidate['valid']} · "
                    f"pending "
                    f"{candidate['pending'] + candidate['retry']} · "
                    f"invalid {candidate['invalid']}"
                ),
                (
                    f"   raw {raw} → eligible {eligible} "
                    f"→ new {new} · dup {duplicates} "
                    f"· rejected {rejected}"
                ),
                (
                    "   last run: "
                    + html.escape(
                        _format_time(
                            source.get("last_run_at")
                        )
                    )
                ),
            ]
        )
        if schedule.get("next_run_at"):
            lines.append(
                "   next run: "
                + html.escape(
                    _format_time(
                        schedule.get("next_run_at")
                    )
                )
            )
        if source.get("last_error"):
            lines.append(
                "   error: "
                + html.escape(
                    str(source["last_error"])[:260]
                )
            )

    if latest_validation:
        lines.extend(
            [
                "",
                (
                    "🕒 Latest endpoint validation: "
                    + html.escape(
                        _format_time(latest_validation)
                    )
                    + " · "
                    + html.escape(
                        _age(latest_validation)
                    )
                ),
            ]
        )

    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE:
        text = (
            text[: MAX_MESSAGE - 90]
            + "\n\nOpen a provider button for full details."
        )

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": (
                        f"Personio {registered['personio']}"
                    ),
                    "callback_data": "cc:jbh:p:pe:0",
                },
                {
                    "text": (
                        f"Pinpoint {registered['pinpoint']}"
                    ),
                    "callback_data": "cc:jbh:p:pi:0",
                },
            ],
            [
                {
                    "text": (
                        f"Comeet {registered['comeet']}"
                    ),
                    "callback_data": "cc:jbh:p:co:0",
                },
                {
                    "text": (
                        f"Recruitee {registered['recruitee']}"
                    ),
                    "callback_data": "cc:jbh:p:re:0",
                },
            ],
            [
                {
                    "text": (
                        "❌ Errors "
                        f"{total_invalid + total_unresolved}"
                    ),
                    "callback_data": "cc:jbh:x:al:0",
                },
                {
                    "text": f"⏳ Pending {total_pending}",
                    "callback_data": "cc:jbh:q:al:0",
                },
            ],
            [
                {
                    "text": "🔄 Refresh",
                    "callback_data": "cc:jbh:r",
                },
                {
                    "text": "🎛 Menu",
                    "callback_data": "cc:jbh:m",
                },
            ],
        ]
    }
    return text, keyboard


def _provider_from_code(code: str) -> str | None:
    if code == "al":
        return None
    return CODES.get(code)


def _detail_rows(
    mode: str,
    provider: str | None,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    connection = get_connection()
    try:
        if not _table_exists(
            connection,
            "employer_board_discovery_candidates",
        ):
            return [], 0

        conditions = [
            "lower(provider) IN "
            "('personio','pinpoint','comeet','recruitee')"
        ]
        params: list[Any] = []

        if provider:
            conditions.append("lower(provider)=?")
            params.append(provider)

        if mode == "errors":
            conditions.append(
                """
                (
                    lower(validation_status) IN (
                        'invalid','unresolved','retry'
                    )
                    OR trim(
                        COALESCE(validation_error,'')
                    ) <> ''
                )
                """
            )
        elif mode == "pending":
            conditions.append(
                """
                lower(validation_status) IN (
                    'pending','retry','unresolved'
                )
                """
            )

        where = " AND ".join(conditions)
        total = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM employer_board_discovery_candidates
                WHERE {where}
                """,
                params,
            ).fetchone()[0]
        )
        rows = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT
                    id,
                    lower(provider) AS provider,
                    company_name,
                    board_locator,
                    board_url,
                    validation_status,
                    validation_error,
                    last_http_status,
                    visible_jobs,
                    enabled,
                    discovery_source,
                    first_seen_at,
                    last_seen_at,
                    last_validated_at
                FROM employer_board_discovery_candidates
                WHERE {where}
                ORDER BY
                    CASE lower(validation_status)
                        WHEN 'invalid' THEN 0
                        WHEN 'unresolved' THEN 1
                        WHEN 'retry' THEN 2
                        WHEN 'pending' THEN 3
                        WHEN 'valid' THEN 4
                        ELSE 5
                    END,
                    lower(provider),
                    lower(company_name),
                    id
                LIMIT ? OFFSET ?
                """,
                params + [PAGE_SIZE, max(0, offset)],
            ).fetchall()
        ]

        registered_urls: set[str] = set()

        if _table_exists(
            connection,
            "public_adapter_boards",
        ):
            registered_urls.update(
                str(row[0] or "").casefold()
                for row in connection.execute(
                    """
                    SELECT board_url
                    FROM public_adapter_boards
                    WHERE COALESCE(enabled,1)=1
                    """
                ).fetchall()
            )

        if _table_exists(
            connection,
            "market_public_boards",
        ):
            registered_urls.update(
                str(row[0] or "").casefold()
                for row in connection.execute(
                    """
                    SELECT board_url
                    FROM market_public_boards
                    WHERE COALESCE(enabled,1)=1
                    """
                ).fetchall()
            )

        for row in rows:
            row["runtime_registered"] = (
                str(
                    row.get("board_url") or ""
                ).casefold()
                in registered_urls
            )

        return rows, total
    finally:
        connection.close()


def _icon(status: str) -> str:
    status = str(status or "").casefold()
    if status == "valid":
        return "✅"
    if status in {"pending", "retry"}:
        return "⏳"
    if status == "unresolved":
        return "⚠️"
    if status == "invalid":
        return "❌"
    return "▫️"


def build_detail(
    mode: str,
    provider_code: str,
    offset: int,
) -> tuple[str, dict[str, Any]]:
    provider = _provider_from_code(provider_code)
    rows, total = _detail_rows(
        mode,
        provider,
        offset,
    )
    title = {
        "provider": "Provider boards",
        "errors": "Board errors and warnings",
        "pending": "Pending board validation",
    }.get(mode, "Job board health")
    provider_title = (
        NAMES[provider]
        if provider
        else "All providers"
    )
    start = offset + 1 if rows else 0
    end = offset + len(rows)

    lines = [
        f"🩺 <b>{html.escape(title)}</b>",
        (
            "Provider: "
            f"<b>{html.escape(provider_title)}</b>"
        ),
        f"Showing: <b>{start}-{end}</b> of <b>{total}</b>",
    ]

    if not rows:
        lines.extend(
            ["", "No matching job boards were found."]
        )

    for row in rows:
        status = str(
            row.get("validation_status") or "pending"
        )
        provider_name = NAMES.get(
            str(row.get("provider") or ""),
            str(row.get("provider") or "Unknown"),
        )
        lines.extend(
            [
                "",
                (
                    f"{_icon(status)} "
                    f"<b>{html.escape(str(row.get('company_name') or 'Unknown employer'))}</b>"
                ),
                (
                    "   provider: "
                    + html.escape(provider_name)
                    + " · "
                    + html.escape(status)
                ),
                (
                    "   endpoint: <code>"
                    + html.escape(
                        _safe_url(
                            row.get("board_url")
                        )
                    )
                    + "</code>"
                ),
                (
                    "   HTTP: "
                    + html.escape(
                        str(
                            row.get("last_http_status")
                            if row.get("last_http_status")
                            is not None
                            else "not recorded"
                        )
                    )
                    + " · visible jobs: "
                    + str(
                        int(
                            row.get("visible_jobs") or 0
                        )
                    )
                ),
                (
                    "   runtime: "
                    + (
                        "registered"
                        if row.get("runtime_registered")
                        else "not registered"
                    )
                    + " · enabled: "
                    + (
                        "yes"
                        if int(row.get("enabled") or 0)
                        else "no"
                    )
                ),
                (
                    "   validated: "
                    + html.escape(
                        _format_time(
                            row.get("last_validated_at")
                        )
                    )
                    + " · "
                    + html.escape(
                        _age(
                            row.get("last_validated_at")
                        )
                    )
                ),
            ]
        )
        if row.get("validation_error"):
            lines.append(
                "   error: "
                + html.escape(
                    str(
                        row["validation_error"]
                    )[:340]
                )
            )

    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE:
        text = (
            text[: MAX_MESSAGE - 90]
            + "\n\nPage shortened to Telegram's limit."
        )

    mode_code = {
        "provider": "p",
        "errors": "x",
        "pending": "q",
    }[mode]
    keyboard: list[list[dict[str, str]]] = []
    nav: list[dict[str, str]] = []

    if offset > 0:
        nav.append(
            {
                "text": "⬅️ Previous",
                "callback_data": (
                    f"cc:jbh:{mode_code}:"
                    f"{provider_code}:"
                    f"{max(0, offset - PAGE_SIZE)}"
                ),
            }
        )

    if offset + PAGE_SIZE < total:
        nav.append(
            {
                "text": "Next ➡️",
                "callback_data": (
                    f"cc:jbh:{mode_code}:"
                    f"{provider_code}:"
                    f"{offset + PAGE_SIZE}"
                ),
            }
        )

    if nav:
        keyboard.append(nav)

    if mode == "provider" and provider:
        code = REVERSE_CODES[provider]
        keyboard.append(
            [
                {
                    "text": "❌ Provider errors",
                    "callback_data": (
                        f"cc:jbh:x:{code}:0"
                    ),
                },
                {
                    "text": "⏳ Provider pending",
                    "callback_data": (
                        f"cc:jbh:q:{code}:0"
                    ),
                },
            ]
        )

    keyboard.append(
        [
            {
                "text": "🩺 Summary",
                "callback_data": "cc:jbh:s",
            },
            {
                "text": "🔄 Refresh page",
                "callback_data": (
                    f"cc:jbh:{mode_code}:"
                    f"{provider_code}:{offset}"
                ),
            },
        ]
    )
    return text, {"inline_keyboard": keyboard}


def _send(
    chat_id: int,
    text: str,
    keyboard: dict[str, Any],
) -> dict[str, Any]:
    from app.telegram_client import telegram_request
    return telegram_request(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": json.dumps(
                keyboard,
                ensure_ascii=False,
            ),
        },
    )


def _edit(
    chat_id: int,
    message_id: int,
    text: str,
    keyboard: dict[str, Any],
) -> dict[str, Any]:
    from app.telegram_client import telegram_request
    return telegram_request(
        "editMessageText",
        {
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": json.dumps(
                keyboard,
                ensure_ascii=False,
            ),
        },
    )


def send_job_board_health(
    chat_id: int,
    query: str | None = None,
) -> dict[str, Any]:
    requested = str(query or "").strip().casefold()
    if requested in PROVIDERS:
        text, keyboard = build_detail(
            "provider",
            REVERSE_CODES[requested],
            0,
        )
    elif requested in {
        "error",
        "errors",
        "failed",
        "failures",
    }:
        text, keyboard = build_detail(
            "errors",
            "al",
            0,
        )
    elif requested in {
        "pending",
        "retry",
        "unresolved",
    }:
        text, keyboard = build_detail(
            "pending",
            "al",
            0,
        )
    else:
        text, keyboard = build_summary()

    response = _send(
        chat_id,
        text,
        keyboard,
    )
    return {
        "success": bool(response.get("ok")),
        "message": "Job-board health report sent.",
        "telegram_response": response,
    }


def handle_job_board_health_callback(
    callback_data: str,
    chat_id: int,
    message_id: int,
) -> tuple[bool, str, bool]:
    parts = str(callback_data or "").split(":")

    if parts == ["cc", "jbh", "m"]:
        from app.telegram_control_center import (
            send_control_panel,
        )
        result = send_control_panel(chat_id)
        return (
            bool(result.get("success")),
            "Menu sent.",
            not bool(result.get("success")),
        )

    try:
        if parts in (
            ["cc", "jbh", "s"],
            ["cc", "jbh", "r"],
        ):
            text, keyboard = build_summary()
        elif (
            len(parts) == 5
            and parts[:2] == ["cc", "jbh"]
            and parts[2] in {"p", "x", "q"}
        ):
            mode = {
                "p": "provider",
                "x": "errors",
                "q": "pending",
            }[parts[2]]
            text, keyboard = build_detail(
                mode,
                parts[3],
                max(0, int(parts[4])),
            )
        else:
            return (
                False,
                "Invalid job-board health action.",
                True,
            )

        _edit(
            chat_id,
            message_id,
            text,
            keyboard,
        )
        return True, "Job-board health updated.", False
    except Exception as error:
        return (
            False,
            f"Job-board health error: {error}"[:180],
            True,
        )
