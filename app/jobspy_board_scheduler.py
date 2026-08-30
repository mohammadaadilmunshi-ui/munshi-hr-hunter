from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.jobspy_board_common import active_work_reason, run_board, status_rows

LOG = Path(__file__).resolve().parent.parent / "logs" / "jobspy_board_scheduler.log"


def log(payload: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "timestamp": datetime.now().isoformat(),
            **payload,
        }, ensure_ascii=False, default=str) + "\n")


def main() -> int:
    reason = active_work_reason()
    if reason:
        log({"status": "skipped_active_work", "reason": reason})
        return 0

    rows = status_rows()
    due = []
    now = datetime.now().replace(microsecond=0)
    for row in rows:
        if not int(row.get("dashboard_enabled") or 0):
            continue
        blocked = row.get("blocked_until")
        next_run = row.get("next_run_at")
        try:
            blocked_dt = datetime.fromisoformat(blocked) if blocked else None
        except ValueError:
            blocked_dt = None
        try:
            next_dt = datetime.fromisoformat(next_run) if next_run else None
        except ValueError:
            next_dt = None
        if blocked_dt and blocked_dt > now:
            continue
        if next_dt is None or next_dt <= now:
            due.append(row)

    if not due:
        log({"status": "idle"})
        return 0

    due.sort(key=lambda item: str(item.get("next_run_at") or ""))
    selected = due[0]
    result = run_board(str(selected["source_key"]), no_store=False, force=False)
    log({
        "status": "source_result",
        "source_key": selected["source_key"],
        "result": result,
        "remaining_due": [item["source_key"] for item in due[1:]],
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
