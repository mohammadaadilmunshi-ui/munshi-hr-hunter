from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from app.database import ROOT_DIR


JOBSPY_PYTHON = (
    ROOT_DIR
    / "tools"
    / "venvs"
    / "jobspy"
    / "bin"
    / "python"
)

PROJECT_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
CONTAINER_PYTHON = Path("/usr/local/bin/python")


def resolve_jobspy_python() -> Path:
    """Prefer isolated JobSpy Python, then project venv, then container Python."""
    candidates = (JOBSPY_PYTHON, PROJECT_PYTHON, CONTAINER_PYTHON)
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(
        "No usable JobSpy Python was found. Checked: "
        + ", ".join(str(path) for path in candidates)
    )

JOBSPY_RUNNER = (
    ROOT_DIR
    / "tools"
    / "runners"
    / "jobspy_runner.py"
)


def run_jobspy_command(
    arguments: list[str],
    *,
    timeout: int = 180,
) -> dict[str, Any]:
    jobspy_python = resolve_jobspy_python()

    if not JOBSPY_RUNNER.exists():
        raise RuntimeError(
            f"JobSpy runner was not found: "
            f"{JOBSPY_RUNNER}"
        )

    command = [
        str(jobspy_python),
        str(JOBSPY_RUNNER),
        *arguments,
    ]

    result = subprocess.run(
        command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "JobSpy runner failed.\n"
            f"Exit code: {result.returncode}\n"
            f"stderr: {result.stderr.strip()}"
        )

    stdout = result.stdout.strip()

    if not stdout:
        raise RuntimeError(
            "JobSpy runner returned empty output."
        )

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "JobSpy runner returned invalid JSON.\n"
            f"stdout: {stdout[:1000]}\n"
            f"stderr: {result.stderr[:1000]}"
        ) from error


def jobspy_self_test() -> dict[str, Any]:
    return run_jobspy_command(
        ["--self-test"],
        timeout=30,
    )


def fetch_jobspy_jobs(
    *,
    sites: list[str],
    search_term: str,
    location: str,
    results_wanted: int = 5,
    hours_old: int = 72,
    job_type: str = "internship",
    remote_only: bool = False,
) -> dict[str, Any]:
    arguments = [
        "--sites",
        ",".join(sites),
        "--search-term",
        search_term,
        "--location",
        location,
        "--results",
        str(results_wanted),
        "--hours-old",
        str(hours_old),
        "--job-type",
        job_type,
    ]

    if remote_only:
        arguments.append("--remote-only")

    return run_jobspy_command(
        arguments,
        timeout=300,
    )
