"""Smart, truth-bound answer preparation for application autofill.

The 100-question catalog is recognition coverage, not a 100-question onboarding
form. This module prepares only safe deterministic answers from the current
confirmed Candidate Truth profile and stores reusable normal-domain answers under
the same semantic keys used by both MUNSHI Apply and AutoApply.

No candidate-specific value is hard-coded here. Protected/self-ID/credential/
post-offer domains remain outside the normal answer vault.
"""
from __future__ import annotations

from typing import Any, Mapping

from app import answer_brain_v2
from app import application_field_intelligence_v1 as fields
from app import application_question_catalog_v1 as catalog
from app import phase45_truth_binding as truth_binding

AUTOFILL_VERSION = "application-answer-autofill-v1"

# Structural form variants that providers commonly split even when Candidate
# Truth stores one canonical source value. These are transformations, not user
# data and not provider-specific selectors.
_DERIVED_PROJECTIONS: tuple[tuple[str, str, str], ...] = (
    ("contact.first_name", "contact.full_name", "first_name"),
    ("contact.middle_name", "contact.full_name", "middle_name"),
    ("contact.last_name", "contact.full_name", "last_name"),
    ("contact.city", "contact.location", "location_city"),
    ("contact.region", "contact.location", "location_region"),
    ("contact.postal_code", "contact.location", "location_postal"),
    ("contact.country", "contact.location", "location_country"),
)

# These concepts are meaningful only in the context of a real job or concrete
# skill. They stay in the recognition catalog, but should never become generic
# onboarding questions such as "years with this skill?".
_CONTEXTUAL_REUSABLE_KEYS = {
    "experience.relevant_years",
    "experience.skill_years",
}


def _binding_conditions(snapshot: Mapping[str, Any], projection: str) -> dict[str, Any]:
    return {
        "profile_projection": str(projection),
        "candidate_profile_revision": int(snapshot["profile_revision"]),
        "candidate_profile_digest": str(snapshot["profile_digest"]),
    }


def enrich_requirement(requirement: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a structural projection to the current confirmed profile revision.

    A changed profile digest therefore stops an older derived answer from matching
    silently. Direct catalog facts continue to use Answer Brain's truth-binding
    sidecar instead of duplicating profile content into conditions.
    """
    result = dict(requirement)
    conditions = dict(result.get("conditions") or {})
    projection = str(conditions.get("profile_projection") or "").strip()
    if not projection or projection == "identity":
        return result
    try:
        snapshot = truth_binding.current_candidate_profile_snapshot()
    except (LookupError, RuntimeError, ValueError):
        return result
    conditions.update(_binding_conditions(snapshot, projection))
    result["conditions"] = conditions
    result["answer_autofill_version"] = AUTOFILL_VERSION
    return result


def _fact(snapshot: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    wanted = str(key or "").strip().casefold()
    for raw in snapshot.get("facts") or []:
        item = dict(raw)
        if str(item.get("key") or "").strip().casefold() == wanted:
            return item
    return None


def prepare_from_candidate_truth() -> dict[str, Any]:
    """Prepare every safe reusable answer that Candidate Truth already supports.

    This function is intentionally idempotent. Calling it again after a profile
    edit refreshes profile-bound answers and writes new digest-scoped derived
    answers. It never creates protected/self-ID/post-offer/credential plaintext.
    """
    snapshot = truth_binding.current_candidate_profile_snapshot()
    direct_saved: list[str] = []
    derived_saved: list[str] = []
    skipped: list[dict[str, str]] = []

    for concept in catalog.CONCEPTS:
        if concept.sensitivity_class != "NORMAL":
            continue
        if not concept.autofill_default or not concept.profile_fact_key:
            continue
        fact = _fact(snapshot, concept.profile_fact_key)
        if fact is None or fact.get("protected") is True:
            skipped.append({"question_key": concept.key, "reason": "profile_fact_missing"})
            continue
        try:
            answer_brain_v2.save_profile_answer(
                question_family=concept.family,
                question_key=concept.key,
                profile_fact_key=concept.profile_fact_key,
                autofill_allowed=True,
            )
        except (LookupError, RuntimeError, ValueError) as error:
            skipped.append({"question_key": concept.key, "reason": str(error)})
        else:
            direct_saved.append(concept.key)

    for question_key, fact_key, projection in _DERIVED_PROJECTIONS:
        resolved = fields.resolve_profile_projection(
            profile_fact_key=fact_key,
            projection=projection,
        )
        if resolved.get("status") != "ANSWERED":
            skipped.append(
                {
                    "question_key": question_key,
                    "reason": str(resolved.get("reason") or "projection_unavailable"),
                }
            )
            continue
        answer = dict(resolved.get("answer") or {})
        value = str(answer.get("canonical_answer") or "").strip()
        if not value:
            continue
        answer_brain_v2.save_profile_projection_answer(
            question_family="candidate_fact",
            question_key=question_key,
            profile_fact_key=fact_key,
            projection=projection,
            autofill_allowed=True,
        )
        derived_saved.append(question_key)

    return {
        "version": AUTOFILL_VERSION,
        "profile_revision": int(snapshot["profile_revision"]),
        "profile_digest": str(snapshot["profile_digest"]),
        "direct_saved": direct_saved,
        "derived_saved": derived_saved,
        "prepared_count": len(direct_saved) + len(derived_saved),
        "skipped": skipped,
    }


def onboarding_delta(readiness: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return only useful reusable gaps; never turn the whole catalog into a form."""
    deferred_families = {
        "open_ended_job_specific",
        "post_offer_sensitive",
        "voluntary_self_identification",
        "credential_requirement",
    }
    rows: list[dict[str, Any]] = []
    for raw in readiness.get("rows") or []:
        row = dict(raw)
        if row.get("ready"):
            continue
        if str(row.get("family") or "") in deferred_families:
            continue
        if str(row.get("question_key") or "") in _CONTEXTUAL_REUSABLE_KEYS:
            continue
        if str(row.get("sensitivity_class") or "") != "NORMAL":
            continue
        rows.append(row)
    return rows
