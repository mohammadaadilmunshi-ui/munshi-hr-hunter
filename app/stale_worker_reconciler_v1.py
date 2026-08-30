from __future__ import annotations

import fcntl
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
DB = PROJECT / "data/hunter.db"
LOCK_PATH = Path(
    "/tmp/aadil_hr_hunter_source_metrics_existing_scheduler_v1.lock"
)
MARKER = "AADIL_DASHBOARD_STALE_WORKER_RECONCILER_V1"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_datetime(value: Any) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _target_state(next_run_at: Any, now: datetime) -> str:
    next_run = _parse_datetime(next_run_at)
    return (
        "ready"
        if next_run is None or next_run <= now
        else "cooldown"
    )


def reconcile_dashboard_stale_workers() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    recovered: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []

    try:
        process_text = subprocess.run(
            ["ps", "-axo", "command="],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        ).stdout
    except Exception:
        process_text = ""

    # Runtime import avoids a module cycle when the runner invokes us.
    from app.randomized_source_runner import discover_worker

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    with LOCK_PATH.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            connection = sqlite3.connect(DB, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=30000")

            rows = connection.execute(
                """
                SELECT
                  h.source_name,
                  h.enabled,
                  h.health_status,
                  h.last_error,
                  h.last_success_at,
                  h.last_failure_at,
                  s.schedule_state,
                  s.next_run_at,
                  s.last_started_at,
                  s.last_completed_at,
                  s.last_worker_status,
                  s.last_worker_returncode,
                  s.consecutive_scheduler_failures
                FROM source_health h
                JOIN source_random_schedule s
                  ON lower(s.source_name)=lower(h.source_name)
                WHERE h.enabled=1
                ORDER BY h.source_name
                """
            ).fetchall()

            try:
                connection.execute("BEGIN IMMEDIATE")

                for row in rows:
                    source = str(row["source_name"])
                    health = _clean(row["health_status"]).casefold()
                    state = _clean(row["schedule_state"]).casefold()
                    worker = _clean(row["last_worker_status"]).casefold()
                    error = _clean(row["last_error"])
                    returncode = row["last_worker_returncode"]
                    failures = int(
                        row["consecutive_scheduler_failures"] or 0
                    )

                    started = _parse_datetime(row["last_started_at"])
                    completed = _parse_datetime(row["last_completed_at"])
                    success_at = _parse_datetime(row["last_success_at"])
                    failure_at = _parse_datetime(row["last_failure_at"])

                    module = discover_worker(source)
                    live_worker = bool(
                        module and f"-m {module}" in process_text
                    )
                    running_age = (
                        (now - started).total_seconds()
                        if started is not None
                        else None
                    )
                    orphaned_running = bool(
                        state == "running"
                        and not live_worker
                        and running_age is not None
                        and running_age >= 120
                    )

                    completed_ok = bool(
                        worker in {
                            "completed", "success", "succeeded",
                            "zero_yield", "zero_eligible",
                            "duplicate_only", "startup_recovered",
                            "stale_backoff_recovered",
                        }
                        and returncode in (None, "", 0, "0")
                        and completed is not None
                        and (started is None or completed >= started)
                    )
                    newer_success = bool(
                        success_at is not None
                        and (failure_at is None or success_at > failure_at)
                    )
                    blank_backoff = bool(
                        health == "healthy"
                        and state == "failure_backoff"
                        and not error
                    )
                    completed_running = bool(
                        state == "running" and completed_ok
                    )
                    due_deferred = bool(
                        health == "healthy"
                        and state == "deferred"
                        and worker == "active_work_deferred"
                        and _target_state(row["next_run_at"], now) == "ready"
                    )
                    stale_after_success = bool(
                        state in {
                            "failure_backoff", "failed", "runtime_failure"
                        }
                        and newer_success
                    )

                    if not any((
                        orphaned_running,
                        blank_backoff,
                        completed_running,
                        due_deferred,
                        stale_after_success,
                    )):
                        if state in {
                            "failure_backoff", "failed", "runtime_failure",
                            "running", "deferred",
                        }:
                            preserved.append({
                                "source": source,
                                "health_status": health,
                                "schedule_state": state,
                                "worker_status": worker,
                                "live_worker": live_worker,
                                "running_age_seconds": running_age,
                                "error": error[:180],
                            })
                        continue

                    if orphaned_running:
                        target = "ready"
                        next_run_value = now.isoformat()
                        reason = "orphaned_running_recovered_v2"
                        final_worker = "orphaned_running_recovered"
                        final_returncode = 124
                        failures += 1
                    else:
                        target = _target_state(row["next_run_at"], now)
                        if due_deferred:
                            target = "ready"
                        next_run_value = row["next_run_at"]
                        reason = (
                            "dashboard_blank_backoff_recovered_v2"
                            if blank_backoff
                            else "dashboard_completed_running_recovered_v2"
                            if completed_running
                            else "dashboard_due_deferred_recovered_v2"
                            if due_deferred
                            else "dashboard_newer_success_recovered_v2"
                        )
                        final_worker = "stale_state_recovered"
                        final_returncode = 0
                        if newer_success or completed_running:
                            failures = 0

                    connection.execute(
                        """
                        UPDATE source_random_schedule
                        SET
                          next_run_at=?,
                          schedule_state=?,
                          schedule_reason=?,
                          last_worker_status=?,
                          last_worker_returncode=?,
                          consecutive_scheduler_failures=?,
                          last_completed_at=CASE
                            WHEN ? THEN CURRENT_TIMESTAMP
                            ELSE last_completed_at
                          END,
                          updated_at=CURRENT_TIMESTAMP
                        WHERE lower(source_name)=lower(?)
                        """,
                        (
                            next_run_value,
                            target,
                            reason,
                            final_worker,
                            final_returncode,
                            failures,
                            int(orphaned_running),
                            source,
                        ),
                    )

                    if newer_success or completed_running:
                        connection.execute(
                            """
                            UPDATE source_health
                            SET
                              health_status='healthy',
                              last_error=NULL,
                              consecutive_failures=0,
                              updated_at=CURRENT_TIMESTAMP
                            WHERE lower(source_name)=lower(?)
                              AND enabled=1
                            """,
                            (source,),
                        )

                    recovered.append({
                        "source": source,
                        "previous_schedule_state": state,
                        "new_schedule_state": target,
                        "reason": reason,
                        "live_worker": live_worker,
                        "running_age_seconds": running_age,
                    })

                integrity = str(
                    connection.execute("PRAGMA integrity_check").fetchone()[0]
                )
                if integrity.casefold() != "ok":
                    raise RuntimeError("Hunter database integrity failed.")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    return {
        "success": True,
        "marker": "AADIL_PROCESS_AWARE_STALE_WORKER_RECONCILER_V2",
        "recovered": recovered,
        "preserved": preserved,
        "process_aware": True,
        "orphan_grace_seconds": 120,
        "source_names_hardcoded": False,
        "dashboard_enabled_only": True,
        "cadence_changed": False,
        "jitter_changed": False,
        "source_enablement_changed": False,
        "database_integrity": integrity,
    }



def main() -> None:
    print(
        json.dumps(
            reconcile_dashboard_stale_workers(),
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
