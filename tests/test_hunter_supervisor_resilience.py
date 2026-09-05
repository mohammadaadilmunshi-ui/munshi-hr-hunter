from __future__ import annotations

import runpy
from pathlib import Path


SUPERVISOR = Path(__file__).resolve().parents[1] / "docker" / "hunter-supervisor.py"


def load_supervisor() -> dict[str, object]:
    return runpy.run_path(str(SUPERVISOR))


def test_telegram_lane_is_nonfatal_and_repeating(monkeypatch) -> None:
    monkeypatch.delenv("HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS", raising=False)
    namespace = load_supervisor()

    lane = namespace["telegram_lane"]("python")

    assert lane.name == "telegram"
    assert lane.command == ["python", "-m", "app.telegram_listener"]
    assert lane.required is False
    assert lane.repeat is True
    assert lane.interval == 30


def test_telegram_restart_interval_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS", "1")
    namespace = load_supervisor()

    lane = namespace["telegram_lane"]("python")

    assert lane.interval == 5


def test_invalid_telegram_restart_interval_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("HUNTER_TELEGRAM_RESTART_INTERVAL_SECONDS", "invalid")
    namespace = load_supervisor()

    lane = namespace["telegram_lane"]("python")

    assert lane.interval == 30
