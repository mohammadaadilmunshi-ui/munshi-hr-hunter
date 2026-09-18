"""Phase 6 Opportunity Intelligence V3 persistence hardening.

V3 preserves the deterministic evidence/policy scoring of V2, then re-reads all
binding inputs immediately before persistence. If Candidate Truth, the job,
preferences, or policy changed during evaluation, persistence fails closed.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app import opportunity_intelligence_v2 as v2
from app.database import get_connection
from app.phase67_common import sha256_json
from app.tenant_foundation import current_owner

OPPORTUNITY_INTELLIGENCE_VERSION = "opportunity-intelligence-v3-persist-revalidated"
_BINDING_FIELDS = (
    "tenant_id",
    "user_id",
    "source_extraction_id",
    "profile_revision",
    "profile_digest",
    "preferences_sha256",
    "policy_sha256",
    "job_snapshot_sha256",
)


def ensure_schema(connection=None) -> None:
    v2.ensure_schema(connection)


def opportunity_intelligence_enabled() -> bool:
    return v2.opportunity_intelligence_enabled()


def _binding_changed(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return [field for field in _BINDING_FIELDS if before.get(field) != after.get(field)]


def evaluate_job(job_id: int, *, persist: bool = True) -> dict[str, Any]:
    result, binding = v2._evaluation_core(int(job_id))
    strengthened = {
        **result,
        "version": OPPORTUNITY_INTELLIGENCE_VERSION,
        "persistence_revalidated": False,
    }
    if not persist:
        return strengthened

    _current_result, current_binding = v2._evaluation_core(int(job_id))
    changed = _binding_changed(binding, current_binding)
    if changed:
        raise RuntimeError(
            "Opportunity inputs changed during evaluation; recompute before persistence: "
            + ", ".join(changed)
        )

    evaluation_id = f"opportunity-eval-{uuid4()}"
    result_with_id = {
        **strengthened,
        "evaluation_id": evaluation_id,
        "persistence_revalidated": True,
    }
    result_sha = sha256_json(result_with_id)
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if owner.tenant_id != binding["tenant_id"] or owner.user_id != binding["user_id"]:
            raise ValueError("Opportunity evaluation owner changed before persistence.")
        connection.execute(
            """INSERT INTO opportunity_intelligence_evaluations(
                   evaluation_id,tenant_id,user_id,job_id,source_extraction_id,
                   profile_revision,profile_digest,preferences_sha256,policy_sha256,
                   job_snapshot_sha256,result_sha256,result_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                evaluation_id,
                owner.tenant_id,
                owner.user_id,
                int(job_id),
                binding["source_extraction_id"],
                binding["profile_revision"],
                binding["profile_digest"],
                binding["preferences_sha256"],
                binding["policy_sha256"],
                binding["job_snapshot_sha256"],
                result_sha,
                json.dumps(result_with_id, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {**result_with_id, "result_sha256": result_sha}


def get_evaluation(evaluation_id: str) -> dict[str, Any]:
    return v2.get_evaluation(evaluation_id)


def evaluation_freshness(evaluation_id: str) -> dict[str, Any]:
    return v2.evaluation_freshness(evaluation_id)


SCORE_WEIGHTS = v2.SCORE_WEIGHTS
MIN_SCORE_CONFIDENCE = v2.MIN_SCORE_CONFIDENCE
