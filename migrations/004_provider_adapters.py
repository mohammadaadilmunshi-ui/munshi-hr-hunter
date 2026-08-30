from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, initialize_database, save_setting


MIGRATION_ID = "004_provider_adapters"
RUNTIME_PATH = ROOT_DIR / "config" / "provider_runtime_policy.json"


def migrate() -> dict[str, object]:
    initialize_database()
    runtime = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    checksum = hashlib.sha256(Path(__file__).read_bytes() + RUNTIME_PATH.read_bytes()).hexdigest()
    save_setting("provider_runtime", runtime, changed_by=MIGRATION_ID)
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for provider, module in (
            ("Workday", "app.workday_worker"),
            ("BambooHR", "app.bamboohr_worker"),
        ):
            connection.execute(
                """
                INSERT INTO source_health (
                    source_name, source_tier, enabled, cadence_minutes, cost_mode,
                    health_status
                ) VALUES (?, 1, 0, 360, 'free', 'fixture_tested')
                ON CONFLICT(source_name) DO UPDATE SET
                    health_status = CASE
                        WHEN source_health.last_success_at IS NOT NULL THEN source_health.health_status
                        ELSE 'fixture_tested'
                    END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (provider,),
            )
            connection.execute(
                """
                INSERT INTO adapter_coverage (
                    provider, implementation_module, implemented, fixture_tested,
                    live_tested, enabled, health_status, support_level, notes
                ) VALUES (?, ?, 1, 1, 0, 0, 'fixture_tested', 'public_api',
                          'Canonical fixture fetch, detail extraction, normalization, and targeting passed.')
                ON CONFLICT(provider) DO UPDATE SET
                    implementation_module=excluded.implementation_module,
                    implemented=1,
                    fixture_tested=1,
                    health_status=CASE
                        WHEN adapter_coverage.live_tested=1 THEN adapter_coverage.health_status
                        ELSE 'fixture_tested'
                    END,
                    blocked_reason=NULL,
                    notes=excluded.notes,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (provider, module),
            )
        detail = {
            "providers": ["Workday", "BambooHR"],
            "fixture_tested": True,
            "enabled": False,
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
