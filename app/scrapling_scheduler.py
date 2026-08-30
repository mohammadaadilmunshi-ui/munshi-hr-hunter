from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.scrapling_adapter_common import (
    HUNTER_DB,
    active_work_reason,
    ensure_state_schema,
    load_config,
    now_local,
    parse_dt,
    run_source,
)

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "scrapling_sources_scheduler.log"


def log(payload: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now().isoformat(),
        **payload,
    }
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def main() -> int:
    reason = active_work_reason()
    if reason:
        log({"status": "skipped_active_work", "reason": reason})
        return 0

    config = load_config()
    connection = sqlite3.connect(HUNTER_DB, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        ensure_state_schema(connection)
        due = []
        now = now_local()
        rows = connection.execute(
            """
            SELECT *
            FROM scrapling_source_state
            WHERE external_enabled = 1
            ORDER BY COALESCE(next_run_at, '') ASC
            """
        ).fetchall()
        for row in rows:
            next_run = parse_dt(row["next_run_at"])
            blocked_until = parse_dt(row["blocked_until"])
            if blocked_until and blocked_until > now:
                continue
            if next_run is None or next_run <= now:
                due.append(str(row["source_key"]))

        if not due:
            log({"status": "idle", "enabled_sources": len(rows)})
            return 0
    finally:
        connection.close()

    source_key = due[0]
    result = run_source(source_key, no_store=False, force=False, smoke=False)
    log({
        "status": "source_result",
        "source_key": source_key,
        "result": result,
        "remaining_due_sources": due[1:],
        "max_sources_per_tick": int(
            config["common"]["max_sources_per_scheduler_tick"]
        ),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
