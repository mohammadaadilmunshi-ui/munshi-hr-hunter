"""Additive ingestion adapters for MUNSHI Universal Brain.

Candidate Truth, confirmed resume/profile extraction and Answer Vault remain source
authorities. This module projects them into the Candidate Knowledge Graph without
changing those systems or browser execution behavior.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Iterable, Mapping

from app import answer_brain_v2
from app import application_field_intelligence_v1 as field_intelligence
from app import candidate_knowledge_graph_v1 as graph
from app import candidate_ontology_v1 as ontology
from app import phase45_truth_binding as truth_binding
from app.database import get_connection
from app.tenant_foundation import current_owner

INGESTION_VERSION = "candidate-knowledge-ingestion-v1"

_EDUCATION_RE = re.compile(r"^education\.(?P<index>\d+)\.(?P<field>[a-z0-9_]+)$")
_EXPERIENCE_RE = re.compile(r"^experience\.(?P<index>\d+)\.(?P<field>[a-z0-9_]+)$")
_SKILLS_RE = re.compile(r"^skills\.(?P<index>\d+)\.(?P<field>category|skills)$")

_DIRECT_PROJECTION_EXCLUSIONS = {
    "contact.first_name",
    "contact.middle_name",
    "contact.last_name",
    "work.remote",
    "work.hybrid",
}

_CAPABILITIES = {
    item.concept_id: tuple(alias for alias in item.aliases if not alias.startswith("skill "))
    for item in ontology.all_concepts()
    if item.concept_id.startswith("skill.")
}


def _source_type(fact: Mapping[str, Any]) -> str:
    source = str(fact.get("source") or "")
    if source.startswith("candidate-profile-details-v31:"):
        return "USER_DASHBOARD"
    if source.startswith("candidate-profile-overrides-v1:"):
        return "PROFILE_OVERRIDE"
    if source.startswith("master-resume-extraction:"):
        return "RESUME"
    return "CANDIDATE_TRUTH"


def _trust_level(fact: Mapping[str, Any]) -> str:
    value = str(fact.get("trust_level") or "UNKNOWN").upper()
    return {
        "USER_CONFIRMED": "USER_CONFIRMED",
        "VERIFIED": "VERIFIED",
        "DOCUMENT_CONFIRMED": "SOURCE_CONFIRMED",
        "SOURCE_CONFIRMED": "SOURCE_CONFIRMED",
        "DERIVED": "DERIVED",
    }.get(value, "UNKNOWN")


def _confidence(trust: str) -> float:
    return {
        "USER_CONFIRMED": 1.0,
        "VERIFIED": 1.0,
        "SOURCE_CONFIRMED": 0.98,
        "DERIVED": 0.90,
    }.get(trust, 0.0)


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "string"


def _binding(concept_id: str, *, entity_ref: str | None = None, transform: str | None = None) -> dict[str, Any]:
    return {"concept_id": concept_id, "entity_ref": entity_ref, "transform": transform}


def _bindings_for_snapshot_fact(fact_key: str) -> list[dict[str, Any]]:
    key = str(fact_key or "").strip().casefold()
    if not key:
        return []
    result: list[dict[str, Any]] = []

    # Exact semantic concepts and legacy catalog fact paths where value identity
    # is preserved. Name components and work-mode booleans require transforms.
    for item in ontology.concepts_for_profile_fact(key):
        if item.concept_id in _DIRECT_PROJECTION_EXCLUSIONS:
            continue
        result.append(_binding(item.concept_id))

    education = _EDUCATION_RE.fullmatch(key)
    if education:
        index = int(education.group("index"))
        field = education.group("field")
        entity = f"education.{index}"
        semantic = {
            "institution": "education.institution",
            "degree": "education.degree",
            "field": "education.field",
            "end_date": "education.graduation",
            "gpa": "education.gpa",
        }.get(field)
        if semantic:
            result.append(_binding(semantic, entity_ref=entity))
        if index == 0 and field == "degree":
            result.append(_binding("education.highest_degree"))

    experience = _EXPERIENCE_RE.fullmatch(key)
    if experience:
        index = int(experience.group("index"))
        field = experience.group("field")
        entity = f"experience.{index}"
        semantic = {
            "employer": "employment.employer",
            "title": "employment.title",
            "location": "employment.location",
            "start_date": "employment.start_date",
            "end_date": "employment.end_date",
        }.get(field)
        if semantic:
            result.append(_binding(semantic, entity_ref=entity))
        if index == 0 and field == "employer":
            result.append(_binding("employment.current_employer"))
        if index == 0 and field == "title":
            result.append(_binding("employment.current_title"))

    defaults = {
        "application_defaults.willing_to_relocate": "work.relocation",
        "application_defaults.start_immediately": "availability.start_immediately",
        "application_defaults.in_person_ok": "work.onsite",
        "application_defaults.has_transport": "work.transport",
        "application_defaults.prior_employee": "background.prior_employee",
        "application_defaults.government_clearance": "background.clearance",
        "application_defaults.work_modes": "preference.work_mode",
        "application_defaults.authorized_to_work": "eligibility.work_authorized_us",
        "application_defaults.visa_or_permit": "eligibility.visa_status",
        "application_defaults.sponsorship_required": "eligibility.sponsorship_future",
    }
    if key in defaults:
        result.append(_binding(defaults[key]))

    # Dedupe while preserving entity scope.
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for item in result:
        token = (item["concept_id"], item["entity_ref"], item["transform"])
        if token not in seen:
            seen.add(token)
            deduped.append(item)
    return deduped


def _safe_evidence_text(value: Any, *, protected: bool) -> str | None:
    if protected:
        return None
    if isinstance(value, str):
        return value[:12000]
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)[:12000]
    if value is None:
        return None
    return str(value)[:12000]


def _store_snapshot_binding(
    *,
    snapshot: Mapping[str, Any],
    fact: Mapping[str, Any],
    binding: Mapping[str, Any],
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    concept = ontology.require_concept(str(binding["concept_id"]))
    protected = bool(fact.get("protected")) or concept.sensitivity != "NORMAL"
    source_type = "PROTECTED_POLICY" if protected else _source_type(fact)
    trust = _trust_level(fact)
    source_key = str(fact.get("key") or "")
    source_ref = f"candidate-truth:{source_key}"
    common = {
        "concept_id": concept.concept_id,
        "entity_ref": binding.get("entity_ref"),
        "source_type": source_type,
        "source_ref": source_ref,
        "source_digest": str(snapshot.get("profile_digest") or "") or None,
        "trust_level": trust,
        "confidence": _confidence(trust),
        "sensitivity": concept.sensitivity,
        "autofill_policy": (
            "ALLOW"
            if concept.sensitivity == "NORMAL" and concept.autofill_default and trust in {"USER_CONFIRMED", "VERIFIED", "SOURCE_CONFIRMED"}
            else ("POLICY" if concept.sensitivity != "NORMAL" else "REQUIRE_CONFIRMATION")
        ),
        "connection": connection,
    }
    if protected:
        return graph.store_fact(
            **common,
            value_ref=str(fact.get("value_reference") or ""),
            value_type=concept.value_type,
            evidence=(
                {
                    "source_kind": source_type,
                    "source_ref": str(fact.get("source") or source_ref),
                    "source_digest": str(snapshot.get("profile_digest") or ""),
                    "metadata": {"candidate_truth_fact_id": str(fact.get("fact_id") or "")},
                    "confidence": _confidence(trust),
                },
            ),
        )
    value = fact.get("value")
    return graph.store_fact(
        **common,
        value=value,
        value_type=_value_type(value),
        evidence=(
            {
                "source_kind": source_type,
                "source_ref": str(fact.get("source") or source_ref),
                "source_digest": str(snapshot.get("source_resume_sha256") or snapshot.get("profile_digest") or ""),
                "evidence_text": _safe_evidence_text(value, protected=False),
                "metadata": {
                    "candidate_truth_fact_id": str(fact.get("fact_id") or ""),
                    "candidate_truth_key": source_key,
                },
                "confidence": _confidence(trust),
            },
        ),
    )


def _ingest_name_projections(snapshot: Mapping[str, Any], connection: sqlite3.Connection) -> list[str]:
    fact = next(
        (dict(item) for item in snapshot.get("facts") or [] if str(item.get("key") or "").casefold() == "contact.full_name" and item.get("protected") is not True),
        None,
    )
    if not fact:
        return []
    parts = field_intelligence.split_person_name(str(fact.get("value") or ""))
    stored: list[str] = []
    for suffix, concept_id in (
        ("first_name", "contact.first_name"),
        ("middle_name", "contact.middle_name"),
        ("last_name", "contact.last_name"),
    ):
        value = str(parts.get(suffix) or "").strip()
        if not value:
            continue
        row = graph.store_fact(
            concept_id=concept_id,
            value=value,
            value_type="string",
            source_type="DERIVED",
            source_ref=f"derived:contact.full_name:{suffix}",
            source_digest=str(snapshot.get("profile_digest") or "") or None,
            trust_level="DERIVED",
            confidence=0.99,
            sensitivity="NORMAL",
            autofill_policy="ALLOW",
            evidence=(
                {
                    "source_kind": "CANDIDATE_TRUTH",
                    "source_ref": "candidate-truth:contact.full_name",
                    "source_digest": str(snapshot.get("profile_digest") or ""),
                    "metadata": {"derivation": "structural_name_projection", "source_fact_id": fact.get("fact_id")},
                    "confidence": 0.99,
                },
            ),
            connection=connection,
        )
        stored.append(str(row["fact_id"]))
    return stored


def ingest_candidate_truth_snapshot(
    snapshot: Mapping[str, Any] | None = None,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        graph.ensure_schema(connection)
        snapshot = dict(snapshot or truth_binding.current_candidate_profile_snapshot())
        owner = current_owner(connection)
        if snapshot.get("tenant_id") != owner.tenant_id or snapshot.get("user_id") != owner.user_id:
            raise ValueError("Candidate Truth snapshot owner does not match current candidate.")
        stored: list[str] = []
        reused = 0
        unmapped: list[str] = []
        for raw in snapshot.get("facts") or []:
            fact = dict(raw)
            key = str(fact.get("key") or "").casefold()
            bindings = _bindings_for_snapshot_fact(key)
            if not bindings:
                unmapped.append(key)
                continue
            for binding in bindings:
                row = _store_snapshot_binding(snapshot=snapshot, fact=fact, binding=binding, connection=connection)
                stored.append(str(row["fact_id"]))
                reused += int(bool(row.get("reused")))
        stored.extend(_ingest_name_projections(snapshot, connection))
        if owns:
            connection.commit()
        return {
            "version": INGESTION_VERSION,
            "source": "CANDIDATE_TRUTH",
            "profile_revision": int(snapshot.get("profile_revision") or 0),
            "profile_digest": str(snapshot.get("profile_digest") or ""),
            "stored_count": len(stored),
            "reused_count": reused,
            "unmapped_keys": sorted(set(unmapped)),
            "knowledge_revision": graph.current_revision(connection),
        }
    except Exception:
        if owns and connection.in_transaction:
            connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def _answer_question_key(row: Mapping[str, Any], connection: sqlite3.Connection) -> str:
    try:
        conditions = json.loads(str(row.get("conditions_json") or "{}"))
    except json.JSONDecodeError:
        conditions = {}
    key = str(conditions.get("__munshi_question_key") or "").strip().casefold()
    if key:
        return key
    binding = connection.execute(
        "SELECT question_key FROM application_answer_truth_bindings WHERE answer_id=?",
        (str(row.get("answer_id") or ""),),
    ).fetchone()
    return str(binding["question_key"] if binding else "").strip().casefold()


def ingest_answer_vault(*, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        answer_brain_v2.ensure_schema(connection)
        graph.ensure_schema(connection)
        owner = current_owner(connection)
        rows = connection.execute(
            """SELECT * FROM application_answer_vault
               WHERE tenant_id=? AND user_id=? AND user_confirmed=1
               ORDER BY updated_at,answer_id""",
            (owner.tenant_id, owner.user_id),
        ).fetchall()
        stored = 0
        skipped = 0
        for raw in rows:
            row = dict(raw)
            key = _answer_question_key(row, connection)
            concept = ontology.concept(key)
            if concept is None or concept.sensitivity != "NORMAL":
                skipped += 1
                continue
            answer = str(row.get("canonical_answer") or "").strip()
            if not answer:
                skipped += 1
                continue
            result = graph.store_fact(
                concept_id=concept.concept_id,
                value=answer,
                value_type=concept.value_type,
                source_type="ANSWER_MEMORY",
                source_ref=f"answer-vault:{row['answer_id']}",
                trust_level="USER_CONFIRMED" if bool(row.get("user_confirmed")) else "LEARNED",
                confidence=float(row.get("confidence") or 0.0),
                sensitivity="NORMAL",
                autofill_policy="ALLOW" if bool(row.get("autofill_allowed")) else "REQUIRE_CONFIRMATION",
                evidence=(
                    {
                        "source_kind": "ANSWER_MEMORY",
                        "source_ref": f"answer-vault:{row['answer_id']}",
                        "metadata": {"question_key": key, "source": row.get("source")},
                        "confidence": float(row.get("confidence") or 0.0),
                    },
                ),
                connection=connection,
            )
            stored += int(not result.get("reused"))
        if owns:
            connection.commit()
        return {
            "version": INGESTION_VERSION,
            "source": "ANSWER_MEMORY",
            "stored_count": stored,
            "skipped_count": skipped,
            "knowledge_revision": graph.current_revision(connection),
        }
    except Exception:
        if owns and connection.in_transaction:
            connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def _normalize_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _alias_present(text: str, alias: str) -> bool:
    normalized_text = _normalize_evidence(text)
    normalized_alias = _normalize_evidence(alias)
    if not normalized_text or not normalized_alias:
        return False
    # Preserve tokens such as i-9, power bi and C++-style names while requiring
    # lexical boundaries around simple alphanumeric capability aliases.
    pattern = r"(?<![a-z0-9])" + re.escape(normalized_alias) + r"(?![a-z0-9])"
    return bool(re.search(pattern, normalized_text, re.I))


def ingest_deep_resume_evidence(
    snapshot: Mapping[str, Any] | None = None,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Create only explicit, source-traceable capability evidence and relations."""
    owns = connection is None
    connection = connection or get_connection()
    try:
        graph.ensure_schema(connection)
        snapshot = dict(snapshot or truth_binding.current_candidate_profile_snapshot())
        relations = 0
        evidences = 0
        seen_claims: set[tuple[str, str, str]] = set()
        for raw in snapshot.get("facts") or []:
            fact = dict(raw)
            if fact.get("protected") is True:
                continue
            key = str(fact.get("key") or "").casefold()
            exp = _EXPERIENCE_RE.fullmatch(key)
            skills = _SKILLS_RE.fullmatch(key)
            if not exp and not (skills and skills.group("field") == "skills"):
                continue
            value = fact.get("value")
            texts = value if isinstance(value, list) else [value]
            for offset, item in enumerate(texts):
                evidence_text = str(item or "").strip()
                if not evidence_text:
                    continue
                for concept_id, aliases in _CAPABILITIES.items():
                    if not any(_alias_present(evidence_text, alias) for alias in aliases):
                        continue
                    entity = f"experience.{int(exp.group('index'))}" if exp else f"skills.{int(skills.group('index'))}"
                    claim_token = (entity, concept_id, f"{key}:{offset}")
                    if claim_token in seen_claims:
                        continue
                    seen_claims.add(claim_token)
                    claim_id = "claim-" + hashlib.sha256("|".join(claim_token).encode("utf-8")).hexdigest()[:32]
                    evidence_id = graph.add_evidence(
                        claim_id=claim_id,
                        source_kind="RESUME",
                        source_ref=f"candidate-truth:{key}:{offset}",
                        source_digest=str(snapshot.get("source_resume_sha256") or snapshot.get("profile_digest") or ""),
                        evidence_text=evidence_text,
                        metadata={
                            "entity_ref": entity,
                            "candidate_truth_fact_id": fact.get("fact_id"),
                            "capability_concept": concept_id,
                        },
                        confidence=0.99,
                        connection=connection,
                    )
                    evidences += 1
                    rel = graph.add_relation(
                        subject_concept=entity,
                        predicate="HAS_SKILL",
                        object_concept=concept_id,
                        source_ref=f"candidate-truth:{key}:{offset}:{concept_id}",
                        evidence_ids=(evidence_id,),
                        confidence=0.99,
                        connection=connection,
                    )
                    relations += int(not rel.get("reused"))
        if owns:
            connection.commit()
        return {
            "version": INGESTION_VERSION,
            "source": "DEEP_RESUME_EVIDENCE",
            "evidence_observations": evidences,
            "new_relations": relations,
            "knowledge_revision": graph.current_revision(connection),
        }
    except Exception:
        if owns and connection.in_transaction:
            connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def ingest_current_sources(*, include_answer_memory: bool = True) -> dict[str, Any]:
    """Explicit sync operation; never invoked implicitly from an executing plan."""
    connection = get_connection()
    try:
        graph.ensure_schema(connection)
        snapshot = truth_binding.current_candidate_profile_snapshot()
        candidate_truth = ingest_candidate_truth_snapshot(snapshot, connection=connection)
        deep_evidence = ingest_deep_resume_evidence(snapshot, connection=connection)
        answer_memory = ingest_answer_vault(connection=connection) if include_answer_memory else None
        connection.commit()
        return {
            "version": INGESTION_VERSION,
            "candidate_truth": candidate_truth,
            "deep_evidence": deep_evidence,
            "answer_memory": answer_memory,
            "knowledge_revision": graph.current_revision(connection),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def readonly_candidate_truth_resolution(
    concept_id: str,
    *,
    entity_ref: str | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve Candidate Truth without mutating Candidate Knowledge revision."""
    concept = ontology.require_concept(concept_id)
    snapshot = dict(snapshot or truth_binding.current_candidate_profile_snapshot())

    if concept.concept_id in {"contact.first_name", "contact.middle_name", "contact.last_name"}:
        full = next(
            (dict(item) for item in snapshot.get("facts") or [] if str(item.get("key") or "").casefold() == "contact.full_name" and item.get("protected") is not True),
            None,
        )
        if not full:
            return {"status": "MISSING_FACT", "concept_id": concept.concept_id}
        projection = concept.concept_id.rsplit(".", 1)[-1]
        value = str(field_intelligence.split_person_name(str(full.get("value") or "")).get(projection) or "").strip()
        if not value:
            return {"status": "MISSING_FACT", "concept_id": concept.concept_id}
        return {
            "status": "ANSWERED",
            "concept_id": concept.concept_id,
            "value": value,
            "value_type": "string",
            "source_fact_ids": [str(full.get("fact_id") or "")],
            "source": "CANDIDATE_TRUTH_DERIVED_PROJECTION",
            "confidence": 0.99,
            "sensitivity": "NORMAL",
            "autofill_policy": "ALLOW",
            "knowledge_revision": int(snapshot.get("profile_revision") or 0),
        }

    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw in snapshot.get("facts") or []:
        fact = dict(raw)
        for binding in _bindings_for_snapshot_fact(str(fact.get("key") or "")):
            if str(binding["concept_id"]) != concept.concept_id:
                continue
            binding_entity = str(binding.get("entity_ref") or "") or None
            if entity_ref is not None and binding_entity != str(entity_ref).strip().casefold():
                continue
            matches.append((fact, binding))

    if concept.repeated_entity and entity_ref is None:
        entities = sorted({str(binding.get("entity_ref") or "") for _fact, binding in matches if binding.get("entity_ref")})
        if len(entities) > 1:
            return {"status": "AMBIGUOUS_ENTITY", "concept_id": concept.concept_id, "entity_refs": entities}
    if not matches:
        return {"status": "MISSING_FACT", "concept_id": concept.concept_id}

    # Candidate Truth projection has already resolved source precedence. If two
    # distinct facts still bind to one non-repeatable concept, fail closed.
    values: set[str] = set()
    for fact, _binding in matches:
        if fact.get("protected") is True:
            values.add("ref:" + str(fact.get("value_reference") or ""))
        else:
            values.add(json.dumps(fact.get("value"), sort_keys=True, ensure_ascii=False))
    if len(values) > 1 and not concept.repeated_entity:
        return {
            "status": "CONFLICT",
            "concept_id": concept.concept_id,
            "source_fact_ids": [str(fact.get("fact_id") or "") for fact, _ in matches],
        }

    fact, _binding = matches[0]
    protected = bool(fact.get("protected")) or concept.sensitivity != "NORMAL"
    result = {
        "status": "ANSWERED",
        "concept_id": concept.concept_id,
        "source_fact_ids": [str(fact.get("fact_id") or "")],
        "source": "CANDIDATE_TRUTH",
        "confidence": _confidence(_trust_level(fact)),
        "sensitivity": concept.sensitivity,
        "autofill_policy": "POLICY" if protected else ("ALLOW" if concept.autofill_default else "REQUIRE_CONFIRMATION"),
        "knowledge_revision": int(snapshot.get("profile_revision") or 0),
    }
    if protected:
        result["value_ref"] = str(fact.get("value_reference") or "")
        result["value_type"] = concept.value_type
    else:
        result["value"] = fact.get("value")
        result["value_type"] = _value_type(fact.get("value"))
    return result
