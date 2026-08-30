from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, get_setting, initialize_database, save_setting


MIGRATION_ID = "009_query_and_scheduler_policy"
QUERY_PATH = ROOT_DIR / "config" / "query_strategy.json"
ORCHESTRATION_PATH = ROOT_DIR / "config" / "orchestration_policy.json"


def _merge_defaults(defaults: dict, current: dict) -> dict:
    merged = dict(defaults)
    for key, value in current.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_defaults(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def migrate() -> dict[str, object]:
    initialize_database()
    query_strategy = _merge_defaults(
        json.loads(QUERY_PATH.read_text(encoding="utf-8")),
        dict(get_setting("query_strategy", {}) or {}),
    )
    orchestration = _merge_defaults(
        json.loads(ORCHESTRATION_PATH.read_text(encoding="utf-8")),
        dict(get_setting("orchestration", {}) or {}),
    )
    save_setting("query_strategy", query_strategy, changed_by=MIGRATION_ID)
    save_setting("orchestration", orchestration, changed_by=MIGRATION_ID)
    scheduler_state = _merge_defaults({
        "canonical_source_scheduler": orchestration["source_worker_launch_agent"],
        "canonical_coordinator": orchestration["coordinator_launch_agent"],
        "coordinator_runs_source_workers": False,
        "retired_launch_agents": orchestration["retired_launch_agents"],
        "maintenance_mode": orchestration["maintenance_mode"],
        "observed_loaded_source_schedulers": [],
        "notes": "Desired scheduler topology; observed launchd state is refreshed during acceptance.",
    }, dict(get_setting("scheduler_installation", {}) or {}))
    scheduler_state["maintenance_mode"] = orchestration["maintenance_mode"]
    save_setting("scheduler_installation", scheduler_state, changed_by=MIGRATION_ID)
    digest = hashlib.sha256(Path(__file__).read_bytes())
    digest.update(QUERY_PATH.read_bytes())
    digest.update(ORCHESTRATION_PATH.read_bytes())
    checksum = digest.hexdigest()
    detail = {
        "query_schema_version": query_strategy["schema_version"],
        "maintenance_mode": bool(orchestration["maintenance_mode"]),
        "retired_launch_agents": orchestration["retired_launch_agents"],
    }
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO schema_migrations(migration_id, checksum, detail_json)
            VALUES (?, ?, ?)
            ON CONFLICT(migration_id) DO UPDATE SET
              applied_at=CURRENT_TIMESTAMP, checksum=excluded.checksum, detail_json=excluded.detail_json
            """,
            (MIGRATION_ID, checksum, json.dumps(detail, sort_keys=True)),
        )
        connection.commit()
    finally:
        connection.close()
    return {"migration_id": MIGRATION_ID, "checksum": checksum, **detail}


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2, sort_keys=True))
