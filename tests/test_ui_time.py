from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.ui_time import (
    format_local,
    local_date,
    local_day_bounds_utc,
    parse_timestamp,
    system_timezone,
    timezone_label,
)


def test_system_timezone_honors_dynamic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    assert getattr(system_timezone(), "key", None) == "America/Los_Angeles"


def test_naive_database_timestamp_is_interpreted_as_utc() -> None:
    parsed = parse_timestamp("2026-08-25 01:00:00")
    assert parsed == datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)


def test_utc_timestamp_formats_in_requested_local_timezone() -> None:
    rendered = format_local(
        "2026-08-25 01:00:00",
        tz=ZoneInfo("America/New_York"),
    )
    assert rendered == "Aug 24, 2026 · 9:00 PM EDT"


def test_local_midnight_boundary_uses_mac_calendar_not_utc_calendar() -> None:
    tz = ZoneInfo("America/New_York")
    assert local_date("2026-08-25 01:00:00", tz=tz).isoformat() == "2026-08-24"


def test_local_day_bounds_are_dst_aware() -> None:
    tz = ZoneInfo("America/New_York")
    spring_start, spring_end = local_day_bounds_utc(
        now=datetime(2026, 3, 8, 12, tzinfo=timezone.utc), tz=tz
    )
    assert spring_start == datetime(2026, 3, 8, 5, tzinfo=timezone.utc)
    assert spring_end == datetime(2026, 3, 9, 4, tzinfo=timezone.utc)
    assert (spring_end - spring_start).total_seconds() == 23 * 3600


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-08-25 02:45:31",
        "2026-08-25T09:21:31+00:00",
        "2026-08-24 19:11:19.450",
    ],
)
def test_scheduler_backup_and_n8n_timestamps_share_one_formatter(timestamp: str) -> None:
    assert "EDT" in format_local(timestamp, tz=ZoneInfo("America/New_York"))


def test_timezone_label_includes_dynamic_zone_and_abbreviation() -> None:
    label = timezone_label(
        tz=ZoneInfo("America/New_York"),
        at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert label == "America/New_York · EDT"
