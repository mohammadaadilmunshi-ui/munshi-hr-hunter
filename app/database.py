from __future__ import annotations

import json
import os
import sqlite3
import hashlib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.platform_config import database_path, project_root


ROOT_DIR = project_root()
load_dotenv(ROOT_DIR / ".env")

DB_PATH = database_path()


BOOTSTRAP_CONFIG_PATH = ROOT_DIR / "config" / "bootstrap.json"


def _load_bootstrap_config() -> dict[str, Any]:
    """Load install-time values from configuration, never from Python policy."""
    try:
        payload = json.loads(BOOTSTRAP_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


_BOOTSTRAP = _load_bootstrap_config()
DEFAULT_SETTINGS: dict[str, Any] = dict(_BOOTSTRAP.get("settings") or {})
DEFAULT_LOCATIONS: list[dict[str, Any]] = list(
    _BOOTSTRAP.get("locations") or []
)
DEFAULT_SOURCES: list[tuple[Any, ...]] = [
    tuple(value) for value in (_BOOTSTRAP.get("sources") or [])
]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    setting_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS location_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_name TEXT NOT NULL,
    location_type TEXT NOT NULL,
    city TEXT,
    state TEXT,
    country TEXT NOT NULL DEFAULT 'US',
    remote_allowed INTEGER NOT NULL DEFAULT 1,
    hybrid_allowed INTEGER NOT NULL DEFAULT 1,
    onsite_allowed INTEGER NOT NULL DEFAULT 1,
    hybrid_max_miles INTEGER,
    priority_weight INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(location_name, location_type)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    job_fingerprint TEXT NOT NULL UNIQUE,
    url_fingerprint TEXT,
    ats_job_id TEXT,

    source TEXT NOT NULL,
    source_tier INTEGER,
    company_name TEXT NOT NULL,
    title TEXT NOT NULL,

    location_raw TEXT,
    city TEXT,
    state TEXT,
    country TEXT NOT NULL DEFAULT 'US',
    remote_type TEXT,

    job_url TEXT,
    apply_url TEXT,
    description_raw TEXT,

    salary_raw TEXT,
    normalized_hourly_min REAL,
    normalized_hourly_max REAL,
    salary_confidence TEXT,

    target_track TEXT,
    hunter_score REAL,
    match_label TEXT,

    status TEXT NOT NULL DEFAULT 'found',
    hard_rejection_reason TEXT,

    cpt_trapdoor INTEGER NOT NULL DEFAULT 0,
    ghost_risk_score REAL NOT NULL DEFAULT 0,

    date_posted TEXT,
    apply_deadline TEXT,
    start_date TEXT,
    end_date TEXT,

    manual_job_text TEXT,

    employment_type TEXT,
    hours_per_week TEXT,
    responsibilities TEXT,
    qualifications TEXT,
    preferred_qualifications TEXT,
    preferred_skills TEXT,
    skills_keywords TEXT,
    work_authorization TEXT,
    benefits TEXT,
    company_size TEXT,
    industry TEXT,
    employer_description TEXT,
    detail_extraction_status TEXT,
    detail_extraction_version TEXT,
    detail_extraction_json TEXT,

    telegram_sent INTEGER NOT NULL DEFAULT 0,
    sent_to_n8n INTEGER NOT NULL DEFAULT 0,
    n8n_send_mode TEXT,
    already_applied INTEGER NOT NULL DEFAULT 0,

    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_score
ON jobs(hunter_score);

CREATE INDEX IF NOT EXISTS idx_jobs_status
ON jobs(status);

CREATE INDEX IF NOT EXISTS idx_jobs_company
ON jobs(company_name);

CREATE INDEX IF NOT EXISTS idx_jobs_first_seen
ON jobs(first_seen_at);

CREATE TABLE IF NOT EXISTS source_health (
    source_name TEXT PRIMARY KEY,
    source_tier INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    cadence_minutes INTEGER NOT NULL DEFAULT 360,
    cost_mode TEXT NOT NULL DEFAULT 'free',

    last_success_at TEXT,
    last_failure_at TEXT,
    last_run_at TEXT,

    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_http_status INTEGER,
    jobs_found_last_run INTEGER NOT NULL DEFAULT 0,
    average_response_ms REAL,

    health_status TEXT NOT NULL DEFAULT 'not_tested',
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    event_status TEXT NOT NULL DEFAULT 'recorded',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_events_job
ON events(job_id);

CREATE INDEX IF NOT EXISTS idx_events_type
ON events(event_type);

CREATE TABLE IF NOT EXISTS n8n_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    job_fingerprint TEXT NOT NULL,
    send_mode TEXT NOT NULL,

    n8n_status TEXT NOT NULL,
    final_ats_score REAL,

    resume_doc_url TEXT,
    resume_pdf_url TEXT,
    cover_letter_doc_url TEXT,
    google_sheet_url TEXT,

    recruiter_found INTEGER,
    outreach_draft_created INTEGER,

    error_message TEXT,
    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,

    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS n8n_callback_receipts (
    receipt_key TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL,
    request_id TEXT,
    queue_id INTEGER,
    callback_status TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_n8n_callback_receipts_job
ON n8n_callback_receipts(job_id, received_at DESC);

CREATE TABLE IF NOT EXISTS configuration_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_key TEXT NOT NULL,
    old_value_json TEXT,
    new_value_json TEXT NOT NULL,
    old_hash TEXT,
    new_hash TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_configuration_history_key_time
ON configuration_history(setting_key, changed_at DESC);

CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checksum TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS source_runs (
    run_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    provider TEXT,
    query_name TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    run_status TEXT NOT NULL DEFAULT 'completed',
    request_count INTEGER NOT NULL DEFAULT 0,
    raw_count INTEGER NOT NULL DEFAULT 0,
    normalized_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    eligible_count INTEGER NOT NULL DEFAULT 0,
    new_eligible_count INTEGER NOT NULL DEFAULT 0,
    reject_role_count INTEGER NOT NULL DEFAULT 0,
    reject_location_count INTEGER NOT NULL DEFAULT 0,
    reject_hard_requirement_count INTEGER NOT NULL DEFAULT 0,
    reject_company_count INTEGER NOT NULL DEFAULT 0,
    reject_other_targeting_count INTEGER NOT NULL DEFAULT 0,
    accounting_delta INTEGER NOT NULL DEFAULT 0,
    telegram_count INTEGER NOT NULL DEFAULT 0,
    downstream_success_count INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL,
    error_count INTEGER NOT NULL DEFAULT 0,
    rules_version TEXT,
    rules_hash TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_source_runs_source_time
ON source_runs(source_name, started_at DESC);

CREATE TABLE IF NOT EXISTS telegram_operational_outbox (
    logical_id TEXT PRIMARY KEY,
    notification_kind TEXT NOT NULL,
    source_name TEXT,
    run_id TEXT,
    incident_id TEXT,
    delivery_state TEXT NOT NULL DEFAULT 'pending',
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_token TEXT,
    lease_expires_at TEXT,
    telegram_message_id INTEGER,
    last_error_class TEXT,
    last_error_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT,
    FOREIGN KEY(run_id) REFERENCES source_runs(run_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_telegram_operational_outbox_delivery
ON telegram_operational_outbox(delivery_state, next_attempt_at, created_at);

CREATE INDEX IF NOT EXISTS idx_telegram_operational_outbox_run
ON telegram_operational_outbox(run_id);

CREATE TABLE IF NOT EXISTS telegram_adapter_incidents (
    incident_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    due_at TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_text TEXT NOT NULL,
    incident_state TEXT NOT NULL DEFAULT 'open',
    alert_logical_id TEXT NOT NULL UNIQUE,
    recovery_logical_id TEXT UNIQUE,
    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    resolved_run_id TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(resolved_run_id) REFERENCES source_runs(run_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_telegram_adapter_incidents_open
ON telegram_adapter_incidents(incident_state, source_name, due_at);

CREATE TABLE IF NOT EXISTS source_run_stages (
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL,
    stage_status TEXT NOT NULL DEFAULT 'completed',
    detail_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, stage),
    FOREIGN KEY(run_id) REFERENCES source_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS targeting_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    source_name TEXT,
    external_id TEXT,
    job_identity TEXT,
    title TEXT,
    company_name TEXT,
    location_raw TEXT,
    primary_category TEXT NOT NULL,
    secondary_reasons_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    rules_version TEXT,
    rules_hash TEXT,
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_targeting_decisions_run_category
ON targeting_decisions(run_id, primary_category);

CREATE UNIQUE INDEX IF NOT EXISTS idx_targeting_decisions_identity
ON targeting_decisions(run_id, job_identity);

CREATE TABLE IF NOT EXISTS query_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    source_name TEXT NOT NULL,
    provider TEXT,
    query_name TEXT NOT NULL,
    role_family TEXT,
    request_count INTEGER NOT NULL DEFAULT 0,
    raw_count INTEGER NOT NULL DEFAULT 0,
    normalized_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    eligible_count INTEGER NOT NULL DEFAULT 0,
    new_eligible_count INTEGER NOT NULL DEFAULT 0,
    telegram_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL,
    measured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_query_performance_lookup
ON query_performance(source_name, query_name, measured_at DESC);

CREATE TABLE IF NOT EXISTS query_rotation_state (
    source_name TEXT PRIMARY KEY,
    cursor INTEGER NOT NULL DEFAULT 0,
    last_selected_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS adapter_coverage (
    provider TEXT PRIMARY KEY,
    implementation_module TEXT,
    implemented INTEGER NOT NULL DEFAULT 0,
    fixture_tested INTEGER NOT NULL DEFAULT 0,
    live_tested INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 0,
    health_status TEXT NOT NULL DEFAULT 'not_tested',
    support_level TEXT,
    us_board_count INTEGER NOT NULL DEFAULT 0,
    blocked_reason TEXT,
    last_verified_at TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS provider_board_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    tenant TEXT,
    site_name TEXT,
    board_url TEXT,
    careers_url TEXT,
    us_relevance TEXT NOT NULL DEFAULT 'unknown',
    enabled INTEGER NOT NULL DEFAULT 0,
    priority_weight INTEGER NOT NULL DEFAULT 0,
    last_verified_at TEXT,
    health_status TEXT NOT NULL DEFAULT 'not_tested',
    last_job_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, company_name, tenant, site_name)
);

CREATE INDEX IF NOT EXISTS idx_provider_board_registry_provider_enabled
ON provider_board_registry(provider, enabled);

CREATE TABLE IF NOT EXISTS backup_inventory (
    path TEXT PRIMARY KEY,
    created_at TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    backup_type TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    restore_scope TEXT,
    retained_reason TEXT,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS storage_metrics (
    measured_at TEXT PRIMARY KEY,
    disk_free_bytes INTEGER,
    project_bytes INTEGER,
    runtime_bytes INTEGER,
    backup_bytes INTEGER,
    diagnostic_bytes INTEGER,
    quarantine_bytes INTEGER,
    reclaimed_bytes INTEGER NOT NULL DEFAULT 0,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30.0,
        check_same_thread=False,
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    connection.execute("PRAGMA busy_timeout = 30000;")

    return connection



JOB_DETAIL_COLUMNS: dict[str, str] = {
    "employment_type": "TEXT",
    "hours_per_week": "TEXT",
    "responsibilities": "TEXT",
    "qualifications": "TEXT",
    "preferred_qualifications": "TEXT",
    "preferred_skills": "TEXT",
    "skills_keywords": "TEXT",
    "work_authorization": "TEXT",
    "benefits": "TEXT",
    "company_size": "TEXT",
    "industry": "TEXT",
    "employer_description": "TEXT",
    "detail_extraction_status": "TEXT",
    "detail_extraction_version": "TEXT",
    "detail_extraction_json": "TEXT",
    "score_breakdown_json": "TEXT NOT NULL DEFAULT '{}'",
    "scoring_version": "TEXT",
    "last_scored_at": "TEXT",
    "primary_decision": "TEXT",
    "secondary_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
    "decision_evidence_json": "TEXT NOT NULL DEFAULT '{}'",
    "targeting_rules_version": "TEXT",
    "targeting_rules_hash": "TEXT",
    "role_evidence_json": "TEXT NOT NULL DEFAULT '{}'",
    "experience_evidence_json": "TEXT NOT NULL DEFAULT '[]'",
    "location_evidence_json": "TEXT NOT NULL DEFAULT '{}'",
    "duplicate_group": "TEXT",
    "source_provenance_json": "TEXT NOT NULL DEFAULT '[]'",
    "preference_score": "INTEGER NOT NULL DEFAULT 0",
}


OPERATIONAL_COLUMNS: dict[str, dict[str, str]] = {
    "source_health": {
        "raw_jobs_last_run": "INTEGER NOT NULL DEFAULT 0",
        "eligible_jobs_last_run": "INTEGER NOT NULL DEFAULT 0",
        "inserted_jobs_last_run": "INTEGER NOT NULL DEFAULT 0",
        "duplicate_jobs_last_run": "INTEGER NOT NULL DEFAULT 0",
        "rejected_jobs_last_run": "INTEGER NOT NULL DEFAULT 0",
        "provider_used_last_run": "TEXT",
        "filter_summary_json": "TEXT",
        "targeting_rules_hash": "TEXT",
        "normalized_jobs_last_run": "INTEGER NOT NULL DEFAULT 0",
        "reject_role_last_run": "INTEGER NOT NULL DEFAULT 0",
        "reject_location_last_run": "INTEGER NOT NULL DEFAULT 0",
        "reject_hard_requirement_last_run": "INTEGER NOT NULL DEFAULT 0",
        "reject_company_last_run": "INTEGER NOT NULL DEFAULT 0",
        "reject_other_targeting_last_run": "INTEGER NOT NULL DEFAULT 0",
        "accounting_delta_last_run": "INTEGER NOT NULL DEFAULT 0",
        "last_run_id": "TEXT",
        "request_count_last_run": "INTEGER NOT NULL DEFAULT 0",
        "error_count_last_run": "INTEGER NOT NULL DEFAULT 0",
        "last_duration_ms": "REAL",
    },
    "location_rules": {
        "rule_purpose": "TEXT NOT NULL DEFAULT 'preference'",
    },
}


def ensure_job_detail_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(jobs)")
    }
    for name, ddl in JOB_DETAIL_COLUMNS.items():
        if name not in columns:
            connection.execute(
                f'ALTER TABLE jobs ADD COLUMN "{name}" {ddl}'
            )


def ensure_operational_columns(connection: sqlite3.Connection) -> None:
    for table, definitions in OPERATIONAL_COLUMNS.items():
        existing = {
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        for name, ddl in definitions.items():
            if name not in existing:
                connection.execute(
                    f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}'
                )

def seed_defaults(connection: sqlite3.Connection) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        connection.execute(
            """
            INSERT OR IGNORE INTO settings(setting_key, value_json)
            VALUES (?, ?)
            """,
            (key, json.dumps(value)),
        )

    for location in DEFAULT_LOCATIONS:
        connection.execute(
            """
            INSERT OR IGNORE INTO location_rules (
                location_name,
                location_type,
                city,
                state,
                country,
                remote_allowed,
                hybrid_allowed,
                onsite_allowed,
                hybrid_max_miles,
                priority_weight,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                location["location_name"],
                location["location_type"],
                location["city"],
                location["state"],
                location["country"],
                location["remote_allowed"],
                location["hybrid_allowed"],
                location["onsite_allowed"],
                location["hybrid_max_miles"],
                location["priority_weight"],
                location["notes"],
            ),
        )

    connection.executemany(
        """
        INSERT OR IGNORE INTO source_health (
            source_name,
            source_tier,
            enabled,
            cadence_minutes,
            cost_mode
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        DEFAULT_SOURCES,
    )


def initialize_database() -> Path:
    connection = get_connection()

    try:
        connection.execute("PRAGMA journal_mode = WAL;")
        connection.executescript(SCHEMA_SQL)
        ensure_job_detail_columns(connection)
        ensure_operational_columns(connection)
        seed_defaults(connection)
        connection.commit()
    finally:
        connection.close()

    return DB_PATH


def get_setting(setting_key: str, default: Any = None) -> Any:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT value_json
            FROM settings
            WHERE setting_key = ?
            """,
            (setting_key,),
        ).fetchone()

        if row is None:
            return default

        return json.loads(row["value_json"])
    finally:
        connection.close()


def save_setting(
    setting_key: str,
    value: Any,
    *,
    changed_by: str = "application",
) -> None:
    connection = get_connection()

    try:
        new_value_json = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = connection.execute(
            "SELECT value_json FROM settings WHERE setting_key = ?",
            (setting_key,),
        ).fetchone()
        old_value_json = str(existing["value_json"]) if existing else None

        if old_value_json is not None:
            try:
                old_value_json = json.dumps(
                    json.loads(old_value_json),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            except json.JSONDecodeError:
                pass

        if old_value_json == new_value_json:
            return

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO settings(setting_key, value_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key)
            DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (setting_key, new_value_json),
        )
        connection.execute(
            """
            INSERT INTO configuration_history (
                setting_key,
                old_value_json,
                new_value_json,
                old_hash,
                new_hash,
                changed_by
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                setting_key,
                old_value_json,
                new_value_json,
                (
                    hashlib.sha256(old_value_json.encode("utf-8")).hexdigest()
                    if old_value_json is not None
                    else None
                ),
                hashlib.sha256(new_value_json.encode("utf-8")).hexdigest(),
                str(changed_by or "application")[:120],
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _append_configuration_history(
    connection: sqlite3.Connection,
    *,
    setting_key: str,
    old_value: Any,
    new_value: Any,
    changed_by: str,
) -> bool:
    old_json = _canonical_json(old_value)
    new_json = _canonical_json(new_value)
    if old_json == new_json:
        return False
    connection.execute(
        """
        INSERT INTO configuration_history(
            setting_key,old_value_json,new_value_json,old_hash,new_hash,changed_by
        ) VALUES (?,?,?,?,?,?)
        """,
        (
            setting_key,
            old_json,
            new_json,
            hashlib.sha256(old_json.encode("utf-8")).hexdigest(),
            hashlib.sha256(new_json.encode("utf-8")).hexdigest(),
            str(changed_by or "application")[:120],
        ),
    )
    return True


def save_source_policy(
    source_name: str,
    *,
    enabled: bool,
    cadence_minutes: int,
    changed_by: str = "application",
) -> bool:
    cadence = max(5, min(int(cadence_minutes), 10080))
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT enabled,cadence_minutes,cost_mode FROM source_health WHERE source_name=?",
            (source_name,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown source registry entry: {source_name}")

        # AADIL_FREE_ONLY_SOURCE_POLICY_V16
        normalized_source = str(source_name or "").strip().casefold()
        cost_mode = str(row["cost_mode"] or "").strip().casefold()
        if bool(enabled) and (
            normalized_source in {"apify", "serpapi"}
            or cost_mode in {"paid", "paid_only", "paid_required", "metered", "credits"}
        ):
            raise ValueError(
                f"Paid source enablement is blocked by AADIL free-only policy: {source_name}"
            )
        schedule_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_random_schedule'"
        ).fetchone() is not None
        schedule = (
            connection.execute(
                "SELECT base_cadence_minutes FROM source_random_schedule WHERE source_name=?",
                (source_name,),
            ).fetchone()
            if schedule_exists
            else None
        )
        old_value = {
            "enabled": bool(row["enabled"]),
            "cadence_minutes": int(row["cadence_minutes"]),
            "schedule_base_cadence_minutes": (
                int(schedule["base_cadence_minutes"]) if schedule is not None else None
            ),
        }
        new_value = {
            "enabled": bool(enabled),
            "cadence_minutes": cadence,
            "schedule_base_cadence_minutes": cadence if schedule is not None else None,
        }
        if _canonical_json(old_value) == _canonical_json(new_value):
            return False
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE source_health
            SET enabled=?,cadence_minutes=?,updated_at=CURRENT_TIMESTAMP
            WHERE source_name=?
            """,
            (int(enabled), cadence, source_name),
        )
        if schedule is not None:
            connection.execute(
                """
                UPDATE source_random_schedule
                SET base_cadence_minutes=?,
                    schedule_state=?,
                    schedule_reason=?,
                    next_run_at=CASE
                        WHEN ?=1 THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE source_name=?
                """,
                (
                    cadence,
                    "ready" if enabled else "disabled",
                    (
                        "source_enabled_by_canonical_policy"
                        if enabled
                        else "source_disabled_by_canonical_policy"
                    ),
                    int(enabled),
                    source_name,
                ),
            )
        _append_configuration_history(
            connection,
            setting_key=f"source_registry:{source_name}",
            old_value=old_value,
            new_value=new_value,
            changed_by=changed_by,
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def save_board_policy(
    board_id: int,
    *,
    enabled: bool,
    priority_weight: int,
    notes: str,
    changed_by: str = "application",
) -> bool:
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id,company_name,provider,enabled,priority_weight,COALESCE(notes,'') AS notes
            FROM provider_board_registry WHERE id=?
            """,
            (int(board_id),),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown provider board registry entry: {board_id}")
        old_value = {
            "company": str(row["company_name"]),
            "provider": str(row["provider"]),
            "enabled": bool(row["enabled"]),
            "priority_weight": int(row["priority_weight"]),
            "notes": str(row["notes"]),
        }
        new_value = {
            **{key: old_value[key] for key in ("company", "provider")},
            "enabled": bool(enabled),
            "priority_weight": max(-100, min(int(priority_weight), 100)),
            "notes": str(notes or "").strip(),
        }
        if _canonical_json(old_value) == _canonical_json(new_value):
            return False
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE provider_board_registry
            SET enabled=?,priority_weight=?,notes=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                int(new_value["enabled"]),
                new_value["priority_weight"],
                new_value["notes"],
                int(board_id),
            ),
        )
        _append_configuration_history(
            connection,
            setting_key=f"provider_board_registry:{int(board_id)}",
            old_value=old_value,
            new_value=new_value,
            changed_by=changed_by,
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
