# AADIL_COMMAND_CENTER_V2_2_UI_STABILITY_FIX
from __future__ import annotations

import html
import json
import re
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from app.platform_config import endpoint_url, n8n_database_path

VERSION = "2.0.0"
ET = ZoneInfo("America/New_York")
N8N_DB = n8n_database_path()
FINAL_QUEUE_STATES = {"completed", "failed", "cancelled", "canceled", "closed"}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _esc(value: Any) -> str:
    return html.escape(_clean(value))


def _num(value: Any, digits: int = 0) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:,.{digits}f}"


def _pct(part: float, whole: float) -> float:
    if not whole:
        return 0.0
    return round(float(part) / float(whole) * 100.0, 1)


def _ro_connect(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve()
    connection = sqlite3.connect(
        f"file:{resolved}?mode=ro",
        uri=True,
        timeout=4,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {
        str(row["name"])
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    }


def _first(columns: set[str], names: list[str]) -> str | None:
    return next((name for name in names if name in columns), None)


def _count(
    connection: sqlite3.Connection,
    table: str,
    where: str = "",
    params: tuple[Any, ...] = (),
) -> int:
    if not _table_exists(connection, table):
        return 0
    sql = f'SELECT COUNT(*) FROM "{table}"'
    if where:
        sql += f" WHERE {where}"
    try:
        return int(connection.execute(sql, params).fetchone()[0] or 0)
    except Exception:
        return 0


def _scalar(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
    default: Any = 0,
) -> Any:
    try:
        row = connection.execute(sql, params).fetchone()
        if not row or row[0] is None:
            return default
        return row[0]
    except Exception:
        return default


def _rows(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    try:
        return [
            dict(row)
            for row in connection.execute(sql, params).fetchall()
        ]
    except Exception:
        return []


def _timestamp_column(connection: sqlite3.Connection, table: str) -> str | None:
    return _first(
        _columns(connection, table),
        [
            "created_at",
            "added_at",
            "queued_at",
            "started_at",
            "timestamp",
            "updated_at",
            "completed_at",
        ],
    )


def _latest_timestamp(connection: sqlite3.Connection, table: str) -> str:
    column = _timestamp_column(connection, table)
    if not column:
        return ""
    return str(
        _scalar(
            connection,
            f'SELECT MAX("{column}") FROM "{table}"',
            default="",
        )
        or ""
    )


def _parse_dt(value: Any) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            pass
    return None


def _format_et(value: Any) -> str:
    parsed = _parse_dt(value)
    if parsed is None:
        return "—"
    return parsed.astimezone(ET).strftime("%b %d · %I:%M %p ET")


def _age(value: Any) -> str:
    parsed = _parse_dt(value)
    if parsed is None:
        return "—"
    seconds = max(
        0,
        int(
            (
                datetime.now(timezone.utc)
                - parsed.astimezone(timezone.utc)
            ).total_seconds()
        ),
    )
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h {minutes % 60}m"
    days = hours // 24
    return f"{days}d {hours % 24}h"


def _service(name: str, url: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Aadil-HR-Hunter-Command-Center/2.0"},
        )
        with urllib.request.urlopen(request, timeout=2.5) as response:
            return {
                "name": name,
                "online": 200 <= int(response.status) < 500,
                "latency_ms": round(
                    (time.perf_counter() - started) * 1000,
                    1,
                ),
                "status_code": int(response.status),
                "error": "",
            }
    except Exception as error:
        return {
            "name": name,
            "online": False,
            "latency_ms": round(
                (time.perf_counter() - started) * 1000,
                1,
            ),
            "status_code": None,
            "error": str(error),
        }


def _process_count(pattern: str) -> int:
    process = subprocess.run(
        ["ps", "-axo", "command="],
        text=True,
        capture_output=True,
        check=False,
    )
    regex = re.compile(pattern, re.I)
    return sum(
        1 for line in process.stdout.splitlines() if regex.search(line)
    )


def _breakdown(
    connection: sqlite3.Connection,
    table: str,
    column: str | None,
) -> dict[str, int]:
    if not column or column not in _columns(connection, table):
        return {}
    rows = _rows(
        connection,
        f'''
        SELECT
            COALESCE(NULLIF(TRIM(CAST("{column}" AS TEXT)), ''), '(blank)') AS label,
            COUNT(*) AS count
        FROM "{table}"
        GROUP BY
            COALESCE(NULLIF(TRIM(CAST("{column}" AS TEXT)), ''), '(blank)')
        ORDER BY count DESC
        ''',
    )
    return {
        str(row["label"]): int(row["count"] or 0)
        for row in rows
    }


def _daily_counts(
    connection: sqlite3.Connection,
    table: str,
    days: int = 14,
) -> pd.DataFrame:
    timestamp = _timestamp_column(connection, table)
    if not timestamp:
        return pd.DataFrame(columns=["date", "count"])
    rows = _rows(
        connection,
        f'''
        SELECT
            date("{timestamp}", 'localtime') AS date,
            COUNT(*) AS count
        FROM "{table}"
        WHERE datetime("{timestamp}") >= datetime('now', ?)
        GROUP BY date("{timestamp}", 'localtime')
        ORDER BY date
        ''',
        (f"-{int(days)} days",),
    )
    if not rows:
        return pd.DataFrame(columns=["date", "count"])
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["count"] = pd.to_numeric(
        frame["count"],
        errors="coerce",
    ).fillna(0)
    return frame


def _jobs(connection: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(connection, "jobs"):
        return {"total": 0}

    columns = _columns(connection, "jobs")
    timestamp = _timestamp_column(connection, "jobs")
    score = _first(columns, ["hunter_score", "score", "match_score"])
    source = _first(columns, ["source", "source_name", "adapter"])
    status = _first(columns, ["status", "decision_status"])
    sent = _first(columns, ["sent_to_n8n", "n8n_sent"])
    title = _first(columns, ["title", "job_title"])
    company = _first(columns, ["company_name", "company"])
    location = _first(columns, ["location", "job_location"])

    total = _count(connection, "jobs")
    today = (
        _count(
            connection,
            "jobs",
            f'date("{timestamp}", "localtime")=date("now", "localtime")',
        )
        if timestamp
        else 0
    )
    yesterday = (
        _count(
            connection,
            "jobs",
            f'date("{timestamp}", "localtime")='
            f'date("now", "-1 day", "localtime")',
        )
        if timestamp
        else 0
    )
    last_7 = (
        _count(
            connection,
            "jobs",
            f'datetime("{timestamp}")>=datetime("now","-7 days")',
        )
        if timestamp
        else 0
    )

    elite = (
        _count(connection, "jobs", f'COALESCE("{score}",0)>=93')
        if score
        else 0
    )
    high = (
        _count(
            connection,
            "jobs",
            f'COALESCE("{score}",0)>=85 AND COALESCE("{score}",0)<93',
        )
        if score
        else 0
    )
    good = (
        _count(
            connection,
            "jobs",
            f'COALESCE("{score}",0)>=75 AND COALESCE("{score}",0)<85',
        )
        if score
        else 0
    )
    low = max(total - elite - high - good, 0)
    sent_count = (
        _count(connection, "jobs", f'COALESCE("{sent}",0)=1')
        if sent
        else 0
    )

    select_map = {
        "id": "id" if "id" in columns else None,
        "company": company,
        "title": title,
        "location": location,
        "source": source,
        "score": score,
        "status": status,
        "added_at": timestamp,
        "sent_to_n8n": sent,
    }
    expressions = [
        f'"{column}" AS "{alias}"'
        for alias, column in select_map.items()
        if column
    ]
    recent = []
    if expressions:
        recent = _rows(
            connection,
            f'''
            SELECT {", ".join(expressions)}
            FROM jobs
            ORDER BY "{timestamp or 'id'}" DESC
            LIMIT 25
            ''',
        )

    source_rows = []
    if source:
        score_expr = f'COALESCE("{score}",0)' if score else "0"
        sent_expr = f'COALESCE("{sent}",0)' if sent else "0"
        source_rows = _rows(
            connection,
            f'''
            SELECT
                COALESCE(NULLIF(TRIM(CAST("{source}" AS TEXT)), ''), 'Unknown') AS source,
                COUNT(*) AS stored,
                SUM(CASE WHEN {score_expr}>=85 THEN 1 ELSE 0 END) AS high_score,
                SUM(CASE WHEN {score_expr}>=93 THEN 1 ELSE 0 END) AS elite,
                SUM(CASE WHEN {sent_expr}=1 THEN 1 ELSE 0 END) AS sent_to_n8n,
                ROUND(AVG({score_expr}),1) AS avg_score
            FROM jobs
            GROUP BY
                COALESCE(NULLIF(TRIM(CAST("{source}" AS TEXT)), ''), 'Unknown')
            ORDER BY stored DESC
            ''',
        )
        for row in source_rows:
            row["high_yield_pct"] = _pct(
                row.get("high_score") or 0,
                row.get("stored") or 0,
            )
            row["dispatch_pct"] = _pct(
                row.get("sent_to_n8n") or 0,
                row.get("stored") or 0,
            )

    return {
        "total": total,
        "today": today,
        "yesterday": yesterday,
        "today_delta": today - yesterday,
        "last_7": last_7,
        "elite": elite,
        "high": high,
        "good": good,
        "low": low,
        "sent": sent_count,
        "dispatch_rate": _pct(sent_count, total),
        "elite_rate": _pct(elite, total),
        "score_chart": pd.DataFrame(
            {
                "Band": ["93–100", "85–92", "75–84", "Below 75"],
                "Jobs": [elite, high, good, low],
            }
        ),
        "daily": _daily_counts(connection, "jobs"),
        "recent": recent,
        "sources": source_rows,
        "status": _breakdown(connection, "jobs", status),
        "latest": _latest_timestamp(connection, "jobs"),
    }


def _queue(connection: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(connection, "n8n_dispatch_queue"):
        return {
            "total": 0,
            "open": 0,
            "completed": 0,
            "failed": 0,
            "reliability": 0.0,
            "status": {},
            "open_rows": [],
            "p50": 0.0,
            "p95": 0.0,
            "oldest_age": "—",
        }

    columns = _columns(connection, "n8n_dispatch_queue")
    status_column = _first(columns, ["queue_status", "status"])
    queued = _first(columns, ["queued_at", "created_at"])
    completed_at = _first(columns, ["completed_at", "updated_at"])
    status = _breakdown(
        connection,
        "n8n_dispatch_queue",
        status_column,
    )
    total = sum(status.values()) or _count(
        connection,
        "n8n_dispatch_queue",
    )
    completed = sum(
        count
        for label, count in status.items()
        if label.lower() == "completed"
    )
    failed = sum(
        count
        for label, count in status.items()
        if label.lower() == "failed"
    )
    open_count = sum(
        count
        for label, count in status.items()
        if label.lower() not in FINAL_QUEUE_STATES
    )

    open_rows = []
    if status_column:
        selected = [
            column
            for column in (
                "id",
                "job_id",
                status_column,
                "request_id",
                "attempt_count",
                "http_status",
                "last_error",
                queued,
                "accepted_at",
                "updated_at",
            )
            if column and column in columns
        ]
        open_rows = _rows(
            connection,
            f'''
            SELECT {", ".join(f'"{column}"' for column in selected)}
            FROM n8n_dispatch_queue
            WHERE lower(COALESCE("{status_column}",'')) NOT IN
                ('completed','failed','cancelled','canceled','closed')
            ORDER BY "{queued or 'id'}" ASC
            LIMIT 25
            ''',
        )

    latencies: list[float] = []
    if queued and completed_at:
        rows = _rows(
            connection,
            f'''
            SELECT
                (julianday("{completed_at}")-julianday("{queued}"))*86400.0
                AS seconds
            FROM n8n_dispatch_queue
            WHERE "{completed_at}" IS NOT NULL
              AND "{queued}" IS NOT NULL
              AND (julianday("{completed_at}")-julianday("{queued}"))>=0
            ''',
        )
        latencies = [
            float(row["seconds"])
            for row in rows
            if row.get("seconds") is not None
        ]

    p50 = p95 = 0.0
    if latencies:
        series = pd.Series(latencies)
        p50 = round(float(series.quantile(0.50)), 1)
        p95 = round(float(series.quantile(0.95)), 1)

    oldest_value = (
        open_rows[0].get(queued or "queued_at")
        if open_rows
        else ""
    )
    return {
        "total": total,
        "open": open_count,
        "completed": completed,
        "failed": failed,
        "reliability": _pct(completed, completed + failed),
        "status": status,
        "open_rows": open_rows,
        "p50": p50,
        "p95": p95,
        "oldest_age": _age(oldest_value),
        "latest": _latest_timestamp(
            connection,
            "n8n_dispatch_queue",
        ),
    }


def _results(connection: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(connection, "n8n_results"):
        return {
            "total": 0,
            "avg_ats": 0.0,
            "max_ats": 0.0,
            "ready": 0,
            "review": 0,
            "failed": 0,
            "writer_success": 0,
            "writer_failed": 0,
            "writer_rate": 0.0,
            "one_page": 0,
            "one_page_rate": 0.0,
            "status": {},
            "ats_daily": pd.DataFrame(),
            "recent": [],
        }

    columns = _columns(connection, "n8n_results")
    status_column = _first(
        columns,
        ["n8n_status", "status", "run_status"],
    )
    ats = _first(columns, ["final_ats_score", "ats_score"])
    writer = _first(
        columns,
        ["writer_success", "resume_writer_success"],
    )
    pages = _first(
        columns,
        [
            "verified_pdf_pages",
            "verified_pdf_page_count",
            "pdf_pages",
        ],
    )
    timestamp = _timestamp_column(connection, "n8n_results")

    total = _count(connection, "n8n_results")
    avg_ats = (
        round(
            float(
                _scalar(
                    connection,
                    f'SELECT AVG(COALESCE("{ats}",0)) FROM n8n_results',
                    default=0,
                )
            ),
            1,
        )
        if ats
        else 0.0
    )
    max_ats = (
        round(
            float(
                _scalar(
                    connection,
                    f'SELECT MAX(COALESCE("{ats}",0)) FROM n8n_results',
                    default=0,
                )
            ),
            1,
        )
        if ats
        else 0.0
    )
    status = _breakdown(
        connection,
        "n8n_results",
        status_column,
    )
    ready = sum(
        count
        for label, count in status.items()
        if label.lower() == "application_ready"
    )
    review = sum(
        count
        for label, count in status.items()
        if label.lower()
        in {
            "ats_review_required",
            "completed_with_warnings",
            "completed_without_writer",
            "placement_or_verified_keyword_review_required",
        }
    )
    failed = sum(
        count
        for label, count in status.items()
        if "fail" in label.lower()
    )

    writer_success = 0
    if writer:
        writer_success = _count(
            connection,
            "n8n_results",
            f'COALESCE("{writer}",0)=1',
        )
    else:
        url = _first(columns, ["resume_pdf_url", "resume_doc_url"])
        if url:
            writer_success = _count(
                connection,
                "n8n_results",
                f'TRIM(COALESCE("{url}",\'\'))<>\'\'',
            )
    writer_failed = max(total - writer_success, 0)

    one_page = (
        _count(
            connection,
            "n8n_results",
            f'CAST(COALESCE("{pages}",0) AS INTEGER)=1',
        )
        if pages
        else 0
    )

    ats_daily = pd.DataFrame(columns=["date", "average_ats"])
    if timestamp and ats:
        rows = _rows(
            connection,
            f'''
            SELECT
                date("{timestamp}",'localtime') AS date,
                ROUND(AVG(COALESCE("{ats}",0)),1) AS average_ats
            FROM n8n_results
            WHERE datetime("{timestamp}")>=datetime('now','-14 days')
            GROUP BY date("{timestamp}",'localtime')
            ORDER BY date
            ''',
        )
        if rows:
            ats_daily = pd.DataFrame(rows)
            ats_daily["date"] = pd.to_datetime(ats_daily["date"])
            ats_daily["average_ats"] = pd.to_numeric(
                ats_daily["average_ats"],
                errors="coerce",
            ).fillna(0)

    selected = [
        column
        for column in (
            "id",
            "job_id",
            status_column,
            ats,
            "ats_gate_status",
            writer,
            pages,
            "resume_doc_url",
            "resume_pdf_url",
            "error_message",
            timestamp,
        )
        if column and column in columns
    ]
    recent = []
    if selected:
        recent = _rows(
            connection,
            f'''
            SELECT {", ".join(f'"{column}"' for column in selected)}
            FROM n8n_results
            ORDER BY "{timestamp or 'id'}" DESC
            LIMIT 25
            ''',
        )

    return {
        "total": total,
        "avg_ats": avg_ats,
        "max_ats": max_ats,
        "ready": ready,
        "ready_rate": _pct(ready, total),
        "review": review,
        "failed": failed,
        "writer_success": writer_success,
        "writer_failed": writer_failed,
        "writer_rate": _pct(writer_success, total),
        "one_page": one_page,
        "one_page_rate": _pct(one_page, total),
        "status": status,
        "ats_daily": ats_daily,
        "recent": recent,
        "latest": _latest_timestamp(connection, "n8n_results"),
    }


def _sources(connection: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(connection, "source_health"):
        return {
            "total": 0,
            "enabled": 0,
            "disabled": 0,
            "stale": 0,
            "rows": [],
        }

    columns = _columns(connection, "source_health")
    name = _first(columns, ["source_name", "name"])
    enabled_column = _first(columns, ["enabled", "is_enabled"])
    health = _first(columns, ["health_status", "status"])
    last_run = _first(columns, ["last_run_at", "updated_at"])

    total = _count(connection, "source_health")
    enabled = (
        _count(
            connection,
            "source_health",
            f'COALESCE("{enabled_column}",0)=1',
        )
        if enabled_column
        else 0
    )

    select_map = {
        "source": name,
        "enabled": enabled_column,
        "tier": _first(columns, ["source_tier", "tier"]),
        "health": health,
        "cadence_minutes": _first(
            columns,
            ["cadence_minutes", "cadence"],
        ),
        "last_run_at": last_run,
        "jobs_found_last_run": _first(
            columns,
            ["jobs_found_last_run", "jobs_found"],
        ),
        "last_error": _first(columns, ["last_error", "error"]),
        "cost_mode": _first(columns, ["cost_mode"]),
    }
    expressions = [
        f'"{column}" AS "{alias}"'
        for alias, column in select_map.items()
        if column
    ]
    rows = _rows(
        connection,
        f'''
        SELECT {", ".join(expressions)}
        FROM source_health
        ORDER BY
            COALESCE("{enabled_column}",0) DESC,
            "{name or 'rowid'}"
        ''',
    ) if expressions else []

    stale = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        if not bool(row.get("enabled")):
            continue
        parsed = _parse_dt(row.get("last_run_at"))
        if parsed is None:
            stale += 1
        elif (
            now - parsed.astimezone(timezone.utc)
        ).total_seconds() > 10800:
            stale += 1

    return {
        "total": total,
        "enabled": enabled,
        "disabled": max(total - enabled, 0),
        "stale": stale,
        "rows": rows,
        "health": _breakdown(
            connection,
            "source_health",
            health,
        ),
        "latest": _latest_timestamp(connection, "source_health"),
    }


def _events(connection: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(connection, "events"):
        return {
            "total": 0,
            "last_24h": 0,
            "top": {},
            "recent": [],
            "callbacks": 0,
            "dispatches": 0,
            "duplicates": 0,
            "failures": 0,
        }

    columns = _columns(connection, "events")
    event_type = _first(columns, ["event_type", "type"])
    timestamp = _timestamp_column(connection, "events")
    actor = _first(columns, ["actor"])
    job_id = _first(columns, ["job_id"])
    status = _first(columns, ["event_status", "status"])
    details = _first(columns, ["payload", "payload_json", "details"])

    total = _count(connection, "events")
    last_24h = (
        _count(
            connection,
            "events",
            f'datetime("{timestamp}")>=datetime("now","-24 hours")',
        )
        if timestamp
        else 0
    )
    top = _breakdown(connection, "events", event_type)

    select_map = {
        "id": "id" if "id" in columns else None,
        "timestamp": timestamp,
        "event_type": event_type,
        "job_id": job_id,
        "actor": actor,
        "status": status,
        "details": details,
    }
    expressions = [
        f'"{column}" AS "{alias}"'
        for alias, column in select_map.items()
        if column
    ]
    recent = _rows(
        connection,
        f'''
        SELECT {", ".join(expressions)}
        FROM events
        ORDER BY "{timestamp or 'id'}" DESC
        LIMIT 50
        ''',
    ) if expressions else []
    for row in recent:
        if "details" in row:
            row["details"] = _clean(row["details"])[:180]

    callbacks = dispatches = duplicates = failures = 0
    for label, count in top.items():
        lower = label.lower()
        if "callback" in lower:
            callbacks += count
        if "dispatch" in lower:
            dispatches += count
        if "duplicate" in lower:
            duplicates += count
        if "fail" in lower or "error" in lower:
            failures += count

    return {
        "total": total,
        "last_24h": last_24h,
        "top": dict(list(top.items())[:25]),
        "recent": recent,
        "callbacks": callbacks,
        "dispatches": dispatches,
        "duplicates": duplicates,
        "failures": failures,
        "latest": _latest_timestamp(connection, "events"),
    }


def _quality(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(connection, "jobs"):
        return []
    columns = _columns(connection, "jobs")
    total = _count(connection, "jobs")
    checks = [
        ("Missing company", ["company_name", "company"]),
        ("Missing title", ["title", "job_title"]),
        ("Missing location", ["location", "job_location"]),
        (
            "Missing description",
            ["description", "job_description", "description_raw"],
        ),
        ("Missing source", ["source", "source_name"]),
        ("Missing salary", ["salary", "salary_text", "pay_range"]),
        ("Missing job URL", ["job_url", "url", "posting_url"]),
    ]
    output = []
    for label, names in checks:
        column = _first(columns, names)
        if not column:
            continue
        count = _count(
            connection,
            "jobs",
            f'TRIM(COALESCE(CAST("{column}" AS TEXT),\'\'))=\'\'',
        )
        output.append(
            {
                "check": label,
                "count": count,
                "pct": _pct(count, total),
            }
        )
    return output


def _n8n_executions() -> dict[str, Any]:
    if not N8N_DB.exists():
        return {"available": False, "active": [], "recent": []}

    try:
        connection = _ro_connect(N8N_DB)
    except Exception as error:
        return {
            "available": False,
            "active": [],
            "recent": [],
            "error": str(error),
        }

    try:
        if not _table_exists(connection, "execution_entity"):
            return {"available": False, "active": [], "recent": []}

        columns = _columns(connection, "execution_entity")
        selected = [
            column
            for column in (
                "id",
                "workflowId",
                "status",
                "finished",
                "mode",
                "startedAt",
                "stoppedAt",
            )
            if column in columns
        ]

        recent = _rows(
            connection,
            f"""
            SELECT {", ".join(f'"{column}"' for column in selected)}
            FROM execution_entity
            ORDER BY CAST(id AS INTEGER) DESC
            LIMIT 20
            """,
        )

        for row in recent:
            start = _parse_dt(row.get("startedAt"))
            stop = _parse_dt(row.get("stoppedAt"))
            row["started_et"] = _format_et(row.get("startedAt"))

            if start:
                end = stop or datetime.now(timezone.utc)
                seconds = max(
                    0,
                    int(
                        (
                            end.astimezone(timezone.utc)
                            - start.astimezone(timezone.utc)
                        ).total_seconds()
                    ),
                )
                if seconds < 60:
                    row["duration"] = f"{seconds}s"
                elif seconds < 3600:
                    row["duration"] = f"{seconds // 60}m {seconds % 60}s"
                else:
                    row["duration"] = (
                        f"{seconds // 3600}h "
                        f"{(seconds % 3600) // 60}m"
                    )
            else:
                row["duration"] = "—"

        active_statuses = {"new", "running", "waiting"}
        active = [
            row
            for row in recent
            if (
                str(row.get("status") or "").strip().lower()
                in active_statuses
                and not row.get("stoppedAt")
            )
        ]

        return {
            "available": True,
            "active": active,
            "recent": recent,
        }
    finally:
        connection.close()



def _insights(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    jobs = snapshot["jobs"]
    queue = snapshot["queue"]
    results = snapshot["results"]
    sources = snapshot["sources"]
    services = snapshot["services"]
    output: list[dict[str, str]] = []

    offline = [
        name
        for name, service in services.items()
        if not service.get("online")
    ]
    if offline:
        output.append(
            {
                "severity": "critical",
                "title": "Service attention required",
                "body": "Offline: " + ", ".join(offline),
            }
        )
    else:
        output.append(
            {
                "severity": "good",
                "title": "Core services healthy",
                "body": (
                    "FastAPI, n8n, Ollama, and the Telegram listener "
                    "are online."
                ),
            }
        )

    if queue["open"]:
        output.append(
            {
                "severity": "warning",
                "title": "Open n8n work detected",
                "body": (
                    f"{queue['open']} queue item(s) are open. "
                    f"Oldest age: {queue['oldest_age']}."
                ),
            }
        )
    else:
        output.append(
            {
                "severity": "good",
                "title": "Queue clear",
                "body": (
                    f"No open queue items. Historical reliability: "
                    f"{queue['reliability']:.1f}%."
                ),
            }
        )

    if results["review"] > results["ready"]:
        output.append(
            {
                "severity": "warning",
                "title": "ATS review is the main bottleneck",
                "body": (
                    f"{results['review']} results require review versus "
                    f"{results['ready']} application-ready."
                ),
            }
        )

    if results["writer_rate"] < 85 and results["total"]:
        output.append(
            {
                "severity": "warning",
                "title": "Writer success below target",
                "body": (
                    f"Writer success is {results['writer_rate']:.1f}%. "
                    "Prioritize page-count and document-generation failures."
                ),
            }
        )

    if sources["stale"]:
        output.append(
            {
                "severity": "warning",
                "title": "Source freshness needs attention",
                "body": (
                    f"{sources['stale']} enabled source(s) have no run "
                    "within the last three hours."
                ),
            }
        )
    else:
        output.append(
            {
                "severity": "good",
                "title": "Discovery state is live",
                "body": (
                    f"{sources['enabled']} of {sources['total']} sources "
                    "are enabled and current."
                ),
            }
        )

    if jobs["today_delta"]:
        output.append(
            {
                "severity": "info",
                "title": "Discovery-volume change",
                "body": (
                    f"{jobs['today']} jobs today, "
                    f"{jobs['today_delta']:+d} versus yesterday."
                ),
            }
        )

    source_rows = jobs.get("sources") or []
    if source_rows:
        top = max(
            source_rows,
            key=lambda row: (
                float(row.get("high_score") or 0),
                float(row.get("stored") or 0),
            ),
        )
        output.append(
            {
                "severity": "info",
                "title": "Best current source",
                "body": (
                    f"{top.get('source','Unknown')} produced "
                    f"{int(top.get('high_score') or 0)} high-score jobs "
                    f"from {int(top.get('stored') or 0)} stored."
                ),
            }
        )
    return output[:8]


@st.cache_data(ttl=15, show_spinner=False)
def load_snapshot(
    db_path: str,
    runtime_json: str,
    scoring_json: str,
) -> dict[str, Any]:
    runtime = json.loads(runtime_json or "{}")
    scoring = json.loads(scoring_json or "{}")
    connection = _ro_connect(db_path)
    try:
        jobs = _jobs(connection)
        queue = _queue(connection)
        results = _results(connection)
        sources = _sources(connection)
        events = _events(connection)
        quality = _quality(connection)
        integrity = _scalar(
            connection,
            "PRAGMA integrity_check",
            default="unknown",
        )
    finally:
        connection.close()

    services = {
        "FastAPI": _service(
            "FastAPI",
            endpoint_url("fastapi", "/health"),
        ),
        "n8n": _service(
            "n8n",
            endpoint_url("n8n", "/healthz"),
        ),
        "Ollama": _service(
            "Ollama",
            endpoint_url("ollama", "/api/tags"),
        ),
        "Telegram": {
            "name": "Telegram",
            "online": _process_count(
                r"python.*-m\s+app\.telegram_listener"
            )
            == 1,
            "latency_ms": None,
            "status_code": None,
            "error": "",
        },
    }
    executions = _n8n_executions()

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_et": datetime.now(ET).strftime(
            "%b %d, %Y · %I:%M:%S %p ET"
        ),
        "runtime": runtime,
        "scoring": scoring,
        "database_integrity": str(integrity),
        "jobs": jobs,
        "queue": queue,
        "results": results,
        "sources": sources,
        "events": events,
        "quality": quality,
        "services": services,
        "executions": executions,
        "thresholds": {
            "high": int(
                scoring.get(
                    "telegram_high_alert_threshold",
                    85,
                )
                or 85
            ),
            "auto": int(
                scoring.get("auto_n8n_threshold", 93) or 93
            ),
            "auto_limit": int(
                scoring.get("daily_auto_n8n_limit", 7) or 7
            ),
            "manual_limit": int(
                scoring.get("daily_manual_n8n_limit", 25) or 25
            ),
        },
    }
    snapshot["insights"] = _insights(snapshot)
    return snapshot


def _css() -> None:
    st.markdown(
        '''
        <style>
        .stApp {
            background:
                radial-gradient(circle at 92% 0%, rgba(48,112,190,.12), transparent 27%),
                radial-gradient(circle at 5% 8%, rgba(124,88,210,.09), transparent 24%),
                #0b0f14;
        }
        .block-container {
            max-width: 1700px;
            padding-top: 1rem;
            padding-bottom: 3rem;
        }
        [data-testid="stSidebar"] {
            background:#0e141c;
            border-right:1px solid rgba(255,255,255,.07);
        }
        div[data-testid="stMetric"] {
            background:linear-gradient(180deg,#171f2b,#101720);
            border:1px solid rgba(255,255,255,.08);
            border-radius:16px;
            padding:.88rem 1rem;
            min-height:112px;
            box-shadow:0 12px 28px rgba(0,0,0,.16);
        }
        div[data-testid="stMetric"] label {
            color:#9aa8b7 !important;
            font-size:.77rem !important;
        }
        div[data-testid="stMetricValue"] {
            font-size:1.72rem !important;
            letter-spacing:-.03em;
        }
        .cc-shell {
            display:flex;
            justify-content:space-between;
            gap:1rem;
            align-items:flex-start;
            margin:0 0 .75rem 0;
            padding:1.05rem 1.2rem;
            border:1px solid rgba(255,255,255,.08);
            border-radius:18px;
            background:linear-gradient(135deg,#171f2b,#0e151e);
            box-shadow:0 16px 42px rgba(0,0,0,.20);
        }
        .cc-title {
            color:#f6f8fb;
            font-size:1.46rem;
            font-weight:760;
            letter-spacing:-.025em;
        }
        .cc-subtitle {
            color:#92a2b2;
            font-size:.86rem;
            margin-top:.2rem;
        }
        .cc-version {
            font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
            color:#8fbcff;
            font-size:.72rem;
            border:1px solid rgba(91,156,255,.28);
            border-radius:999px;
            padding:.32rem .56rem;
            white-space:nowrap;
        }
        .health-grid {
            display:grid;
            grid-template-columns:repeat(6,minmax(0,1fr));
            gap:.58rem;
            margin:.45rem 0 .92rem 0;
        }
        .health-card {
            border:1px solid rgba(255,255,255,.07);
            border-radius:14px;
            padding:.68rem .74rem;
            background:#111923;
        }
        .health-card.good { border-color:rgba(54,211,153,.30); }
        .health-card.warn { border-color:rgba(245,186,73,.32); }
        .health-card.bad { border-color:rgba(255,97,110,.35); }
        .health-name {
            color:#93a3b3;
            font-size:.68rem;
            text-transform:uppercase;
            letter-spacing:.075em;
        }
        .health-value {
            color:#f3f7fb;
            font-size:.91rem;
            font-weight:700;
            margin-top:.22rem;
        }
        .health-meta {
            color:#748493;
            font-size:.69rem;
            margin-top:.12rem;
        }
        .insight-card {
            border:1px solid rgba(255,255,255,.075);
            border-left-width:4px;
            border-radius:14px;
            background:#111923;
            padding:.78rem .9rem;
            margin-bottom:.56rem;
        }
        .insight-card.good { border-left-color:#36d399; }
        .insight-card.warning { border-left-color:#f5ba49; }
        .insight-card.critical { border-left-color:#ff616e; }
        .insight-card.info { border-left-color:#5b9cff; }
        .insight-title {
            color:#f2f5f8;
            font-weight:700;
            font-size:.87rem;
        }
        .insight-body {
            color:#9cabb9;
            font-size:.77rem;
            line-height:1.42;
            margin-top:.18rem;
        }
        .section-label {
            color:#edf2f7;
            font-size:1rem;
            font-weight:730;
            letter-spacing:-.015em;
            margin:.35rem 0 .48rem 0;
        }
        .muted {
            color:#8494a4;
            font-size:.75rem;
        }
        .funnel-step {
            background:#111923;
            border:1px solid rgba(255,255,255,.075);
            border-radius:14px;
            padding:.8rem;
            text-align:center;
            min-height:100px;
        }
        .funnel-label {
            color:#90a0b0;
            font-size:.68rem;
            text-transform:uppercase;
            letter-spacing:.065em;
        }
        .funnel-value {
            color:#f7f9fc;
            font-size:1.45rem;
            font-weight:770;
            margin-top:.18rem;
        }
        .funnel-rate {
            color:#718190;
            font-size:.69rem;
            margin-top:.15rem;
        }
        .pill {
            display:inline-block;
            border:1px solid rgba(255,255,255,.10);
            border-radius:999px;
            background:#151e29;
            color:#c8d2dc;
            padding:.24rem .5rem;
            font-size:.69rem;
            margin:.08rem .16rem .08rem 0;
        }
        div[data-testid="stDataFrame"] {
            border:1px solid rgba(255,255,255,.07);
            border-radius:14px;
            overflow:hidden;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap:.2rem;
            background:rgba(9,14,20,.55);
            padding:.23rem;
            border-radius:12px;
        }
        .stTabs [data-baseweb="tab"] {
            border-radius:9px;
            padding:.4rem .7rem;
        }
        @media (max-width:1100px) {
            .health-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
        }
        </style>
        ''',
        unsafe_allow_html=True,
    )


def render_app_shell_header() -> None:
    _css()
    st.markdown(
        f'''
        <div class="cc-shell">
            <div>
                <div class="cc-title">Aadil HR Hunter</div>
                <div class="cc-subtitle">
                    Technical command center · discovery, scoring, Telegram, n8n, and application delivery
                </div>
            </div>
            <div class="cc-version">COMMAND CENTER v{VERSION}</div>
        </div>
        ''',
        unsafe_allow_html=True,
    )


def _health(snapshot: dict[str, Any]) -> None:
    services = snapshot["services"]
    queue = snapshot["queue"]
    active_count = len(snapshot["executions"].get("active") or [])

    columns = st.columns(6)
    for column, name in zip(
        columns[:4],
        ("FastAPI", "n8n", "Ollama", "Telegram"),
    ):
        service = services[name]
        online = bool(service.get("online"))
        latency = service.get("latency_ms")
        detail = (
            f"{float(latency):.1f} ms"
            if isinstance(latency, (int, float))
            else "listener state"
        )
        with column:
            st.metric(
                name,
                "ONLINE" if online else "OFFLINE",
                delta=detail,
                delta_color="normal" if online else "inverse",
            )

    with columns[4]:
        st.metric(
            "n8n Queue",
            "CLEAR" if queue["open"] == 0 else f"{queue['open']} OPEN",
            delta=f"p95 {queue['p95']:.0f}s",
            delta_color="normal" if queue["open"] == 0 else "inverse",
        )

    with columns[5]:
        st.metric(
            "Execution",
            "IDLE" if active_count == 0 else f"{active_count} ACTIVE",
            delta="production runtime",
            delta_color="normal" if active_count == 0 else "inverse",
        )



def _controls(snapshot: dict[str, Any]) -> None:
    left, middle, right = st.columns([1, 1.8, 1.4])
    with left:
        if st.button(
            "↻ Refresh data",
            use_container_width=True,
            key="cc_v2_refresh",
        ):
            st.cache_data.clear()
            st.rerun()
    with middle:
        st.caption(
            f"Snapshot: {snapshot['generated_et']} · cache TTL 15 seconds"
        )
    with right:
        st.markdown(
            f'''
            <div class="muted" style="text-align:right">
                <a href="{endpoint_url("n8n")}" target="_blank">n8n</a>
                · <a href="{endpoint_url("fastapi", "/docs")}" target="_blank">FastAPI</a>
                · <a href="{endpoint_url("streamlit")}" target="_blank">Streamlit</a>
            </div>
            ''',
            unsafe_allow_html=True,
        )


def _kpis(snapshot: dict[str, Any]) -> None:
    jobs = snapshot["jobs"]
    queue = snapshot["queue"]
    results = snapshot["results"]
    sources = snapshot["sources"]
    active = len(snapshot["executions"].get("active") or [])

    row = st.columns(8)
    row[0].metric(
        "Jobs today",
        _num(jobs["today"]),
        delta=f"{jobs['today_delta']:+d} vs yesterday",
    )
    row[1].metric(
        "Stored jobs",
        _num(jobs["total"]),
        delta=f"{jobs['last_7']} in 7 days",
    )
    row[2].metric(
        "High matches",
        _num(jobs["elite"] + jobs["high"]),
        delta=f"{jobs['elite']} elite",
    )
    row[3].metric(
        "Sent to n8n",
        _num(jobs["sent"]),
        delta=f"{jobs['dispatch_rate']:.1f}% of stored",
    )
    row[4].metric(
        "Open queue",
        _num(queue["open"]),
        delta=f"{queue['reliability']:.1f}% reliability",
    )
    row[5].metric(
        "Active executions",
        _num(active),
        delta="idle" if active == 0 else "running",
    )
    row[6].metric(
        "Application-ready",
        _num(results["ready"]),
        delta=f"{results['ready_rate']:.1f}% of results",
    )
    row[7].metric(
        "Average ATS",
        _num(results["avg_ats"], 1),
        delta=f"max {results['max_ats']:.0f}",
    )

    second = st.columns(6)
    second[0].metric(
        "Writer success",
        f"{results['writer_rate']:.1f}%",
        delta=f"{results['writer_success']} successful",
    )
    second[1].metric(
        "One-page verified",
        f"{results['one_page_rate']:.1f}%",
        delta=f"{results['one_page']} resumes",
    )
    second[2].metric(
        "ATS review required",
        _num(results["review"]),
        delta="main bottleneck"
        if results["review"] > results["ready"]
        else "controlled",
        delta_color="inverse",
    )
    second[3].metric(
        "Enabled sources",
        _num(sources["enabled"]),
        delta=f"{sources['disabled']} disabled",
    )
    second[4].metric(
        "Events · 24h",
        _num(snapshot["events"]["last_24h"]),
        delta=f"{snapshot['events']['total']:,} lifetime",
    )
    second[5].metric(
        "DB integrity",
        str(snapshot["database_integrity"]).upper(),
        delta="read-only analytics",
    )


def _funnel(snapshot: dict[str, Any]) -> None:
    jobs = snapshot["jobs"]
    results = snapshot["results"]
    stages = [
        ("Stored", jobs["total"], 100.0),
        (
            "75+ Matches",
            jobs["elite"] + jobs["high"] + jobs["good"],
            _pct(
                jobs["elite"] + jobs["high"] + jobs["good"],
                jobs["total"],
            ),
        ),
        ("Sent to n8n", jobs["sent"], _pct(jobs["sent"], jobs["total"])),
        (
            "Results stored",
            results["total"],
            _pct(results["total"], jobs["sent"]),
        ),
        (
            "Writer success",
            results["writer_success"],
            _pct(results["writer_success"], results["total"]),
        ),
        (
            "Application-ready",
            results["ready"],
            _pct(results["ready"], results["total"]),
        ),
    ]

    for column, (label, value, rate) in zip(
        st.columns(len(stages)),
        stages,
    ):
        with column:
            st.metric(
                label,
                _num(value),
                delta=f"{rate:.1f}% conversion",
            )



def _insight_cards(snapshot: dict[str, Any]) -> None:
    icon_map = {
        "good": "✓",
        "warning": "!",
        "critical": "×",
        "info": "i",
    }

    for insight in snapshot["insights"]:
        severity = insight.get("severity", "info")
        icon = icon_map.get(severity, "i")

        with st.container(border=True):
            icon_column, text_column = st.columns([0.10, 0.90])

            with icon_column:
                if severity == "good":
                    st.success(icon)
                elif severity in {"warning", "critical"}:
                    st.warning(icon)
                else:
                    st.info(icon)

            with text_column:
                st.markdown(f"**{insight.get('title', '')}**")
                st.caption(insight.get("body", ""))



def _breakdown_frame(
    values: dict[str, int],
    label: str,
) -> pd.DataFrame:
    friendly = {
        "ats_review_required": "ATS review",
        "completed_with_warnings": "Warnings",
        "completed_without_writer": "No writer",
        "application_ready": "App ready",
        "completed": "Completed",
        "failed": "Failed",
        "error": "Error",
        "canceled": "Canceled",
        "cancelled": "Canceled",
    }

    rows = []
    for key, count in values.items():
        display = str(key)
        if label == "Result":
            display = friendly.get(
                display.strip().lower(),
                display.replace("_", " ").strip().title(),
            )
        elif len(display) > 28:
            display = display[:25] + "..."

        rows.append(
            {
                label: display,
                "Count": int(count or 0),
            }
        )

    return pd.DataFrame(rows)



def _overview(snapshot: dict[str, Any]) -> None:
    st.markdown(
        '<div class="section-label">Operational funnel</div>',
        unsafe_allow_html=True,
    )
    _funnel(snapshot)

    left, right = st.columns([1.65, 1])

    with left:
        st.markdown(
            '<div class="section-label">Discovery velocity · 14 days</div>',
            unsafe_allow_html=True,
        )
        daily = snapshot["jobs"]["daily"]

        if daily.empty:
            st.info("No timestamped job data is available.")
        else:
            st.line_chart(
                daily.set_index("date")["count"],
                height=280,
            )

        first_chart, second_chart = st.columns(2)

        with first_chart:
            st.markdown(
                '<div class="section-label">Hunter score distribution</div>',
                unsafe_allow_html=True,
            )
            chart = snapshot["jobs"]["score_chart"].copy()
            chart["Band"] = pd.Categorical(
                chart["Band"],
                categories=[
                    "Below 75",
                    "75–84",
                    "85–92",
                    "93–100",
                ],
                ordered=True,
            )
            chart = chart.sort_values("Band")
            st.bar_chart(
                chart.set_index("Band")["Jobs"],
                height=280,
            )

        with second_chart:
            st.markdown(
                '<div class="section-label">n8n result distribution</div>',
                unsafe_allow_html=True,
            )
            frame = _breakdown_frame(
                snapshot["results"]["status"],
                "Result",
            )

            if frame.empty:
                st.info("No n8n result-status data is available.")
            else:
                st.bar_chart(
                    frame.set_index("Result")["Count"],
                    height=280,
                )

    with right:
        st.markdown(
            '<div class="section-label">System intelligence</div>',
            unsafe_allow_html=True,
        )
        _insight_cards(snapshot)

        st.markdown(
            '<div class="section-label">Current execution</div>',
            unsafe_allow_html=True,
        )
        active = snapshot["executions"].get("active") or []
        recent = snapshot["executions"].get("recent") or []

        if active:
            for execution in active:
                st.info(
                    f"Running execution #{execution.get('id')} · "
                    f"{execution.get('status')} · "
                    f"{execution.get('duration')}"
                )
        elif recent:
            latest = recent[0]
            latest_status = str(
                latest.get("status") or "unknown"
            ).lower()

            if latest_status == "success":
                st.success(
                    f"No active execution. Latest #{latest.get('id')} "
                    f"completed successfully in "
                    f"{latest.get('duration')}."
                )
            elif latest_status in {
                "error",
                "failed",
                "canceled",
                "cancelled",
            }:
                st.warning(
                    f"No active execution. Latest #{latest.get('id')} "
                    f"ended as {latest_status} after "
                    f"{latest.get('duration')}."
                )
            else:
                st.info(
                    f"No active execution. Latest #{latest.get('id')} · "
                    f"{latest_status} · {latest.get('duration')}."
                )
        else:
            st.info("No execution history is available.")

        thresholds = snapshot["thresholds"]
        st.markdown(
            '<div class="section-label">Thresholds and limits</div>',
            unsafe_allow_html=True,
        )
        threshold_columns = st.columns(2)
        threshold_columns[0].metric(
            "High alert",
            f"{thresholds['high']}+",
        )
        threshold_columns[1].metric(
            "Auto n8n",
            f"{thresholds['auto']}+",
        )
        threshold_columns[0].metric(
            "Daily auto limit",
            thresholds["auto_limit"],
        )
        threshold_columns[1].metric(
            "Daily manual limit",
            thresholds["manual_limit"],
        )



def _pipeline(snapshot: dict[str, Any]) -> None:
    st.markdown(
        '<div class="section-label">ATS trend · 14 days</div>',
        unsafe_allow_html=True,
    )
    ats_daily = snapshot["results"]["ats_daily"]
    if ats_daily.empty:
        st.info("No timestamped ATS trend data is available.")
    else:
        st.line_chart(
            ats_daily.set_index("date")["average_ats"],
            height=300,
        )

    queue = snapshot["queue"]
    results = snapshot["results"]
    row = st.columns(6)
    row[0].metric("Queue p50", f"{queue['p50']:.0f}s")
    row[1].metric("Queue p95", f"{queue['p95']:.0f}s")
    row[2].metric(
        "Dispatch reliability",
        f"{queue['reliability']:.1f}%",
    )
    row[3].metric("Writer failures", results["writer_failed"])
    row[4].metric("Result failures", results["failed"])
    row[5].metric("Ready rate", f"{results['ready_rate']:.1f}%")

    left, right = st.columns(2)
    with left:
        st.markdown(
            '<div class="section-label">Recent n8n results</div>',
            unsafe_allow_html=True,
        )
        frame = pd.DataFrame(results["recent"])
        if frame.empty:
            st.info("No n8n results are available.")
        else:
            st.dataframe(
                frame,
                use_container_width=True,
                hide_index=True,
                height=430,
            )
    with right:
        st.markdown(
            '<div class="section-label">Recent n8n executions</div>',
            unsafe_allow_html=True,
        )
        frame = pd.DataFrame(
            snapshot["executions"].get("recent") or []
        )
        if frame.empty:
            st.info("No n8n execution history is available.")
        else:
            preferred = [
                column
                for column in (
                    "id",
                    "status",
                    "mode",
                    "started_et",
                    "duration",
                    "workflowId",
                )
                if column in frame.columns
            ]
            st.dataframe(
                frame[preferred],
                use_container_width=True,
                hide_index=True,
                height=430,
            )

    st.markdown(
        '<div class="section-label">Open queue detail</div>',
        unsafe_allow_html=True,
    )
    open_frame = pd.DataFrame(queue["open_rows"])
    if open_frame.empty:
        st.success("No open n8n queue rows.")
    else:
        st.dataframe(
            open_frame,
            use_container_width=True,
            hide_index=True,
        )


def _sources_view(snapshot: dict[str, Any]) -> None:
    sources = snapshot["sources"]
    source_jobs = snapshot["jobs"]["sources"]

    row = st.columns(5)
    row[0].metric("Enabled", sources["enabled"])
    row[1].metric("Disabled", sources["disabled"])
    row[2].metric("Stale enabled", sources["stale"])
    best = (
        max(
            source_jobs,
            key=lambda item: float(item.get("high_score") or 0),
        ).get("source")
        if source_jobs
        else "—"
    )
    row[3].metric("Best source", best)
    best_yield = max(
        (
            float(item.get("high_yield_pct") or 0)
            for item in source_jobs
        ),
        default=0.0,
    )
    row[4].metric("Best high-score yield", f"{best_yield:.1f}%")

    left, right = st.columns([1.25, 1])
    with left:
        st.markdown(
            '<div class="section-label">Source yield and dispatch</div>',
            unsafe_allow_html=True,
        )
        frame = pd.DataFrame(source_jobs)
        if frame.empty:
            st.info("No source-level job data is available.")
        else:
            st.dataframe(
                frame,
                use_container_width=True,
                hide_index=True,
                height=520,
            )
    with right:
        st.markdown(
            '<div class="section-label">Source health</div>',
            unsafe_allow_html=True,
        )
        frame = pd.DataFrame(sources["rows"])
        if frame.empty:
            st.info("No source-health data is available.")
        else:
            st.dataframe(
                frame,
                use_container_width=True,
                hide_index=True,
                height=520,
            )

    frame = pd.DataFrame(source_jobs)
    if not frame.empty and {
        "source",
        "stored",
        "high_score",
    }.issubset(frame.columns):
        st.markdown(
            '<div class="section-label">Stored vs high-score jobs</div>',
            unsafe_allow_html=True,
        )
        st.bar_chart(
            frame.set_index("source")[["stored", "high_score"]],
            height=320,
        )


def _reliability(snapshot: dict[str, Any]) -> None:
    queue = snapshot["queue"]
    events = snapshot["events"]
    results = snapshot["results"]

    row = st.columns(6)
    row[0].metric(
        "Queue reliability",
        f"{queue['reliability']:.1f}%",
    )
    row[1].metric("Queue failures", queue["failed"])
    row[2].metric("Callback events", events["callbacks"])
    row[3].metric("Dispatch events", events["dispatches"])
    row[4].metric("Writer failures", results["writer_failed"])
    row[5].metric("Logged failures", events["failures"])

    left, right = st.columns([1.1, 1])
    with left:
        st.markdown(
            '<div class="section-label">Event taxonomy</div>',
            unsafe_allow_html=True,
        )
        frame = _breakdown_frame(events["top"], "Event type")
        if frame.empty:
            st.info("No event taxonomy is available.")
        else:
            st.dataframe(
                frame,
                use_container_width=True,
                hide_index=True,
                height=500,
            )
    with right:
        st.markdown(
            '<div class="section-label">Data-quality exceptions</div>',
            unsafe_allow_html=True,
        )
        frame = pd.DataFrame(snapshot["quality"])
        if frame.empty:
            st.info("No supported data-quality checks are available.")
        else:
            st.dataframe(
                frame,
                use_container_width=True,
                hide_index=True,
                height=500,
            )

    st.markdown(
        '<div class="section-label">Recent operational events</div>',
        unsafe_allow_html=True,
    )
    frame = pd.DataFrame(events["recent"])
    if frame.empty:
        st.info("No recent event rows are available.")
    else:
        st.dataframe(
            frame,
            use_container_width=True,
            hide_index=True,
            height=540,
        )


def _recent_jobs(snapshot: dict[str, Any]) -> None:
    left, right = st.columns([1.65, 1])
    with left:
        st.markdown(
            '<div class="section-label">Latest jobs</div>',
            unsafe_allow_html=True,
        )
        frame = pd.DataFrame(snapshot["jobs"]["recent"])
        if frame.empty:
            st.info("No recent jobs are available.")
        else:
            st.dataframe(
                frame,
                use_container_width=True,
                hide_index=True,
                height=620,
            )
    with right:
        st.markdown(
            '<div class="section-label">Job status mix</div>',
            unsafe_allow_html=True,
        )
        frame = _breakdown_frame(
            snapshot["jobs"]["status"],
            "Status",
        )
        if frame.empty:
            st.info("No job-status field is available.")
        else:
            st.bar_chart(
                frame.set_index("Status")["Count"],
                height=300,
            )
            st.dataframe(
                frame,
                use_container_width=True,
                hide_index=True,
            )


def render_command_center_v2(
    *,
    get_connection: Any = None,
    db_path: str | Path,
    runtime: dict[str, Any] | None = None,
    scoring: dict[str, Any] | None = None,
    latest_n8n_callback: dict[str, Any] | None = None,
) -> None:
    runtime = runtime or {}
    scoring = scoring or {}
    snapshot = load_snapshot(
        str(Path(db_path).expanduser()),
        json.dumps(runtime, sort_keys=True, default=str),
        json.dumps(scoring, sort_keys=True, default=str),
    )

    _controls(snapshot)
    _health(snapshot)
    _kpis(snapshot)

    overview, pipeline, sources, reliability, recent_jobs = st.tabs(
        [
            "Overview",
            "Pipeline & ATS",
            "Sources",
            "Reliability",
            "Recent Jobs",
        ]
    )
    with overview:
        _overview(snapshot)
    with pipeline:
        _pipeline(snapshot)
    with sources:
        _sources_view(snapshot)
    with reliability:
        _reliability(snapshot)
    with recent_jobs:
        _recent_jobs(snapshot)

    with st.expander("Technical snapshot", expanded=False):
        st.json(
            {
                "version": VERSION,
                "generated_at": snapshot["generated_at"],
                "database_integrity": snapshot["database_integrity"],
                "freshness": {
                    "jobs": snapshot["jobs"]["latest"],
                    "queue": snapshot["queue"]["latest"],
                    "results": snapshot["results"]["latest"],
                    "sources": snapshot["sources"]["latest"],
                    "events": snapshot["events"]["latest"],
                },
                "latest_n8n_callback": latest_n8n_callback or {},
                "runtime": runtime,
                "thresholds": snapshot["thresholds"],
            }
        )
