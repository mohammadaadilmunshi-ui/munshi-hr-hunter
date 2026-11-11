"""Phase 7 Relationship Intelligence V3 hardening.

V3 preserves V2 evidence scoring but prevents incomplete relationship evidence
from upgrading an APPLY strategy into NETWORK + APPLY. It also revalidates job,
relationship, and linked Phase 6 inputs immediately before persistence.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app import relationship_intelligence_v2 as v2
from app.database import get_connection
from app.phase67_common import sha256_json
from app.tenant_foundation import current_owner

RELATIONSHIP_INTELLIGENCE_VERSION = "relationship-intelligence-v3-evidence-complete"


def ensure_schema(connection=None) -> None:
    v2.ensure_schema(connection)


def relationship_intelligence_enabled() -> bool:
    return v2.relationship_intelligence_enabled()


def _qualified_networking_upgrade(top: dict[str, Any] | None) -> bool:
    if not top:
        return False
    score = top.get("relationship_score")
    return bool(
        score is not None
        and float(score) >= 70.0
        and float(top.get("score_confidence") or 0.0) >= v2.MIN_CONTACT_SCORE_CONFIDENCE
        and not list(top.get("unknowns") or [])
        and str(top.get("recommended_action") or "") in {"connect", "request_introduction"}
    )


def _combined_strategy_hardened(
    ranked: list[dict[str, Any]],
    opportunity: dict[str, Any] | None,
) -> dict[str, Any]:
    combined = v2._combined_strategy(ranked, opportunity)
    top = ranked[0] if ranked else None
    if (
        combined.get("base_pursuit_state") == "APPLY"
        and combined.get("combined_pursuit_state") == "NETWORK + APPLY"
        and not _qualified_networking_upgrade(top)
    ):
        return {
            "base_pursuit_state": "APPLY",
            "combined_pursuit_state": "APPLY",
            "networking_action": "review" if top else "no_action",
            "reason": (
                "Relationship evidence is not complete enough to upgrade the Phase 6 APPLY recommendation."
            ),
            "automatic_actions_executed": False,
        }
    return combined


def strategy_for_job(
    job_id: int,
    *,
    opportunity_evaluation_id: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    if not relationship_intelligence_enabled():
        raise RuntimeError("Relationship intelligence is disabled.")
    resolved_job_id = int(job_id)
    state, relationship_digest = v2._relationship_state(resolved_job_id)
    opportunity = v2._opportunity_context(resolved_job_id, opportunity_evaluation_id)
    combined = _combined_strategy_hardened(state["ranked_contacts"], opportunity)
    result = {
        "version": RELATIONSHIP_INTELLIGENCE_VERSION,
        "job_id": resolved_job_id,
        "job_snapshot_sha256": state["job_snapshot"]["job_snapshot_sha256"],
        "relationship_evidence_sha256": relationship_digest,
        "opportunity_context": opportunity,
        "ranked_contacts": state["ranked_contacts"],
        "strategy": combined,
        "persistence_revalidated": False,
        "advisory_only": True,
        "automatic_outreach_executed": False,
    }
    if not persist:
        return result

    current_state, current_relationship_digest = v2._relationship_state(resolved_job_id)
    changed: list[str] = []
    if current_state["job_snapshot"]["job_snapshot_sha256"] != state["job_snapshot"]["job_snapshot_sha256"]:
        changed.append("job_snapshot_sha256")
    if current_relationship_digest != relationship_digest:
        changed.append("relationship_evidence_sha256")
    if opportunity_evaluation_id is not None:
        current_opportunity = v2._opportunity_context(resolved_job_id, opportunity_evaluation_id)
        if current_opportunity != opportunity:
            changed.append("opportunity_context")
    if changed:
        raise RuntimeError(
            "Relationship inputs changed during strategy calculation; recompute before persistence: "
            + ", ".join(changed)
        )

    strategy_id = f"relationship-strategy-{uuid4()}"
    persisted = {
        **result,
        "strategy_id": strategy_id,
        "persistence_revalidated": True,
    }
    result_sha = sha256_json(persisted)
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        snapshot = state["job_snapshot"]
        if owner.tenant_id != snapshot["tenant_id"] or owner.user_id != snapshot["user_id"]:
            raise ValueError("Relationship strategy owner changed before persistence.")
        connection.execute(
            """INSERT INTO relationship_strategy_snapshots(
                   strategy_id,tenant_id,user_id,job_id,opportunity_evaluation_id,
                   opportunity_result_sha256,job_snapshot_sha256,
                   relationship_evidence_sha256,result_sha256,result_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                strategy_id,
                owner.tenant_id,
                owner.user_id,
                resolved_job_id,
                opportunity_evaluation_id,
                opportunity.get("result_sha256") if opportunity else None,
                snapshot["job_snapshot_sha256"],
                relationship_digest,
                result_sha,
                json.dumps(persisted, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {**persisted, "result_sha256": result_sha}


def get_strategy(strategy_id: str) -> dict[str, Any]:
    return v2.get_strategy(strategy_id)


def strategy_freshness(strategy_id: str) -> dict[str, Any]:
    return v2.strategy_freshness(strategy_id)
