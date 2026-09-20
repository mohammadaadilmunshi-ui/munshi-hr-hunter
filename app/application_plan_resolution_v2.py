from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from app import answer_brain_v2, application_plan_delivery_v2, application_plan_v2


def _handoff_key(
    *,
    prior_plan_id: str,
    replacement_plan_id: str,
    revision_idempotency_key: str,
) -> str:
    material = (
        f"{prior_plan_id}\x1f{replacement_plan_id}\x1f"
        f"{revision_idempotency_key}"
    )
    return "runtime-resolution-handoff-" + hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()


def resolve_runtime_needs_input(
    *,
    prior_plan_id: str,
    expected_prior_plan_digest: str,
    revision_idempotency_key: str,
    runtime_context: Mapping[str, Any],
    answer_resolutions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve runtime NEEDS_INPUT only through a replacement immutable Plan."""
    replacement = application_plan_v2.reprepare_runtime_needs_input_plan(
        prior_plan_id=prior_plan_id,
        expected_prior_plan_digest=expected_prior_plan_digest,
        idempotency_key=revision_idempotency_key,
        runtime_context=runtime_context,
        answer_resolutions=answer_resolutions,
    )
    delivered = application_plan_delivery_v2.deliver_application_plan(
        plan_id=str(replacement["plan_id"]),
        idempotency_key=_handoff_key(
            prior_plan_id=prior_plan_id,
            replacement_plan_id=str(replacement["plan_id"]),
            revision_idempotency_key=revision_idempotency_key,
        ),
    )
    if (
        delivered.plan_id != str(replacement["plan_id"])
        or delivered.plan_digest != str(replacement["plan_digest"])
        or delivered.state != "PLAN_ACCEPTED"
        or delivered.submission_authority is not False
    ):
        raise RuntimeError(
            "Runtime replacement Application Plan acknowledgement is not exact."
        )

    # After Apply has atomically accepted the replacement, promote only the
    # explicitly user-confirmed NORMAL runtime facts into Answer Vault. Candidate
    # values never enter Teach MUNSHI; Teach remains mechanics-only.
    requirements = {
        str(item.get("question_key") or ""): dict(item)
        for item in list(replacement["inputs"].get("answer_requirements") or [])
        if isinstance(item, Mapping)
    }
    promoted = 0
    for raw in answer_resolutions:
        key = str(raw.get("question_key") or "")
        requirement = requirements.get(key)
        if not requirement:
            continue
        family = str(requirement.get("question_family") or "").strip().casefold()
        if family not in answer_brain_v2.NORMAL_QUESTION_FAMILIES:
            continue
        answer_brain_v2.save_answer(
            question_family=family,
            canonical_answer=str(raw.get("canonical_answer") or ""),
            source="user",
            user_confirmed=True,
            confidence=1.0,
            autofill_allowed=True,
            conditions=dict(requirement.get("conditions") or {}),
            question_key=key,
        )
        promoted += 1

    return {
        "success": True,
        "prior_plan_id": str(prior_plan_id),
        "replacement_plan_id": str(replacement["plan_id"]),
        "replacement_plan_digest": str(replacement["plan_digest"]),
        "handoff_id": delivered.handoff_id,
        "state": delivered.state,
        "replayed": delivered.replayed,
        "answer_vault_promotions": promoted,
        "submission_authority": False,
    }
