from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import get_connection, get_setting

SCHEDULE_TABLE = "source_random_schedule"

RATE_LIMIT_TERMS = (
    "429", "403", "captcha", "rate limit", "rate_limit",
    "too many requests", "too many 429", "/sorry/",
    "google.com/sorry", "unusual traffic",
    "verify you are human", "access denied",
    "temporarily blocked",
)

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEDULE_TABLE} (
    source_name TEXT PRIMARY KEY COLLATE NOCASE,
    next_run_at TEXT,
    last_scheduled_at TEXT,
    last_started_at TEXT,
    last_completed_at TEXT,
    base_cadence_minutes INTEGER NOT NULL DEFAULT 60,
    jitter_minutes INTEGER NOT NULL DEFAULT 0,
    schedule_reason TEXT NOT NULL DEFAULT 'seeded',
    schedule_state TEXT NOT NULL DEFAULT 'cooldown',
    last_worker_status TEXT,
    last_worker_returncode INTEGER,
    consecutive_scheduler_failures INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_RNG = secrets.SystemRandom()


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def normalize_source_name(value: str) -> str:
    return "".join(
        ch for ch in str(value or "").lower() if ch.isalnum()
    )


def _source_schedule_policy() -> dict[str, Any]:
    runtime = get_setting("provider_runtime", {}) or {}
    policy = runtime.get("source_schedule") if isinstance(runtime, dict) else None
    if not isinstance(policy, dict):
        raise RuntimeError("Canonical provider_runtime.source_schedule is not configured.")
    return policy


def _policy_number(name: str, *, integer: bool = True) -> int | float:
    value = _source_schedule_policy().get(name)
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise RuntimeError(f"Canonical source schedule value is invalid: {name}") from None


def _retired_source_keys() -> set[str]:
    registry = get_setting("source_worker_registry", {}) or {}
    return {
        normalize_source_name(value)
        for value in (registry.get("retired_source_keys") or [])
    }


def _minimum_cadence_by_source() -> dict[str, int]:
    configured = _source_schedule_policy().get("minimum_cadence_minutes_by_source") or {}
    if not isinstance(configured, dict):
        raise RuntimeError("minimum_cadence_minutes_by_source must be a mapping.")
    return {
        normalize_source_name(name): max(1, int(minutes))
        for name, minutes in configured.items()
    }


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
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
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _is_rate_limited(value: Any) -> bool:
    text = str(value or "").lower()
    return any(term in text for term in RATE_LIMIT_TERMS)


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(SCHEMA_SQL)
    connection.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{SCHEDULE_TABLE}_next_run
        ON {SCHEDULE_TABLE}(next_run_at)
        """
    )


def _load_source_row(
    source_name: str,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owned = connection is None
    db = connection or get_connection()
    try:
        row = db.execute(
            """
            SELECT * FROM source_health
            WHERE lower(source_name) = lower(?)
            LIMIT 1
            """,
            (source_name,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        if owned:
            db.close()


def get_effective_cadence_minutes(
    source_name: str,
    row: Any = None,
) -> int:
    data = _row_to_dict(row) if row is not None else _load_source_row(source_name)
    resolved = str(data.get("source_name") or source_name or "Unknown")
    default_minimum = int(_policy_number("default_minimum_cadence_minutes"))
    configured = max(1, _integer(data.get("cadence_minutes"), default_minimum))
    minimum = _minimum_cadence_by_source().get(
        normalize_source_name(resolved),
        default_minimum,
    )
    return max(configured, minimum)


def _jitter_range(base_minutes: int) -> tuple[int, int]:
    base = max(1, int(base_minutes))
    low = max(
        int(_policy_number("jitter_floor_minutes")),
        round(base * float(_policy_number("jitter_min_ratio", integer=False))),
    )
    high = max(
        low,
        min(
            int(_policy_number("jitter_ceiling_minutes")),
            round(base * float(_policy_number("jitter_max_ratio", integer=False))),
        ),
    )
    return low, high


def _random_jitter(base_minutes: int) -> int:
    low, high = _jitter_range(base_minutes)
    return _RNG.randint(low, high)


def _random_start_delay(base_minutes: int) -> int:
    high = min(
        int(_policy_number("startup_delay_ceiling_minutes")),
        max(
            int(_policy_number("startup_delay_floor_max_minutes")),
            int(base_minutes) // int(_policy_number("startup_base_divisor")),
        ),
    )
    return _RNG.randint(
        int(_policy_number("startup_delay_min_minutes")),
        high,
    )


def _schedule_row(
    connection: sqlite3.Connection,
    source_name: str,
) -> dict[str, Any]:
    ensure_schema(connection)
    row = connection.execute(
        f"""
        SELECT * FROM {SCHEDULE_TABLE}
        WHERE lower(source_name) = lower(?)
        LIMIT 1
        """,
        (source_name,),
    ).fetchone()
    return _row_to_dict(row)


def _write_schedule(
    connection: sqlite3.Connection,
    *,
    source_name: str,
    next_run_at: datetime | None,
    base_cadence_minutes: int,
    jitter_minutes: int,
    schedule_reason: str,
    schedule_state: str,
    last_worker_status: str | None = None,
    last_worker_returncode: int | None = None,
    consecutive_scheduler_failures: int | None = None,
    started: bool = False,
    completed: bool = False,
) -> None:
    existing = _schedule_row(connection, source_name)
    failures = (
        int(consecutive_scheduler_failures)
        if consecutive_scheduler_failures is not None
        else _integer(existing.get("consecutive_scheduler_failures"), 0)
    )
    connection.execute(
        f"""
        INSERT INTO {SCHEDULE_TABLE} (
            source_name, next_run_at, last_scheduled_at,
            last_started_at, last_completed_at,
            base_cadence_minutes, jitter_minutes,
            schedule_reason, schedule_state,
            last_worker_status, last_worker_returncode,
            consecutive_scheduler_failures, updated_at
        )
        VALUES (
            ?, ?, CURRENT_TIMESTAMP,
            CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,
            CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,
            ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
        )
        ON CONFLICT(source_name) DO UPDATE SET
            next_run_at = excluded.next_run_at,
            last_scheduled_at = CURRENT_TIMESTAMP,
            last_started_at = CASE
                WHEN ? THEN CURRENT_TIMESTAMP
                ELSE {SCHEDULE_TABLE}.last_started_at
            END,
            last_completed_at = CASE
                WHEN ? THEN CURRENT_TIMESTAMP
                ELSE {SCHEDULE_TABLE}.last_completed_at
            END,
            base_cadence_minutes = excluded.base_cadence_minutes,
            jitter_minutes = excluded.jitter_minutes,
            schedule_reason = excluded.schedule_reason,
            schedule_state = excluded.schedule_state,
            last_worker_status = COALESCE(
                excluded.last_worker_status,
                {SCHEDULE_TABLE}.last_worker_status
            ),
            last_worker_returncode = COALESCE(
                excluded.last_worker_returncode,
                {SCHEDULE_TABLE}.last_worker_returncode
            ),
            consecutive_scheduler_failures =
                excluded.consecutive_scheduler_failures,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            source_name, iso(next_run_at),
            int(started), int(completed),
            int(base_cadence_minutes), int(jitter_minutes),
            schedule_reason, schedule_state,
            last_worker_status, last_worker_returncode,
            failures, int(started), int(completed),
        ),
    )



def clear_source_schedule(source_name: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = _load_source_row(source_name, connection)
        resolved = str(row.get("source_name") or source_name or "Unknown").strip()
        base = get_effective_cadence_minutes(resolved, row=row)
        _write_schedule(
            connection,
            source_name=resolved,
            next_run_at=None,
            base_cadence_minutes=base,
            jitter_minutes=0,
            schedule_reason="source_disabled",
            schedule_state="disabled",
        )
        connection.commit()
        return _schedule_row(connection, resolved)
    finally:
        connection.close()


def ensure_source_schedule(
    source_name: str,
    *,
    row: Any = None,
    force_reseed: bool = False,
) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        data = (
            _row_to_dict(row)
            if row is not None
            else _load_source_row(source_name, connection)
        )
        resolved = str(
            data.get("source_name") or source_name or "Unknown"
        ).strip()
        enabled = _integer(data.get("enabled"), 0) == 1
        normalized = normalize_source_name(resolved)
        base = get_effective_cadence_minutes(resolved, row=data)
        existing = _schedule_row(connection, resolved)

        retired_keys = _retired_source_keys()
        if not enabled or normalized in retired_keys:
            _write_schedule(
                connection,
                source_name=resolved,
                next_run_at=None,
                base_cadence_minutes=base,
                jitter_minutes=0,
                schedule_reason=(
                    "retired_source"
                    if normalized in retired_keys
                    else "source_disabled"
                ),
                schedule_state="disabled",
            )
            connection.commit()
            return _schedule_row(connection, resolved)

        existing_next = _parse_timestamp(existing.get("next_run_at"))
        if existing_next is not None and not force_reseed:
            return existing

        current = now_utc()
        anchors = [
            _parse_timestamp(data.get("last_run_at")),
            _parse_timestamp(data.get("last_failure_at")),
            _parse_timestamp(data.get("last_success_at")),
        ]
        anchor = max((x for x in anchors if x is not None), default=None)
        jitter = _random_jitter(base)
        normal_due = (
            anchor + timedelta(minutes=base + jitter)
            if anchor is not None
            else None
        )

        if normal_due is not None and normal_due > current:
            next_run = normal_due
            reason = "last_run_plus_base_and_random_delay"
        else:
            jitter = _random_start_delay(base)
            next_run = current + timedelta(minutes=jitter)
            reason = "randomized_startup_spread"

        if _is_rate_limited(data.get("last_error")):
            failure_anchor = (
                _parse_timestamp(data.get("last_failure_at"))
                or _parse_timestamp(data.get("last_run_at"))
                or current
            )
            backoff = max(
                base,
                int(_policy_number("rate_limit_base_minutes")),
            ) + _RNG.randint(
                int(_policy_number("rate_limit_jitter_min_minutes")),
                int(_policy_number("rate_limit_jitter_max_minutes")),
            )
            rate_due = failure_anchor + timedelta(minutes=backoff)
            if rate_due > next_run:
                next_run = rate_due
                jitter = max(0, backoff - base)
                reason = "rate_limit_backoff_plus_random_delay"

        _write_schedule(
            connection,
            source_name=resolved,
            next_run_at=next_run,
            base_cadence_minutes=base,
            jitter_minutes=jitter,
            schedule_reason=reason,
            schedule_state=(
                "rate_limited"
                if "rate_limit" in reason
                else "cooldown"
            ),
        )
        connection.commit()
        return _schedule_row(connection, resolved)
    finally:
        connection.close()


def schedule_source_randomly(
    source_name: str,
    *,
    reason: str = "normal_completed_run",
    state: str = "cooldown",
    minimum_delay_minutes: int | None = None,
    maximum_delay_minutes: int | None = None,
    worker_status: str | None = None,
    worker_returncode: int | None = None,
    consecutive_failures: int | None = None,
    completed: bool = False,
) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = _load_source_row(source_name, connection)
        resolved = str(
            row.get("source_name") or source_name or "Unknown"
        ).strip()
        base = get_effective_cadence_minutes(resolved, row=row)

        if minimum_delay_minutes is None:
            jitter = _random_jitter(base)
            delay = base + jitter
        else:
            low = max(1, int(minimum_delay_minutes))
            high = max(
                low,
                int(
                    maximum_delay_minutes
                    if maximum_delay_minutes is not None
                    else low
                ),
            )
            delay = _RNG.randint(low, high)
            jitter = max(0, delay - base)

        next_run = now_utc() + timedelta(minutes=delay)
        _write_schedule(
            connection,
            source_name=resolved,
            next_run_at=next_run,
            base_cadence_minutes=base,
            jitter_minutes=jitter,
            schedule_reason=reason,
            schedule_state=state,
            last_worker_status=worker_status,
            last_worker_returncode=worker_returncode,
            consecutive_scheduler_failures=consecutive_failures,
            completed=completed,
        )
        connection.commit()
        return _schedule_row(connection, resolved)
    finally:
        connection.close()


def mark_source_started(
    source_name: str,
    reservation_minutes: int | None = None,
) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = _load_source_row(source_name, connection)
        resolved = str(
            row.get("source_name") or source_name or "Unknown"
        ).strip()
        base = get_effective_cadence_minutes(resolved, row=row)
        existing = _schedule_row(connection, resolved)
        reservation = (
            int(reservation_minutes)
            if reservation_minutes is not None
            else int(_policy_number("running_reservation_minutes"))
        )
        _write_schedule(
            connection,
            source_name=resolved,
            next_run_at=now_utc() + timedelta(
                minutes=max(
                    int(_policy_number("running_reservation_floor_minutes")),
                    reservation,
                )
            ),
            base_cadence_minutes=base,
            jitter_minutes=0,
            schedule_reason="worker_running_reservation",
            schedule_state="running",
            consecutive_scheduler_failures=_integer(
                existing.get("consecutive_scheduler_failures"),
                0,
            ),
            started=True,
        )
        connection.commit()
        return _schedule_row(connection, resolved)
    finally:
        connection.close()


def mark_source_completed(
    source_name: str,
    *,
    success: bool,
    blocked: bool = False,
    deferred: bool = False,
    worker_status: str | None = None,
    worker_returncode: int | None = None,
) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = _load_source_row(source_name, connection)
        resolved = str(
            row.get("source_name") or source_name or "Unknown"
        ).strip()
        base = get_effective_cadence_minutes(resolved, row=row)
        existing = _schedule_row(connection, resolved)
        failures = _integer(
            existing.get("consecutive_scheduler_failures"),
            0,
        )

        if deferred:
            low = int(_policy_number("active_work_deferral_min_minutes"))
            high = int(_policy_number("active_work_deferral_max_minutes"))
            reason = "active_work_random_deferral"
            state = "deferred"
        elif blocked:
            failures += 1
            backoff_base = max(base, int(_policy_number("rate_limit_base_minutes")))
            low = min(
                int(_policy_number("blocked_low_ceiling_minutes")),
                backoff_base * min(
                    int(_policy_number("blocked_multiplier_cap")), failures
                ),
            )
            high = min(
                int(_policy_number("blocked_high_ceiling_minutes")),
                low + int(_policy_number("blocked_high_extension_minutes")),
            )
            reason = "rate_limit_random_backoff"
            state = "rate_limited"
        elif success:
            failures = 0
            low = high = None
            reason = "normal_completed_run_randomized"
            state = "cooldown"
        else:
            failures += 1
            failure_base = min(
                max(base, int(_policy_number("rate_limit_base_minutes"))),
                int(_policy_number("failure_base_minutes"))
                * (
                    2
                    ** min(
                        max(failures - 1, 0),
                        int(_policy_number("failure_exponent_cap")),
                    )
                ),
            )
            low = max(int(_policy_number("failure_minimum_minutes")), failure_base)
            high = min(
                max(base, int(_policy_number("rate_limit_base_minutes")))
                + int(_policy_number("failure_high_base_extension_minutes")),
                low + int(_policy_number("failure_high_extension_minutes")),
            )
            reason = "failure_random_backoff"
            state = "failure_backoff"

        if low is None:
            jitter = _random_jitter(base)
            delay = base + jitter
        else:
            delay = _RNG.randint(int(low), int(high))
            jitter = max(0, delay - base)

        _write_schedule(
            connection,
            source_name=resolved,
            next_run_at=now_utc() + timedelta(minutes=delay),
            base_cadence_minutes=base,
            jitter_minutes=jitter,
            schedule_reason=reason,
            schedule_state=state,
            last_worker_status=worker_status,
            last_worker_returncode=worker_returncode,
            consecutive_scheduler_failures=failures,
            completed=not deferred,
        )
        connection.commit()
        return _schedule_row(connection, resolved)
    finally:
        connection.close()


def format_remaining(seconds: int | float | None) -> str:
    total = max(0, int(seconds or 0))
    if total <= 0:
        return "Ready now"
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or (not days and not hours):
        parts.append(f"{minutes}m")
    if not days and not hours and minutes < 5 and seconds:
        parts.append(f"{seconds}s")
    return " ".join(parts[:3])


def _format_next_run(value: datetime | None) -> str:
    if value is None:
        return "Not scheduled"
    try:
        from zoneinfo import ZoneInfo
        return value.astimezone(
            ZoneInfo("America/New_York")
        ).strftime("%b %-d, %Y · %-I:%M %p ET")
    except Exception:
        return value.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )


def get_adapter_timer(
    source_name: str,
    row: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    data = (
        _row_to_dict(row)
        if row is not None
        else _load_source_row(source_name)
    )
    resolved = str(
        data.get("source_name") or source_name or "Unknown"
    ).strip()
    normalized = normalize_source_name(resolved)
    enabled = _integer(data.get("enabled"), 0) == 1
    base = get_effective_cadence_minutes(resolved, row=data)
    schedule = ensure_source_schedule(resolved, row=data)

    current = now or now_utc()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    next_run = _parse_timestamp(schedule.get("next_run_at"))
    schedule_state = str(schedule.get("schedule_state") or "cooldown")
    seconds_remaining = (
        max(0, int((next_run - current).total_seconds()))
        if next_run is not None
        else 0
    )
    retired_keys = _retired_source_keys()
    due = bool(
        enabled
        and normalized not in retired_keys
        and next_run is not None
        and seconds_remaining <= 0
        and schedule_state != "running"
    )

    if not enabled or normalized in retired_keys:
        state = "disabled"
        reason = "Source is disabled."
    elif schedule_state == "running":
        state = "running"
        reason = "Worker is running or reserved."
    elif due:
        state = "ready"
        reason = "Randomized cooldown has expired."
    elif schedule_state == "rate_limited":
        state = "rate_limited"
        reason = "Rate-limit backoff plus random delay is active."
    elif schedule_state == "failure_backoff":
        state = "failure_backoff"
        reason = "Failure backoff plus random delay is active."
    elif schedule_state == "deferred":
        state = "deferred"
        reason = "Run was randomly deferred because production work was active."
    else:
        state = "cooldown"
        reason = "Base cooldown plus random delay is active."

    low_jitter, high_jitter = _jitter_range(base)
    return {
        "source_name": resolved,
        "enabled": enabled and normalized not in retired_keys,
        "configured_cadence_minutes": max(
            1,
            _integer(
                data.get("cadence_minutes"),
                int(_policy_number("default_minimum_cadence_minutes")),
            ),
        ),
        "effective_cadence_minutes": base,
        "minimum_cadence_minutes": (
            _minimum_cadence_by_source().get(
                normalized,
                int(_policy_number("default_minimum_cadence_minutes")),
            )
        ),
        "random_jitter_min_minutes": low_jitter,
        "random_jitter_max_minutes": high_jitter,
        "randomized_schedule": True,
        "schedule_reason": schedule.get("schedule_reason"),
        "schedule_state": schedule_state,
        "next_allowed_at": iso(next_run),
        "next_allowed_display": _format_next_run(next_run),
        "seconds_remaining": seconds_remaining,
        "remaining_display": format_remaining(seconds_remaining),
        "due": due,
        "state": state,
        "reason": reason,
        "rate_limited": state == "rate_limited",
        "consecutive_failures": _integer(
            data.get("consecutive_failures"), 0
        ),
        "scheduler_failures": _integer(
            schedule.get("consecutive_scheduler_failures"), 0
        ),
        "health_status": str(
            data.get("health_status") or "not_tested"
        ),
        "last_error": (
            str(data.get("last_error"))
            if data.get("last_error") not in (None, "")
            else None
        ),
    }


def list_adapter_timers(
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT * FROM source_health
            {"WHERE enabled = 1" if enabled_only else ""}
            ORDER BY source_tier, source_name
            """
        ).fetchall()
        current = now_utc()
        return [
            get_adapter_timer(
                str(row["source_name"]),
                row=row,
                now=current,
            )
            for row in rows
        ]
    finally:
        connection.close()


def add_timer_columns(frame: Any) -> Any:
    if frame is None or not hasattr(frame, "copy"):
        return frame
    try:
        result = frame.copy()
    except Exception:
        return frame
    if (
        not hasattr(result, "columns")
        or "source_name" not in result.columns
    ):
        return result

    timers = [
        get_adapter_timer(
            str(row.get("source_name") or ""),
            row=row.to_dict(),
        )
        for _, row in result.iterrows()
    ]
    values = {
        "effective_cadence_minutes": [
            timer["effective_cadence_minutes"] for timer in timers
        ],
        "random_delay_window": [
            (
                f"+{timer['random_jitter_min_minutes']}"
                f"–{timer['random_jitter_max_minutes']} min"
                if timer["enabled"]
                else "Disabled"
            )
            for timer in timers
        ],
        "run_state": [timer["state"] for timer in timers],
        "run_timer": [timer["remaining_display"] for timer in timers],
        "next_allowed_at": [
            timer["next_allowed_display"] for timer in timers
        ],
    }
    for name in values:
        if name in result.columns:
            result = result.drop(columns=[name])
    index = (
        list(result.columns).index("cadence_minutes") + 1
        if "cadence_minutes" in result.columns
        else len(result.columns)
    )
    for offset, name in enumerate(
        (
            "effective_cadence_minutes",
            "random_delay_window",
            "run_state",
            "run_timer",
            "next_allowed_at",
        )
    ):
        result.insert(index + offset, name, values[name])
    return result


def is_source_ready(source_name: str) -> bool:
    return bool(get_adapter_timer(source_name).get("due"))


def get_source_runtime_state(source_name: str) -> dict[str, Any]:
    return get_adapter_timer(source_name)


def mark_source_run(
    source_name: str,
    *,
    status: str,
    http_code: int | None = None,
) -> dict[str, Any]:
    status_text = str(status or "").lower()
    blocked = (
        http_code in (403, 429)
        or status_text in {"rate_limited", "blocked", "captcha"}
    )
    success = (
        status_text in {"healthy", "success", "completed"}
        and not blocked
    )
    return mark_source_completed(
        source_name,
        success=success,
        blocked=blocked,
        worker_status=status_text,
        worker_returncode=0 if success else 1,
    )


def seed_all_enabled_sources(
    *,
    force_reseed: bool = True,
) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT * FROM source_health
            WHERE enabled = 1
            ORDER BY source_tier, source_name
            """
        ).fetchall()
    finally:
        connection.close()
    return [
        ensure_source_schedule(
            str(row["source_name"]),
            row=row,
            force_reseed=force_reseed,
        )
        for row in rows
    ]

# AADIL_SOURCE_COOLDOWN_DB_WRITE_RETRY_V1
_aadil_source_cooldown_write_before_retry_v1 = _write_schedule


def _write_schedule(*args: Any, **kwargs: Any) -> Any:
    """Serialize schedule writes and retry transient SQLite lock collisions."""
    import fcntl
    import random
    import time
    from pathlib import Path

    lock_path = Path(
        "/tmp/aadil_hr_hunter_source_metrics_existing_scheduler_v1.lock"
    )

    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            attempts = int(_policy_number("sqlite_write_retry_attempts"))
            for attempt in range(1, attempts + 1):
                try:
                    return _aadil_source_cooldown_write_before_retry_v1(
                        *args,
                        **kwargs,
                    )
                except sqlite3.OperationalError as error:
                    if "locked" not in str(error).casefold():
                        raise
                    if attempt >= attempts:
                        raise
                    delay = min(
                        float(_policy_number("sqlite_write_retry_cap_seconds", integer=False)),
                        float(_policy_number("sqlite_write_retry_base_seconds", integer=False))
                        * (2 ** (attempt - 1)),
                    )
                    delay += random.uniform(
                        float(_policy_number("sqlite_write_retry_jitter_min_seconds", integer=False)),
                        float(_policy_number("sqlite_write_retry_jitter_max_seconds", integer=False)),
                    )
                    time.sleep(delay)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

# AADIL_SOURCE_LIFECYCLE_FINALIZATION_RETRY_V2
_aadil_mark_source_started_before_lifecycle_v2 = mark_source_started
_aadil_mark_source_completed_before_lifecycle_v2 = mark_source_completed


def mark_source_started(
    source_name: str,
    reservation_minutes: int | None = None,
) -> dict[str, Any]:
    import time as _time

    last_error: Exception | None = None
    delays = _source_schedule_policy().get("lifecycle_start_retry_delays_seconds")
    if not isinstance(delays, list) or not delays:
        raise RuntimeError("lifecycle_start_retry_delays_seconds is not configured.")
    for delay in (float(value) for value in delays):
        if delay:
            _time.sleep(delay)
        try:
            return _aadil_mark_source_started_before_lifecycle_v2(
                source_name,
                reservation_minutes=reservation_minutes,
            )
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).casefold():
                raise
            last_error = error
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to reserve source lifecycle row.")


def mark_source_completed(
    source_name: str,
    *,
    success: bool,
    blocked: bool = False,
    deferred: bool = False,
    worker_status: str | None = None,
    worker_returncode: int | None = None,
) -> dict[str, Any]:
    import time as _time

    last_error: Exception | None = None
    delays = _source_schedule_policy().get("lifecycle_completion_retry_delays_seconds")
    if not isinstance(delays, list) or not delays:
        raise RuntimeError("lifecycle_completion_retry_delays_seconds is not configured.")
    for delay in (float(value) for value in delays):
        if delay:
            _time.sleep(delay)
        try:
            return _aadil_mark_source_completed_before_lifecycle_v2(
                source_name,
                success=success,
                blocked=blocked,
                deferred=deferred,
                worker_status=worker_status,
                worker_returncode=worker_returncode,
            )
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).casefold():
                raise
            last_error = error
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to finalize source lifecycle row.")
