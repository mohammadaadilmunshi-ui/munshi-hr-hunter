#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.runtime_recovery import HUNTER_DB, N8N_DB, RuntimeRecovery  # noqa: E402


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True).encode()).hexdigest()


def _rows(connection: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(sql)]


def evidence() -> dict[str, Any]:
    recovery = RuntimeRecovery()
    connection = sqlite3.connect(f"file:{HUNTER_DB}?mode=ro", uri=True, timeout=10)
    source_policy = _rows(
        connection,
        "SELECT source_name,enabled,cadence_minutes,cost_mode,health_status,consecutive_failures FROM source_health ORDER BY source_name",
    )
    schedules = _rows(
        connection,
        "SELECT source_name,next_run_at,last_started_at,last_completed_at,schedule_state,consecutive_scheduler_failures FROM source_random_schedule ORDER BY source_name",
    )
    latest_runs = _rows(
        connection,
        "SELECT source_name,run_status,started_at,completed_at,raw_count,eligible_count,new_eligible_count FROM source_runs ORDER BY datetime(COALESCE(completed_at,started_at)) DESC LIMIT 10",
    )
    paid_guards = _rows(
        connection,
        "SELECT source_name,enabled,cost_mode FROM source_health WHERE lower(source_name) IN ('apify','serpapi') ORDER BY source_name",
    )
    counts = {
        table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in (
            "jobs",
            "source_runs",
            "targeting_decisions",
            "telegram_delivery_claims",
            "n8n_dispatch_queue",
        )
    }
    targeting = connection.execute(
        "SELECT value_json FROM settings WHERE setting_key='targeting'"
    ).fetchone()
    connection.close()

    n8n = sqlite3.connect(f"file:{N8N_DB}?mode=ro", uri=True, timeout=10)
    n8n_counts = {
        "executions": int(n8n.execute("SELECT COUNT(*) FROM execution_entity").fetchone()[0]),
        "active_executions": int(
            n8n.execute(
                "SELECT COUNT(*) FROM execution_entity WHERE lower(status) IN ('new','running','waiting') AND stoppedAt IS NULL"
            ).fetchone()[0]
        ),
    }
    n8n.close()
    durable = recovery.durable_state_snapshot()
    return {
        "captured_at_system_local": datetime.now().astimezone().isoformat(),
        "runtime": recovery.status_snapshot(),
        "durable_state": durable,
        "counts": counts,
        "source_policy": source_policy,
        "source_policy_hash": _hash(source_policy),
        "source_schedules": schedules,
        "source_schedule_hash": _hash(schedules),
        "latest_source_runs": latest_runs,
        "paid_source_guards": paid_guards,
        "targeting_hash": hashlib.sha256(str(targeting[0] if targeting else "").encode()).hexdigest(),
        "n8n": n8n_counts,
        "secret_values_included": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("before", "after", "natural_followup"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    snapshot = evidence()
    path = args.output / f"live_recovery_{args.phase}.json"
    path.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    if args.phase == "after":
        before = json.loads((args.output / "live_recovery_before.json").read_text(encoding="utf-8"))
        durable_before = before["durable_state"]
        durable_after = snapshot["durable_state"]
        keys = sorted(set(durable_before) | set(durable_after))
        preservation = {key: durable_before.get(key) == durable_after.get(key) for key in keys}
        comparison = {
            "durable_state_preserved": preservation,
            "all_durable_state_preserved": all(preservation.values()),
            "source_policy_preserved": before["source_policy_hash"] == snapshot["source_policy_hash"],
            "source_schedule_preserved": before["source_schedule_hash"] == snapshot["source_schedule_hash"],
            "targeting_preserved": before["targeting_hash"] == snapshot["targeting_hash"],
            "paid_source_guards_preserved": before["paid_source_guards"] == snapshot["paid_source_guards"],
            "n8n_execution_count_preserved": before["n8n"]["executions"] == snapshot["n8n"]["executions"],
            "secret_values_included": False,
        }
        (args.output / "live_recovery_comparison.json").write_text(
            json.dumps(comparison, indent=2), encoding="utf-8"
        )
        if not all(
            (
                comparison["all_durable_state_preserved"],
                comparison["source_policy_preserved"],
                comparison["source_schedule_preserved"],
                comparison["targeting_preserved"],
                comparison["paid_source_guards_preserved"],
                comparison["n8n_execution_count_preserved"],
            )
        ):
            return 2
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
