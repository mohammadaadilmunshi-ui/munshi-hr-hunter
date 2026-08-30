from __future__ import annotations

import argparse
import fcntl
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.n8n_dispatch import (
    DB_PATH,
    dispatch_pending,
    ensure_schema,
    get_connection,
    queue_candidates,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
LOCK_PATH = (
    ROOT_DIR
    / "data"
    / "unified_hourly_coordinator.lock"
)

COORDINATOR_VERSION = (
    "unified_hourly_coordinator_v1"
)


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def manual_or_stored_worker_active() -> str:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "command="],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        process_text = completed.stdout.casefold()
        for marker in (
            "app.manual_input_worker",
            "app.stored_job_n8n_worker",
            "manual_input_worker.py",
            "stored_job_n8n_worker.py",
        ):
            if marker in process_text:
                return marker
    except Exception:
        pass
    return ""


def load_enabled_sources() -> list[str]:
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = sqlite3.Row

    try:
        rows = connection.execute(
            """
            SELECT source_name
            FROM source_health
            WHERE enabled = 1
            ORDER BY
                source_tier,
                source_name
            """
        ).fetchall()
    finally:
        connection.close()

    return [
        str(row["source_name"])
        for row in rows
    ]


def record_coordinator_event(
    summary: dict[str, Any],
) -> None:
    connection = get_connection()

    try:
        ensure_schema(connection)

        connection.execute(
            """
            INSERT INTO events (
                job_id,
                event_type,
                actor,
                event_status,
                payload_json
            )
            VALUES (
                NULL,
                'unified_hourly_coordinator_run',
                'unified_hourly_coordinator',
                ?,
                ?
            )
            """,
            (
                (
                    "completed"
                    if summary["success"]
                    else "failed"
                ),
                json.dumps(
                    summary,
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()
    finally:
        connection.close()


def run_coordinator(
    *,
    skip_workers: bool,
    force_workers: bool,
    dry_run: bool,
    webhook_mode: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    worker_execution_requested = bool(not skip_workers or force_workers)
    # Source discovery belongs exclusively to randomized_source_runner. Keep
    # these legacy parameters parseable for old launch commands, but never let
    # this queue/downstream coordinator create a competing source lane.
    skip_workers = True
    force_workers = False
    active_manual = manual_or_stored_worker_active()
    if active_manual:
        return {
            "success": True,
            "skipped": True,
            "reason": f"manual_priority_active:{active_manual}",
            "coordinator_version": COORDINATOR_VERSION,
            "worker_results": [],
            "queue_result": {},
            "dispatch_result": {
                "success": True,
                "dispatch_status": "manual_priority_deferred",
                "dispatched": [],
                "errors": [],
                "n8n_calls": 0,
            },
            "n8n_calls": 0,
        }

    enabled_sources = (
        load_enabled_sources()
    )

    worker_results = []
    unsupported_sources = []

    queue_result = queue_candidates(
        webhook_mode=webhook_mode,
        dry_run=dry_run,
    )

    dispatch_result = dispatch_pending(
        webhook_mode=webhook_mode,
        dry_run=dry_run,
        allow_disabled=False,
    )

    failed_workers = [
        result
        for result in worker_results
        if not result["success"]
    ]

    summary = {
        "success": not bool(
            failed_workers
            or dispatch_result[
                "errors"
            ]
        ),
        "coordinator_version": (
            COORDINATOR_VERSION
        ),
        "started_at": utc_now(),
        "completed_at": utc_now(),
        "skip_workers": (
            skip_workers
        ),
        "queue_only_invariant": True,
        "source_worker_execution_requested_but_blocked": worker_execution_requested,
        "force_workers": (
            force_workers
        ),
        "dry_run": dry_run,
        "webhook_mode": (
            webhook_mode
        ),
        "enabled_sources": (
            enabled_sources
        ),
        "supported_worker_count": (
            len(worker_results)
        ),
        "unsupported_enabled_sources": (
            unsupported_sources
        ),
        "worker_results": (
            worker_results
        ),
        "queue_result": (
            queue_result
        ),
        "dispatch_result": (
            dispatch_result
        ),
        "n8n_calls": (
            dispatch_result[
                "n8n_calls"
            ]
        ),
    }

    record_coordinator_event(
        summary
    )

    return summary


def main() -> None:
    from app.database import get_setting

    orchestration = get_setting("orchestration", {}) or {}
    if bool(orchestration.get("maintenance_mode", False)):
        print(json.dumps({
            "success": True,
            "skipped": True,
            "reason": "canonical_orchestration_maintenance_mode",
            "worker_results": [],
            "queue_result": {},
            "dispatch_result": {},
            "n8n_calls": 0,
        }, indent=2))
        return

    # AADIL_OPT_US_NATIONWIDE_INTEGRITY_V1
    try:
        import json as _aadil_json_v1
        from app.opt_us_nationwide_integrity_v1 import reconcile_n8n_queue as _aadil_reconcile_n8n_queue_v1
        _aadil_reconcile_result_v1 = _aadil_reconcile_n8n_queue_v1()
        if _aadil_reconcile_result_v1.get('terminalized'):
            print(_aadil_json_v1.dumps({'n8n_queue_reconciled': _aadil_reconcile_result_v1['terminalized']}, default=str), flush=True)
    except Exception as _aadil_reconcile_error_v1:
        print(f'n8n queue reconciliation warning: {type(_aadil_reconcile_error_v1).__name__}', flush=True)

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--skip-workers",
        action="store_true",
    )

    parser.add_argument(
        "--force-workers",
        action="store_true",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    parser.add_argument(
        "--mode",
        choices=[
            "test",
            "production",
        ],
        default="production",
    )

    parser.add_argument(
        "--worker-timeout",
        type=int,
        default=900,
    )

    args = parser.parse_args()

    LOCK_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with LOCK_PATH.open(
        "w"
    ) as lock_file:
        try:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX
                | fcntl.LOCK_NB,
            )

        except BlockingIOError:
            print(
                json.dumps(
                    {
                        "success": True,
                        "skipped": True,
                        "reason": (
                            "coordinator_already_running"
                        ),
                        "n8n_calls": 0,
                    },
                    indent=2,
                )
            )

            return

        result = run_coordinator(
            skip_workers=(
                args.skip_workers
            ),
            force_workers=(
                args.force_workers
            ),
            dry_run=args.dry_run,
            webhook_mode=args.mode,
            timeout_seconds=max(
                60,
                args.worker_timeout,
            ),
        )

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            )
        )

        if not result["success"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
