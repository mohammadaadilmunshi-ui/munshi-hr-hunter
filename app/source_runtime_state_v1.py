from __future__ import annotations

import asyncio
import functools
import html
import inspect
import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Sequence


MARKER = "AADIL_SOURCE_RUNTIME_STATE_INTELLIGENCE_V1"
PROJECT = Path(
    os.environ.get(
        "AADIL_HR_HUNTER_PROJECT",
        str(Path(__file__).resolve().parent.parent),
    )
).expanduser().resolve()
DB = PROJECT / "data/hunter.db"

PENDING_HEALTH = {
    "enabled_pending_first_run",
    "installed_pending_first_run",
    "pending_first_run",
    "awaiting_first_live_run",
}
SETUP_HEALTH = {
    "needs_credentials",
    "configuration_required",
    "setup_required",
    "not_configured",
}
FAILED_HEALTH = {
    "failed",
    "error",
    "runtime_failure",
    "degraded",
}
RUNNING_WORDS = {
    "running",
    "started",
    "executing",
    "in_progress",
}
COMPLETED_WORDS = {
    "completed",
    "success",
    "successful",
    "healthy",
}
FAILURE_WORDS = {
    "failed",
    "error",
    "runtime_failure",
}
STATE_ORDER = (
    "running",
    "due_waiting_selection",
    "failed_waiting_retry",
    "completed_successfully",
    "awaiting_first_live_run",
    "setup_required",
    "disabled",
)
STATE_DISPLAY = {
    "running": ("⚙️", "Currently executing"),
    "due_waiting_selection": ("🕒", "Due, waiting for scheduler selection"),
    "failed_waiting_retry": ("❌", "Failed, waiting for retry"),
    "completed_successfully": ("✅", "Completed successfully"),
    "awaiting_first_live_run": ("⏳", "Awaiting first live run"),
    "setup_required": ("🧩", "Setup required"),
    "disabled": ("⏸", "Disabled"),
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def parse_datetime(value: Any) -> datetime | None:
    text = _clean(value)
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    candidates = [normalized]
    if "T" not in normalized and " " in normalized:
        candidates.append(normalized.replace(" ", "T", 1))

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _max_datetime(*values: Any) -> datetime | None:
    parsed = [
        item
        for item in (
            parse_datetime(value)
            for value in values
        )
        if item is not None
    ]
    return max(parsed) if parsed else None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _has_run_evidence(row: Mapping[str, Any]) -> bool:
    if any(
        parse_datetime(row.get(key)) is not None
        for key in (
            "last_run_at",
            "last_success_at",
            "last_failure_at",
            "last_started_at",
            "last_completed_at",
            "last_scheduled_at",
        )
    ):
        return True

    if any(
        _as_int(row.get(key)) > 0
        for key in (
            "jobs_found_last_run",
            "raw_jobs_last_run",
            "eligible_jobs_last_run",
            "inserted_jobs_last_run",
            "rejected_jobs_last_run",
        )
    ):
        return True

    worker = _clean(
        row.get("last_worker_status")
    ).casefold()
    return worker in (
        RUNNING_WORDS
        | COMPLETED_WORDS
        | FAILURE_WORDS
    )


def _running_from_timestamps(
    row: Mapping[str, Any],
    now: datetime,
) -> bool:
    started = parse_datetime(
        row.get("last_started_at")
    )
    completed = parse_datetime(
        row.get("last_completed_at")
    )
    if started is None:
        return False
    if completed is not None and completed >= started:
        return False

    cadence = max(
        1,
        _as_int(
            row.get(
                "base_cadence_minutes",
                row.get("cadence_minutes"),
            ),
            60,
        ),
    )
    maximum_runtime = max(
        120,
        min(720, cadence * 2),
    )
    return (
        now - started
    ) <= timedelta(
        minutes=maximum_runtime
    )


def _freshness_window_minutes(
    row: Mapping[str, Any],
) -> int:
    cadence = max(
        1,
        _as_int(
            row.get(
                "base_cadence_minutes",
                row.get("cadence_minutes"),
            ),
            60,
        ),
    )
    jitter = max(
        0,
        _as_int(
            row.get("jitter_minutes"),
            0,
        ),
    )
    grace = max(
        60,
        int(cadence * 0.25),
    )
    return max(
        180,
        cadence + jitter + grace,
    )


def classify_row(
    row: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    # AADIL_RUNTIME_STATE_TIMESTAMP_TRUTH_V1_1
    current = (
        now.astimezone(timezone.utc)
        if now is not None
        else datetime.now(timezone.utc)
    )
    enabled = _as_int(row.get("enabled"), 0) == 1
    health = _clean(row.get("health_status")).casefold()
    schedule = _clean(row.get("schedule_state")).casefold()
    worker = _clean(row.get("last_worker_status")).casefold()
    returncode = row.get("last_worker_returncode")
    next_run = parse_datetime(row.get("next_run_at"))
    run_evidence = _has_run_evidence(row)

    last_success = parse_datetime(row.get("last_success_at"))
    last_failure = parse_datetime(row.get("last_failure_at"))
    newest_outcome_is_success = bool(
        last_success is not None
        and (
            last_failure is None
            or last_success > last_failure
        )
    )
    newest_outcome_is_failure = bool(
        last_failure is not None
        and (
            last_success is None
            or last_failure >= last_success
        )
    )

    # AADIL_RUNTIME_DEFERRED_TRUTH_V16
    # Deferred scheduler reservations deliberately do not write last_completed_at.
    # Timestamp inference must therefore never turn an explicit defer into running.
    explicitly_deferred = bool(
        schedule == "deferred"
        or worker in {"deferred", "active_work_deferred"}
    )
    running = bool(
        not explicitly_deferred
        and (
            schedule in RUNNING_WORDS
            or worker in RUNNING_WORDS
            or _running_from_timestamps(row, current)
        )
    )

    schedule_failure = schedule in {
        "failure_backoff",
        "failed",
        "runtime_failure",
    }
    health_failure = health in FAILED_HEALTH
    worker_failure = (
        worker in FAILURE_WORDS
        or returncode not in (None, "", 0, "0")
    )

    failed = bool(
        (
            schedule_failure
            and not newest_outcome_is_success
        )
        or (
            health_failure
            and not newest_outcome_is_success
        )
        or (
            worker_failure
            and newest_outcome_is_failure
        )
    )
    setup = health in SETUP_HEALTH
    due = (
        enabled
        and not running
        and not failed
        and not setup
        and (
            schedule == "ready"
            or (
                next_run is not None
                and next_run <= current
            )
        )
    )

    if not enabled:
        state = "disabled"
    elif setup:
        state = "setup_required"
    elif running:
        state = "running"
    elif failed:
        state = "failed_waiting_retry"
    elif not run_evidence:
        state = "awaiting_first_live_run"
    elif due:
        state = "due_waiting_selection"
    else:
        state = "completed_successfully"

    last_activity = _max_datetime(
        row.get("last_completed_at"),
        row.get("last_run_at"),
        row.get("last_success_at"),
        row.get("last_failure_at"),
        row.get("last_started_at"),
    )
    freshness_window = _freshness_window_minutes(row)
    age_minutes = (
        int((current - last_activity).total_seconds() // 60)
        if last_activity is not None
        else None
    )
    freshness_overdue = bool(
        enabled
        and run_evidence
        and state not in {
            "running",
            "failed_waiting_retry",
            "setup_required",
            "disabled",
        }
        and age_minutes is not None
        and age_minutes > freshness_window
    )

    if state == "running":
        next_action = "Worker is executing now."
    elif state == "due_waiting_selection":
        next_action = "Waiting for the randomized scheduler to select this due source."
    elif state == "failed_waiting_retry":
        next_action = "Waiting for retry/backoff. Review the latest worker error."
    elif state == "completed_successfully":
        next_action = "Last execution completed; waiting for the next scheduled run."
    elif state == "awaiting_first_live_run":
        next_action = "No live execution evidence exists yet."
    elif state == "setup_required":
        next_action = "Complete source configuration."
    else:
        next_action = "Source is disabled."

    emoji, label = STATE_DISPLAY[state]
    return {
        **dict(row),
        "runtime_state": state,
        "runtime_emoji": emoji,
        "runtime_label": label,
        "runtime_display": f"{emoji} {label}",
        "next_action": next_action,
        "has_run_evidence": run_evidence,
        "pending_label_is_stale": bool(
            health in PENDING_HEALTH
            and run_evidence
        ),
        "last_activity_at": (
            last_activity.isoformat()
            if last_activity
            else None
        ),
        "last_activity_age_minutes": age_minutes,
        "freshness_window_minutes": freshness_window,
        "freshness_overdue": freshness_overdue,
        "next_run_at_normalized": (
            next_run.isoformat()
            if next_run
            else None
        ),
        "newest_outcome_is_success": newest_outcome_is_success,
        "newest_outcome_is_failure": newest_outcome_is_failure,
    }


def load_rows(
    db_path: Path | str = DB,
) -> list[dict[str, Any]]:
    path = Path(db_path)
    connection = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
        timeout=30,
    )
    connection.row_factory = (
        sqlite3.Row
    )
    connection.execute(
        "PRAGMA query_only=ON"
    )

    try:
        health_exists = (
            connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type='table'
                  AND name='source_health'
                """
            ).fetchone()
            is not None
        )
        if not health_exists:
            return []

        schedule_exists = (
            connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type='table'
                  AND name='source_random_schedule'
                """
            ).fetchone()
            is not None
        )

        if schedule_exists:
            query = """
                SELECT
                    h.*,
                    s.next_run_at,
                    s.last_scheduled_at,
                    s.last_started_at,
                    s.last_completed_at,
                    s.base_cadence_minutes,
                    s.jitter_minutes,
                    s.schedule_reason,
                    s.schedule_state,
                    s.last_worker_status,
                    s.last_worker_returncode,
                    s.consecutive_scheduler_failures,
                    s.updated_at
                        AS schedule_updated_at
                FROM source_health h
                LEFT JOIN source_random_schedule s
                  ON lower(s.source_name)
                   = lower(h.source_name)
                ORDER BY h.source_name
            """
        else:
            query = """
                SELECT *
                FROM source_health
                ORDER BY source_name
            """

        return [
            dict(row)
            for row in connection.execute(
                query
            ).fetchall()
        ]
    finally:
        connection.close()


def runtime_snapshot(
    db_path: Path | str = DB,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    return [
        classify_row(
            row,
            now=now,
        )
        for row in load_rows(db_path)
    ]


def grouped_snapshot(
    db_path: Path | str = DB,
    *,
    enabled_only: bool = False,
    now: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    groups = {
        key: []
        for key in STATE_ORDER
    }
    for row in runtime_snapshot(
        db_path,
        now=now,
    ):
        if (
            enabled_only
            and not _as_int(
                row.get("enabled")
            )
        ):
            continue
        groups[
            row["runtime_state"]
        ].append(row)
    return groups


def freshness_attention(
    db_path: Path | str = DB,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    rows = runtime_snapshot(
        db_path,
        now=now,
    )
    overdue = [
        row
        for row in rows
        if row[
            "freshness_overdue"
        ]
    ]
    return {
        "overdue_count": len(overdue),
        "overdue_sources": [
            row["source_name"]
            for row in overdue
        ],
        "rows": overdue,
        "policy": (
            "Cadence-aware: max(3 hours, "
            "cadence + jitter + grace). "
            "Running and failure-backoff sources "
            "are reported in their own states, "
            "not mislabeled as freshness failures."
        ),
    }


def _compact_names(
    rows: Sequence[
        Mapping[str, Any]
    ],
    *,
    limit: int = 12,
) -> str:
    names = [
        _clean(
            row.get("source_name")
        )
        for row in rows
        if _clean(
            row.get("source_name")
        )
    ]
    if not names:
        return "None"
    visible = names[:limit]
    suffix = (
        f" +{len(names)-limit} more"
        if len(names) > limit
        else ""
    )
    return ", ".join(visible) + suffix


def telegram_runtime_section(
    db_path: Path | str = DB,
) -> str:
    groups = grouped_snapshot(
        db_path,
        enabled_only=False,
    )
    freshness = freshness_attention(
        db_path
    )
    lines = [
        "",
        "🧭 <b>Source execution state</b>",
        "",
        (
            "🕒 <b>Due, waiting for scheduler:</b> "
            + html.escape(
                _compact_names(
                    groups[
                        "due_waiting_selection"
                    ]
                )
            )
        ),
        (
            "⚙️ <b>Currently executing:</b> "
            + html.escape(
                _compact_names(
                    groups["running"]
                )
            )
        ),
        (
            "✅ <b>Completed successfully:</b> "
            + html.escape(
                _compact_names(
                    groups[
                        "completed_successfully"
                    ]
                )
            )
        ),
        (
            "❌ <b>Failed, waiting for retry:</b> "
            + html.escape(
                _compact_names(
                    groups[
                        "failed_waiting_retry"
                    ]
                )
            )
        ),
    ]

    awaiting = groups[
        "awaiting_first_live_run"
    ]
    if awaiting:
        lines.append(
            "⏳ <b>Actually awaiting first live run:</b> "
            + html.escape(
                _compact_names(
                    awaiting
                )
            )
        )
    else:
        lines.append(
            "✅ <b>First-run truth:</b> "
            "No enabled source is genuinely "
            "awaiting its first run."
        )

    if freshness[
        "overdue_count"
    ]:
        lines.append(
            "⚠️ <b>Cadence-aware freshness:</b> "
            f"{freshness['overdue_count']} overdue: "
            + html.escape(
                ", ".join(
                    freshness[
                        "overdue_sources"
                    ][:12]
                )
            )
        )
    else:
        lines.append(
            "✅ <b>Cadence-aware freshness:</b> "
            "No enabled source is overdue."
        )

    return "\n".join(lines)


def _remove_old_awaiting_section(
    text: str,
) -> str:
    patterns = (
        re.compile(
            r"\n?⏳\s*<b>Awaiting first live run</b>"
            r"\s*\n[^\n]*(?:\n|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\n?Awaiting first live run"
            r"\s*\n[^\n]*(?:\n|$)",
            re.IGNORECASE,
        ),
    )
    result = text
    for pattern in patterns:
        result = pattern.sub(
            "\n",
            result,
        )
    return result


def _rewrite_freshness_phrase(
    text: str,
    db_path: Path | str = DB,
) -> str:
    freshness = freshness_attention(
        db_path
    )
    if freshness[
        "overdue_count"
    ]:
        replacement = (
            "Source freshness needs attention: "
            f"{freshness['overdue_count']} enabled "
            "source(s) are overdue beyond their "
            "cadence-aware window: "
            + ", ".join(
                freshness[
                    "overdue_sources"
                ][:12]
            )
        )
    else:
        replacement = (
            "Source freshness is healthy under "
            "the cadence-aware policy."
        )

    result = re.sub(
        r"Source freshness needs attention"
        r"(?:\s*\n+|\s*[:.-]\s*)?"
        r"(?:\d+\s+enabled source\(s\)"
        r"[^\n]*three hours[^\n]*)?",
        replacement,
        text,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"\d+\s+enabled source\(s\)"
        r"\s+have no run within the last "
        r"three hours\.?",
        replacement,
        result,
        flags=re.IGNORECASE,
    )
    return result


def augment_status_text(
    value: Any,
    db_path: Path | str = DB,
) -> Any:
    # AADIL_RUNTIME_AUGMENT_IDEMPOTENT_V1_1
    if not isinstance(value, str):
        return value

    marker = "🧭 <b>Source execution state</b>"
    base = value
    if marker in base:
        base = base.split(marker, 1)[0].rstrip()

    base = _remove_old_awaiting_section(base)
    base = _rewrite_freshness_phrase(base, db_path)
    section = telegram_runtime_section(db_path)

    maximum = 4000
    if len(base) + len(section) + 2 > maximum:
        available = max(500, maximum - len(section) - 20)
        base = base[:available].rstrip() + "\n…"

    return base.rstrip() + "\n\n" + section.lstrip()


def rewrite_payload(
    value: Any,
    db_path: Path | str = DB,
) -> Any:
    if isinstance(value, str):
        return augment_status_text(
            value,
            db_path,
        )
    if isinstance(value, list):
        return [
            rewrite_payload(
                item,
                db_path,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            rewrite_payload(
                item,
                db_path,
            )
            for item in value
        )
    if isinstance(value, dict):
        return {
            key: rewrite_payload(
                item,
                db_path,
            )
            for key, item in value.items()
        }
    return value


def _function_source(
    function: Callable[..., Any],
) -> str:
    try:
        return inspect.getsource(
            function
        )
    except Exception:
        return ""


def _should_wrap_function(
    function: Callable[..., Any],
) -> bool:
    source = _function_source(
        function
    ).casefold()
    if not source:
        return False

    tokens = (
        "awaiting first live run",
        "enabled_pending_first_run",
        "installed_pending_first_run",
        "source freshness",
        "last three hours",
        "source_random_schedule",
        "runtime failures",
        "ready now",
    )
    return any(
        token in source
        for token in tokens
    )


def _wrap_sync(
    function: Callable[..., Any],
) -> Callable[..., Any]:
    @functools.wraps(function)
    def wrapper(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = function(
            *args,
            **kwargs,
        )
        return rewrite_payload(
            result
        )

    setattr(
        wrapper,
        "_aadil_runtime_state_wrapper_v1",
        True,
    )
    return wrapper


def _wrap_async(
    function: Callable[..., Any],
) -> Callable[..., Any]:
    @functools.wraps(function)
    async def wrapper(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = await function(
            *args,
            **kwargs,
        )
        return rewrite_payload(
            result
        )

    setattr(
        wrapper,
        "_aadil_runtime_state_wrapper_v1",
        True,
    )
    return wrapper


def install_output_wrappers(
    namespace: MutableMapping[
        str,
        Any,
    ],
) -> list[str]:
    wrapped: list[str] = []

    for name, value in list(
        namespace.items()
    ):
        if name.startswith(
            "_aadil_"
        ):
            continue
        if not inspect.isfunction(
            value
        ):
            continue
        if getattr(
            value,
            "_aadil_runtime_state_wrapper_v1",
            False,
        ):
            continue
        if not _should_wrap_function(
            value
        ):
            continue

        namespace[name] = (
            _wrap_async(value)
            if inspect.iscoroutinefunction(
                value
            )
            else _wrap_sync(value)
        )
        wrapped.append(name)

    namespace[
        "_AADIL_RUNTIME_STATE_WRAPPED_FUNCTIONS_V1"
    ] = tuple(wrapped)
    return wrapped


def dataframe_rows(
    db_path: Path | str = DB,
) -> list[dict[str, Any]]:
    rows = runtime_snapshot(
        db_path
    )
    result: list[dict[str, Any]] = []

    for row in rows:
        result.append(
            {
                "Source": row.get(
                    "source_name"
                ),
                "Operational state": (
                    row[
                        "runtime_display"
                    ]
                ),
                "Next action": row[
                    "next_action"
                ],
                "Health label": row.get(
                    "health_status"
                ),
                "Schedule state": row.get(
                    "schedule_state"
                ),
                "Last worker": row.get(
                    "last_worker_status"
                ),
                "Return code": row.get(
                    "last_worker_returncode"
                ),
                "Last activity": row.get(
                    "last_activity_at"
                ),
                "Next run": row.get(
                    "next_run_at_normalized"
                ),
                "Freshness": (
                    "Overdue"
                    if row[
                        "freshness_overdue"
                    ]
                    else "Within cadence"
                ),
                "Cadence (min)": row.get(
                    "base_cadence_minutes",
                    row.get(
                        "cadence_minutes"
                    ),
                ),
                "Raw": row.get(
                    "raw_jobs_last_run",
                    row.get(
                        "jobs_found_last_run",
                    ),
                ),
                "Eligible": row.get(
                    "eligible_jobs_last_run"
                ),
                "New": row.get(
                    "inserted_jobs_last_run"
                ),
                "Provider": row.get(
                    "provider_used_last_run"
                ),
                "Last error": row.get(
                    "last_error"
                ),
            }
        )

    return result


def render_compact_streamlit_panel() -> None:
    import pandas as pd
    import streamlit as st

    rows = runtime_snapshot()
    groups = {
        key: [
            row
            for row in rows
            if row[
                "runtime_state"
            ]
            == key
        ]
        for key in STATE_ORDER
    }

    st.divider()
    st.subheader(
        "Live source execution state"
    )
    st.caption(
        "Operational truth derived from the "
        "scheduler, worker outcome, timestamps, "
        "and cadence. Pending-first-run labels "
        "are ignored once real run evidence exists."
    )

    columns = st.columns(4)
    columns[0].metric(
        "Due, waiting",
        len(
            groups[
                "due_waiting_selection"
            ]
        ),
    )
    columns[1].metric(
        "Executing",
        len(groups["running"]),
    )
    columns[2].metric(
        "Completed",
        len(
            groups[
                "completed_successfully"
            ]
        ),
    )
    columns[3].metric(
        "Failed / retry",
        len(
            groups[
                "failed_waiting_retry"
            ]
        ),
    )

    table = pd.DataFrame(
        dataframe_rows()
    )
    if not table.empty:
        st.dataframe(
            table,
            hide_index=True,
            use_container_width=True,
        )


def self_test() -> dict[str, Any]:
    now = datetime(
        2026,
        7,
        19,
        4,
        0,
        tzinfo=timezone.utc,
    )

    due = classify_row(
        {
            "source_name": "Due",
            "enabled": 1,
            "health_status": "healthy",
            "schedule_state": "ready",
            "next_run_at": "2026-07-19T03:59:00+00:00",
            "last_completed_at": "2026-07-19T02:00:00+00:00",
            "last_worker_status": "completed",
            "last_worker_returncode": 0,
            "base_cadence_minutes": 180,
        },
        now=now,
    )
    running = classify_row(
        {
            "source_name": "Running",
            "enabled": 1,
            "health_status": "healthy",
            "schedule_state": "ready",
            "last_started_at": "2026-07-19T03:58:00+00:00",
            "last_completed_at": "2026-07-19T02:00:00+00:00",
            "base_cadence_minutes": 180,
        },
        now=now,
    )
    completed = classify_row(
        {
            "source_name": "Completed",
            "enabled": 1,
            "health_status": "enabled_pending_first_run",
            "schedule_state": "cooldown",
            "next_run_at": "2026-07-19T06:00:00+00:00",
            "last_completed_at": "2026-07-19T03:00:00+00:00",
            "last_worker_status": "completed",
            "last_worker_returncode": 0,
            "raw_jobs_last_run": 10,
            "base_cadence_minutes": 180,
        },
        now=now,
    )
    failed = classify_row(
        {
            "source_name": "Failed",
            "enabled": 1,
            "health_status": "installed_pending_first_run",
            "schedule_state": "failure_backoff",
            "last_completed_at": "2026-07-19T03:00:00+00:00",
            "last_worker_status": "failed",
            "last_worker_returncode": 1,
            "base_cadence_minutes": 180,
        },
        now=now,
    )
    six_hour_fresh = classify_row(
        {
            "source_name": "SixHour",
            "enabled": 1,
            "health_status": "healthy",
            "schedule_state": "cooldown",
            "last_completed_at": "2026-07-19T00:30:00+00:00",
            "last_worker_status": "completed",
            "last_worker_returncode": 0,
            "base_cadence_minutes": 360,
            "jitter_minutes": 30,
        },
        now=now,
    )

    return {
        "success": True,
        "marker": MARKER,
        "due_state": due[
            "runtime_state"
        ],
        "running_state": running[
            "runtime_state"
        ],
        "completed_state": completed[
            "runtime_state"
        ],
        "failed_state": failed[
            "runtime_state"
        ],
        "pending_label_corrected_for_display": (
            completed[
                "pending_label_is_stale"
            ]
        ),
        "six_hour_source_not_false_stale": (
            not six_hour_fresh[
                "freshness_overdue"
            ]
        ),
        "provider_calls": 0,
        "telegram_calls": 0,
        "n8n_calls": 0,
        "database_writes": 0,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            self_test(),
            indent=2,
        )
    )
