"""Canonical visible-question bridge into immutable Application Plan V2.

Provider adapters, MUNSHI Apply, and AutoApply should not invent their own
question keys. They hand visible labels/prompts/field metadata to this module;
it first uses deterministic structural field intelligence (HTML autocomplete,
labels, names) and then falls back to the shared semantic question catalog.

This is a producer-side convenience layer only. Application Plan V2 remains the
answer, permission, provider-policy, digest, and idempotency authority.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app import application_answer_autofill_v1 as answer_autofill
from app import application_answer_router_v1 as answer_router
from app import application_field_intelligence_v1 as field_intelligence
from app import application_plan_v2
from app import candidate_knowledge_graph_v1 as universal_graph
from app import universal_brain_shadow_v1 as universal_shadow
from app import universal_question_resolver_v1 as universal_questions
from app import universal_semantic_model_v1 as universal_semantic_model
from app import application_semantic_binding_v1 as universal_binding

BRIDGE_VERSION = "application-plan-question-bridge-v1"


def normalize_visible_question(value: str | Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one provider/browser question into an Application Plan requirement."""
    if isinstance(value, Mapping):
        raw = dict(value)
        text = str(
            raw.get("question")
            or raw.get("label")
            or raw.get("prompt")
            or raw.get("normalized_question")
            or raw.get("rawText")
            or raw.get("aria_label")
            or raw.get("ariaLabel")
            or raw.get("placeholder")
            or raw.get("field_name")
            or raw.get("fieldName")
            or raw.get("name")
            or ""
        ).strip()
        required = bool(raw.get("required", True))
    else:
        raw = {}
        text = str(value or "").strip()
        required = True

    structural = (
        field_intelligence.requirement_from_field(raw, required=required)
        if raw
        else None
    )
    if structural is not None:
        requirement = structural
    else:
        # Apply semantic hints for protected/self-ID domains are intentionally not
        # converted into NORMAL structural fields. Preserve their visible raw text
        # here so the canonical catalog can recover the protected semantic key.
        requirement = answer_router.requirement_from_question(text, required=required)

    # Browser/provider metadata may narrow a requirement, but it must not silently
    # change a semantic key or downgrade the catalog's sensitivity classification.
    conditions = dict(requirement.get("conditions") or {})
    explicit_conditions = raw.get("conditions")
    if isinstance(explicit_conditions, Mapping):
        for key, item in explicit_conditions.items():
            name = str(key or "").strip()
            if name:
                conditions[name] = item
    requirement["conditions"] = conditions

    explicit_profile_key = str(raw.get("profile_fact_key") or "").strip()
    if explicit_profile_key and not requirement.get("profile_fact_key"):
        requirement["profile_fact_key"] = explicit_profile_key

    # Bind derived projections to the current confirmed profile digest/revision so
    # a later profile edit cannot silently reuse an older first/last/city projection.
    requirement = answer_autofill.enrich_requirement(requirement)

    # Universal Brain semantic metadata is value-free. In shadow mode it can be
    # compared/logged without changing current authority. When the live NORMAL
    # canary is enabled, Application Plan V2 may consume this canonical concept.
    observation = raw or {"raw_question": text, "label": text}
    if universal_graph.universal_brain_shadow_enabled() or universal_graph.universal_brain_enabled():
        semantic = universal_questions.resolve_question(
            observation, model_interpreter=universal_semantic_model.interpret_fail_closed
        )
        if semantic.get("state") == "RESOLVED":
            requirement["universal_concept_id"] = str(semantic["concept_id"])
            requirement["universal_semantic_confidence"] = float(semantic.get("confidence") or 0.0)
            requirement["universal_classification_lane"] = str(semantic.get("classification_lane") or "")
            requirement["universal_reason_code"] = str(semantic.get("reason_code") or "")
            requirement["universal_sensitivity"] = str(semantic.get("sensitivity") or "NORMAL")
            try:
                requirement["universal_binding_fingerprint"] = universal_binding.fingerprint_parts(observation)[
                    "logical_field_fingerprint"
                ]
            except ValueError:
                # Unscoped repeated fields intentionally do not receive a durable
                # binding fingerprint; they must remain unresolved/fail closed.
                requirement["universal_binding_fingerprint"] = None
        if universal_graph.universal_brain_shadow_enabled():
            requirement["universal_brain_shadow"] = universal_shadow.compare(
                observation, current_requirement=requirement
            )

    requirement["provider_field_id"] = str(
        raw.get("field_id")
        or raw.get("provider_field_id")
        or raw.get("controlId")
        or ""
    ).strip() or None
    requirement["provider_field_name"] = str(
        raw.get("field_name")
        or raw.get("fieldName")
        or raw.get("name")
        or ""
    ).strip() or None
    requirement["answer_router_version"] = answer_router.ROUTER_VERSION
    requirement["question_bridge_version"] = BRIDGE_VERSION
    requirement["field_intelligence_version"] = (
        field_intelligence.INTELLIGENCE_VERSION if structural is not None else None
    )
    requirement["answer_autofill_version"] = answer_autofill.AUTOFILL_VERSION
    return requirement


def normalize_visible_questions(
    values: Sequence[str | Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Normalize questions while preserving order and collapsing exact duplicates."""
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for value in values or ():
        requirement = normalize_visible_question(value)
        key = (
            str(requirement["question_key"]),
            requirement.get("provider_field_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(requirement)
    return normalized


def prepare_application_plan_from_questions(
    *,
    preflight_package_id: str,
    idempotency_key: str,
    visible_questions: Sequence[str | Mapping[str, Any]] | None = None,
    permissions: Mapping[str, Any] | None = None,
    provider_policy: Mapping[str, Any] | None = None,
    secure_resolver_refs: Mapping[str, Any] | None = None,
    resume_format: str = "PDF",
    cover_letter_id: str | None = None,
) -> dict[str, Any]:
    """Build one Application Plan using path-neutral normalized questions."""
    requirements = normalize_visible_questions(visible_questions)
    plan = application_plan_v2.prepare_application_plan(
        preflight_package_id=preflight_package_id,
        idempotency_key=idempotency_key,
        answer_requirements=requirements,
        permissions=permissions,
        provider_policy=provider_policy,
        secure_resolver_refs=secure_resolver_refs,
        resume_format=resume_format,
        cover_letter_id=cover_letter_id,
    )
    result = dict(plan)
    result["question_bridge"] = {
        "version": BRIDGE_VERSION,
        "answer_router_version": answer_router.ROUTER_VERSION,
        "field_intelligence_version": field_intelligence.INTELLIGENCE_VERSION,
        "answer_autofill_version": answer_autofill.AUTOFILL_VERSION,
        "universal_brain_shadow_version": universal_shadow.SHADOW_VERSION,
        "universal_brain_shadow_enabled": universal_graph.universal_brain_shadow_enabled(),
        "normalized_question_count": len(requirements),
    }
    return result
