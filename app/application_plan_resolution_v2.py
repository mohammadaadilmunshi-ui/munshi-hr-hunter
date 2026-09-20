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



def promote_runtime_resolutions_after_verified_submission(
    *,
    plan_id: str,
) -> dict[str, Any]:
    """Promote runtime NORMAL facts only after verified employer submission.

    Answer Vault participates in preflight freshness, so promoting these facts
    while the browser session is active would invalidate the immutable plan that
    is currently executing.  Candidate values never enter Teach MUNSHI.
    """
    current_plan_id = str(plan_id)
    promoted = 0
    skipped_candidate_truth = 0
    skipped_existing = 0
    conflicts: list[str] = []
    visited: set[str] = set()

    while current_plan_id and current_plan_id not in visited:
        visited.add(current_plan_id)
        connection = application_plan_v2.get_connection()
        try:
            application_plan_v2.ensure_schema(connection)
            owner = application_plan_v2.current_owner(connection)
            row = connection.execute(
                """SELECT * FROM application_plan_revisions_v2
                   WHERE tenant_id=? AND user_id=? AND replacement_plan_id=?""",
                (owner.tenant_id, owner.user_id, current_plan_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            break

        revision = dict(row)
        replacement = application_plan_v2.get_application_plan(current_plan_id)
        requirements = {
            str(item.get("question_key") or ""): dict(item)
            for item in list(replacement["inputs"].get("answer_requirements") or [])
            if isinstance(item, Mapping)
        }
        payload = application_plan_v2.json.loads(str(revision["resolutions_json"]))
        resolutions = list(payload.get("resolutions") or []) if isinstance(payload, Mapping) else []

        for raw in resolutions:
            if not isinstance(raw, Mapping):
                continue
            key = str(raw.get("question_key") or "")
            value = str(raw.get("canonical_answer") or "").strip()
            requirement = requirements.get(key)
            if not requirement or not value:
                continue
            family = str(requirement.get("question_family") or "").strip().casefold()
            if family not in answer_brain_v2.NORMAL_QUESTION_FAMILIES:
                continue

            resolved = answer_brain_v2.resolve_answer(
                question_family=family,
                conditions=dict(requirement.get("conditions") or {}),
                profile_fact_key=requirement.get("profile_fact_key"),
                question_key=key,
            )
            if isinstance(resolved, Mapping) and resolved.get("status") == "ANSWERED":
                answer = dict(resolved.get("answer") or {})
                existing_value = str(answer.get("canonical_answer") or "").strip()
                source = str(answer.get("source") or resolved.get("resolution") or "")
                if existing_value == value:
                    if source in {"profile_evidence", "candidate_truth_projection"}:
                        skipped_candidate_truth += 1
                    else:
                        skipped_existing += 1
                    continue
                if source in {"profile_evidence", "candidate_truth_projection"}:
                    conflicts.append(key)
                    continue

            answer_brain_v2.save_answer(
                question_family=family,
                canonical_answer=value,
                source="user",
                user_confirmed=True,
                confidence=1.0,
                autofill_allowed=True,
                conditions=dict(requirement.get("conditions") or {}),
                question_key=key,
            )
            promoted += 1

        current_plan_id = str(revision["prior_plan_id"])

    return {
        "promoted": promoted,
        "skipped_candidate_truth": skipped_candidate_truth,
        "skipped_existing": skipped_existing,
        "conflicts": sorted(set(conflicts)),
        "teach_munshi_raw_value_writes": 0,
    }
