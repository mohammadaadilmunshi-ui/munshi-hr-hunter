"""Fresh-volume adapter control-plane bootstrap for isolated staging.

This migration intentionally does not:
- enable any source,
- start any scheduler,
- copy production board tokens,
- perform network requests,
- send Telegram messages,
- call n8n,
- store jobs, or
- touch MUNSHI Apply.

It installs deterministic configuration and source registrations so later
provider-specific staging probes can be enabled one at a time.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
MIGRATION_ID = "052_adapter_runtime_foundation_v1"

CONFIG_PATHS = {
    "source_catalog": ROOT_DIR / "config" / "source_catalog.json",
    "adapter_catalog": ROOT_DIR / "config" / "adapter_catalog.json",
    "source_worker_registry": ROOT_DIR / "config" / "source_worker_registry.json",
    "provider_runtime": ROOT_DIR / "config" / "provider_runtime_policy.json",
    "downstream_contract": ROOT_DIR / "config" / "downstream_contract.json",
    "query_strategy": ROOT_DIR / "config" / "query_strategy.json",
    "orchestration": ROOT_DIR / "config" / "orchestration_policy.json",
    "verified_provider_boards": ROOT_DIR / "config" / "verified_provider_boards.json",
    "canonical_targeting_policy": ROOT_DIR / "config" / "canonical_targeting_policy.json",
}

SCHEDULE_SQL = """
CREATE TABLE IF NOT EXISTS source_random_schedule (
    source_name TEXT PRIMARY KEY COLLATE NOCASE,
    next_run_at TEXT,
    last_scheduled_at TEXT,
    last_started_at TEXT,
    last_completed_at TEXT,
    base_cadence_minutes INTEGER NOT NULL DEFAULT 60,
    jitter_minutes INTEGER NOT NULL DEFAULT 0,
    schedule_reason TEXT NOT NULL DEFAULT 'seeded',
    schedule_state TEXT NOT NULL DEFAULT 'cooldown',
    last_worker_status TEXT,
    last_worker_returncode INTEGER,
    consecutive_scheduler_failures INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

ATS_REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS ats_company_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL UNIQUE,
    ats_type TEXT NOT NULL DEFAULT 'unknown',
    board_token TEXT,
    careers_url TEXT,
    enabled INTEGER NOT NULL DEFAULT 0,
    priority_weight INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    last_success_at TEXT,
    last_failure_at TEXT,
    last_run_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    jobs_found_last_run INTEGER NOT NULL DEFAULT 0,
    health_status TEXT NOT NULL DEFAULT 'not_configured',
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().casefold() in {
        "1", "true", "yes", "on"
    }


def _assert_isolated_staging() -> None:
    if not _truthy("CLOUD_SHADOW_MODE"):
        raise RuntimeError(
            "Adapter Runtime Foundation is restricted to CLOUD_SHADOW_MODE staging."
        )
    forbidden = (
        "PRODUCTION_STATE_IMPORTED",
        "PRODUCTION_CALLBACKS_ENABLED",
        "HUNTER_ENABLE_TELEGRAM",
        "HUNTER_ENABLE_DISCOVERY_SCHEDULER",
        "HUNTER_ENABLE_COORDINATOR",
    )
    active = [name for name in forbidden if _truthy(name)]
    if active:
        raise RuntimeError(
            "Adapter Runtime Foundation refuses active production/writer lanes: "
            + ", ".join(active)
        )


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def _setting(connection, key: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT value_json FROM settings WHERE setting_key=?",
        (key,),
    ).fetchone()
    if row is None:
        return {}
    try:
        value = json.loads(str(row[0] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_setting(connection, key: str, value: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO settings(setting_key,value_json,updated_at)
        VALUES(?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(setting_key) DO UPDATE SET
          value_json=excluded.value_json,
          updated_at=CURRENT_TIMESTAMP
        """,
        (key, json.dumps(value, sort_keys=True)),
    )


def _merge_defaults(
    defaults: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(defaults)
    for key, value in current.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_defaults(
                dict(merged[key]),
                value,
            )
        else:
            merged[key] = value
    return merged


def _checksum() -> str:
    digest = hashlib.sha256(Path(__file__).read_bytes())
    for key in sorted(CONFIG_PATHS):
        digest.update(CONFIG_PATHS[key].read_bytes())
    return digest.hexdigest()


def _install_settings(connection) -> None:
    for setting_key in (
        "source_worker_registry",
        "provider_runtime",
        "downstream_contract",
        "query_strategy",
        "orchestration",
    ):
        defaults = _load(CONFIG_PATHS[setting_key])
        current = _setting(connection, setting_key)
        _save_setting(
            connection,
            setting_key,
            _merge_defaults(defaults, current),
        )

    _save_setting(
        connection,
        "adapter_catalog",
        _load(CONFIG_PATHS["adapter_catalog"]),
    )
    _save_setting(
        connection,
        "verified_provider_boards",
        _load(CONFIG_PATHS["verified_provider_boards"]),
    )

    policy = _load(CONFIG_PATHS["canonical_targeting_policy"])
    targeting = _setting(connection, "targeting")
    for key in (
        "schema_version",
        "mode",
        "eligibility",
        "experience_policy",
        "title_only_hard_rejects",
        "role_negative_contexts",
        "role_families",
    ):
        if key in policy:
            targeting[key] = policy[key]
    _save_setting(connection, "targeting", targeting)

    authorization = _setting(connection, "authorization")
    authorization["authorization_mode"] = str(policy.get("mode") or "")
    eligibility = policy.get("eligibility") or {}
    authorization["normal_discovery_geography"] = str(
        eligibility.get("label") or ""
    )
    authorization["historical_cpt_fields_are_eligibility_rules"] = False
    _save_setting(connection, "authorization", authorization)

    _save_setting(
        connection,
        "location_policy",
        {"rule_purposes": policy.get("location_rule_purposes") or {}},
    )

    columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(location_rules)"
        ).fetchall()
    }
    if "rule_purpose" in columns:
        for name, purpose in (
            policy.get("location_rule_purposes") or {}
        ).items():
            connection.execute(
                """
                UPDATE location_rules
                SET rule_purpose=?, updated_at=CURRENT_TIMESTAMP
                WHERE location_name=?
                """,
                (purpose, name),
            )


def _install_source_catalog(connection) -> int:
    catalog = _load(CONFIG_PATHS["source_catalog"])
    sources = catalog.get("sources") or []
    if not isinstance(sources, list):
        raise RuntimeError("source_catalog.sources must be a list")

    connection.execute(SCHEDULE_SQL)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_source_random_schedule_next_run
        ON source_random_schedule(next_run_at)
        """
    )

    count = 0
    for item in sources:
        if not isinstance(item, dict):
            continue
        name = str(item.get("source_name") or "").strip()
        if not name:
            continue
        tier = int(item["source_tier"])
        cadence = int(item["cadence_minutes"])
        cost_mode = str(item.get("cost_mode") or "free").strip()
        health = (
            "disabled_by_policy"
            if cost_mode == "paid"
            else "staging_registered"
        )

        connection.execute(
            """
            INSERT INTO source_health(
              source_name,source_tier,enabled,cadence_minutes,cost_mode,
              health_status,last_error,updated_at
            ) VALUES(?,?,0,?,?,?,NULL,CURRENT_TIMESTAMP)
            ON CONFLICT(source_name) DO UPDATE SET
              source_tier=excluded.source_tier,
              cadence_minutes=excluded.cadence_minutes,
              cost_mode=excluded.cost_mode,
              health_status=CASE
                WHEN source_health.last_success_at IS NOT NULL
                  THEN source_health.health_status
                ELSE excluded.health_status
              END,
              enabled=source_health.enabled,
              updated_at=CURRENT_TIMESTAMP
            """,
            (name, tier, cadence, cost_mode, health),
        )

        enabled = int(
            connection.execute(
                """
                SELECT enabled
                FROM source_health
                WHERE lower(source_name)=lower(?)
                """,
                (name,),
            ).fetchone()[0]
        )

        existing_schedule = connection.execute(
            """
            SELECT 1
            FROM source_random_schedule
            WHERE lower(source_name)=lower(?)
            """,
            (name,),
        ).fetchone()

        if existing_schedule is None:
            connection.execute(
                """
                INSERT INTO source_random_schedule(
                  source_name,next_run_at,base_cadence_minutes,jitter_minutes,
                  schedule_reason,schedule_state
                ) VALUES(?,NULL,?,0,'source_disabled','disabled')
                """,
                (name, cadence),
            )
        elif enabled == 0:
            connection.execute(
                """
                UPDATE source_random_schedule
                SET next_run_at=NULL,
                    base_cadence_minutes=?,
                    jitter_minutes=0,
                    schedule_reason='source_disabled',
                    schedule_state='disabled',
                    updated_at=CURRENT_TIMESTAMP
                WHERE lower(source_name)=lower(?)
                """,
                (cadence, name),
            )
        else:
            connection.execute(
                """
                UPDATE source_random_schedule
                SET base_cadence_minutes=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE lower(source_name)=lower(?)
                """,
                (cadence, name),
            )
        count += 1
    return count


def _install_adapter_coverage(connection) -> int:
    catalog = _load(CONFIG_PATHS["adapter_catalog"])
    providers = catalog.get("providers") or []
    count = 0
    for item in providers:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip()
        if not provider:
            continue

        module = str(item.get("module") or "").strip()
        declared = bool(item.get("implemented"))
        implemented = bool(
            declared
            and module
            and importlib.util.find_spec(module) is not None
        )
        blocked_reason = str(
            item.get("blocked_reason") or ""
        ).strip() or None
        health = (
            "blocked"
            if blocked_reason
            else "staging_registered"
            if implemented
            else "not_implemented"
        )

        existing = connection.execute(
            """
            SELECT enabled,live_tested,health_status
            FROM adapter_coverage
            WHERE provider=?
            """,
            (provider,),
        ).fetchone()
        preserved_enabled = int(existing[0]) if existing else 0
        preserved_live = int(existing[1]) if existing else 0
        preserved_health = (
            str(existing[2])
            if existing and preserved_live
            else health
        )

        connection.execute(
            """
            INSERT INTO adapter_coverage(
              provider,implementation_module,implemented,fixture_tested,
              live_tested,enabled,health_status,support_level,blocked_reason,
              notes,updated_at
            ) VALUES(?,?,?,0,?,?,?, ?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(provider) DO UPDATE SET
              implementation_module=excluded.implementation_module,
              implemented=excluded.implemented,
              enabled=excluded.enabled,
              health_status=excluded.health_status,
              support_level=excluded.support_level,
              blocked_reason=excluded.blocked_reason,
              notes=excluded.notes,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                provider,
                module or None,
                int(implemented),
                preserved_live,
                preserved_enabled,
                preserved_health,
                str(item.get("support_level") or "unknown"),
                blocked_reason,
                (
                    "Registered by isolated-staging Adapter Runtime "
                    "Foundation V1."
                ),
            ),
        )
        count += 1
    return count


def _install_provider_board_registry(connection) -> int:
    payload = _load(CONFIG_PATHS["verified_provider_boards"])
    boards = payload.get("boards") or []
    count = 0

    for board in boards:
        if not isinstance(board, dict):
            continue
        company = str(board.get("company_name") or "").strip()
        provider = str(board.get("provider") or "").strip()
        if not company or not provider:
            continue

        existing = connection.execute(
            """
            SELECT rowid,enabled
            FROM provider_board_registry
            WHERE provider=? AND company_name=?
              AND COALESCE(tenant,'')=COALESCE(?, '')
              AND COALESCE(site_name,'')=COALESCE(?, '')
            LIMIT 1
            """,
            (
                provider,
                company,
                board.get("tenant"),
                board.get("site_name"),
            ),
        ).fetchone()
        preserved_enabled = int(existing[1]) if existing else 0
        values = (
            board.get("board_url"),
            board.get("careers_url"),
            str(board.get("us_relevance") or "unknown"),
            preserved_enabled,
            int(board.get("priority_weight") or 0),
            "staging_registered",
            int(board.get("last_job_count") or 0),
            (
                "Registered disabled in isolated staging. "
                + str(board.get("notes") or "")
            ).strip(),
        )

        if existing:
            connection.execute(
                """
                UPDATE provider_board_registry
                SET board_url=?,
                    careers_url=?,
                    us_relevance=?,
                    enabled=?,
                    priority_weight=?,
                    health_status=?,
                    last_job_count=?,
                    notes=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE rowid=?
                """,
                (*values, int(existing[0])),
            )
        else:
            connection.execute(
                """
                INSERT INTO provider_board_registry(
                  company_name,provider,tenant,site_name,board_url,careers_url,
                  us_relevance,enabled,priority_weight,last_verified_at,
                  health_status,last_job_count,notes
                ) VALUES(?,?,?,?,?,?,?,?,?,NULL,?,?,?)
                """,
                (
                    company,
                    provider,
                    board.get("tenant"),
                    board.get("site_name"),
                    *values,
                ),
            )
        count += 1
    return count


def _install_ats_registry_schema(connection) -> None:
    connection.execute(ATS_REGISTRY_SQL)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ats_registry_enabled
        ON ats_company_registry(enabled)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ats_registry_type
        ON ats_company_registry(ats_type)
        """
    )


def apply(connection) -> dict[str, Any]:
    _assert_isolated_staging()

    _install_settings(connection)
    source_count = _install_source_catalog(connection)
    adapter_count = _install_adapter_coverage(connection)
    provider_board_count = _install_provider_board_registry(connection)
    _install_ats_registry_schema(connection)

    detail = {
        "source_count": source_count,
        "adapter_count": adapter_count,
        "provider_board_count": provider_board_count,
        "ats_registry_schema": True,
        "all_new_sources_default_disabled": True,
        "network_requests": 0,
        "telegram_calls": 0,
        "n8n_calls": 0,
        "jobs_stored": 0,
        "apply_mutations": 0,
    }
    connection.execute(
        """
        INSERT INTO schema_migrations(
          migration_id,checksum,detail_json
        ) VALUES(?,?,?)
        ON CONFLICT(migration_id) DO UPDATE SET
          applied_at=CURRENT_TIMESTAMP,
          checksum=excluded.checksum,
          detail_json=excluded.detail_json
        """,
        (
            MIGRATION_ID,
            _checksum(),
            json.dumps(detail, sort_keys=True),
        ),
    )
    return detail
