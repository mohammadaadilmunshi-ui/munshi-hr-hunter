from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, get_setting, initialize_database, save_setting


MIGRATION_ID = "003_control_center_v3"
TARGETING_POLICY_PATH = ROOT_DIR / "config" / "canonical_targeting_policy.json"
ADAPTER_CATALOG_PATH = ROOT_DIR / "config" / "adapter_catalog.json"
ORCHESTRATION_POLICY_PATH = ROOT_DIR / "config" / "orchestration_policy.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return value


def _checksum() -> str:
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    for path in (TARGETING_POLICY_PATH, ADAPTER_CATALOG_PATH, ORCHESTRATION_POLICY_PATH):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def migrate() -> dict[str, Any]:
    """Install canonical control-plane state without replacing user-maintained lists."""
    initialize_database()
    checksum = _checksum()
    connection = get_connection()
    try:
        existing = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id = ?",
            (MIGRATION_ID,),
        ).fetchone()
        if existing and str(existing["checksum"]) == checksum:
            return {"migration_id": MIGRATION_ID, "already_applied": True, "checksum": checksum}
    finally:
        connection.close()

    policy = _load(TARGETING_POLICY_PATH)
    targeting = get_setting("targeting", {}) or {}
    if not isinstance(targeting, dict):
        targeting = {}
    for key in (
        "schema_version",
        "mode",
        "eligibility",
        "experience_policy",
        "title_only_hard_rejects",
        "role_negative_contexts",
        "role_families",
    ):
        targeting[key] = policy[key]
    save_setting("targeting", targeting, changed_by=MIGRATION_ID)

    authorization = get_setting("authorization", {}) or {}
    if not isinstance(authorization, dict):
        authorization = {}
    authorization["authorization_mode"] = str(policy["mode"])
    authorization["normal_discovery_geography"] = str(policy["eligibility"]["label"])
    authorization["historical_cpt_fields_are_eligibility_rules"] = False
    save_setting("authorization", authorization, changed_by=MIGRATION_ID)

    orchestration = _load(ORCHESTRATION_POLICY_PATH)
    save_setting("orchestration", orchestration, changed_by=MIGRATION_ID)
    adapter_catalog = _load(ADAPTER_CATALOG_PATH)
    save_setting("adapter_catalog", adapter_catalog, changed_by=MIGRATION_ID)
    save_setting(
        "location_policy",
        {"rule_purposes": policy["location_rule_purposes"]},
        changed_by=MIGRATION_ID,
    )

    location_updates = 0
    adapter_rows = 0
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for location_name, purpose in policy["location_rule_purposes"].items():
            cursor = connection.execute(
                """
                UPDATE location_rules
                SET rule_purpose = ?, updated_at = CURRENT_TIMESTAMP
                WHERE location_name = ?
                """,
                (purpose, location_name),
            )
            location_updates += int(cursor.rowcount or 0)

        for item in adapter_catalog.get("providers", []):
            if not isinstance(item, dict) or not str(item.get("provider") or "").strip():
                continue
            provider = str(item["provider"]).strip()
            module = str(item.get("module") or "").strip()
            declared_implemented = bool(item.get("implemented"))
            implemented = declared_implemented and bool(module) and importlib.util.find_spec(module) is not None
            blocked_reason = str(item.get("blocked_reason") or "").strip() or None
            health = "blocked" if blocked_reason else "not_tested"
            connection.execute(
                """
                INSERT INTO adapter_coverage (
                    provider, implementation_module, implemented, enabled,
                    health_status, support_level, blocked_reason, notes, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(provider) DO UPDATE SET
                    implementation_module = excluded.implementation_module,
                    implemented = excluded.implemented,
                    health_status = CASE
                        WHEN adapter_coverage.live_tested = 1 THEN adapter_coverage.health_status
                        ELSE excluded.health_status
                    END,
                    support_level = excluded.support_level,
                    blocked_reason = excluded.blocked_reason,
                    notes = excluded.notes,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    provider,
                    module or None,
                    int(implemented),
                    health,
                    str(item.get("support_level") or "unknown"),
                    blocked_reason,
                    (
                        None
                        if implemented or not declared_implemented
                        else "Catalog declaration is not implemented on this machine."
                    ),
                ),
            )
            adapter_rows += 1

        detail = {
            "targeting_schema_version": policy["schema_version"],
            "active_mode": str(policy["mode"]),
            "geography": str(policy["eligibility"]["label"]),
            "location_updates": location_updates,
            "adapter_rows": adapter_rows,
        }
        connection.execute(
            """
            INSERT INTO schema_migrations (migration_id, checksum, detail_json)
            VALUES (?, ?, ?)
            ON CONFLICT(migration_id) DO UPDATE SET
                applied_at = CURRENT_TIMESTAMP,
                checksum = excluded.checksum,
                detail_json = excluded.detail_json
            """,
            (MIGRATION_ID, checksum, json.dumps(detail, sort_keys=True)),
        )
        connection.commit()
        return {"migration_id": MIGRATION_ID, "checksum": checksum, **detail}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2, sort_keys=True))
