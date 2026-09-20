"""Supervised low-latency runner for durable Prepare operations.

This lane advances only already-created Prepare operations. It does not admit
new jobs, dispatch n8n, open a browser, or grant submission authority.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from app import autonomous_prepare_v1


def _source_runner_status() -> dict[str, Any]:
    """Read the canonical discovery lock without opening the Hunter database."""
    try:
        from app.randomized_source_runner import inspect_source_runner_lock

        return dict(inspect_source_runner_lock())
    except Exception as error:
        return {
            "state": "unknown",
            "reason": f"source_lock_check_failed:{type(error).__name__}",
        }


def run_once(*, limit: int = 1) -> dict[str, Any]:
    source_runner = _source_runner_status()
    if str(source_runner.get("state") or "").casefold() == "active":
        return {
            "success": True,
            "status": "deferred",
            "reason": "source_discovery_active",
            "source_runner_pid": source_runner.get("pid"),
            "operation_count": 0,
            "results": [],
            "errors": [],
            "submission_authority": False,
        }

    resolved = max(1, min(int(limit), 5))
    minimum = str(
        os.getenv("MUNSHI_PREPARE_RUNTIME_MIN_CREATED_AT") or ""
    ).strip()
    return autonomous_prepare_v1.drive_open_operations(
        limit=resolved,
        newest_first=True,
        min_created_at=minimum or None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Advance open MUNSHI Prepare operations once.")
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()

    result = run_once(limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str), flush=True)
    return 0 if bool(result.get("success")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
