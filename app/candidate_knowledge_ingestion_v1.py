"""Read/write adapters from existing candidate authorities into Universal Brain.

Candidate Truth, encrypted profile state, Answer Vault and resume extraction remain
the source systems.  This module can preview them read-only for shadow resolution
or explicitly synchronize them into the versioned Candidate Knowledge Graph.
No synchronization is triggered merely by importing or resolving a question.
"""
from __future__ import annotations

from typing import Any, Mapping

from app import answer_brain as answer_v1
from app import candidate_derivation_engine_v1 as derivation
from app import candidate_knowledge_graph_v1 as graph
from app import candidate_ontology_v1 as ontology
from app import native_resume_service_v3 as resume_v3
from app import phase45_truth_binding as truth_binding

INGESTION_VERSION = "candidate-knowledge-ingestion-v1"


def _source_type(source: Any, *, protected: bool = False) -> str:
    text = str(source or "").casefold()
    if protected:
        return "PROTECTED_POLICY"
    if "override" in text or "profile-details" in text or "candidate-profile" in text:
        return "USER_DASHBOARD"
    if "resume" in text or "extraction" in text:
        return "RESUME"
    return "CANDIDATE_TRUTH"


def _sensitivity_for_fact(fact: Mapping[str, Any]) -> str:
    key = str(fact.get("key") or "").casefold()
    if fact.get("protected") is not True:
        return "NORMAL"
    if any(token in key for token in ("gender", "race", "ethnicity", "veteran", "disability")):
        return "SELF_ID"
    return "PROTECTED"


def _fact_confidence(trust: Any) -> float:
    return {
        "VERIFIED": 1.0,
        "USER_CONFIRMED": 1.0,
        "DOCUMENT_CONFIRMED": 0.98,
        "SOURCE_CONFIRMED": 0.98,
        "DERIVED": 0.8,
        "LEARNED": 0.6,
    }.get(str(trust or "").upper(), 0.0)


def current_truth_snapshot() -> dict[str, Any]:
    return truth_binding.current_candidate_profile_snapshot()


def current_profile_extract(snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    current = dict(snapshot or current_truth_snapshot())
    extraction_id = str(current.get("source_extraction_id") or "").strip()
    if not extraction_id:
        raise RuntimeError("Candidate Truth snapshot has no source extraction identity.")
    extracted = resume_v3.get_profile_extract(extraction_id)
    if str(extracted.get("status") or "") != "CONFIRMED":
        raise RuntimeError("Candidate profile extraction is not confirmed.")
    return extracted


def _canonical_targets(fact_key: str) -> tuple[str, ...]:
    matches = ontology.concepts_for_profile_fact(fact_key)
    ids = [item.concept_id for item in matches]
    # Preserve every source fact atomically even when no employer-question concept
    # has been defined for it yet. Dynamic entity concepts retain repeated rows.
    if ontology.concept(fact_key) is not None:
        ids.append(str(fact_key).casefold())
    return tuple(dict.fromkeys(ids))


def preview_candidate_truth(snapshot: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    current = dict(snapshot or current_truth_snapshot())
    rows: list[dict[str, Any]] = []
    for raw in current.get("facts") or []:
        fact = dict(raw)
        key = str(fact.get("key") or "").strip().casefold()
        if not key:
            continue
        protected = fact.get("protected") is True
        sensitivity = _sensitivity_for_fact(fact)
        targets = _canonical_targets(key)
        if not targets:
            continue
        for concept_id in targets:
            row = {
                "concept_id": concept_id,
                "source_fact_key": key,
                "source_type": _source_type(fact.get("source"), protected=protected),
                "source_ref": str(fact.get("source") or f"candidate-truth:{key}"),
                "source_digest": str(current.get("profile_digest") or "") or None,
                "trust_level": str(fact.get("trust_level") or "SOURCE_CONFIRMED").upper(),
                "confidence": _fact_confidence(fact.get("trust_level")),
                "sensitivity": sensitivity,
                "autofill_policy": "POLICY" if protected else "ALLOW",
                "candidate_profile_revision": int(current.get("profile_revision") or 0),
                "candidate_profile_digest": str(current.get("profile_digest") or ""),
                "source_fact_id": str(fact.get("fact_id") or ""),
            }
            if protected:
                reference = str(fact.get("value_reference") or "").strip()
                if not reference:
                    continue
                row["value_ref"] = reference
            else:
                if "value" not in fact:
                    continue
                row["value"] = fact.get("value")
            rows.append(row)
    return rows


def preview_answer_memory() -> list[dict[str, Any]]:
    """Return only normal, verified Answer Vault rows with stable question keys."""
    try:
        answers = answer_v1.normal_answer_projection()
    except (LookupError, RuntimeError, ValueError):
        return []
    result: list[dict[str, Any]] = []
    for raw in answers:
        row = dict(raw)
        if not row.get("user_confirmed"):
            continue
        conditions = dict(row.get("conditions") or {})
        key = str(conditions.get("__munshi_question_key") or "").strip().casefold()
        if not key or ontology.concept(key) is None:
            continue
        result.append(
            {
                "concept_id": key,
                "value": row.get("canonical_answer"),
                "source_type": "ANSWER_MEMORY",
                "source_ref": f"answer-vault:{row.get('answer_id')}",
                "trust_level": "USER_CONFIRMED",
                "confidence": float(row.get("confidence") or 0.0),
                "sensitivity": "NORMAL",
                "autofill_policy": "ALLOW" if row.get("autofill_allowed") else "REQUIRE_CONFIRMATION",
                "answer_id": str(row.get("answer_id") or ""),
                "conditions": {k: v for k, v in conditions.items() if k != "__munshi_question_key"},
            }
        )
    return result


def preview_resume_claims(snapshot: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    current = dict(snapshot or current_truth_snapshot())
    extracted = current_profile_extract(current)
    return derivation.extract_profile_claims(
        dict(extracted.get("profile") or {}),
        extraction_id=str(extracted.get("extraction_id") or ""),
        source_digest=str(extracted.get("source_sha256") or ""),
    )


def read_only_knowledge_view() -> dict[str, Any]:
    """Complete value-bearing local view for trusted shadow resolution only."""
    snapshot = current_truth_snapshot()
    extracted = current_profile_extract(snapshot)
    source_facts = preview_candidate_truth(snapshot)
    try:
        persisted_facts = graph.current_facts()
    except (LookupError, RuntimeError, ValueError):
        persisted_facts = []
    return {
        "version": INGESTION_VERSION,
        "profile_revision": int(snapshot.get("profile_revision") or 0),
        "profile_digest": str(snapshot.get("profile_digest") or ""),
        "facts": [*persisted_facts, *source_facts],
        "persisted_graph_fact_count": len(persisted_facts),
        "answer_memory": preview_answer_memory(),
        "claims": derivation.extract_profile_claims(
            dict(extracted.get("profile") or {}),
            extraction_id=str(extracted.get("extraction_id") or ""),
            source_digest=str(extracted.get("source_sha256") or ""),
        ),
        "profile": dict(extracted.get("profile") or {}),
        "source_extraction_id": str(extracted.get("extraction_id") or ""),
        "source_resume_sha256": str(extracted.get("source_sha256") or ""),
    }


def sync_candidate_truth(snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    rows = preview_candidate_truth(snapshot)
    stored: list[str] = []
    reused = 0
    for row in rows:
        item = graph.store_fact(
            concept_id=str(row["concept_id"]),
            value=row.get("value") if "value" in row else None,
            value_ref=row.get("value_ref"),
            source_type=str(row["source_type"]),
            source_ref=str(row["source_ref"]),
            source_digest=row.get("source_digest"),
            trust_level=str(row["trust_level"]),
            confidence=float(row["confidence"]),
            sensitivity=str(row["sensitivity"]),
            autofill_policy=str(row["autofill_policy"]),
            evidence=[
                {
                    "source_kind": str(row["source_type"]),
                    "source_ref": str(row["source_ref"]),
                    "source_digest": row.get("source_digest"),
                    "metadata": {
                        "source_fact_id": row.get("source_fact_id"),
                        "source_fact_key": row.get("source_fact_key"),
                        "candidate_profile_revision": row.get("candidate_profile_revision"),
                    },
                    "confidence": float(row["confidence"]),
                }
            ],
        )
        stored.append(str(item["fact_id"]))
        reused += int(bool(item.get("reused")))
    return {"stored": len(stored), "reused": reused, "fact_ids": stored}


def sync_answer_memory() -> dict[str, Any]:
    stored: list[str] = []
    reused = 0
    for row in preview_answer_memory():
        item = graph.store_fact(
            concept_id=str(row["concept_id"]), value=row.get("value"),
            source_type="ANSWER_MEMORY", source_ref=str(row["source_ref"]),
            trust_level="USER_CONFIRMED", confidence=float(row["confidence"]),
            sensitivity="NORMAL", autofill_policy=str(row["autofill_policy"]),
        )
        stored.append(str(item["fact_id"]))
        reused += int(bool(item.get("reused")))
    return {"stored": len(stored), "reused": reused, "fact_ids": stored}


def sync_resume_claims(snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    claims = preview_resume_claims(snapshot)
    stored: list[str] = []
    reused = 0
    for claim in claims:
        item = graph.store_fact(
            concept_id=str(claim["concept"]), value=True,
            source_type="RESUME", source_ref=str(claim["source_ref"]),
            source_digest=claim.get("source_digest"), trust_level="SOURCE_CONFIRMED",
            confidence=float(claim["confidence"]), sensitivity="NORMAL",
            autofill_policy="ALLOW",
            evidence=[
                {
                    "source_kind": "RESUME",
                    "source_ref": str(claim["source_ref"]),
                    "source_digest": claim.get("source_digest"),
                    "evidence_text": str(claim.get("evidence_text") or ""),
                    "metadata": {
                        "claim_id": claim.get("claim_id"),
                        "entity_ref": claim.get("entity_ref"),
                        "evidence_text_ref": claim.get("evidence_text_ref"),
                    },
                    "confidence": float(claim["confidence"]),
                }
            ],
        )
        stored.append(str(item["fact_id"]))
        reused += int(bool(item.get("reused")))
    return {"stored": len(stored), "reused": reused, "fact_ids": stored, "claim_count": len(claims)}


def sync_all_current_sources() -> dict[str, Any]:
    """Explicit synchronization entrypoint. Never invoked by import or shadow lookup."""
    snapshot = current_truth_snapshot()
    truth = sync_candidate_truth(snapshot)
    memory = sync_answer_memory()
    claims = sync_resume_claims(snapshot)
    return {
        "version": INGESTION_VERSION,
        "truth": truth,
        "answer_memory": memory,
        "resume_claims": claims,
        "knowledge_revision": graph.current_revision(),
    }
