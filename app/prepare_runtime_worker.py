"""Supervised low-latency runner for durable Prepare operations.

This lane advances only already-created Prepare operations. It does not admit
new jobs, dispatch n8n, open a browser, or grant submission authority.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import autonomous_prepare_v1, prepare_orchestrator_v1

HEARTBEAT_PATH = Path(
    os.getenv("MUNSHI_PREPARE_RUNTIME_HEARTBEAT_PATH")
    or Path(__file__).resolve().parents[1] / "data" / "prepare_runtime_heartbeat.json"
)


def _source_runner_status() -> dict[str, Any]:
    """Read the canonical discovery lock without opening the Hunter database."""
    try:
        from app.randomized_source_runner import inspect_source_runner_lock

        return dict(inspect_source_runner_lock())
    except Exception as error:  # noqa: BLE001 - lock telemetry cannot stop Prepare
        return {
            "state": "unknown",
            "reason": f"source_lock_check_failed:{type(error).__name__}",
        }


def _sqlite_writer_busy(error: BaseException) -> bool:
    if not isinstance(error, sqlite3.OperationalError):
        return False
    text = str(error or "").casefold()
    return any(
        marker in text
        for marker in (
            "database is locked",
            "database table is locked",
            "database is busy",
        )
    )


def _deferred_for_sqlite_busy(*, operation_count: int = 0) -> dict[str, Any]:
    return {
        "success": True,
        "status": "deferred",
        "reason": "sqlite_writer_busy",
        "operation_count": max(0, int(operation_count)),
        "results": [],
        "errors": [],
        "submission_authority": False,
    }


def _write_heartbeat(result: dict[str, Any]) -> None:
    """Publish value-free worker health without taking the SQLite writer."""
    payload = {
        "version": "prepare-runtime-heartbeat-v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "status": str(result.get("status") or ("healthy" if result.get("success") else "degraded")),
        "success": bool(result.get("success")),
        "reason": str(result.get("reason") or ""),
        "operation_count": int(result.get("operation_count") or 0),
        "error_count": len(result.get("errors") or []),
        "lease_recovery": dict(result.get("lease_recovery") or {}),
        "submission_authority": False,
    }
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = HEARTBEAT_PATH.with_name(
            f".{HEARTBEAT_PATH.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, HEARTBEAT_PATH)
    except OSError:
        # Health publication must never turn a successful worker cycle into a
        # failed Prepare cycle.
        return


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    _write_heartbeat(result)
    return result


def run_once(*, limit: int = 5) -> dict[str, Any]:
    source_runner = _source_runner_status()
    resolved = max(1, min(int(limit), 5))
    minimum = str(
        os.getenv("MUNSHI_PREPARE_RUNTIME_MIN_CREATED_AT") or ""
    ).strip()
    try:
        lease_recovery = prepare_orchestrator_v1.recover_expired_leases(
            limit=max(25, resolved * 10)
        )
    except sqlite3.OperationalError as error:
        if _sqlite_writer_busy(error):
            deferred = _deferred_for_sqlite_busy()
            deferred["lease_recovery"] = {}
            deferred["source_runner"] = source_runner
            return _finish(deferred)
        raise
    try:
        result = autonomous_prepare_v1.drive_open_operations(
            limit=resolved,
            newest_first=True,
            min_created_at=minimum or None,
        )
    except sqlite3.OperationalError as error:
        if _sqlite_writer_busy(error):
            deferred = _deferred_for_sqlite_busy()
            deferred["lease_recovery"] = lease_recovery
            deferred["source_runner"] = source_runner
            return _finish(deferred)
        raise

    errors = list(result.get("errors") or [])
    if errors:
        busy = [
            item
            for item in errors
            if str(item.get("error_type") or "") == "OperationalError"
            and any(
                marker in str(item.get("error") or "").casefold()
                for marker in (
                    "database is locked",
                    "database table is locked",
                    "database is busy",
                )
            )
        ]
        if busy and len(busy) == len(errors):
            deferred = _deferred_for_sqlite_busy(
                operation_count=int(result.get("operation_count") or 0)
            )
            deferred["deferred_operation_ids"] = [
                str(item.get("operation_id") or "")
                for item in busy
                if str(item.get("operation_id") or "").strip()
            ]
            deferred["lease_recovery"] = lease_recovery
            deferred["source_runner"] = source_runner
            return _finish(deferred)
    result["lease_recovery"] = lease_recovery
    result["source_runner"] = source_runner
    result.setdefault("status", "healthy" if result.get("success") else "degraded")
    return _finish(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Advance open MUNSHI Prepare operations once.")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    result = run_once(limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str), flush=True)
    return 0 if bool(result.get("success")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
