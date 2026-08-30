from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, get_setting, initialize_database, save_setting


MIGRATION_ID = "010_runtime_registry"
REGISTRY_PATH = ROOT_DIR / "config" / "source_worker_registry.json"
RUNTIME_PATH = ROOT_DIR / "config" / "provider_runtime_policy.json"
INTEGRATION_PATH = ROOT_DIR / "config" / "integration_health_policy.json"


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
    registry = _merge_defaults(
        json.loads(REGISTRY_PATH.read_text(encoding="utf-8")),
        dict(get_setting("source_worker_registry", {}) or {}),
    )
    runtime = _merge_defaults(
        json.loads(RUNTIME_PATH.read_text(encoding="utf-8")),
        dict(get_setting("provider_runtime", {}) or {}),
    )
    integration = _merge_defaults(
        json.loads(INTEGRATION_PATH.read_text(encoding="utf-8")),
        dict(get_setting("integration_health", {}) or {}),
    )
    missing_modules = [
        module
        for module in registry.get("workers", {}).values()
        if not module or importlib.util.find_spec(str(module)) is None
    ]
    if missing_modules:
        raise RuntimeError(f"Configured worker modules are missing: {missing_modules}")
    save_setting("source_worker_registry", registry, changed_by=MIGRATION_ID)
    save_setting("provider_runtime", runtime, changed_by=MIGRATION_ID)
    save_setting("integration_health", integration, changed_by=MIGRATION_ID)
    digest = hashlib.sha256(Path(__file__).read_bytes())
    for path in (REGISTRY_PATH, RUNTIME_PATH, INTEGRATION_PATH):
        digest.update(path.read_bytes())
    checksum = digest.hexdigest()
    detail = {
        "worker_count": len(registry.get("workers", {})),
        "missing_modules": missing_modules,
        "n8n_database_access": "read_only",
        "runtime_policy_source": "SQLite",
    }
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO schema_migrations(migration_id,checksum,detail_json)
            VALUES (?,?,?)
            ON CONFLICT(migration_id) DO UPDATE SET
              applied_at=CURRENT_TIMESTAMP,checksum=excluded.checksum,detail_json=excluded.detail_json
            """,
            (MIGRATION_ID, checksum, json.dumps(detail, sort_keys=True)),
        )
        connection.commit()
    finally:
        connection.close()
    return {"migration_id": MIGRATION_ID, "checksum": checksum, **detail}


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2, sort_keys=True))
