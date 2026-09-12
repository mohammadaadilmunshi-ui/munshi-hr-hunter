"""Canonical visible-question bridge into immutable Application Plan V2.

Provider adapters, MUNSHI Apply, and AutoApply should not invent their own
question keys.  They hand visible labels/prompts to this module; it performs the
same deterministic semantic normalization and passes the resulting requirements
to Application Plan V2.

This is a producer-side convenience layer only.  Application Plan V2 remains the
answer, permission, provider-policy, digest, and idempotency authority.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app import application_answer_router_v1 as answer_router
from app import application_plan_v2

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
            or ""
        ).strip()
        required = bool(raw.get("required", True))
    else:
        raw = {}
        text = str(value or "").strip()
        required = True

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

    requirement["provider_field_id"] = str(
        raw.get("field_id") or raw.get("provider_field_id") or ""
    ).strip() or None
    requirement["provider_field_name"] = str(
        raw.get("field_name") or raw.get("name") or ""
    ).strip() or None
    requirement["answer_router_version"] = answer_router.ROUTER_VERSION
    requirement["question_bridge_version"] = BRIDGE_VERSION
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
        "normalized_question_count": len(requirements),
    }
    return result
