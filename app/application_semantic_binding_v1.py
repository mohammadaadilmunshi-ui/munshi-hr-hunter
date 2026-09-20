"""Durable browser-control -> canonical-concept bindings for Universal Brain.

Bindings contain semantic identity only.  Candidate values never enter this
ledger. Logical fingerprints intentionally exclude unstable DOM IDs so a safe
binding can survive SPA rerenders and plan supersession while repeat-group scope
prevents cross-filling duplicate employment/education rows.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from typing import Any, Mapping, Sequence
from uuid import uuid4

from app import candidate_ontology_v1 as ontology
from app.database import get_connection
from app.tenant_foundation import OwnerContext, current_owner, ensure_schema as ensure_tenant_schema
from app.universal_question_resolver_v1 import QuestionObservation

BINDING_VERSION = "application-semantic-binding-v1"
CLASSIFICATION_SOURCES = frozenset({"DETERMINISTIC", "ONTOLOGY", "MODEL"})

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS application_semantic_binding_revisions(
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(tenant_id,user_id),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE TABLE IF NOT EXISTS application_semantic_bindings(
        binding_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        plan_id TEXT,
        checkpoint_id TEXT,
        control_id TEXT,
        logical_field_fingerprint TEXT NOT NULL CHECK(length(logical_field_fingerprint)=64),
        question_fingerprint TEXT NOT NULL CHECK(length(question_fingerprint)=64),
        section_fingerprint TEXT NOT NULL CHECK(length(section_fingerprint)=64),
        option_signature TEXT NOT NULL CHECK(length(option_signature)=64),
        concept_id TEXT NOT NULL,
        classification_source TEXT NOT NULL,
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        binding_revision INTEGER NOT NULL CHECK(binding_revision >= 1),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        superseded_at TEXT,
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_application_semantic_binding_current
       ON application_semantic_bindings(
          tenant_id,user_id,application_id,session_id,logical_field_fingerprint,
          superseded_at,binding_revision DESC
       );""",
    """CREATE INDEX IF NOT EXISTS idx_application_semantic_binding_control
       ON application_semantic_bindings(tenant_id,user_id,session_id,control_id,superseded_at);""",
)


def browser_binding_enabled() -> bool:
    return str(os.getenv("MUNSHI_UNIVERSAL_BROWSER_BINDING_ENABLED") or "").strip().casefold() in {"1", "true", "yes", "on"}


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _owner(connection: sqlite3.Connection) -> OwnerContext:
    return current_owner(connection)


def _norm(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^a-z0-9+/#. _-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def option_signature(options: Sequence[str]) -> str:
    normalized = [_norm(item) for item in options if _norm(item)]
    # Preserve order because ATS option order can distinguish otherwise identical
    # controls; candidate selection state is not part of this signature.
    return _sha(normalized[:250])


def fingerprint_parts(observation: QuestionObservation | Mapping[str, Any]) -> dict[str, str]:
    raw = dict(observation) if isinstance(observation, Mapping) else {}
    scanner_inputs = raw.get("logical_field_fingerprint_inputs")
    if not isinstance(scanner_inputs, Mapping):
        scanner_inputs = raw.get("logicalFieldFingerprintInputs")
    scanner_inputs = dict(scanner_inputs) if isinstance(scanner_inputs, Mapping) else {}

    obs = observation if isinstance(observation, QuestionObservation) else QuestionObservation.from_mapping(observation)

    def scanner_or(key: str, fallback: Any) -> Any:
        # Presence is authoritative even when the scanner intentionally emitted
        # an empty string (e.g. custom SELECT shells with role=combobox).
        return scanner_inputs[key] if key in scanner_inputs else fallback

    repeat_group = scanner_or("repeatGroupId", obs.repeat_group)
    repeat_index = scanner_or("repeatIndex", obs.repeat_index)
    try:
        repeat_index = int(repeat_index) if repeat_index is not None else None
    except (TypeError, ValueError):
        repeat_index = None
    if repeat_group and repeat_index is None:
        raise ValueError("Repeated browser fields require an explicit repeat index before durable binding.")

    question_identity = {
        "raw_question": _norm(obs.raw_question),
        "label": _norm(scanner_or("label", obs.label)),
        "placeholder": _norm(obs.placeholder),
        "name": _norm(scanner_or("fieldName", obs.name)),
    }
    section_identity = {
        "section": _norm(scanner_or("sectionHeading", obs.section_heading)),
        "previous": [_norm(item) for item in obs.previous_labels],
        "next": [_norm(item) for item in obs.next_labels],
    }
    scanner_option_signature = _norm(scanner_inputs.get("optionSignature"))
    options = (
        _sha({"scanner_option_signature": scanner_option_signature})
        if scanner_option_signature
        else option_signature(obs.options)
    )
    logical = {
        "question": question_identity,
        "section": section_identity,
        "input_type": _norm(scanner_or("inputType", obs.input_type)),
        "role": _norm(scanner_or("role", obs.role)),
        "autocomplete": _norm(scanner_or("autocomplete", obs.autocomplete)),
        "option_signature": options,
        "repeat_group": _norm(repeat_group),
        "repeat_index": repeat_index,
    }
    return {
        "logical_field_fingerprint": _sha(logical),
        "question_fingerprint": _sha(question_identity),
        "section_fingerprint": _sha(section_identity),
        "option_signature": options,
    }


def _next_revision(connection: sqlite3.Connection, owner: OwnerContext) -> int:
    connection.execute(
        """INSERT INTO application_semantic_binding_revisions(tenant_id,user_id,revision)
           VALUES (?,?,0) ON CONFLICT(tenant_id,user_id) DO NOTHING""",
        (owner.tenant_id, owner.user_id),
    )
    connection.execute(
        """UPDATE application_semantic_binding_revisions SET revision=revision+1,updated_at=CURRENT_TIMESTAMP
           WHERE tenant_id=? AND user_id=?""",
        (owner.tenant_id, owner.user_id),
    )
    row = connection.execute(
        "SELECT revision FROM application_semantic_binding_revisions WHERE tenant_id=? AND user_id=?",
        (owner.tenant_id, owner.user_id),
    ).fetchone()
    return int(row["revision"])


def current_binding_revision(connection: sqlite3.Connection | None = None) -> int:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        row = connection.execute(
            "SELECT revision FROM application_semantic_binding_revisions WHERE tenant_id=? AND user_id=?",
            (owner.tenant_id, owner.user_id),
        ).fetchone()
        return int(row["revision"]) if row else 0
    finally:
        if owns:
            connection.close()


def bind(
    *,
    application_id: str,
    session_id: str,
    observation: QuestionObservation | Mapping[str, Any],
    concept_id: str,
    classification_source: str,
    confidence: float,
    plan_id: str | None = None,
    checkpoint_id: str | None = None,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        obs = observation if isinstance(observation, QuestionObservation) else QuestionObservation.from_mapping(observation)
        parts = fingerprint_parts(obs)
        concept = ontology.require_concept(concept_id)
        source = str(classification_source or "").strip().upper()
        if source not in CLASSIFICATION_SOURCES:
            raise ValueError("Unsupported semantic binding classification source.")
        score = float(confidence)
        if not 0 <= score <= 1:
            raise ValueError("Semantic binding confidence must be between 0 and 1.")
        app_id = str(application_id or "").strip()
        sess_id = str(session_id or "").strip()
        if not app_id or not sess_id:
            raise ValueError("Application and session identity are required for semantic binding.")

        existing = connection.execute(
            """SELECT * FROM application_semantic_bindings
               WHERE tenant_id=? AND user_id=? AND application_id=? AND session_id=?
                 AND logical_field_fingerprint=? AND superseded_at IS NULL
               ORDER BY binding_revision DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id, app_id, sess_id, parts["logical_field_fingerprint"]),
        ).fetchone()
        if existing is not None:
            row = dict(existing)
            if str(row["concept_id"]) != concept.concept_id:
                raise ValueError("Logical browser field is already bound to a different canonical concept.")
            if str(row.get("control_id") or "") == str(obs.control_id or "") and str(row.get("plan_id") or "") == str(plan_id or ""):
                row["reused"] = True
                return row

        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        revision = _next_revision(connection, owner)
        if existing is not None:
            connection.execute(
                "UPDATE application_semantic_bindings SET superseded_at=CURRENT_TIMESTAMP WHERE binding_id=?",
                (str(existing["binding_id"]),),
            )
        binding_id = f"binding-{uuid4()}"
        connection.execute(
            """INSERT INTO application_semantic_bindings(
                 binding_id,tenant_id,user_id,application_id,session_id,plan_id,checkpoint_id,
                 control_id,logical_field_fingerprint,question_fingerprint,section_fingerprint,
                 option_signature,concept_id,classification_source,confidence,binding_revision
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                binding_id, owner.tenant_id, owner.user_id, app_id, sess_id,
                str(plan_id or "").strip() or None, str(checkpoint_id or "").strip() or None,
                str(obs.control_id or "").strip() or None,
                parts["logical_field_fingerprint"], parts["question_fingerprint"],
                parts["section_fingerprint"], parts["option_signature"], concept.concept_id,
                source, score, revision,
            ),
        )
        if owns:
            connection.commit()
        row = connection.execute(
            "SELECT * FROM application_semantic_bindings WHERE binding_id=?", (binding_id,)
        ).fetchone()
        result = dict(row)
        result["reused"] = False
        return result
    except Exception:
        if owns and connection.in_transaction:
            connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def lookup(
    *,
    application_id: str,
    session_id: str,
    observation: QuestionObservation | Mapping[str, Any],
    allow_application_fallback: bool = False,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        parts = fingerprint_parts(observation)
        row = connection.execute(
            """SELECT * FROM application_semantic_bindings
               WHERE tenant_id=? AND user_id=? AND application_id=? AND session_id=?
                 AND logical_field_fingerprint=? AND superseded_at IS NULL
               ORDER BY binding_revision DESC LIMIT 1""",
            (
                owner.tenant_id, owner.user_id, str(application_id), str(session_id),
                parts["logical_field_fingerprint"],
            ),
        ).fetchone()
        if row is not None:
            result = dict(row)
            result["cross_session"] = False
            return result
        if allow_application_fallback:
            prior = connection.execute(
                """SELECT * FROM application_semantic_bindings
                   WHERE tenant_id=? AND user_id=? AND application_id=?
                     AND logical_field_fingerprint=?
                   ORDER BY binding_revision DESC LIMIT 1""",
                (
                    owner.tenant_id, owner.user_id, str(application_id),
                    parts["logical_field_fingerprint"],
                ),
            ).fetchone()
            if prior is not None:
                result = dict(prior)
                result["cross_session"] = str(result.get("session_id") or "") != str(session_id)
                return result
        return None
    finally:
        if owns:
            connection.close()


def supersede_session_bindings(
    *,
    application_id: str,
    session_id: str,
    reason: str,
    connection: sqlite3.Connection | None = None,
) -> int:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        # Reason is intentionally validated but not stored with candidate values;
        # execution-event ledgers retain plan-level supersession reasons.
        if not str(reason or "").strip():
            raise ValueError("Semantic binding supersession reason is required.")
        cursor = connection.execute(
            """UPDATE application_semantic_bindings SET superseded_at=CURRENT_TIMESTAMP
               WHERE tenant_id=? AND user_id=? AND application_id=? AND session_id=?
                 AND superseded_at IS NULL""",
            (owner.tenant_id, owner.user_id, str(application_id), str(session_id)),
        )
        if owns:
            connection.commit()
        return int(cursor.rowcount)
    finally:
        if owns:
            connection.close()


def public_binding(row: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "binding_id", "application_id", "session_id", "plan_id", "checkpoint_id",
        "control_id", "logical_field_fingerprint", "question_fingerprint",
        "section_fingerprint", "option_signature", "concept_id",
        "classification_source", "confidence", "binding_revision", "created_at",
        "superseded_at",
    }
    return {key: value for key, value in dict(row).items() if key in allowed}
