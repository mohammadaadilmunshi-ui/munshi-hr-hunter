from __future__ import annotations

import os
import runpy
from pathlib import Path


SUPERVISOR = Path(__file__).resolve().parents[1] / "docker" / "hunter-supervisor.py"


def load_supervisor() -> dict[str, object]:
    return runpy.run_path(str(SUPERVISOR))


def test_telegram_lane_is_nonfatal_and_repeating() -> None:
    old = os.environ.pop("HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS", None)
    try:
        namespace = load_supervisor()
        lane = namespace["telegram_lane"]("python")
        assert lane.name == "telegram"
        assert lane.command == ["python", "-m", "app.telegram_listener"]
        assert lane.required is False
        assert lane.repeat is True
        assert lane.interval == 30
    finally:
        if old is not None:
            os.environ["HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS"] = old


def test_telegram_restart_interval_is_bounded() -> None:
    old = os.environ.get("HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS")
    os.environ["HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS"] = "1"
    try:
        lane = load_supervisor()["telegram_lane"]("python")
        assert lane.interval == 5
    finally:
        if old is None:
            os.environ.pop("HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS", None)
        else:
            os.environ["HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS"] = old


def test_invalid_telegram_restart_interval_falls_back() -> None:
    old = os.environ.get("HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS")
    os.environ["HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS"] = "invalid"
    try:
        lane = load_supervisor()["telegram_lane"]("python")
        assert lane.interval == 30
    finally:
        if old is None:
            os.environ.pop("HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS", None)
        else:
            os.environ["HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS"] = old
