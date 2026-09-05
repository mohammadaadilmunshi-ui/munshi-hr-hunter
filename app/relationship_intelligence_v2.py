"""Strengthened Phase 7 Relationship Intelligence.

V2 scores only already-stored, tenant-owned relationship evidence and can refine an
existing Phase 6 advisory pursuit strategy. It never discovers people, guesses an
email, sends outreach, accesses Gmail, controls a browser, or submits an application.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping
from uuid import uuid4

from app import opportunity_intelligence_v2 as opportunity_v2
from app import relationship_intelligence as v1
from app.database import get_connection
from app.phase67_common import canonical_text, safe_owned_job_snapshot, sha256_json
from app.tenant_foundation import current_owner

RELATIONSHIP_INTELLIGENCE_VERSION = "relationship-intelligence-v2-evidence-scored"
MIN_CONTACT_SCORE_CONFIDENCE = 0.60
SOURCE_RELIABILITY = {
    "user_supplied": 1.0,
    "public_company": 0.95,
    "public_profile": 0.85,
    "existing_contact_finder": 0.65,
}
CONTACT_TYPE_RELEVANCE = {
    "hiring_manager": 1.0,
    "department_lead": 0.95,
    "team_lead": 0.90,
    "recruiter": 0.90,
    "ta_partner": 0.90,
    "hrbp": 0.80,
    "mutual_connection": 0.85,
    "alumni": 0.80,
    "relevant_employee": 0.65,
}

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS relationship_strategy_snapshots (
        strategy_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        opportunity_evaluation_id TEXT,
        opportunity_result_sha256 TEXT,
        job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
        relationship_evidence_sha256 TEXT NOT NULL CHECK(length(relationship_evidence_sha256)=64),
        result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64),
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_relationship_strategy_owner_job
       ON relationship_strategy_snapshots(tenant_id,user_id,job_id,created_at DESC);""",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        v1.ensure_schema(connection)
        opportunity_v2.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def relationship_intelligence_enabled() -> bool:
    return v1.relationship_intelligence_enabled()


def _normal(value: Any) -> str:
    return canonical_text(value).casefold()


def _company_alignment(contact: Mapping[str, Any], job: Mapping[str, Any]) -> float | None:
    contact_company = _normal(contact.get("company_name"))
    job_company = _normal(job.get("company_name"))
    if not contact_company or not job_company:
        return None
    if contact_company == job_company or contact_company in job_company or job_company in contact_company:
        return 1.0
    return 0.0


def _evidence_strength(evidence: list[Mapping[str, Any]]) -> tuple[float | None, dict[str, Any]]:
    if not evidence:
        return None, {"evidence_count": 0, "sources": [], "max_confidence": None}
    confidences = [float(item["confidence"]) for item in evidence]
    # Give strongest direct evidence meaningful weight without allowing one item
    # to erase the rest of the evidence trail.
    average = sum(confidences) / len(confidences)
    strongest = max(confidences)
    value = (average * 0.6) + (strongest * 0.4)
    return max(0.0, min(1.0, value)), {
        "evidence_count": len(evidence),
        "sources": sorted({str(item["source"]) for item in evidence}),
        "max_confidence": round(strongest, 4),
        "evidence_ids": sorted(str(item["evidence_id"]) for item in evidence),
    }


def _contact_score(contact: Mapping[str, Any], evidence: list[Mapping[str, Any]], job: Mapping[str, Any]) -> dict[str, Any]:
    evidence_value, evidence_meta = _evidence_strength(evidence)
    components: list[dict[str, Any]] = []
    unknowns: list[str] = []

    raw_components: list[tuple[str, float, float | None, dict[str, Any]]] = [
        (
            "stored_contact_confidence",
            25.0,
            float(contact["confidence"]),
            {"source_field": "relationship_contacts.confidence"},
        ),
        (
            "source_reliability",
            15.0,
            SOURCE_RELIABILITY.get(str(contact["source"])),
            {"source": str(contact["source"])},
        ),
        (
            "relationship_evidence_strength",
            30.0,
            evidence_value,
            evidence_meta,
        ),
        (
            "company_alignment",
            15.0,
            _company_alignment(contact, job),
            {
                "contact_company": canonical_text(contact.get("company_name")),
                "job_company": canonical_text(job.get("company_name")),
            },
        ),
        (
            "contact_type_relevance",
            15.0,
            CONTACT_TYPE_RELEVANCE.get(str(contact["contact_type"])),
            {"contact_type": str(contact["contact_type"])},
        ),
    ]

    for name, weight, value, meta in raw_components:
        if value is None:
            unknowns.append(name)
            continue
        bounded = max(0.0, min(1.0, float(value)))
        components.append(
            {
                "input": name,
                "weight": weight,
                "value": round(bounded, 4),
                "weighted_points": round(bounded * weight, 4),
                "evidence": meta,
            }
        )

    known_weight = sum(float(item["weight"]) for item in components)
    points = sum(float(item["weighted_points"]) for item in components)
    score = round((points / known_weight) * 100.0, 2) if known_weight else None
    score_confidence = round(known_weight / 100.0, 4)

    # Free-text `relevance` and email availability intentionally do not contribute
    # to score. They are display/context fields, not evidence of relationship value.
    contact_type = str(contact["contact_type"])
    if evidence_value is None or score is None or score_confidence < MIN_CONTACT_SCORE_CONFIDENCE:
        action = "review"
    elif score >= 80 and contact_type in {"mutual_connection", "alumni"}:
        action = "request_introduction"
    elif score >= 70:
        action = "connect"
    elif score >= 45:
        action = "review"
    else:
        action = "no_action"

    return {
        "contact_id": str(contact["contact_id"]),
        "display_name": str(contact["display_name"]),
        "company_name": contact.get("company_name"),
        "title": contact.get("title"),
        "contact_type": contact_type,
        "relationship_score": score,
        "score_confidence": score_confidence,
        "score_explanation": components,
        "unknowns": sorted(set(unknowns)),
        "recommended_action": action,
        "contact_information_state": v1.contact_information_state(str(contact["contact_id"])),
        "automatic_outreach_executed": False,
    }


def _relationship_state(job_id: int) -> tuple[dict[str, Any], str]:
    job_snapshot = safe_owned_job_snapshot(job_id)
    contacts = v1.contacts_for_job(job_id=int(job_id))
    ranked: list[dict[str, Any]] = []
    digest_records: list[dict[str, Any]] = []
    for contact in contacts:
        evidence = v1.contact_evidence(str(contact["contact_id"]))
        ranked.append(_contact_score(contact, evidence, job_snapshot["job"]))
        digest_records.append(
            {
                "contact_id": str(contact["contact_id"]),
                "company_name": canonical_text(contact.get("company_name")),
                "title": canonical_text(contact.get("title")),
                "contact_type": str(contact["contact_type"]),
                "confidence": float(contact["confidence"]),
                "source": str(contact["source"]),
                "email_provenance": str(contact["email_provenance"]),
                "evidence": sorted(
                    (
                        {
                            "evidence_id": str(item["evidence_id"]),
                            "source": str(item["source"]),
                            "evidence_url": canonical_text(item.get("evidence_url")),
                            "evidence_summary": canonical_text(item.get("evidence_summary")),
                            "confidence": float(item["confidence"]),
                        }
                        for item in evidence
                    ),
                    key=lambda item: item["evidence_id"],
                ),
            }
        )
    ranked.sort(
        key=lambda item: (
            -(float(item["relationship_score"]) if item["relationship_score"] is not None else -1.0),
            -float(item["score_confidence"]),
            item["display_name"].casefold(),
            item["contact_id"],
        )
    )
    relationship_digest = sha256_json(sorted(digest_records, key=lambda item: item["contact_id"]))
    return {
        "job_snapshot": job_snapshot,
        "ranked_contacts": ranked,
    }, relationship_digest


def _opportunity_context(job_id: int, evaluation_id: str | None) -> dict[str, Any] | None:
    if evaluation_id is None:
        return None
    evaluation = opportunity_v2.get_evaluation(evaluation_id)
    if int(evaluation["job_id"]) != int(job_id):
        raise ValueError("Opportunity evaluation belongs to a different job.")
    freshness = opportunity_v2.evaluation_freshness(evaluation_id)
    return {
        "evaluation_id": evaluation_id,
        "result_sha256": evaluation["result_sha256"],
        "fresh": bool(freshness["fresh"]),
        "changed_bindings": freshness["changed_bindings"],
        "status": evaluation["status"],
        "pursuit_state": evaluation["pursuit_strategy"]["pursuit_state"],
        "hard_failures": list(evaluation.get("hard_failures") or []),
        "unknowns": list(evaluation.get("unknowns") or []),
    }


def _combined_strategy(ranked: list[dict[str, Any]], opportunity: dict[str, Any] | None) -> dict[str, Any]:
    top = ranked[0] if ranked else None
    top_score = top.get("relationship_score") if top else None
    top_action = top.get("recommended_action") if top else "no_action"

    if opportunity is None:
        return {
            "base_pursuit_state": None,
            "combined_pursuit_state": None,
            "networking_action": top_action,
            "reason": "Relationship-only advisory; no Phase 6 opportunity evaluation was supplied.",
            "automatic_actions_executed": False,
        }

    base = str(opportunity["pursuit_state"])
    if not opportunity["fresh"]:
        return {
            "base_pursuit_state": base,
            "combined_pursuit_state": "WATCH",
            "networking_action": "review",
            "reason": "The linked Phase 6 evaluation is stale and must be recomputed before refinement.",
            "automatic_actions_executed": False,
        }
    if opportunity["hard_failures"] or base == "IGNORE":
        return {
            "base_pursuit_state": base,
            "combined_pursuit_state": "IGNORE",
            "networking_action": "no_action",
            "reason": "Relationship evidence cannot override a Phase 6 hard-policy failure.",
            "automatic_actions_executed": False,
        }
    if opportunity["unknowns"] or base == "WATCH":
        return {
            "base_pursuit_state": base,
            "combined_pursuit_state": "WATCH",
            "networking_action": "review" if top else "no_action",
            "reason": "Unresolved Phase 6 evidence remains authoritative; relationship evidence cannot upgrade it.",
            "automatic_actions_executed": False,
        }

    combined = base
    networking_action = top_action
    if base == "APPLY" and top_score is not None and float(top_score) >= 70:
        combined = "NETWORK + APPLY"
    return {
        "base_pursuit_state": base,
        "combined_pursuit_state": combined,
        "networking_action": networking_action,
        "reason": (
            "Relationship evidence refined networking priority without changing Phase 6 hard-policy authority."
            if top
            else "No linked relationship evidence is currently available."
        ),
        "automatic_actions_executed": False,
    }


def strategy_for_job(
    job_id: int,
    *,
    opportunity_evaluation_id: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    if not relationship_intelligence_enabled():
        raise RuntimeError("Relationship intelligence is disabled.")
    state, relationship_digest = _relationship_state(job_id)
    opportunity = _opportunity_context(job_id, opportunity_evaluation_id)
    combined = _combined_strategy(state["ranked_contacts"], opportunity)
    result = {
        "version": RELATIONSHIP_INTELLIGENCE_VERSION,
        "job_id": int(job_id),
        "job_snapshot_sha256": state["job_snapshot"]["job_snapshot_sha256"],
        "relationship_evidence_sha256": relationship_digest,
        "opportunity_context": opportunity,
        "ranked_contacts": state["ranked_contacts"],
        "strategy": combined,
        "advisory_only": True,
        "automatic_outreach_executed": False,
    }
    if not persist:
        return result

    strategy_id = f"relationship-strategy-{uuid4()}"
    persisted = {**result, "strategy_id": strategy_id}
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
                int(job_id),
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
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM relationship_strategy_snapshots
               WHERE strategy_id=? AND tenant_id=? AND user_id=?""",
            (str(strategy_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Relationship strategy is unavailable to the current user.")
        result = json.loads(str(row["result_json"]))
        if sha256_json(result) != row["result_sha256"]:
            raise RuntimeError("Stored relationship strategy digest does not match its payload.")
        result["result_sha256"] = row["result_sha256"]
        return result
    finally:
        connection.close()


def strategy_freshness(strategy_id: str) -> dict[str, Any]:
    stored = get_strategy(strategy_id)
    current_state, current_relationship_digest = _relationship_state(int(stored["job_id"]))
    changed: list[str] = []
    if stored["job_snapshot_sha256"] != current_state["job_snapshot"]["job_snapshot_sha256"]:
        changed.append("job_snapshot_sha256")
    if stored["relationship_evidence_sha256"] != current_relationship_digest:
        changed.append("relationship_evidence_sha256")
    opportunity = stored.get("opportunity_context")
    if opportunity:
        freshness = opportunity_v2.evaluation_freshness(str(opportunity["evaluation_id"]))
        if not freshness["fresh"]:
            changed.append("opportunity_evaluation")
        current_eval = opportunity_v2.get_evaluation(str(opportunity["evaluation_id"]))
        if current_eval["result_sha256"] != opportunity.get("result_sha256"):
            changed.append("opportunity_result_sha256")
    return {
        "strategy_id": strategy_id,
        "fresh": not changed,
        "changed_bindings": sorted(set(changed)),
        "automatic_actions_executed": False,
    }
