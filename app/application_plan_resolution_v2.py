from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from app import application_plan_delivery_v2, application_plan_v2


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
    return {
        "success": True,
        "prior_plan_id": str(prior_plan_id),
        "replacement_plan_id": str(replacement["plan_id"]),
        "replacement_plan_digest": str(replacement["plan_digest"]),
        "handoff_id": delivered.handoff_id,
        "state": delivered.state,
        "replayed": delivered.replayed,
        "submission_authority": False,
    }
