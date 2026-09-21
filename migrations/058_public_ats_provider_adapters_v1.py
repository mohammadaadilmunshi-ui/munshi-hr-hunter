from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, initialize_database, save_setting
from app.market_board_discovery import import_existing_career_urls


MIGRATION_ID = "058_public_ats_provider_adapters_v1"
CATALOG_PATH = ROOT_DIR / "config" / "adapter_catalog.json"
MAPPING_PATH = ROOT_DIR / "config" / "adapter_source_mapping.json"
REGISTRY_PATH = ROOT_DIR / "config" / "source_worker_registry.json"
RUNTIME_PATH = ROOT_DIR / "config" / "provider_runtime_policy.json"
PROVIDERS = [('Naukri', 'naukri'), ('Rippling', 'rippling'), ('iCIMS', 'icims'), ('BrassRing', 'brassring'), ('JazzHR', 'jazzhr'), ('Jobvite', 'jobvite'), ('BreezyHR', 'breezyhr'), ('Oracle Recruiting', 'oracle_recruiting'), ('Paylocity', 'paylocity'), ('UKG / UltiPro', 'ukg_ultipro'), ('Zoho Recruit', 'zoho_recruit'), ('ADP Recruiting', 'adp_recruiting'), ('Dover', 'dover'), ('Gem', 'gem'), ('SAP SuccessFactors', 'sap_successfactors'), ('Teamtailor', 'teamtailor'), ('Polymer', 'polymer'), ('Phenom', 'phenom'), ('Dayforce', 'dayforce'), ('JOIN', 'join'), ('CareerPlug', 'careerplug'), ('Hireology', 'hireology')]


def migrate() -> dict[str, object]:
    initialize_database()
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    runtime = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    save_setting("adapter_catalog", catalog, changed_by=MIGRATION_ID)
    save_setting("adapter_source_mapping", mapping, changed_by=MIGRATION_ID)
    save_setting("source_worker_registry", registry, changed_by=MIGRATION_ID)
    save_setting("provider_runtime", runtime, changed_by=MIGRATION_ID)

    checksum = hashlib.sha256(
        Path(__file__).read_bytes()
        + CATALOG_PATH.read_bytes()
        + MAPPING_PATH.read_bytes()
        + REGISTRY_PATH.read_bytes()
        + RUNTIME_PATH.read_bytes()
    ).hexdigest()

    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for provider, slug in PROVIDERS:
            module = f"app.{slug}_worker"
            connection.execute(
                """
                INSERT INTO source_health(
                    source_name,source_tier,enabled,cadence_minutes,cost_mode,
                    health_status
                ) VALUES(?,1,0,360,'free','installed_disabled')
                ON CONFLICT(source_name) DO UPDATE SET
                    source_tier=1,
                    cost_mode='free',
                    health_status=CASE
                        WHEN source_health.last_run_at IS NOT NULL
                        THEN source_health.health_status
                        WHEN source_health.enabled=1
                        THEN 'enabled_pending_first_run'
                        ELSE 'installed_disabled'
                    END,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (provider,),
            )
            connection.execute(
                """
                INSERT INTO adapter_coverage(
                    provider,implementation_module,implemented,fixture_tested,
                    live_tested,enabled,health_status,support_level,
                    blocked_reason,notes
                ) VALUES(?,?,1,1,0,0,'fixture_tested','public_page_fallback',
                          NULL,?)
                ON CONFLICT(provider) DO UPDATE SET
                    implementation_module=excluded.implementation_module,
                    implemented=1,
                    fixture_tested=1,
                    enabled=(
                        SELECT enabled
                        FROM source_health
                        WHERE lower(source_name)=lower(excluded.provider)
                    ),
                    health_status=CASE
                        WHEN adapter_coverage.live_tested=1
                        THEN adapter_coverage.health_status
                        ELSE 'fixture_tested'
                    END,
                    support_level='public_page_fallback',
                    blocked_reason=NULL,
                    notes=excluded.notes,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    provider,
                    module,
                    (
                        "Canonical public career-page adapter. Uses provider "
                        "board registry, bounded retries/browser fallback, "
                        "dashboard targeting, dedupe/persist, and source metrics."
                    ),
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    discovery_sync = import_existing_career_urls()

    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for provider, _slug in PROVIDERS:
            board_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM provider_board_registry
                    WHERE lower(provider)=lower(?) AND enabled=1
                    """,
                    (provider,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                UPDATE adapter_coverage
                SET us_board_count=(
                        SELECT COUNT(*)
                        FROM provider_board_registry
                        WHERE lower(provider)=lower(?)
                          AND enabled=1
                          AND lower(us_relevance) IN
                              ('us','high','confirmed','nationwide')
                    ),
                    enabled=(
                        SELECT enabled FROM source_health
                        WHERE lower(source_name)=lower(?)
                    ),
                    notes=notes || ?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE provider=?
                """,
                (
                    provider,
                    provider,
                    (
                        f" Configured public boards after local sync: "
                        f"{board_count}."
                    ),
                    provider,
                ),
            )
        detail = {
            "providers": [name for name, _slug in PROVIDERS],
            "fixture_tested": True,
            "enabled_by_migration": False,
            "local_board_sync": discovery_sync,
"runtime_board_policy": "Employer Board Discovery validation required before provider_board_registry promotion",
        }
        connection.execute(
            """
            INSERT INTO schema_migrations(migration_id,checksum,detail_json)
            VALUES(?,?,?)
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
    print(json.dumps(migrate(), indent=2, sort_keys=True, default=str))
