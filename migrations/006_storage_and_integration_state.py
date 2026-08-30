from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, get_setting, initialize_database, save_setting


MIGRATION_ID = "006_storage_and_integration_state"
INTEGRATION_PATH = ROOT_DIR / "config" / "integration_health_policy.json"
BACKUP_INDEX_PATH = ROOT_DIR / "config" / "backup_index_seed.json"
RETENTION_PATH = ROOT_DIR / "config" / "backup_retention.json"


def _merge_defaults(defaults: dict, current: dict) -> dict:
    merged = dict(defaults)
    for key, value in current.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_defaults(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _du_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    output = subprocess.check_output(["du", "-sk", str(path)], text=True)
    return int(output.split()[0]) * 1024


def migrate() -> dict[str, object]:
    initialize_database()
    integration = _merge_defaults(
        json.loads(INTEGRATION_PATH.read_text(encoding="utf-8")),
        dict(get_setting("integration_health", {}) or {}),
    )
    backup_seed = json.loads(BACKUP_INDEX_PATH.read_text(encoding="utf-8"))
    retention = _merge_defaults(
        json.loads(RETENTION_PATH.read_text(encoding="utf-8")),
        dict(get_setting("backup_retention", {}) or {}),
    )
    digest = hashlib.sha256(Path(__file__).read_bytes())
    for path in (INTEGRATION_PATH, BACKUP_INDEX_PATH, RETENTION_PATH):
        digest.update(path.read_bytes())
    checksum = digest.hexdigest()
    save_setting("integration_health", integration, changed_by=MIGRATION_ID)
    save_setting("backup_retention", retention, changed_by=MIGRATION_ID)

    # Disk walks can take several seconds on historical backup trees.  Measure
    # before opening the write transaction so live readers and heartbeat
    # writers are never held behind a needlessly long SQLite lock.
    project_bytes = _du_bytes(ROOT_DIR)
    runtime_bytes = _du_bytes(Path(integration["runtime_path"]))
    backup_bytes = sum(
        _du_bytes(ROOT_DIR / value)
        for value in ("backups", "patch_backups", "rollback")
    )
    diagnostic_bytes = sum(
        _du_bytes(ROOT_DIR / value)
        for value in ("diagnostics", "reports", "logs")
    )
    quarantine_bytes = _du_bytes(ROOT_DIR / "quarantine")
    baseline = dict(backup_seed.get("storage_baseline") or {})
    reclaimed = max(0, int(baseline.get("project_bytes") or 0) - project_bytes)
    free_bytes = shutil.disk_usage(ROOT_DIR).free
    measured_at = datetime.now(timezone.utc).isoformat()
    detail = {
        "baseline": baseline,
        "current_project_bytes": project_bytes,
        "logical_project_reclaimed_bytes": reclaimed,
        "personal_files_touched": False,
        "n8n_live_database_touched": False,
        "cleanup_reports": [
            "reports/storage_cleanup_inventory_20260824_041509.json"
        ],
    }

    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        retained = 0
        for item in backup_seed.get("backups", []):
            path = ROOT_DIR / item["backup_path"]
            if not path.is_file():
                continue
            connection.execute(
                """
                INSERT INTO backup_inventory (
                    path, created_at, size_bytes, sha256, backup_type,
                    verified, restore_scope, retained_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    size_bytes=excluded.size_bytes,
                    sha256=excluded.sha256,
                    backup_type=excluded.backup_type,
                    verified=excluded.verified,
                    restore_scope=excluded.restore_scope,
                    retained_reason=excluded.retained_reason,
                    recorded_at=CURRENT_TIMESTAMP
                """,
                (
                    item["backup_path"],
                    datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                    path.stat().st_size,
                    item["sha256"],
                    item["backup_type"],
                    int(bool(item.get("verified"))),
                    item.get("restore_scope"),
                    item.get("retained_reason"),
                ),
            )
            retained += 1

        connection.execute(
            """
            INSERT INTO storage_metrics (
                measured_at, disk_free_bytes, project_bytes, runtime_bytes,
                backup_bytes, diagnostic_bytes, quarantine_bytes,
                reclaimed_bytes, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                measured_at, free_bytes, project_bytes, runtime_bytes,
                backup_bytes, diagnostic_bytes, quarantine_bytes,
                reclaimed, json.dumps(detail, sort_keys=True),
            ),
        )
        migration_detail = {
            "retained_backup_records": retained,
            "project_bytes": project_bytes,
            "reclaimed_bytes": reclaimed,
            "n8n_read_only": True,
        }
        connection.execute(
            """
            INSERT INTO schema_migrations(migration_id, checksum, detail_json)
            VALUES (?, ?, ?)
            ON CONFLICT(migration_id) DO UPDATE SET
                applied_at=CURRENT_TIMESTAMP,
                checksum=excluded.checksum,
                detail_json=excluded.detail_json
            """,
            (MIGRATION_ID, checksum, json.dumps(migration_detail, sort_keys=True)),
        )
        connection.commit()
        return {"migration_id": MIGRATION_ID, "checksum": checksum, **migration_detail}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2, sort_keys=True))
