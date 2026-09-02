from __future__ import annotations

import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_coordinator_command_matches_production_launchd(monkeypatch) -> None:
    module = runpy.run_path(str(ROOT / "docker" / "hunter-supervisor.py"))
    monkeypatch.setenv("HUNTER_COORDINATOR_SKIP_WORKERS", "true")
    monkeypatch.setenv("HUNTER_COORDINATOR_MODE", "production")
    monkeypatch.setenv("HUNTER_COORDINATOR_WORKER_TIMEOUT", "420")
    assert module["coordinator_command"]("python") == [
        "python",
        "-m",
        "app.unified_hourly_coordinator",
        "--mode",
        "production",
        "--worker-timeout",
        "420",
        "--skip-workers",
    ]


def test_fastapi_readiness_gate_precedes_writer_lanes(monkeypatch) -> None:
    module = runpy.run_path(str(ROOT / "docker" / "hunter-supervisor.py"))

    class Process:
        @staticmethod
        def poll():
            return None

    class Response:
        status = 200

    lane = module["Lane"]("fastapi", ["python"])
    lane.process = Process()
    monkeypatch.setattr(module["urllib"].request, "urlopen", lambda *_args, **_kwargs: Response())
    assert module["wait_for_fastapi"](lane, timeout_seconds=1) is True
