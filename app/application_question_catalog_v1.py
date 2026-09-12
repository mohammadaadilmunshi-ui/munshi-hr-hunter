"""Compatibility import for the corrected canonical application catalog.

V2 preserves the public V1 functions/types while fixing the initial catalog-size
mistake and tightening United States wording normalization. New code may import
V2 directly; existing callers remain stable through this module.

This compatibility layer also exposes each concept's deterministic-autofill
policy in the requirement shape consumed by Application Plan V2.  The policy is
metadata only; Candidate Truth and Application Plan permissions remain the
actual execution authority.
"""
from app import application_question_catalog_v2 as _v2
from app.application_question_catalog_v2 import *  # noqa: F403


def requirement_from_question(question: str, *, required: bool = True):
    requirement = _v2.requirement_from_question(question, required=required)
    if str(requirement.get("question_family") or "") == "unknown":
        requirement["autofill_default"] = False
        return requirement

    key = str(requirement.get("question_key") or "")
    concept = next((item for item in _v2.CONCEPTS if item.key == key), None)
    if concept is not None:
        requirement["autofill_default"] = bool(concept.autofill_default)
    else:
        # Dynamic requirements such as experience.skill_years already expose the
        # policy through match_question(); remain fail-closed when unavailable.
        match = _v2.match_question(question)
        requirement["autofill_default"] = bool(match.get("autofill_default", False))
    return requirement
