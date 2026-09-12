"""Compatibility import for the corrected canonical application catalog.

V2 preserves the public V1 functions/types while fixing the initial catalog-size
mistake and tightening United States wording normalization. New code may import
V2 directly; existing callers remain stable through this module.

This compatibility layer also exposes each concept's deterministic-autofill
policy in the requirement shape consumed by Application Plan V2. The policy is
metadata only; Candidate Truth and Application Plan permissions remain the
actual execution authority.

Generic catalog placeholders such as "this skill" / "this technology" must stay
on the reusable ``experience.skill_years`` concept instead of being interpreted
as literal dynamic skill names. Otherwise saving the UI gap creates an unrelated
``experience.skill_years.this.skill`` answer and the gap never advances.
"""
from __future__ import annotations

from typing import Any

from app import application_question_catalog_v2 as _v2
from app.application_question_catalog_v2 import *  # noqa: F403


_GENERIC_SKILL_YEAR_KEY = "experience.skill_years"


def _generic_skill_year_match(question: str) -> dict[str, Any] | None:
    """Resolve the catalog's placeholder skill-year aliases statically."""
    normalized = _v2.normalize_question(question)
    concept = next(item for item in _v2.CONCEPTS if item.key == _GENERIC_SKILL_YEAR_KEY)
    matched_alias = next(
        (
            alias
            for alias in concept.aliases
            if _v2.normalize_question(alias) == normalized
        ),
        None,
    )
    if matched_alias is None:
        return None
    return {
        "matched": True,
        "catalog_version": _v2.CATALOG_VERSION,
        "question_key": concept.key,
        "question_family": concept.family,
        "answer_type": concept.answer_type,
        "sensitivity_class": concept.sensitivity_class,
        "profile_fact_key": concept.profile_fact_key,
        "autofill_default": concept.autofill_default,
        "conditions": {},
        "canonical_concept": concept.key,
        "confidence": 1.0,
        "match_type": "exact",
        "matched_alias": matched_alias,
        "normalized_question": normalized,
    }


def match_question(
    question: str,
    *,
    minimum_score: float = 0.84,
    minimum_margin: float = 0.05,
):
    generic = _generic_skill_year_match(question)
    if generic is not None:
        return generic
    return _v2.match_question(
        question,
        minimum_score=minimum_score,
        minimum_margin=minimum_margin,
    )


def requirement_from_question(question: str, *, required: bool = True):
    generic = _generic_skill_year_match(question)
    if generic is not None:
        requirement = {
            "question_key": generic["question_key"],
            "question_family": generic["question_family"],
            "normalized_question": generic["normalized_question"],
            "required": bool(required),
            "profile_fact_key": generic["profile_fact_key"],
            "conditions": {},
            "sensitivity_class": generic["sensitivity_class"],
            "catalog_match": {
                "catalog_version": generic["catalog_version"],
                "canonical_concept": generic["canonical_concept"],
                "confidence": generic["confidence"],
                "match_type": generic["match_type"],
            },
        }
    else:
        requirement = _v2.requirement_from_question(question, required=required)

    if str(requirement.get("question_family") or "") == "unknown":
        requirement["autofill_default"] = False
        return requirement

    key = str(requirement.get("question_key") or "")
    concept = next((item for item in _v2.CONCEPTS if item.key == key), None)
    if concept is not None:
        requirement["autofill_default"] = bool(concept.autofill_default)
    else:
        # Concrete dynamic skills such as Power BI still use V2's bounded dynamic
        # matching and remain fail-closed when no reusable answer is available.
        match = match_question(question)
        requirement["autofill_default"] = bool(match.get("autofill_default", False))
    return requirement
