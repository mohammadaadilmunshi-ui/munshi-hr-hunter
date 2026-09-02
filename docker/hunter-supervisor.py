"""Small foreground supervisor for the proven Hunter runtime lanes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass


def enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def coordinator_command(python: str) -> list[str]:
    mode = os.getenv("HUNTER_COORDINATOR_MODE", "production").strip().lower()
    if mode not in {"test", "production"}:
        raise SystemExit("HUNTER_COORDINATOR_MODE must be test or production.")
    timeout = max(60, int(os.getenv("HUNTER_COORDINATOR_WORKER_TIMEOUT", "420")))
    command = [
        python,
        "-m",
        "app.unified_hourly_coordinator",
        "--mode",
        mode,
        "--worker-timeout",
        str(timeout),
    ]
    if enabled("HUNTER_COORDINATOR_SKIP_WORKERS"):
        command.append("--skip-workers")
    return command


@dataclass
class Lane:
    name: str
    command: list[str]
    required: bool = True
    repeat: bool = False
    interval: int = 3600
    process: subprocess.Popen[str] | None = None


children: list[Lane] = []
stopping = False


def terminate_children(_signum: int, _frame: object) -> None:
    global stopping
    stopping = True
    for lane in children:
        if lane.process and lane.process.poll() is None:
            try:
                os.killpg(lane.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def start(lane: Lane) -> None:
    lane.process = subprocess.Popen(
        lane.command,
        cwd="/app/hunter",
        start_new_session=True,
    )
    print(f"Hunter lane started: {lane.name} (pid={lane.process.pid})", flush=True)


def wait_for_fastapi(lane: Lane, timeout_seconds: int = 120) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if lane.process is None or lane.process.poll() is not None:
            return False
        try:
            response = urllib.request.urlopen(
                "http://127.0.0.1:8000/health",
                timeout=2,
            )
            if response.status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def stop_all() -> None:
    for lane in children:
        process = lane.process
        if process and process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)


def main() -> int:
    global children
    python = sys.executable
    children = [
        Lane("fastapi", [python, "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]),
        Lane("streamlit", [python, "-m", "streamlit", "run", "app/dashboard.py", "--server.address", "0.0.0.0", "--server.port", "8501", "--server.headless", "true"]),
    ]
    if enabled("HUNTER_ENABLE_TELEGRAM"):
        children.append(Lane("telegram", [python, "-m", "app.telegram_listener"]))
    discovery = enabled("HUNTER_ENABLE_DISCOVERY_SCHEDULER")
    coordinator = enabled("HUNTER_ENABLE_COORDINATOR")
    if discovery:
        children.append(Lane("discovery-scheduler", [python, "-m", "app.randomized_source_runner", "--scheduled"], repeat=True, interval=max(30, int(os.getenv("HUNTER_DISCOVERY_INTERVAL_SECONDS", "3600")))))
    if coordinator:
        children.append(Lane("coordinator", coordinator_command(python), repeat=True, interval=max(30, int(os.getenv("HUNTER_COORDINATOR_INTERVAL_SECONDS", "3600")))))

    signal.signal(signal.SIGTERM, terminate_children)
    signal.signal(signal.SIGINT, terminate_children)
    for lane in children:
        start(lane)
        if lane.name == "fastapi" and not wait_for_fastapi(lane):
            print("FastAPI did not become ready before writer lanes.", file=sys.stderr, flush=True)
            return 1

    try:
        while not stopping:
            for lane in children:
                if lane.process is None or lane.process.poll() is None:
                    continue
                code = lane.process.returncode
                if lane.repeat and not stopping and code == 0:
                    time.sleep(lane.interval)
                    if not stopping:
                        start(lane)
                    continue
                if not stopping and lane.required:
                    print(f"Required Hunter lane exited: {lane.name} ({code})", file=sys.stderr, flush=True)
                    return 1
            time.sleep(1)
    finally:
        terminate_children(signal.SIGTERM, None)
        stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
