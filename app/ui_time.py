from __future__ import annotations

import os
from datetime import date, datetime, time, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UTC = timezone.utc
_ZONEINFO_MARKERS = ("/zoneinfo/", "/zoneinfo.default/")


def system_timezone() -> tzinfo:
    """Return the Mac's current timezone without hardcoding the owner's location."""
    env_name = os.environ.get("TZ", "").strip()
    if env_name:
        try:
            return ZoneInfo(env_name)
        except ZoneInfoNotFoundError:
            pass

    try:
        target = str(Path("/etc/localtime").resolve())
        for marker in _ZONEINFO_MARKERS:
            if marker in target:
                return ZoneInfo(target.split(marker, 1)[1])
    except (OSError, RuntimeError, ZoneInfoNotFoundError):
        pass

    return datetime.now().astimezone().tzinfo or UTC


def timezone_label(*, tz: tzinfo | None = None, at: datetime | None = None) -> str:
    active = tz or system_timezone()
    moment = at or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    local = moment.astimezone(active)
    key = getattr(active, "key", None)
    abbreviation = local.tzname() or "local time"
    return f"{key} · {abbreviation}" if key else abbreviation


def parse_timestamp(value: Any, *, naive_timezone: tzinfo = UTC) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    else:
        text_value = str(value).strip()
        if not text_value or text_value.casefold() in {"none", "null", "nat"}:
            return None
        normalized = text_value[:-1] + "+00:00" if text_value.endswith("Z") else text_value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return parsed.replace(tzinfo=naive_timezone) if parsed.tzinfo is None else parsed


def to_local(value: Any, *, tz: tzinfo | None = None, naive_timezone: tzinfo = UTC) -> datetime | None:
    parsed = parse_timestamp(value, naive_timezone=naive_timezone)
    return parsed.astimezone(tz or system_timezone()) if parsed else None


def format_local(
    value: Any,
    *,
    empty: str = "Not available",
    tz: tzinfo | None = None,
    include_zone: bool = True,
    date_style: str = "%b %-d, %Y · %-I:%M %p",
) -> str:
    local = to_local(value, tz=tz)
    if local is None:
        return empty
    rendered = local.strftime(date_style)
    return f"{rendered} {local.tzname()}" if include_zone else rendered


def format_local_short(value: Any, *, empty: str = "Not available", tz: tzinfo | None = None) -> str:
    return format_local(
        value,
        empty=empty,
        tz=tz,
        include_zone=True,
        date_style="%b %-d · %-I:%M %p",
    )


def format_local_clock(value: Any, *, empty: str = "Not available", tz: tzinfo | None = None) -> str:
    return format_local(
        value,
        empty=empty,
        tz=tz,
        include_zone=True,
        date_style="%-I:%M %p",
    )


def local_date(value: Any, *, tz: tzinfo | None = None, naive_timezone: tzinfo = UTC) -> date | None:
    local = to_local(value, tz=tz, naive_timezone=naive_timezone)
    return local.date() if local else None


def local_day_bounds_utc(
    *,
    now: datetime | None = None,
    tz: tzinfo | None = None,
) -> tuple[datetime, datetime]:
    active = tz or system_timezone()
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=active)
    local_now = current.astimezone(active)
    start_local = datetime.combine(local_now.date(), time.min, tzinfo=active)
    next_local = datetime.combine(local_now.date().fromordinal(local_now.date().toordinal() + 1), time.min, tzinfo=active)
    return start_local.astimezone(UTC), next_local.astimezone(UTC)


def sqlite_utc(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
