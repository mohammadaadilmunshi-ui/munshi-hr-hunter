"""Canonical Candidate Knowledge Graph for MUNSHI Universal Brain.

The graph is an additive semantic/evidence layer over Candidate Truth, resume
profile extraction, explicit preferences and verified answer memory.  It does
not replace those source systems and does not execute browser actions.

Protected/secret facts are reference-only here: plaintext values are rejected.
All writes are tenant scoped, revisioned, source-attributed and conflict aware.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from uuid import uuid4

from app import candidate_ontology_v1 as ontology
from app.database import get_connection
from app.tenant_foundation import OwnerContext, current_owner, ensure_schema as ensure_tenant_schema

GRAPH_VERSION = "candidate-knowledge-graph-v1"

SOURCE_PRECEDENCE = {
    "USER_DASHBOARD": 700,
    "CANDIDATE_TRUTH": 700,
    "PROTECTED_POLICY": 690,
    "PROFILE_OVERRIDE": 650,
    "RESUME": 600,
    "PROFILE": 600,
    "DERIVED": 500,
    "ANSWER_MEMORY": 400,
    "DIGITAL_TWIN": 350,
    "MODEL_INTERPRETATION": 100,
}
TRUST_RANK = {
    "USER_CONFIRMED": 500,
    "VERIFIED": 500,
    "SOURCE_CONFIRMED": 450,
    "DOCUMENT_CONFIRMED": 450,
    "DERIVED": 300,
    "LEARNED": 200,
    "MODEL_INTERPRETED": 100,
    "UNKNOWN": 0,
}
AUTOFILL_POLICIES = frozenset({"ALLOW", "POLICY", "REQUIRE_CONFIRMATION", "BLOCK"})
REFERENCE_ONLY_SENSITIVITIES = frozenset({"PROTECTED", "SELF_ID", "SECRET", "CREDENTIAL", "POST_OFFER"})

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS candidate_knowledge_revisions(
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(tenant_id,user_id),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE TABLE IF NOT EXISTS candidate_knowledge_facts(
        fact_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        concept_id TEXT NOT NULL,
        entity_ref TEXT,
        value_json TEXT,
        value_ref TEXT,
        value_type TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        source_digest TEXT,
        trust_level TEXT NOT NULL,
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        sensitivity TEXT NOT NULL,
        autofill_policy TEXT NOT NULL,
        valid_from TEXT,
        valid_to TEXT,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        supersedes_fact_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        superseded_at TEXT,
        CHECK((value_json IS NULL) != (value_ref IS NULL)),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(supersedes_fact_id)
          REFERENCES candidate_knowledge_facts(fact_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_candidate_knowledge_fact_current
       ON candidate_knowledge_facts(tenant_id,user_id,concept_id,entity_ref,superseded_at,revision DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_candidate_knowledge_fact_source
       ON candidate_knowledge_facts(tenant_id,user_id,source_type,source_ref,concept_id);""",
    """CREATE TABLE IF NOT EXISTS candidate_knowledge_evidence(
        evidence_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        fact_id TEXT,
        claim_id TEXT,
        source_kind TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        source_digest TEXT,
        evidence_text TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(fact_id IS NOT NULL OR claim_id IS NOT NULL),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(fact_id)
          REFERENCES candidate_knowledge_facts(fact_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_candidate_knowledge_evidence_fact
       ON candidate_knowledge_evidence(tenant_id,user_id,fact_id,created_at);""",
    """CREATE TABLE IF NOT EXISTS candidate_knowledge_relations(
        relation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        subject_concept TEXT NOT NULL,
        predicate TEXT NOT NULL,
        object_concept TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        source_ref TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        superseded_at TEXT,
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_candidate_knowledge_relation_current
       ON candidate_knowledge_relations(tenant_id,user_id,subject_concept,predicate,superseded_at);""",
    """CREATE TABLE IF NOT EXISTS candidate_knowledge_conflicts(
        conflict_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        concept_id TEXT NOT NULL,
        incumbent_fact_id TEXT NOT NULL,
        candidate_fact_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','RESOLVED','IGNORED')),
        created_revision INTEGER NOT NULL CHECK(created_revision >= 1),
        resolution_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        resolved_at TEXT,
        UNIQUE(tenant_id,user_id,incumbent_fact_id,candidate_fact_id),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(incumbent_fact_id)
          REFERENCES candidate_knowledge_facts(fact_id) ON DELETE RESTRICT,
        FOREIGN KEY(candidate_fact_id)
          REFERENCES candidate_knowledge_facts(fact_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_candidate_knowledge_conflict_open
       ON candidate_knowledge_conflicts(tenant_id,user_id,concept_id,status);""",
    """CREATE TABLE IF NOT EXISTS candidate_derived_fact_cache(
        cache_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        concept_id TEXT NOT NULL,
        knowledge_revision INTEGER NOT NULL,
        derivation_version TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,concept_id,knowledge_revision,derivation_version),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
)


def universal_brain_enabled() -> bool:
    return str(os.getenv("MUNSHI_UNIVERSAL_BRAIN_ENABLED") or "").strip().casefold() in {"1", "true", "yes", "on"}


def universal_brain_shadow_enabled() -> bool:
    return str(os.getenv("MUNSHI_UNIVERSAL_BRAIN_SHADOW") or "").strip().casefold() in {"1", "true", "yes", "on"}


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


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _confidence(value: float) -> float:
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError("Confidence must be between 0 and 1.")
    return result


def _bounded_text(value: Any, label: str, maximum: int = 4000, *, required: bool = True) -> str:
    result = " ".join(str(value or "").split())
    if (required and not result) or len(result) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return result


def _next_revision(connection: sqlite3.Connection, owner: OwnerContext) -> int:
    connection.execute(
        """INSERT INTO candidate_knowledge_revisions(tenant_id,user_id,revision)
           VALUES (?,?,0) ON CONFLICT(tenant_id,user_id) DO NOTHING""",
        (owner.tenant_id, owner.user_id),
    )
    connection.execute(
        """UPDATE candidate_knowledge_revisions
           SET revision=revision+1, updated_at=CURRENT_TIMESTAMP
           WHERE tenant_id=? AND user_id=?""",
        (owner.tenant_id, owner.user_id),
    )
    row = connection.execute(
        "SELECT revision FROM candidate_knowledge_revisions WHERE tenant_id=? AND user_id=?",
        (owner.tenant_id, owner.user_id),
    ).fetchone()
    return int(row["revision"])


def current_revision(connection: sqlite3.Connection | None = None) -> int:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        row = connection.execute(
            "SELECT revision FROM candidate_knowledge_revisions WHERE tenant_id=? AND user_id=?",
            (owner.tenant_id, owner.user_id),
        ).fetchone()
        return int(row["revision"]) if row else 0
    finally:
        if owns:
            connection.close()


def _decode_fact(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    if result.get("value_json") is not None:
        result["value"] = json.loads(str(result.pop("value_json")))
    else:
        result.pop("value_json", None)
    return result


def _same_value(left: Mapping[str, Any], *, value_json: str | None, value_ref: str | None) -> bool:
    return (
        (left.get("value_json") if "value_json" in left else None) == value_json
        and (left.get("value_ref") if "value_ref" in left else None) == value_ref
    )


def _authority(row: Mapping[str, Any]) -> tuple[int, int, float, int]:
    return (
        SOURCE_PRECEDENCE.get(str(row.get("source_type") or "").upper(), 0),
        TRUST_RANK.get(str(row.get("trust_level") or "UNKNOWN").upper(), 0),
        float(row.get("confidence") or 0.0),
        int(row.get("revision") or 0),
    )


def store_fact(
    *,
    concept_id: str,
    value: Any = None,
    value_ref: str | None = None,
    value_type: str | None = None,
    entity_ref: str | None = None,
    source_type: str,
    source_ref: str,
    trust_level: str,
    confidence: float,
    sensitivity: str | None = None,
    autofill_policy: str | None = None,
    source_digest: str | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    evidence: Sequence[Mapping[str, Any]] | None = None,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Append or replace one source-scoped fact without silently overwriting truth."""
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        concept = ontology.require_concept(concept_id)
        source = _bounded_text(source_type, "Source type", 80).upper()
        if source not in SOURCE_PRECEDENCE:
            raise ValueError("Unsupported Candidate Knowledge source type.")
        ref = _bounded_text(source_ref, "Source reference", 1000)
        entity = str(entity_ref or "").strip().casefold() or None
        if entity and len(entity) > 240:
            raise ValueError("Entity reference is too long.")
        if entity and not concept.repeated_entity:
            # Entity scope is harmless for source provenance, but semantic binding
            # may only depend on it for concepts explicitly marked repeatable.
            entity = None
        trust = _bounded_text(trust_level, "Trust level", 80).upper()
        if trust not in TRUST_RANK:
            raise ValueError("Unsupported Candidate Knowledge trust level.")
        effective_sensitivity = ontology.sensitivity_at_least(concept.sensitivity, sensitivity or concept.sensitivity)
        policy = str(autofill_policy or ("ALLOW" if concept.autofill_default and effective_sensitivity == "NORMAL" else "REQUIRE_CONFIRMATION")).strip().upper()
        if policy not in AUTOFILL_POLICIES:
            raise ValueError("Unsupported Candidate Knowledge autofill policy.")
        if effective_sensitivity != "NORMAL" and policy == "ALLOW":
            raise ValueError("Protected Candidate Knowledge cannot use unconditional autofill.")

        protected = effective_sensitivity in REFERENCE_ONLY_SENSITIVITIES
        if protected:
            if value is not None:
                raise ValueError("Protected Candidate Knowledge must be reference-only.")
            normalized_ref = _bounded_text(value_ref, "Protected value reference", 2000)
            normalized_json = None
        else:
            if value_ref is not None:
                raise ValueError("Normal Candidate Knowledge cannot use a protected value reference.")
            if value is None:
                raise ValueError("Normal Candidate Knowledge requires a value.")
            normalized_json = _json(value)
            normalized_ref = None

        active_same_source = connection.execute(
            """SELECT * FROM candidate_knowledge_facts
               WHERE tenant_id=? AND user_id=? AND concept_id=? AND source_ref=?
                 AND COALESCE(entity_ref,'')=COALESCE(?, '') AND superseded_at IS NULL
               ORDER BY revision DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id, concept.concept_id, ref, entity),
        ).fetchone()
        if active_same_source and _same_value(dict(active_same_source), value_json=normalized_json, value_ref=normalized_ref):
            result = _decode_fact(dict(active_same_source))
            result["reused"] = True
            return result

        if connection.in_transaction and owns:
            connection.commit()
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")

        revision = _next_revision(connection, owner)
        supersedes = str(active_same_source["fact_id"]) if active_same_source else None
        if active_same_source:
            connection.execute(
                "UPDATE candidate_knowledge_facts SET superseded_at=CURRENT_TIMESTAMP WHERE fact_id=?",
                (supersedes,),
            )
        fact_id = f"fact-{uuid4()}"
        connection.execute(
            """INSERT INTO candidate_knowledge_facts(
                 fact_id,tenant_id,user_id,concept_id,entity_ref,value_json,value_ref,value_type,
                 source_type,source_ref,source_digest,trust_level,confidence,sensitivity,
                 autofill_policy,valid_from,valid_to,revision,supersedes_fact_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                fact_id, owner.tenant_id, owner.user_id, concept.concept_id, entity,
                normalized_json, normalized_ref, str(value_type or concept.value_type),
                source, ref, str(source_digest or "").strip() or None, trust,
                _confidence(confidence), effective_sensitivity, policy,
                valid_from, valid_to, revision, supersedes,
            ),
        )

        # Preserve competing active sources and record a durable conflict instead
        # of allowing the latest writer to become truth by accident.
        competitors = connection.execute(
            """SELECT * FROM candidate_knowledge_facts
               WHERE tenant_id=? AND user_id=? AND concept_id=? AND superseded_at IS NULL
                 AND fact_id<>? AND COALESCE(entity_ref,'')=COALESCE(?, '')""",
            (owner.tenant_id, owner.user_id, concept.concept_id, fact_id, entity),
        ).fetchall()
        candidate_row = connection.execute(
            "SELECT * FROM candidate_knowledge_facts WHERE fact_id=?", (fact_id,)
        ).fetchone()
        for competitor in competitors:
            if _same_value(dict(competitor), value_json=normalized_json, value_ref=normalized_ref):
                continue
            incumbent_id, candidate_id = str(competitor["fact_id"]), fact_id
            reason = "SOURCE_VALUE_CONFLICT"
            connection.execute(
                """INSERT OR IGNORE INTO candidate_knowledge_conflicts(
                     conflict_id,tenant_id,user_id,concept_id,incumbent_fact_id,
                     candidate_fact_id,reason,created_revision
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    f"conflict-{uuid4()}", owner.tenant_id, owner.user_id,
                    concept.concept_id, incumbent_id, candidate_id, reason, revision,
                ),
            )

        for raw in evidence or ():
            add_evidence(
                fact_id=fact_id,
                source_kind=str(raw.get("source_kind") or source),
                source_ref=str(raw.get("source_ref") or ref),
                source_digest=str(raw.get("source_digest") or source_digest or "") or None,
                evidence_text=str(raw.get("evidence_text") or "") or None,
                metadata=raw.get("metadata") or {},
                confidence=float(raw.get("confidence", confidence)),
                connection=connection,
            )
        if owns:
            connection.commit()
        result = _decode_fact(dict(candidate_row))
        result["reused"] = False
        return result
    except Exception:
        if owns and connection.in_transaction:
            connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def add_evidence(
    *,
    fact_id: str | None = None,
    claim_id: str | None = None,
    source_kind: str,
    source_ref: str,
    source_digest: str | None = None,
    evidence_text: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    confidence: float = 1.0,
    connection: sqlite3.Connection | None = None,
) -> str:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        if not fact_id and not claim_id:
            raise ValueError("Evidence requires a fact or claim identity.")
        if fact_id:
            row = connection.execute(
                "SELECT 1 FROM candidate_knowledge_facts WHERE fact_id=? AND tenant_id=? AND user_id=?",
                (str(fact_id), owner.tenant_id, owner.user_id),
            ).fetchone()
            if row is None:
                raise LookupError("Candidate Knowledge fact does not belong to current owner.")
        existing = connection.execute(
            """SELECT evidence_id FROM candidate_knowledge_evidence
               WHERE tenant_id=? AND user_id=?
                 AND COALESCE(fact_id,'')=COALESCE(?, '')
                 AND COALESCE(claim_id,'')=COALESCE(?, '')
                 AND source_kind=? AND source_ref=?
                 AND COALESCE(source_digest,'')=COALESCE(?, '')
               ORDER BY created_at LIMIT 1""",
            (
                owner.tenant_id, owner.user_id, fact_id, claim_id,
                _bounded_text(source_kind, "Evidence source kind", 80).upper(),
                _bounded_text(source_ref, "Evidence source reference", 1500),
                str(source_digest or "").strip() or None,
            ),
        ).fetchone()
        if existing is not None:
            return str(existing["evidence_id"])
        evidence_id = f"evidence-{uuid4()}"
        connection.execute(
            """INSERT INTO candidate_knowledge_evidence(
                 evidence_id,tenant_id,user_id,fact_id,claim_id,source_kind,source_ref,
                 source_digest,evidence_text,metadata_json,confidence
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                evidence_id, owner.tenant_id, owner.user_id, fact_id, claim_id,
                _bounded_text(source_kind, "Evidence source kind", 80).upper(),
                _bounded_text(source_ref, "Evidence source reference", 1500),
                str(source_digest or "").strip() or None,
                str(evidence_text or "")[:12000] or None,
                _json(dict(metadata or {})), _confidence(confidence),
            ),
        )
        if owns:
            connection.commit()
        return evidence_id
    finally:
        if owns:
            connection.close()


def add_relation(
    *,
    subject_concept: str,
    predicate: str,
    object_concept: str,
    source_ref: str,
    evidence_ids: Sequence[str] = (),
    confidence: float = 1.0,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        subject = _bounded_text(subject_concept, "Relation subject", 240).casefold()
        relation_predicate = _bounded_text(predicate, "Relation predicate", 120).upper()
        obj = _bounded_text(object_concept, "Relation object", 240).casefold()
        relation_source = _bounded_text(source_ref, "Relation source", 1500)
        existing = connection.execute(
            """SELECT relation_id,revision FROM candidate_knowledge_relations
               WHERE tenant_id=? AND user_id=? AND subject_concept=? AND predicate=?
                 AND object_concept=? AND source_ref=? AND superseded_at IS NULL
               ORDER BY revision DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id, subject, relation_predicate, obj, relation_source),
        ).fetchone()
        if existing is not None:
            return {"relation_id": str(existing["relation_id"]), "revision": int(existing["revision"]), "reused": True}
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        revision = _next_revision(connection, owner)
        relation_id = f"rel-{uuid4()}"
        connection.execute(
            """INSERT INTO candidate_knowledge_relations(
                 relation_id,tenant_id,user_id,subject_concept,predicate,object_concept,
                 evidence_ids_json,confidence,source_ref,revision
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                relation_id, owner.tenant_id, owner.user_id,
                subject, relation_predicate, obj,
                _json([str(item) for item in evidence_ids]), _confidence(confidence),
                relation_source, revision,
            ),
        )
        if owns:
            connection.commit()
        return {"relation_id": relation_id, "revision": revision, "reused": False}
    except Exception:
        if owns and connection.in_transaction:
            connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def current_facts(concept_id: str | None = None, *, entity_ref: str | None = None, connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        params: list[Any] = [owner.tenant_id, owner.user_id]
        where = "tenant_id=? AND user_id=? AND superseded_at IS NULL"
        if concept_id:
            where += " AND concept_id=?"
            params.append(ontology.require_concept(concept_id).concept_id)
        if entity_ref is not None:
            where += " AND COALESCE(entity_ref,'')=COALESCE(?, '')"
            params.append(str(entity_ref).strip().casefold() or None)
        rows = connection.execute(
            f"SELECT * FROM candidate_knowledge_facts WHERE {where}", params
        ).fetchall()
        result = [_decode_fact(dict(row)) for row in rows]
        result.sort(key=lambda row: (_authority(row), str(row["fact_id"])), reverse=True)
        return result
    finally:
        if owns:
            connection.close()


def open_conflicts(concept_id: str | None = None, *, connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        params: list[Any] = [owner.tenant_id, owner.user_id]
        where = "tenant_id=? AND user_id=? AND status='OPEN'"
        if concept_id:
            where += " AND concept_id=?"
            params.append(str(concept_id).strip().casefold())
        rows = connection.execute(
            f"SELECT * FROM candidate_knowledge_conflicts WHERE {where} ORDER BY created_revision, conflict_id",
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        if owns:
            connection.close()


def resolve_fact(concept_id: str, *, entity_ref: str | None = None, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Resolve the highest-authority current fact or fail closed on ambiguity/conflict."""
    concept = ontology.require_concept(concept_id)
    facts = current_facts(concept_id, entity_ref=entity_ref, connection=connection)
    if concept.repeated_entity and entity_ref is None:
        entities = sorted({str(row.get("entity_ref") or "") for row in facts if row.get("entity_ref")})
        if len(entities) > 1:
            return {"status": "AMBIGUOUS_ENTITY", "concept_id": concept.concept_id, "entity_refs": entities}

    if not facts:
        return {"status": "MISSING_FACT", "concept_id": str(concept_id).casefold()}
    top = facts[0]
    top_authority = _authority(top)
    tied = [row for row in facts if _authority(row)[:3] == top_authority[:3]]
    fingerprints = {
        _json(row.get("value")) if "value" in row else f"ref:{row.get('value_ref')}"
        for row in tied
    }
    if len(fingerprints) > 1:
        return {
            "status": "CONFLICT",
            "concept_id": top["concept_id"],
            "fact_ids": [row["fact_id"] for row in tied],
            "knowledge_revision": max(int(row["revision"]) for row in tied),
        }
    return {
        "status": "ANSWERED",
        "concept_id": top["concept_id"],
        "fact": top,
        "knowledge_revision": int(top["revision"]),
    }


def value_ref_for_fact(fact: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        _json({
            "fact_id": fact.get("fact_id"),
            "concept_id": fact.get("concept_id"),
            "revision": fact.get("revision"),
            "source_ref": fact.get("source_ref"),
        }).encode("utf-8")
    ).hexdigest()
    return f"hunter-value://candidate-knowledge/{digest[:40]}"


def public_status() -> dict[str, Any]:
    return {
        "version": GRAPH_VERSION,
        "enabled": universal_brain_enabled(),
        "shadow_enabled": universal_brain_shadow_enabled(),
        "ontology_version": ontology.ONTOLOGY_VERSION,
        "source_precedence": dict(SOURCE_PRECEDENCE),
        "protected_plaintext_storage": False,
    }


def current_relations(
    *,
    subject_concept: str | None = None,
    predicate: str | None = None,
    object_concept: str | None = None,
    connection: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        where = ["tenant_id=?", "user_id=?", "superseded_at IS NULL"]
        params: list[Any] = [owner.tenant_id, owner.user_id]
        if subject_concept:
            where.append("subject_concept=?")
            params.append(str(subject_concept).strip().casefold())
        if predicate:
            where.append("predicate=?")
            params.append(str(predicate).strip().upper())
        if object_concept:
            where.append("object_concept=?")
            params.append(str(object_concept).strip().casefold())
        rows = connection.execute(
            "SELECT * FROM candidate_knowledge_relations WHERE " + " AND ".join(where)
            + " ORDER BY revision,relation_id",
            params,
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["evidence_ids"] = json.loads(str(item.pop("evidence_ids_json") or "[]"))
            result.append(item)
        return result
    finally:
        if owns:
            connection.close()


def evidence_records(
    evidence_ids: Sequence[str],
    *,
    connection: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    ids = [str(item) for item in evidence_ids if str(item).strip()]
    if not ids:
        return []
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""SELECT * FROM candidate_knowledge_evidence
                WHERE tenant_id=? AND user_id=? AND evidence_id IN ({placeholders})
                ORDER BY created_at,evidence_id""",
            [owner.tenant_id, owner.user_id, *ids],
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(str(item.pop("metadata_json") or "{}"))
            result.append(item)
        return result
    finally:
        if owns:
            connection.close()
