"""Stage B — evidence-bound Resume Tailoring Plan V1.

Builds a deterministic, content-addressed plan from one exact JD Intelligence
snapshot and one exact Candidate ↔ JD match snapshot. The plan is an inert writer
input: it does not generate a resume, invoke a model, mutate n8n, browse an ATS,
access Gmail, or submit an application.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from app import jd_intelligence_v1 as jd
from app import jd_requirement_match_v1 as matcher
from app.database import get_connection
from app.phase67_common import canonical_text, sha256_json
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema

PLAN_VERSION = "stage-b-resume-tailoring-plan-v1"
SUBMISSION_AUTHORITY = False

_SUPPORTED = frozenset({"DIRECT", "STRONG_TRANSFERABLE", "PARTIAL"})
_FORBIDDEN = frozenset({"NO_EVIDENCE", "CONFLICT", "UNKNOWN"})
_PRIORITY_RANK = {
    "MUST_HAVE": 0,
    "CORE_RESPONSIBILITY": 1,
    "PREFERRED": 2,
    "BONUS": 3,
    "CONTEXT": 4,
    "UNKNOWN": 5,
}
_MATCH_RANK = {
    "DIRECT": 0,
    "STRONG_TRANSFERABLE": 1,
    "PARTIAL": 2,
    "NO_EVIDENCE": 3,
    "CONFLICT": 4,
    "UNKNOWN": 5,
}

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS jd_resume_tailoring_plans (
        plan_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
        jd_snapshot_id TEXT NOT NULL,
        jd_snapshot_digest TEXT NOT NULL CHECK(length(jd_snapshot_digest)=64),
        match_snapshot_id TEXT NOT NULL,
        match_digest TEXT NOT NULL CHECK(length(match_digest)=64),
        source_extraction_id TEXT NOT NULL,
        profile_revision INTEGER NOT NULL CHECK(profile_revision >= 1),
        profile_digest TEXT NOT NULL CHECK(length(profile_digest)=64),
        plan_version TEXT NOT NULL,
        plan_digest TEXT NOT NULL CHECK(length(plan_digest)=64),
        plan_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
        FOREIGN KEY(jd_snapshot_id) REFERENCES jd_intelligence_snapshots(snapshot_id) ON DELETE RESTRICT,
        FOREIGN KEY(match_snapshot_id)
          REFERENCES candidate_job_match_snapshots(match_snapshot_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        UNIQUE(tenant_id,user_id,match_digest,plan_version),
        UNIQUE(tenant_id,user_id,plan_digest)
    );""",
    """CREATE INDEX IF NOT EXISTS idx_jd_resume_plans_owner_job
       ON jd_resume_tailoring_plans(tenant_id,user_id,job_id,created_at DESC);""",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        matcher.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _PRIORITY_RANK.get(str(item.get("priority")), 99),
        _MATCH_RANK.get(str(item.get("match_status")), 99),
        -float(item.get("match_score") or 0.0),
        str(item.get("requirement_id") or ""),
    )


def _requirement_refs(
    jd_snapshot: Mapping[str, Any],
    match_snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    requirements = {
        str(item["requirement_id"]): item
        for item in jd_snapshot.get("requirements") or []
        if isinstance(item, Mapping)
    }
    refs: list[dict[str, Any]] = []
    for match in match_snapshot.get("requirement_matches") or []:
        if not isinstance(match, Mapping):
            continue
        requirement_id = str(match.get("requirement_id") or "")
        requirement = requirements.get(requirement_id)
        if requirement is None:
            raise ValueError(
                f"Candidate ↔ JD match references unknown requirement {requirement_id}."
            )
        refs.append(
            {
                "requirement_id": requirement_id,
                "type": str(requirement["type"]),
                "priority": str(requirement["priority"]),
                "exact_text": str(requirement["exact_text"]),
                "source_field": str(requirement["source_field"]),
                "match_status": str(match["match_status"]),
                "match_score": match.get("match_score"),
                "evidence_ids": list(match.get("evidence_ids") or []),
                "evidence_keys": list(match.get("evidence_keys") or []),
            }
        )
    return sorted(refs, key=_sort_key)


def _supported_terms(
    jd_snapshot: Mapping[str, Any],
    refs: list[dict[str, Any]],
) -> list[str]:
    supported_text = " \n".join(
        ref["exact_text"].casefold()
        for ref in refs
        if ref["match_status"] in _SUPPORTED
    )
    values: list[str] = []
    for raw in jd_snapshot.get("keywords") or []:
        term = canonical_text(raw)
        if not term or term.casefold() not in supported_text:
            continue
        if term.casefold() not in {value.casefold() for value in values}:
            values.append(term)
        if len(values) >= 50:
            break
    return values


def build_plan(
    *,
    jd_snapshot: Mapping[str, Any],
    match_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    jd_value = jd.validate_snapshot(jd_snapshot)
    match_value = matcher.validate_match_snapshot(match_snapshot)
    if (
        int(jd_value["job_id"]) != int(match_value["job_id"])
        or str(jd_value["snapshot_id"]) != str(match_value["jd_snapshot_id"])
        or str(jd_value["snapshot_digest"]) != str(match_value["jd_snapshot_digest"])
        or str(jd_value["job_snapshot_sha256"])
        != str(match_value["job_snapshot_sha256"])
    ):
        raise ValueError("Resume Tailoring Plan inputs are not bound to the same JD state.")

    refs = _requirement_refs(jd_value, match_value)
    supported = [ref for ref in refs if ref["match_status"] in _SUPPORTED]
    forbidden = [ref for ref in refs if ref["match_status"] in _FORBIDDEN]

    summary_ids = [
        ref["requirement_id"]
        for ref in supported
        if ref["priority"] in {"MUST_HAVE", "CORE_RESPONSIBILITY"}
        and ref["type"]
        not in {
            "WORK_AUTHORIZATION",
            "SPONSORSHIP",
            "CITIZENSHIP",
            "CLEARANCE",
            "COMPENSATION",
            "LOCATION",
            "WORKPLACE",
            "EMPLOYMENT_TYPE",
        }
    ][:6]
    skill_ids = [
        ref["requirement_id"]
        for ref in supported
        if ref["type"] in {"SKILL", "TOOL", "DOMAIN_KNOWLEDGE", "PROCESS"}
    ][:18]
    experience_ids = [
        ref["requirement_id"]
        for ref in supported
        if ref["type"]
        in {
            "RESPONSIBILITY",
            "EXPERIENCE",
            "PROCESS",
            "DOMAIN_KNOWLEDGE",
            "SKILL",
            "TOOL",
        }
    ][:16]
    preferred_ids = [
        ref["requirement_id"]
        for ref in supported
        if ref["priority"] in {"PREFERRED", "BONUS"}
    ][:12]

    payload = {
        "contract_version": PLAN_VERSION,
        "authority": "munshi-hr-hunter",
        "tenant_id": str(match_value["tenant_id"]),
        "user_id": str(match_value["user_id"]),
        "job_id": int(match_value["job_id"]),
        "job_snapshot_sha256": str(match_value["job_snapshot_sha256"]),
        "jd_snapshot_id": str(match_value["jd_snapshot_id"]),
        "jd_snapshot_digest": str(match_value["jd_snapshot_digest"]),
        "match_snapshot_id": str(match_value["match_snapshot_id"]),
        "match_digest": str(match_value["match_digest"]),
        "candidate_truth_binding": dict(match_value["candidate_truth_binding"]),
        "requirement_refs": refs,
        "summary_priority_requirement_ids": summary_ids,
        "skills_priority_requirement_ids": skill_ids,
        "experience_priority_requirement_ids": experience_ids,
        "preferred_requirement_ids": preferred_ids,
        "do_not_claim_requirement_ids": [
            ref["requirement_id"] for ref in forbidden
        ],
        "unsupported_must_have_requirement_ids": list(
            match_value.get("unsupported_must_have_requirement_ids") or []
        ),
        "supported_jd_terms": _supported_terms(jd_value, refs),
        "one_page_retention_order": [
            ref["requirement_id"] for ref in supported
        ],
        "diagnostics": {
            "supported_requirement_count": len(supported),
            "do_not_claim_requirement_count": len(forbidden),
            "evidence_coverage_score": match_value.get("evidence_coverage_score"),
            "score_confidence": match_value.get("score_confidence"),
            "automatic_actions_executed": False,
        },
        "mutation_authority": False,
        "submission_authority": False,
    }
    digest = sha256_json(payload)
    return {
        **payload,
        "plan_id": f"jd-resume-plan-{digest[:24]}",
        "plan_digest": digest,
        "generated_at": _now(),
    }


def _digest_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in plan.items()
        if key not in {"plan_id", "plan_digest", "generated_at"}
    }


def validate_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(plan)
    if value.get("contract_version") != PLAN_VERSION:
        raise ValueError("Unsupported Resume Tailoring Plan version.")
    if value.get("submission_authority") is not False:
        raise ValueError("Resume Tailoring Plan cannot have submission authority.")
    refs = value.get("requirement_refs")
    if not isinstance(refs, list):
        raise ValueError("Resume Tailoring Plan requirement refs must be a list.")
    ref_ids = {
        str(item.get("requirement_id"))
        for item in refs
        if isinstance(item, Mapping)
    }
    for key in (
        "summary_priority_requirement_ids",
        "skills_priority_requirement_ids",
        "experience_priority_requirement_ids",
        "preferred_requirement_ids",
        "do_not_claim_requirement_ids",
        "one_page_retention_order",
    ):
        values = value.get(key)
        if not isinstance(values, list) or any(str(item) not in ref_ids for item in values):
            raise ValueError(f"Resume Tailoring Plan {key} contains an unknown requirement.")
    digest = sha256_json(_digest_payload(value))
    if value.get("plan_digest") and str(value["plan_digest"]) != digest:
        raise ValueError("Resume Tailoring Plan digest mismatch.")
    return value


def plan_for_job(job_id: int, *, persist: bool = True) -> dict[str, Any]:
    match_snapshot = matcher.match_job(int(job_id), persist=persist)
    if persist:
        jd_snapshot = jd.get_snapshot(match_snapshot["jd_snapshot_id"])
    else:
        jd_snapshot = jd.analyze_job(int(job_id), persist=False)
        if jd_snapshot["snapshot_digest"] != match_snapshot["jd_snapshot_digest"]:
            raise RuntimeError("JD changed while building the Resume Tailoring Plan.")

    plan = build_plan(jd_snapshot=jd_snapshot, match_snapshot=match_snapshot)
    validate_plan(plan)
    if not persist:
        return plan

    if not matcher.match_freshness(match_snapshot["match_snapshot_id"])["fresh"]:
        raise RuntimeError("Candidate ↔ JD match became stale before plan persistence.")

    binding = plan["candidate_truth_binding"]
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        existing = connection.execute(
            """SELECT plan_json FROM jd_resume_tailoring_plans
               WHERE tenant_id=? AND user_id=? AND match_digest=? AND plan_version=?""",
            (owner.tenant_id, owner.user_id, plan["match_digest"], PLAN_VERSION),
        ).fetchone()
        if existing is not None:
            return validate_plan(json.loads(existing["plan_json"]))

        connection.execute(
            """INSERT INTO jd_resume_tailoring_plans(
                   plan_id,tenant_id,user_id,job_id,job_snapshot_sha256,
                   jd_snapshot_id,jd_snapshot_digest,match_snapshot_id,match_digest,
                   source_extraction_id,profile_revision,profile_digest,
                   plan_version,plan_digest,plan_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                plan["plan_id"],
                owner.tenant_id,
                owner.user_id,
                int(job_id),
                plan["job_snapshot_sha256"],
                plan["jd_snapshot_id"],
                plan["jd_snapshot_digest"],
                plan["match_snapshot_id"],
                plan["match_digest"],
                binding["source_extraction_id"],
                binding["profile_revision"],
                binding["profile_digest"],
                PLAN_VERSION,
                plan["plan_digest"],
                json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            ),
        )
        connection.commit()
        return plan
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_plan(plan_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT plan_json FROM jd_resume_tailoring_plans
               WHERE plan_id=? AND tenant_id=? AND user_id=?""",
            (str(plan_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Resume Tailoring Plan is unavailable.")
        return validate_plan(json.loads(row["plan_json"]))
    finally:
        connection.close()


def plan_freshness(plan_id: str) -> dict[str, Any]:
    plan = get_plan(plan_id)
    match_state = matcher.match_freshness(plan["match_snapshot_id"])
    return {
        "plan_id": plan_id,
        "job_id": int(plan["job_id"]),
        "fresh": match_state["fresh"] is True,
        "match_snapshot_id": plan["match_snapshot_id"],
        "match_digest": plan["match_digest"],
        "job_snapshot_sha256": plan["job_snapshot_sha256"],
        "candidate_truth_binding": plan["candidate_truth_binding"],
    }


def writer_context(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact, non-authoritative context for a future V5 writer."""
    value = validate_plan(plan)
    refs = {
        ref["requirement_id"]: ref
        for ref in value["requirement_refs"]
    }
    selected_ids = list(
        dict.fromkeys(
            value["summary_priority_requirement_ids"]
            + value["skills_priority_requirement_ids"]
            + value["experience_priority_requirement_ids"]
            + value["preferred_requirement_ids"]
        )
    )
    selected = [
        {
            "requirement_id": requirement_id,
            "type": refs[requirement_id]["type"],
            "priority": refs[requirement_id]["priority"],
            "employer_text": refs[requirement_id]["exact_text"],
            "match_status": refs[requirement_id]["match_status"],
            "evidence_ids": refs[requirement_id]["evidence_ids"],
        }
        for requirement_id in selected_ids[:30]
    ]
    forbidden = [
        {
            "requirement_id": requirement_id,
            "employer_text": refs[requirement_id]["exact_text"],
            "reason": refs[requirement_id]["match_status"],
        }
        for requirement_id in value["do_not_claim_requirement_ids"][:20]
    ]
    return {
        "plan_id": value["plan_id"],
        "plan_digest": value["plan_digest"],
        "supported_requirements": selected,
        "do_not_claim": forbidden,
        "supported_jd_terms": value["supported_jd_terms"][:40],
        "one_page_retention_order": value["one_page_retention_order"][:30],
        "submission_authority": False,
    }
