from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.database import get_setting


DEFAULT_TIMEZONE = "America/New_York"


def get_display_timezone_name() -> str:
    runtime = get_setting(
        "runtime",
        {},
    ) or {}

    configured = str(
        runtime.get("timezone") or ""
    ).strip()

    if configured:
        try:
            ZoneInfo(configured)
            return configured
        except ZoneInfoNotFoundError:
            pass

    system_timezone = (
        datetime.now()
        .astimezone()
        .tzinfo
    )

    system_key = getattr(
        system_timezone,
        "key",
        None,
    )

    if system_key:
        try:
            ZoneInfo(system_key)
            return str(system_key)
        except ZoneInfoNotFoundError:
            pass

    return DEFAULT_TIMEZONE


def get_display_timezone() -> ZoneInfo:
    return ZoneInfo(
        get_display_timezone_name()
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    return now_utc().astimezone(
        get_display_timezone()
    )


def parse_utc_timestamp(
    value: object,
) -> datetime | None:
    text = str(value or "").strip()

    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(
                text,
                "%Y-%m-%d %H:%M:%S",
            )
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(timezone.utc)


def utc_iso(
    value: object,
) -> str | None:
    parsed = parse_utc_timestamp(value)

    if parsed is None:
        return None

    return parsed.isoformat()


def local_iso(
    value: object,
) -> str | None:
    parsed = parse_utc_timestamp(value)

    if parsed is None:
        return None

    return parsed.astimezone(
        get_display_timezone()
    ).isoformat()
