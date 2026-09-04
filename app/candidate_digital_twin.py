"""Internal, tenant-scoped candidate digital-twin records.

This is intentionally a storage/service foundation, not an Apply integration or
HTTP surface.  It never imports or infers rows from the legacy singleton
``candidate_profile_facts`` table.  A future trusted bridge may explicitly
confirm and write facts through this module.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.database import get_connection
from app.tenant_foundation import OwnerContext, current_owner, ensure_schema as ensure_tenant_schema


PAYLOAD_VERSION = "candidate-profile-v1"
_MAX_KEY_LENGTH = 120
_MAX_TEXT_LENGTH = 4_000
_VALID_ONBOARDING_STATES = {"not_started", "in_progress", "complete"}

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS candidate_digital_twin_facts (
        fact_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        fact_key TEXT NOT NULL,
        value_json TEXT NOT NULL,
        provenance TEXT NOT NULL,
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        user_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(user_confirmed IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, user_id, fact_key),
        FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
        FOREIGN KEY (user_id) REFERENCES app_users(user_id) ON DELETE RESTRICT,
        FOREIGN KEY (tenant_id, user_id)
            REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE RESTRICT
    );""",
    """CREATE TABLE IF NOT EXISTS candidate_digital_twin_evidence (
        evidence_id TEXT PRIMARY KEY,
        fact_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        provenance TEXT NOT NULL,
        source_reference TEXT NOT NULL,
        excerpt TEXT,
        captured_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (fact_id) REFERENCES candidate_digital_twin_facts(fact_id) ON DELETE RESTRICT,
        FOREIGN KEY (tenant_id, user_id)
            REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_candidate_twin_evidence_fact
        ON candidate_digital_twin_evidence(fact_id, created_at);""",
    """CREATE TABLE IF NOT EXISTS candidate_digital_twin_preferences (
        preference_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        preference_key TEXT NOT NULL,
        value_json TEXT NOT NULL,
        provenance TEXT NOT NULL,
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        user_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(user_confirmed IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, user_id, preference_key),
        FOREIGN KEY (tenant_id, user_id)
            REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE RESTRICT
    );""",
    """CREATE TABLE IF NOT EXISTS candidate_digital_twin_onboarding (
        onboarding_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'not_started',
        completed_steps_json TEXT NOT NULL DEFAULT '[]',
        user_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(user_confirmed IN (0, 1)),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, user_id),
        FOREIGN KEY (tenant_id, user_id)
            REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE RESTRICT
    );""",
)


def digital_twin_enabled() -> bool:
    """Whether callers may use the new internal digital-twin service."""
    return str(os.getenv("MUNSHI_CANDIDATE_DIGITAL_TWIN_ENABLED") or "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def _enabled_owner(connection: sqlite3.Connection) -> OwnerContext:
    if not digital_twin_enabled():
        raise RuntimeError("Candidate digital twin is disabled.")
    return current_owner(connection)


def _key(value: str, label: str) -> str:
    result = str(value or "").strip().casefold()
    if not result or len(result) > _MAX_KEY_LENGTH:
        raise ValueError(f"{label} is required and must be at most {_MAX_KEY_LENGTH} characters.")
    return result


def _text(value: str, label: str, *, required: bool = True) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > _MAX_TEXT_LENGTH:
        raise ValueError(f"{label} is required and must be at most {_MAX_TEXT_LENGTH} characters.")
    return result


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _confidence(value: float) -> float:
    confidence = float(value)
    if not 0 <= confidence <= 1:
        raise ValueError("Confidence must be between 0 and 1.")
    return confidence


def _rows(connection: sqlite3.Connection, owner: OwnerContext, table: str, order_by: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"SELECT * FROM {table} WHERE tenant_id=? AND user_id=? ORDER BY {order_by}",
        (owner.tenant_id, owner.user_id),
    ).fetchall()
    return [dict(row) for row in rows]


def upsert_fact(*, fact_key: str, value: Any, provenance: str, confidence: float, user_confirmed: bool) -> str:
    """Explicitly create/update one fact; returns its stable ID without inference."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = _enabled_owner(connection)
        key, source = _key(fact_key, "Fact key"), _text(provenance, "Provenance")
        row = connection.execute(
            "SELECT fact_id FROM candidate_digital_twin_facts WHERE tenant_id=? AND user_id=? AND fact_key=?",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        fact_id = str(row["fact_id"]) if row else str(uuid4())
        connection.execute(
            """INSERT INTO candidate_digital_twin_facts(
                fact_id,tenant_id,user_id,fact_key,value_json,provenance,confidence,user_confirmed
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id,user_id,fact_key) DO UPDATE SET
                value_json=excluded.value_json, provenance=excluded.provenance,
                confidence=excluded.confidence, user_confirmed=excluded.user_confirmed,
                updated_at=CURRENT_TIMESTAMP""",
            (fact_id, owner.tenant_id, owner.user_id, key, _json(value), source, _confidence(confidence), int(user_confirmed)),
        )
        connection.commit()
        return fact_id
    finally:
        connection.close()


def add_evidence(*, fact_id: str, provenance: str, source_reference: str, excerpt: str = "", metadata: Any = None, captured_at: str | None = None) -> str:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = _enabled_owner(connection)
        normalized_fact_id = _text(fact_id, "Fact ID")
        fact = connection.execute(
            "SELECT 1 FROM candidate_digital_twin_facts WHERE fact_id=? AND tenant_id=? AND user_id=?",
            (normalized_fact_id, owner.tenant_id, owner.user_id),
        ).fetchone()
        if fact is None:
            raise LookupError("Fact does not belong to the current candidate.")
        evidence_id = str(uuid4())
        connection.execute(
            """INSERT INTO candidate_digital_twin_evidence(
                evidence_id,fact_id,tenant_id,user_id,provenance,source_reference,excerpt,captured_at,metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (evidence_id, normalized_fact_id, owner.tenant_id, owner.user_id, _text(provenance, "Provenance"),
             _text(source_reference, "Source reference"), _text(excerpt, "Excerpt", required=False),
             captured_at or datetime.now(timezone.utc).isoformat(), _json(metadata if metadata is not None else {})),
        )
        connection.commit()
        return evidence_id
    finally:
        connection.close()


def upsert_preference(*, preference_key: str, value: Any, provenance: str, confidence: float, user_confirmed: bool) -> str:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = _enabled_owner(connection)
        key = _key(preference_key, "Preference key")
        row = connection.execute("SELECT preference_id FROM candidate_digital_twin_preferences WHERE tenant_id=? AND user_id=? AND preference_key=?", (owner.tenant_id, owner.user_id, key)).fetchone()
        preference_id = str(row["preference_id"]) if row else str(uuid4())
        connection.execute("""INSERT INTO candidate_digital_twin_preferences(preference_id,tenant_id,user_id,preference_key,value_json,provenance,confidence,user_confirmed) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,user_id,preference_key) DO UPDATE SET value_json=excluded.value_json,provenance=excluded.provenance,confidence=excluded.confidence,user_confirmed=excluded.user_confirmed,updated_at=CURRENT_TIMESTAMP""", (preference_id, owner.tenant_id, owner.user_id, key, _json(value), _text(provenance, "Provenance"), _confidence(confidence), int(user_confirmed)))
        connection.commit()
        return preference_id
    finally:
        connection.close()


def save_onboarding(*, state: str, completed_steps: list[str], user_confirmed: bool) -> str:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = _enabled_owner(connection)
        normalized_state = _key(state, "Onboarding state")
        if normalized_state not in _VALID_ONBOARDING_STATES:
            raise ValueError("Invalid onboarding state.")
        if not isinstance(completed_steps, list):
            raise ValueError("Completed steps must be a list.")
        steps = [_text(step, "Completed step") for step in completed_steps]
        row = connection.execute("SELECT onboarding_id FROM candidate_digital_twin_onboarding WHERE tenant_id=? AND user_id=?", (owner.tenant_id, owner.user_id)).fetchone()
        onboarding_id = str(row["onboarding_id"]) if row else str(uuid4())
        connection.execute("""INSERT INTO candidate_digital_twin_onboarding(onboarding_id,tenant_id,user_id,state,completed_steps_json,user_confirmed) VALUES (?,?,?,?,?,?) ON CONFLICT(tenant_id,user_id) DO UPDATE SET state=excluded.state,completed_steps_json=excluded.completed_steps_json,user_confirmed=excluded.user_confirmed,updated_at=CURRENT_TIMESTAMP""", (onboarding_id, owner.tenant_id, owner.user_id, normalized_state, _json(steps), int(user_confirmed)))
        connection.commit()
        return onboarding_id
    finally:
        connection.close()


def internal_profile_payload() -> dict[str, Any]:
    """Versioned internal contract; it does not send data to Apply or any network."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = _enabled_owner(connection)
        facts = _rows(connection, owner, "candidate_digital_twin_facts", "fact_key")
        for fact in facts:
            fact["value"] = json.loads(fact.pop("value_json"))
            fact["evidence"] = [dict(row) for row in connection.execute("SELECT evidence_id,provenance,source_reference,excerpt,captured_at,metadata_json FROM candidate_digital_twin_evidence WHERE fact_id=? ORDER BY created_at", (fact["fact_id"],)).fetchall()]
            for evidence in fact["evidence"]:
                evidence["metadata"] = json.loads(evidence.pop("metadata_json"))
        preferences = _rows(connection, owner, "candidate_digital_twin_preferences", "preference_key")
        for preference in preferences:
            preference["value"] = json.loads(preference.pop("value_json"))
        onboarding = _rows(connection, owner, "candidate_digital_twin_onboarding", "updated_at DESC")
        if onboarding:
            onboarding[0]["completed_steps"] = json.loads(onboarding[0].pop("completed_steps_json"))
        return {"version": PAYLOAD_VERSION, "tenant_id": owner.tenant_id, "user_id": owner.user_id, "facts": facts, "preferences": preferences, "onboarding": onboarding[0] if onboarding else None}
    finally:
        connection.close()
