"""Shared question-routing boundary for MUNSHI Apply and AutoApply.

Provider/browser code supplies visible question text.  This module converts that
text into a stable catalog concept and delegates answer authority to Answer Brain
V2 / Candidate Truth.  It never guesses a factual answer and never owns browser
or submission authority.
"""
from __future__ import annotations

from typing import Any

from app import answer_brain_v2
from app import application_question_catalog_v1 as catalog
from app import phase45_truth_binding as truth_binding

ROUTER_VERSION = "application-answer-router-v1"


def requirement_from_question(question: str, *, required: bool = True) -> dict[str, Any]:
    requirement = catalog.requirement_from_question(question, required=required)
    return {**requirement, "answer_router_version": ROUTER_VERSION}


def resolve_question(question: str, *, required: bool = True) -> dict[str, Any]:
    """Resolve one visible form question through the canonical Answer Brain.

    Protected/self-ID/credential/post-offer values remain explicit NEEDS_INPUT
    domains here. Application Plan's secure resolver remains the only execution
    authority for protected values.
    """
    requirement = requirement_from_question(question, required=required)
    sensitivity = str(requirement["sensitivity_class"])
    if sensitivity != "NORMAL":
        return {
            "status": "NEEDS_INPUT",
            "reason": {
                "PROTECTED": "protected_candidate_truth_requires_secure_execution",
                "SELF_ID": "self_identification_requires_saved_policy_or_user_choice",
                "CREDENTIAL": "credential_requires_authentication_flow",
                "POST_OFFER": "post_offer_sensitive_requires_explicit_flow",
            }.get(sensitivity, "sensitive_question_requires_explicit_flow"),
            "requirement": requirement,
        }
    try:
        resolved = answer_brain_v2.resolve_answer(
            question_family=str(requirement["question_family"]),
            conditions=dict(requirement.get("conditions") or {}),
            profile_fact_key=requirement.get("profile_fact_key"),
            question_key=str(requirement["question_key"]),
        )
    except RuntimeError as error:
        return {
            "status": "NEEDS_INPUT",
            "reason": "answer_brain_disabled_or_unavailable",
            "detail": str(error),
            "requirement": requirement,
        }
    return {**resolved, "requirement": requirement}


def save_user_answer(
    question: str,
    canonical_answer: str,
    *,
    autofill_allowed: bool = True,
) -> str:
    """Save an explicitly confirmed normal-domain answer under one semantic key."""
    requirement = requirement_from_question(question)
    if requirement["question_family"] == "unknown":
        raise ValueError(
            "This question is not recognized confidently enough to save as a reusable answer."
        )
    if requirement["sensitivity_class"] != "NORMAL":
        raise ValueError(
            "Protected, self-identification, credential, and post-offer answers use their dedicated secure policy."
        )
    value = str(canonical_answer or "").strip()
    if not value:
        raise ValueError("A confirmed answer is required.")
    return answer_brain_v2.save_answer(
        question_family=str(requirement["question_family"]),
        canonical_answer=value,
        source="user",
        user_confirmed=True,
        confidence=1.0,
        autofill_allowed=bool(autofill_allowed),
        conditions=dict(requirement.get("conditions") or {}),
        question_key=str(requirement["question_key"]),
    )


def _snapshot_fact_keys() -> set[str]:
    try:
        snapshot = truth_binding.current_candidate_profile_snapshot()
    except (LookupError, RuntimeError, ValueError):
        return set()
    return {
        str(item.get("key") or "").casefold()
        for item in snapshot.get("facts") or []
        if str(item.get("key") or "").strip()
    }


def readiness_projection() -> dict[str, Any]:
    """Non-secret catalog coverage for Profile/Settings readiness UI."""
    fact_keys = _snapshot_fact_keys()
    stored_keys: set[str] = set()
    answer_brain_available = True
    try:
        for answer in answer_brain_v2.normal_answer_projection():
            key = str(answer.get("question_key") or "").strip()
            if key and answer.get("user_confirmed"):
                stored_keys.add(key)
    except RuntimeError:
        answer_brain_available = False

    rows: list[dict[str, Any]] = []
    ready_count = 0
    for concept in catalog.CONCEPTS:
        source = "needs_input"
        ready = False
        if concept.sensitivity_class in {"CREDENTIAL", "POST_OFFER"}:
            source = "ask_when_required"
        elif concept.sensitivity_class == "SELF_ID":
            source = "self_id_policy"
        elif concept.sensitivity_class == "PROTECTED":
            if concept.profile_fact_key and concept.profile_fact_key.casefold() in fact_keys:
                source = "protected_candidate_truth"
                ready = True
            else:
                source = "protected_needs_input"
        elif concept.key in stored_keys:
            source = "confirmed_answer_vault"
            ready = True
        elif concept.profile_fact_key and concept.profile_fact_key.casefold() in fact_keys:
            source = "candidate_truth"
            ready = True
        if ready:
            ready_count += 1
        rows.append(
            {
                "question_key": concept.key,
                "family": concept.family,
                "sensitivity_class": concept.sensitivity_class,
                "ready": ready,
                "source": source,
                "example_question": concept.aliases[0],
            }
        )

    return {
        "version": ROUTER_VERSION,
        "catalog_version": catalog.CATALOG_VERSION,
        "common_question_count": catalog.catalog_metrics()["common_question_count"],
        "concept_count": len(catalog.CONCEPTS),
        "ready_concept_count": ready_count,
        "coverage_percent": round(ready_count / len(catalog.CONCEPTS) * 100, 1),
        "answer_brain_available": answer_brain_available,
        "rows": rows,
    }
