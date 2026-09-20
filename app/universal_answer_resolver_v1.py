"""Universal candidate answer authority and evidence resolver.

Question understanding and answer authority are deliberately separate.  This
module may read candidate values inside Hunter's trusted process, but its public
trace is value-free and it never grants browser/final-submit authority.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Callable, Mapping, Sequence

from app import candidate_derivation_engine_v1 as derivation
from app import candidate_knowledge_graph_v1 as graph
from app import candidate_knowledge_ingestion_v1 as ingestion
from app import candidate_ontology_v1 as ontology
from app import universal_question_resolver_v1 as question_resolver

ANSWER_RESOLVER_VERSION = "universal-answer-resolver-v1"

RESOLUTION_STATES = frozenset({
    "ANSWERED", "DERIVED", "POLICY_RESOLVED", "GENERATED",
    "AMBIGUOUS_CONCEPT", "MISSING_FACT", "CONFLICT",
    "REQUIRES_EXPLICIT_USER", "PROTECTED_CHECKPOINT", "BLOCKED_BY_POLICY",
})


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _opaque_value_ref(concept_id: str, row: Mapping[str, Any], revision: int) -> str:
    payload = {
        "concept_id": concept_id,
        "source_type": row.get("source_type"),
        "source_ref": row.get("source_ref"),
        "revision": revision,
    }
    digest = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
    return f"hunter-value://universal-brain/{digest[:48]}"


def _authority(row: Mapping[str, Any]) -> tuple[int, int, float]:
    return (
        graph.SOURCE_PRECEDENCE.get(str(row.get("source_type") or "").upper(), 0),
        graph.TRUST_RANK.get(str(row.get("trust_level") or "UNKNOWN").upper(), 0),
        float(row.get("confidence") or 0.0),
    )


def _fingerprint_value(row: Mapping[str, Any]) -> str:
    if "value" in row:
        return hashlib.sha256(_json(row.get("value")).encode("utf-8")).hexdigest()
    return hashlib.sha256(f"ref:{row.get('value_ref')}".encode("utf-8")).hexdigest()


def _candidate_rows(concept_id: str, view: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in view.get("facts") or []:
        row = dict(raw)
        if str(row.get("concept_id") or "").casefold() == concept_id:
            rows.append(row)
    for raw in view.get("answer_memory") or []:
        row = dict(raw)
        if str(row.get("concept_id") or "").casefold() == concept_id:
            rows.append(row)
    rows.sort(key=lambda row: (_authority(row), str(row.get("source_ref") or "")), reverse=True)
    return rows


def _select_row(concept_id: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"status": "MISSING_FACT", "concept_id": concept_id}
    top = dict(rows[0])
    top_authority = _authority(top)
    tied = [dict(row) for row in rows if _authority(row) == top_authority]
    if len({_fingerprint_value(row) for row in tied}) > 1:
        return {
            "status": "CONFLICT",
            "concept_id": concept_id,
            "source_refs": [str(row.get("source_ref") or "") for row in tied],
        }
    return {"status": "ANSWERED", "concept_id": concept_id, "row": top}


def _base_result(
    *,
    status: str,
    concept_id: str | None,
    answer_mode: str | None,
    confidence: float,
    sensitivity: str,
    autofill_allowed: bool,
    reason: str,
    knowledge_revision: int,
    question: Mapping[str, Any],
    source_fact_ids: Sequence[str] = (),
    evidence_ids: Sequence[str] = (),
    value_ref: str | None = None,
    value: Any = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in RESOLUTION_STATES:
        raise ValueError(f"Unsupported Universal Brain answer state: {status}")
    result = {
        "answer_resolver_version": ANSWER_RESOLVER_VERSION,
        "concept_id": concept_id,
        "answer_status": status,
        "answer_mode": answer_mode,
        "value_ref": value_ref,
        "source_fact_ids": [str(item) for item in source_fact_ids if str(item)],
        "evidence_ids": [str(item) for item in evidence_ids if str(item)],
        "confidence": round(float(confidence), 6),
        "sensitivity": sensitivity,
        "autofill_allowed": bool(autofill_allowed),
        "reason": reason,
        "knowledge_revision": int(knowledge_revision),
        "question_resolution": dict(question),
    }
    if value is not None:
        result["value"] = value
    if extra:
        result.update(dict(extra))
    return result


def resolve_concept(
    question: Mapping[str, Any],
    *,
    knowledge_view: Mapping[str, Any] | None = None,
    as_of: date | None = None,
    open_ended_generator: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    job_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state = str(question.get("state") or "UNKNOWN")
    sensitivity = str(question.get("sensitivity") or "NORMAL").upper()
    if state == "AMBIGUOUS_CONCEPT":
        return _base_result(
            status="AMBIGUOUS_CONCEPT", concept_id=None, answer_mode=None,
            confidence=float(question.get("confidence") or 0.0), sensitivity=sensitivity,
            autofill_allowed=False, reason="semantic_concept_ambiguous", knowledge_revision=0,
            question=question,
        )
    if state != "RESOLVED" or not question.get("concept_id"):
        return _base_result(
            status="MISSING_FACT", concept_id=None, answer_mode=None, confidence=0.0,
            sensitivity=sensitivity, autofill_allowed=False,
            reason="semantic_concept_unknown", knowledge_revision=0, question=question,
        )

    concept_id = str(question["concept_id"]).casefold()
    concept = ontology.require_concept(concept_id)
    sensitivity = ontology.sensitivity_at_least(concept.sensitivity, sensitivity)
    try:
        view = dict(knowledge_view or ingestion.read_only_knowledge_view())
    except (LookupError, RuntimeError, ValueError):
        view = {
            "profile_revision": 0, "facts": [], "answer_memory": [],
            "claims": [], "profile": {},
        }
    revision = int(view.get("profile_revision") or 0)

    # Protected domains remain separate.  Universal Brain may identify the
    # concept and an opaque owner-scoped source reference, but cannot flatten it
    # into the normal answer path or silently grant autofill authority.
    if sensitivity != "NORMAL":
        rows = _candidate_rows(concept_id, view)
        selected = _select_row(concept_id, rows)
        refs = [str(row.get("value_ref") or "") for row in rows if row.get("value_ref")]
        return _base_result(
            status="PROTECTED_CHECKPOINT",
            concept_id=concept_id, answer_mode=concept.answer_mode,
            confidence=float(question.get("confidence") or 0.0), sensitivity=sensitivity,
            autofill_allowed=False,
            reason="protected_policy_requires_separate_authority" if refs else "protected_fact_or_policy_missing",
            knowledge_revision=revision, question=question,
            value_ref=refs[0] if refs else None,
            extra={"protected_reference_available": bool(refs), "candidate_plaintext_exposed": False},
        )

    # Direct/pref/policy-normal facts: search all current candidate authorities,
    # not just the field classifier that happened to recognize the question.
    rows = _candidate_rows(concept_id, view)
    selected = _select_row(concept_id, rows)
    if selected["status"] == "CONFLICT":
        return _base_result(
            status="CONFLICT", concept_id=concept_id, answer_mode=concept.answer_mode,
            confidence=float(question.get("confidence") or 0.0), sensitivity="NORMAL",
            autofill_allowed=False, reason="top_authority_candidate_facts_conflict",
            knowledge_revision=revision, question=question,
            extra={"conflicting_source_refs": selected.get("source_refs", [])},
        )
    if selected["status"] == "ANSWERED":
        row = dict(selected["row"])
        policy = str(row.get("autofill_policy") or "REQUIRE_CONFIRMATION").upper()
        allowed = policy == "ALLOW" and float(row.get("confidence") or 0.0) >= 0.8
        value_ref = _opaque_value_ref(concept_id, row, revision)
        source_fact_id = str(row.get("source_fact_id") or row.get("fact_id") or "")
        return _base_result(
            status="ANSWERED", concept_id=concept_id, answer_mode=concept.answer_mode,
            confidence=min(float(question.get("confidence") or 0.0), float(row.get("confidence") or 0.0)),
            sensitivity="NORMAL", autofill_allowed=allowed,
            reason=str(row.get("trust_level") or row.get("source_type") or "candidate_knowledge"),
            knowledge_revision=revision, question=question, source_fact_ids=[source_fact_id],
            value_ref=value_ref, value=row.get("value"),
            extra={"answer_source": str(row.get("source_type") or ""), "source_ref": str(row.get("source_ref") or "")},
        )

    # Evidence-backed derivations run only after direct facts and reusable memory,
    # and remain independently rollout-gated from direct NORMAL fact autofill.
    conditions = dict(question.get("conditions") or {})
    if not graph.universal_derivation_enabled() and concept.answer_mode in {
        "COMPUTED_FACT", "EXPERIENCE_EVIDENCE_BOOLEAN", "SKILL_EVIDENCE", "ENUMERATION"
    }:
        return _base_result(
            status="MISSING_FACT", concept_id=concept_id, answer_mode=concept.answer_mode,
            confidence=float(question.get("confidence") or 0.0), sensitivity="NORMAL",
            autofill_allowed=False, reason="universal_derivation_rollout_disabled",
            knowledge_revision=revision, question=question,
        )
    profile = dict(view.get("profile") or {})
    claims = [dict(item) for item in view.get("claims") or []]
    seen_claim_ids = {str(item.get("claim_id") or "") for item in claims}
    for fact in view.get("facts") or []:
        if not isinstance(fact, Mapping):
            continue
        fact_concept = str(fact.get("concept_id") or "").strip().casefold()
        if not fact_concept.startswith("experience.skill.") or fact.get("value") is not True:
            continue
        fact_id = str(fact.get("fact_id") or fact.get("source_fact_id") or fact_concept)
        synthetic_id = f"fact-evidence:{fact_id}"
        if synthetic_id in seen_claim_ids:
            continue
        evidence_rows = [dict(row) for row in fact.get("evidence") or [] if isinstance(row, Mapping)]
        evidence_text = next(
            (str(row.get("evidence_text") or "").strip() for row in evidence_rows if str(row.get("evidence_text") or "").strip()),
            fact_concept.removeprefix("experience.skill.").replace("_", " "),
        )
        claims.append(
            {
                "claim_id": synthetic_id,
                "claim_type": "USER_CONFIRMED_CAPABILITY",
                "concept": fact_concept,
                "capability": fact_concept.removeprefix("experience.skill."),
                "entity_ref": None,
                "evidence_text": evidence_text,
                "evidence_category": "user confirmed skill",
                "source_ref": str(fact.get("source_ref") or "candidate-knowledge"),
                "confidence": float(fact.get("confidence") or 1.0),
            }
        )
        seen_claim_ids.add(synthetic_id)
    derived: dict[str, Any] | None = None
    if concept_id == "experience.total_years":
        derived = derivation.total_experience(profile, as_of=as_of)
    elif concept_id == "experience.skill_years":
        skill = str(conditions.get("skill") or "").strip()
        if skill:
            derived = derivation.capability_years(skill, profile, claims, as_of=as_of)
    elif concept_id.startswith("experience.skill."):
        capability = concept_id.removeprefix("experience.skill.")
        derived = derivation.capability_boolean(capability, claims)
    elif concept_id == "experience.capabilities":
        derived = derivation.supported_capabilities(
            claims, filter_text=str(conditions.get("filter_text") or "")
        )
    if derived and derived.get("status") == "DERIVED":
        evidence_ids = [str(item) for item in derived.get("evidence_ids") or []]
        row = {
            "source_type": "DERIVED",
            "source_ref": f"{derivation.DERIVATION_VERSION}:{concept_id}",
            "trust_level": "DERIVED",
            "confidence": 0.95,
        }
        return _base_result(
            status="DERIVED", concept_id=concept_id,
            answer_mode=str(derived.get("answer_mode") or concept.answer_mode),
            confidence=min(float(question.get("confidence") or 0.0), 0.95), sensitivity="NORMAL",
            autofill_allowed=True, reason="evidence_backed_deterministic_derivation",
            knowledge_revision=revision, question=question, evidence_ids=evidence_ids,
            value_ref=_opaque_value_ref(concept_id, row, revision), value=derived.get("value"),
            extra={"derivation": {k: v for k, v in derived.items() if k not in {"value"}}},
        )

    if concept.answer_mode == "OPEN_ENDED_JOB_SPECIFIC":
        if open_ended_generator is None:
            return _base_result(
                status="REQUIRES_EXPLICIT_USER", concept_id=concept_id,
                answer_mode=concept.answer_mode, confidence=float(question.get("confidence") or 0.0),
                sensitivity="NORMAL", autofill_allowed=False,
                reason="job_specific_generator_not_bound", knowledge_revision=revision, question=question,
            )
        payload = {
            "concept_id": concept_id,
            "job_context": dict(job_context or {}),
            "candidate_evidence": [
                {
                    "claim_id": item.get("claim_id"),
                    "concept": item.get("concept"),
                    "entity_ref": item.get("entity_ref"),
                    "evidence_text": item.get("evidence_text"),
                }
                for item in claims[:200]
            ],
            "truth_guard": "NO_UNSUPPORTED_CLAIMS",
        }
        generated = dict(open_ended_generator(payload) or {})
        value = str(generated.get("answer") or "").strip()
        supported = bool(generated.get("evidence_supported"))
        if value and supported:
            row = {
                "source_type": "DERIVED", "source_ref": "open-ended-generator",
                "trust_level": "DERIVED", "confidence": float(generated.get("confidence") or 0.8),
            }
            return _base_result(
                status="GENERATED", concept_id=concept_id, answer_mode=concept.answer_mode,
                confidence=min(float(question.get("confidence") or 0.0), float(row["confidence"])),
                sensitivity="NORMAL", autofill_allowed=bool(generated.get("autofill_allowed")),
                reason="evidence_guarded_job_specific_generation", knowledge_revision=revision,
                question=question, value_ref=_opaque_value_ref(concept_id, row, revision), value=value,
                evidence_ids=[str(item) for item in generated.get("evidence_ids") or []],
            )
        return _base_result(
            status="BLOCKED_BY_POLICY", concept_id=concept_id, answer_mode=concept.answer_mode,
            confidence=float(question.get("confidence") or 0.0), sensitivity="NORMAL",
            autofill_allowed=False, reason="generated_answer_not_evidence_supported",
            knowledge_revision=revision, question=question,
        )

    return _base_result(
        status="MISSING_FACT", concept_id=concept_id, answer_mode=concept.answer_mode,
        confidence=float(question.get("confidence") or 0.0), sensitivity="NORMAL",
        autofill_allowed=False, reason="complete_candidate_knowledge_search_exhausted",
        knowledge_revision=revision, question=question,
    )


def resolve_observation(
    observation: Mapping[str, Any] | question_resolver.QuestionObservation,
    *,
    model_interpreter: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    knowledge_view: Mapping[str, Any] | None = None,
    as_of: date | None = None,
    open_ended_generator: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    job_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    question = question_resolver.resolve_question(observation, model_interpreter=model_interpreter)
    return resolve_concept(
        question, knowledge_view=knowledge_view, as_of=as_of,
        open_ended_generator=open_ended_generator, job_context=job_context,
    )


def public_trace(result: Mapping[str, Any]) -> dict[str, Any]:
    """Value-free resolution trace suitable for logs, tests and dashboard status."""
    hidden = {"value", "canonical_answer", "display_value"}
    trace = {key: value for key, value in dict(result).items() if key not in hidden}
    trace["value_exposed_in_trace"] = False
    if trace.get("value_ref"):
        # Opaque refs are safe metadata; never resolve them in observability.
        trace["value_ref_present"] = True
        trace["value_ref"] = None
    return trace
