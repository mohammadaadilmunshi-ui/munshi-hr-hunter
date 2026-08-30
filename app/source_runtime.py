from __future__ import annotations

from typing import Any

from app.database import get_connection
from app.time_utils import (
    get_display_timezone_name,
    local_iso,
    now_local,
    now_utc,
    parse_utc_timestamp,
    utc_iso,
)


def get_source_runtime_state(
    source_name: str,
) -> dict[str, Any]:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT *
            FROM source_health
            WHERE lower(source_name) = lower(?)
            """,
            (source_name,),
        ).fetchone()
    finally:
        connection.close()

    timezone_name = (
        get_display_timezone_name()
    )
    checked_at_utc = now_utc()

    if row is None:
        return {
            "exists": False,
            "source_name": source_name,
            "enabled": False,
            "due": False,
            "reason": "source_not_configured",
            "timezone": timezone_name,
            "checked_at_utc": (
                checked_at_utc.isoformat()
            ),
            "checked_at_local": (
                checked_at_utc
                .astimezone(
                    now_local().tzinfo
                )
                .isoformat()
            ),
        }

    source = dict(row)

    enabled = bool(
        source.get("enabled")
    )
    cadence_minutes = int(
        source.get("cadence_minutes")
        or 60
    )

    raw_last_run = source.get(
        "last_run_at"
    )
    last_run = parse_utc_timestamp(
        raw_last_run
    )

    if not enabled:
        due = False
        reason = "source_disabled"
        elapsed_minutes = None

    elif last_run is None:
        due = True
        reason = "never_run"
        elapsed_minutes = None

    else:
        elapsed_minutes = (
            checked_at_utc - last_run
        ).total_seconds() / 60

        due = (
            elapsed_minutes
            >= cadence_minutes
        )

        reason = (
            "cadence_due"
            if due
            else "cadence_not_due"
        )

    return {
        "exists": True,
        "source_name": source[
            "source_name"
        ],
        "enabled": enabled,
        "due": due,
        "reason": reason,
        "cadence_minutes": (
            cadence_minutes
        ),
        "timezone": timezone_name,
        "checked_at_utc": (
            checked_at_utc.isoformat()
        ),
        "checked_at_local": (
            checked_at_utc
            .astimezone(
                now_local().tzinfo
            )
            .isoformat()
        ),
        "last_run_at": raw_last_run,
        "last_run_at_utc": utc_iso(
            raw_last_run
        ),
        "last_run_at_local": local_iso(
            raw_last_run
        ),
        "elapsed_minutes": (
            round(elapsed_minutes, 2)
            if elapsed_minutes
            is not None
            else None
        ),
        "cost_mode": source.get(
            "cost_mode"
        ),
        "health_status": source.get(
            "health_status"
        ),
    }
