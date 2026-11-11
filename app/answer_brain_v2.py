"""Strengthened Phase 5 Answer Brain.

V2 preserves the encrypted-sensitive and disabled-by-default V1 boundaries while
adding stable semantic question identity and Candidate Truth revision binding for
profile-derived memories. It has no browser, Apply, n8n, Gmail, provider, or
submission authority.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app import answer_brain as v1
from app import phase45_truth_binding as truth_binding

ANSWER_VERSION = "application-answer-v2-truth-bound"
_INTERNAL_QUESTION_KEY = "__munshi_question_key"

QUESTION_FAMILIES = v1.QUESTION_FAMILIES
SENSITIVE_FAMILIES = v1.SENSITIVE_FAMILIES
NON_PLAINTEXT_FAMILIES = v1.NON_PLAINTEXT_FAMILIES
NORMAL_QUESTION_FAMILIES = v1.NORMAL_QUESTION_FAMILIES
SELF_ID_CATEGORIES = v1.SELF_ID_CATEGORIES
SELF_ID_POLICIES = v1.SELF_ID_POLICIES
AnswerBrainError = v1.AnswerBrainError


def application_answer_brain_enabled() -> bool:
    return v1.application_answer_brain_enabled()


def ensure_schema(connection=None) -> None:
    truth_binding.ensure_schema(connection)


def classify_question(question: str) -> str:
    return v1.classify_question(question)


def _scoped_conditions(
    conditions: dict[str, Any] | None,
    question_key: str | None,
) -> tuple[dict[str, Any], str | None]:
    values = copy.deepcopy(conditions or {})
    if not isinstance(values, dict):
        raise ValueError("Conditions must be a JSON object.")
    if _INTERNAL_QUESTION_KEY in values:
        raise ValueError("Reserved Answer Brain condition key is not allowed.")
    if question_key is None:
        return values, None
    key = truth_binding.canonical_question_key(question_key)
    values[_INTERNAL_QUESTION_KEY] = key
    return values, key


def _public_answer(answer: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(answer)
    conditions = dict(result.get("conditions") or {})
    key = conditions.pop(_INTERNAL_QUESTION_KEY, None)
    result["conditions"] = conditions
    if key:
        result["question_key"] = key
    return result


def _current_snapshot_or_none() -> dict[str, Any] | None:
    try:
        return truth_binding.current_candidate_profile_snapshot()
    except (LookupError, RuntimeError, ValueError):
        return None


def save_answer(
    *,
    question_family: str,
    canonical_answer: str,
    source: str,
    user_confirmed: bool,
    confidence: float,
    autofill_allowed: bool,
    conditions: dict[str, Any] | None = None,
    question_key: str | None = None,
) -> str:
    """Save candidate/AI memory with optional stable semantic question identity.

    `profile_evidence` is deliberately excluded here; callers must use
    `save_profile_answer` so the value is derived from current canonical truth.
    """
    if str(source or "").strip().casefold() == "profile_evidence":
        raise ValueError("Profile-derived answers must be saved through save_profile_answer().")
    scoped, _ = _scoped_conditions(conditions, question_key)
    return v1.save_answer(
        question_family=question_family,
        canonical_answer=canonical_answer,
        source=source,
        user_confirmed=user_confirmed,
        confidence=confidence,
        autofill_allowed=autofill_allowed,
        conditions=scoped,
    )


def _fact_confidence(fact: dict[str, Any]) -> float:
    trust = str(fact.get("trust_level") or "UNKNOWN").upper()
    return {
        "VERIFIED": 1.0,
        "USER_CONFIRMED": 1.0,
        "DOCUMENT_CONFIRMED": 0.98,
        "DERIVED": 0.8,
        "GENERATED": 0.6,
        "LEARNED": 0.5,
        "UNKNOWN": 0.0,
    }.get(trust, 0.0)


def _save_profile_answer_atomic(
    *,
    family: str,
    answer: str,
    confidence: float,
    autofill_allowed: bool,
    scoped_conditions: dict[str, Any],
    question_key: str,
    profile_fact_key: str,
    snapshot: dict[str, Any],
) -> str:
    """Persist the plaintext memory and truth sidecar in one SQLite transaction."""
    connection = v1.get_connection()
    try:
        ensure_schema(connection)
        owner = v1._owner(connection)
        if connection.in_transaction:
            connection.commit()
        condition_json = v1._conditions(scoped_conditions)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT answer_id FROM application_answer_vault
               WHERE tenant_id=? AND user_id=? AND question_family=? AND conditions_json=?""",
            (owner.tenant_id, owner.user_id, family, condition_json),
        ).fetchone()
        answer_id = str(row["answer_id"]) if row else str(uuid4())
        confirmed_at = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """INSERT INTO application_answer_vault(
                   answer_id,tenant_id,user_id,question_family,canonical_answer,source,
                   user_confirmed,confidence,autofill_allowed,conditions_json,last_confirmed_at
               ) VALUES (?,?,?,?,?,'profile_evidence',1,?,?,?,?)
               ON CONFLICT(tenant_id,user_id,question_family,conditions_json) DO UPDATE SET
                   canonical_answer=excluded.canonical_answer,
                   source=excluded.source,
                   user_confirmed=excluded.user_confirmed,
                   confidence=excluded.confidence,
                   autofill_allowed=excluded.autofill_allowed,
                   last_confirmed_at=excluded.last_confirmed_at,
                   updated_at=CURRENT_TIMESTAMP""",
            (
                answer_id,
                owner.tenant_id,
                owner.user_id,
                family,
                answer,
                float(confidence),
                int(bool(autofill_allowed)),
                condition_json,
                confirmed_at,
            ),
        )
        truth_binding.save_answer_truth_binding(
            answer_id=answer_id,
            question_key=question_key,
            profile_fact_key=profile_fact_key,
            snapshot=snapshot,
            connection=connection,
        )
        connection.commit()
        return answer_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def save_profile_answer(
    *,
    question_family: str,
    question_key: str,
    profile_fact_key: str,
    conditions: dict[str, Any] | None = None,
    autofill_allowed: bool = False,
) -> str:
    """Persist a memory only from a real non-protected current Candidate Truth fact."""
    family = str(question_family or "").strip().casefold()
    if family not in NORMAL_QUESTION_FAMILIES:
        raise ValueError("Profile-derived memory requires a normal Answer Brain family.")
    key = truth_binding.canonical_question_key(question_key)
    snapshot = truth_binding.current_candidate_profile_snapshot()
    fact = truth_binding.profile_fact(snapshot, profile_fact_key)
    if fact is None:
        raise LookupError("Candidate Truth Profile fact was not found.")
    if fact.get("protected") is True:
        raise ValueError("Protected Candidate Truth facts cannot be copied into the normal answer vault.")
    value = fact.get("value")
    if isinstance(value, list):
        answer = " | ".join(str(item).strip() for item in value if str(item).strip())
    else:
        answer = str(value or "").strip()
    if not answer:
        raise ValueError("Candidate Truth Profile fact has no usable answer value.")
    scoped, _ = _scoped_conditions(conditions, key)
    return _save_profile_answer_atomic(
        family=family,
        answer=answer,
        confidence=_fact_confidence(fact),
        autofill_allowed=bool(autofill_allowed),
        scoped_conditions=scoped,
        question_key=key,
        profile_fact_key=str(profile_fact_key),
        snapshot=snapshot,
    )


def _resolved_stored_answer(
    *,
    question_family: str,
    conditions: dict[str, Any],
    question_key: str,
) -> dict[str, Any] | None:
    resolved = v1.resolve_answer(
        question_family=question_family,
        conditions=conditions,
        profile_fact_key=None,
    )
    if resolved.get("status") != "ANSWERED":
        return None
    answer = _public_answer(dict(resolved["answer"]))
    if answer.get("question_key") != question_key:
        return None
    if str(answer.get("source") or "") == "profile_evidence":
        binding = truth_binding.answer_truth_binding(str(answer["answer_id"]))
        snapshot = _current_snapshot_or_none()
        if snapshot is None or not truth_binding.binding_matches_snapshot(binding, snapshot):
            return {
                "status": "NEEDS_INPUT",
                "reason": "stale_profile_answer",
                "question_key": question_key,
            }
        answer["candidate_truth_binding"] = truth_binding.public_binding_state(snapshot)
    return {
        "status": "ANSWERED",
        "resolution": "stored_verified",
        "question_key": question_key,
        "answer": answer,
    }


def resolve_answer(
    *,
    question_family: str,
    conditions: dict[str, Any] | None = None,
    profile_fact_key: str | None = None,
    question_key: str | None = None,
) -> dict[str, Any]:
    family = str(question_family or "").strip().casefold()
    if family in NON_PLAINTEXT_FAMILIES:
        return {"status": "NEEDS_INPUT", "reason": "non_plaintext_answer_requires_separate_policy"}

    if question_key is None:
        return v1.resolve_answer(
            question_family=family,
            conditions=conditions,
            profile_fact_key=profile_fact_key,
        )

    scoped, key = _scoped_conditions(conditions, question_key)
    assert key is not None
    stored = _resolved_stored_answer(
        question_family=family,
        conditions=scoped,
        question_key=key,
    )
    if stored is not None:
        return stored

    if profile_fact_key:
        snapshot = _current_snapshot_or_none()
        if snapshot is None:
            return {"status": "NEEDS_INPUT", "reason": "candidate_truth_profile_unavailable", "question_key": key}
        fact = truth_binding.profile_fact(snapshot, profile_fact_key)
        if fact is None:
            return {"status": "NEEDS_INPUT", "reason": "profile_fact_missing", "question_key": key}
        if fact.get("protected") is True:
            return {
                "status": "NEEDS_INPUT",
                "reason": "protected_profile_fact_requires_explicit_policy",
                "question_key": key,
            }
        return {
            "status": "ANSWERED",
            "resolution": "current_candidate_truth_profile",
            "question_key": key,
            "answer": {
                "canonical_answer": fact.get("value"),
                "source": "profile_evidence",
                "evidence_provenance": fact.get("source"),
                "confidence": _fact_confidence(fact),
                "user_confirmed": fact.get("trust_level") == "USER_CONFIRMED",
                "autofill_allowed": False,
                "fact_key": fact.get("key"),
                "candidate_truth_binding": truth_binding.public_binding_state(snapshot),
            },
        }

    return {"status": "NEEDS_INPUT", "reason": "no_safe_answer", "question_key": key}


def normal_answer_projection() -> list[dict[str, Any]]:
    snapshot = _current_snapshot_or_none()
    result: list[dict[str, Any]] = []
    for raw in v1.normal_answer_projection():
        answer = _public_answer(raw)
        binding = truth_binding.answer_truth_binding(str(answer["answer_id"]))
        if binding:
            answer["candidate_truth_binding"] = {
                "source_extraction_id": binding["source_extraction_id"],
                "profile_revision": int(binding["profile_revision"]),
                "profile_digest": binding["profile_digest"],
            }
            answer["truth_binding_current"] = bool(
                snapshot is not None and truth_binding.binding_matches_snapshot(binding, snapshot)
            )
        result.append(answer)
    return result


def planning_input() -> dict[str, Any]:
    """Return planner-safe normal answers without stale profile-derived content.

    Stale or unbound profile memories are represented only by non-content metadata,
    so an internal planner cannot accidentally reuse an obsolete canonical answer.
    """
    snapshot = _current_snapshot_or_none()
    answers: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for answer in normal_answer_projection():
        if str(answer.get("source") or "") == "profile_evidence" and answer.get("truth_binding_current") is not True:
            excluded.append(
                {
                    "answer_id": str(answer["answer_id"]),
                    "question_key": answer.get("question_key"),
                    "question_family": answer.get("question_family"),
                    "reason": "stale_or_unbound_profile_answer",
                }
            )
            continue
        clean = copy.deepcopy(answer)
        clean["planning_use"] = (
            "autofill_ready"
            if bool(clean.get("user_confirmed")) and bool(clean.get("autofill_allowed"))
            else "context_only"
        )
        answers.append(clean)
    return {
        "version": ANSWER_VERSION,
        "candidate_truth_binding": truth_binding.public_binding_state(snapshot) if snapshot else None,
        "answers": answers,
        "excluded_answers": excluded,
    }


read_sensitive_self_identification = v1.read_sensitive_self_identification
resolve_sensitive_self_identification = v1.resolve_sensitive_self_identification
store_sensitive_self_identification = v1.store_sensitive_self_identification
clear_sensitive_self_identification = v1.clear_sensitive_self_identification
