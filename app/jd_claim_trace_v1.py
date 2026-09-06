"""Stage B — internal JD requirement ↔ resume claim trace V1.

This module traces immutable V4 resume content back to the candidate evidence IDs
already carried by the ResumeDocument and, when possible, forward to the exact
JD requirements represented in an exact Resume Tailoring Plan.

The trace is internal metadata only. It does not change rendered resume text,
invoke a model, access protected Candidate Truth values, mutate n8n, control a
browser/ATS, access Gmail, send outreach, or submit applications.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app import jd_resume_plan_v1 as planner
from app import native_resume_service_v4 as resume_v4
from app.database import get_connection
from app.phase67_common import sha256_json
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema

TRACE_VERSION = "stage-b-jd-resume-claim-trace-v1"
SUBMISSION_AUTHORITY = False

SUPPORT_STATUSES = frozenset(
    {
        "EVIDENCE_SUPPORTED_JD_LINKED",
        "EVIDENCE_SUPPORTED_UNLINKED",
        "REVIEW_REQUIRED",
    }
)

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS jd_resume_claim_traces (
        trace_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
        plan_id TEXT NOT NULL,
        plan_digest TEXT NOT NULL CHECK(length(plan_digest)=64),
        resume_version_id TEXT NOT NULL,
        rendered_resume_sha256 TEXT NOT NULL CHECK(length(rendered_resume_sha256)=64),
        source_extraction_id TEXT NOT NULL,
        profile_revision INTEGER NOT NULL CHECK(profile_revision >= 1),
        profile_digest TEXT NOT NULL CHECK(length(profile_digest)=64),
        trace_version TEXT NOT NULL,
        trace_digest TEXT NOT NULL CHECK(length(trace_digest)=64),
        trace_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
        FOREIGN KEY(plan_id) REFERENCES jd_resume_tailoring_plans(plan_id) ON DELETE RESTRICT,
        FOREIGN KEY(resume_version_id) REFERENCES native_resume_versions(version_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        UNIQUE(tenant_id,user_id,plan_digest,resume_version_id,rendered_resume_sha256,trace_version),
        UNIQUE(tenant_id,user_id,trace_digest)
    );""",
    """CREATE INDEX IF NOT EXISTS idx_jd_claim_traces_owner_job
       ON jd_resume_claim_traces(tenant_id,user_id,job_id,created_at DESC);""",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        planner.ensure_schema(connection)
        resume_v4.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _evidence_claim(
    *,
    claim_id: str,
    section: str,
    text: str,
    evidence_ids: Iterable[str],
) -> dict[str, Any]:
    cleaned_ids = sorted({str(value).strip() for value in evidence_ids if str(value).strip()})
    return {
        "claim_id": claim_id,
        "section": section,
        "text": " ".join(str(text or "").split()),
        "evidence_ids": cleaned_ids,
    }


def _resume_claims(resume: Mapping[str, Any]) -> list[dict[str, Any]]:
    document = resume.get("document")
    if not isinstance(document, Mapping):
        raise ValueError("Resume version is missing its structured document.")

    claims: list[dict[str, Any]] = []
    summary = document.get("summary")
    if isinstance(summary, Mapping):
        claims.append(
            _evidence_claim(
                claim_id="CLAIM-SUMMARY-001",
                section="summary",
                text=str(summary.get("text") or ""),
                evidence_ids=summary.get("evidence_ids") or [],
            )
        )

    for index, item in enumerate(document.get("education") or [], start=1):
        if not isinstance(item, Mapping):
            continue
        text = " | ".join(
            str(item.get(key) or "").strip()
            for key in ("institution", "degree", "dates", "location", "gpa")
            if str(item.get(key) or "").strip()
        )
        claims.append(
            _evidence_claim(
                claim_id=f"CLAIM-EDU-{index:03d}",
                section="education",
                text=text,
                evidence_ids=item.get("evidence_ids") or [],
            )
        )

    for group_index, group in enumerate(document.get("skills") or [], start=1):
        if not isinstance(group, Mapping):
            continue
        label = str(group.get("label") or "").strip()
        skills = [str(value).strip() for value in group.get("skills") or [] if str(value).strip()]
        text = f"{label}: {' | '.join(skills)}" if label else " | ".join(skills)
        claims.append(
            _evidence_claim(
                claim_id=f"CLAIM-SKILLS-{group_index:03d}",
                section="skills",
                text=text,
                evidence_ids=group.get("evidence_ids") or [],
            )
        )

    for item_index, item in enumerate(document.get("experience") or [], start=1):
        if not isinstance(item, Mapping):
            continue
        for bullet_index, bullet in enumerate(item.get("bullets") or [], start=1):
            if not isinstance(bullet, Mapping):
                continue
            claims.append(
                _evidence_claim(
                    claim_id=f"CLAIM-EXP-{item_index:03d}-BULLET-{bullet_index:03d}",
                    section="experience",
                    text=str(bullet.get("text") or ""),
                    evidence_ids=bullet.get("evidence_ids") or [],
                )
            )

    for item_index, item in enumerate(document.get("projects") or [], start=1):
        if not isinstance(item, Mapping):
            continue
        for bullet_index, bullet in enumerate(item.get("bullets") or [], start=1):
            if not isinstance(bullet, Mapping):
                continue
            claims.append(
                _evidence_claim(
                    claim_id=f"CLAIM-PROJECT-{item_index:03d}-BULLET-{bullet_index:03d}",
                    section="projects",
                    text=str(bullet.get("text") or ""),
                    evidence_ids=bullet.get("evidence_ids") or [],
                )
            )

    for index, item in enumerate(document.get("certifications") or [], start=1):
        if not isinstance(item, Mapping):
            continue
        text = " | ".join(
            str(item.get(key) or "").strip()
            for key in ("name", "issuer")
            if str(item.get(key) or "").strip()
        )
        claims.append(
            _evidence_claim(
                claim_id=f"CLAIM-CERT-{index:03d}",
                section="certifications",
                text=text,
                evidence_ids=item.get("evidence_ids") or [],
            )
        )

    if not claims:
        raise ValueError("Resume contains no traceable evidence-bearing claims.")
    return claims


def _assert_binding_alignment(plan: Mapping[str, Any], resume: Mapping[str, Any]) -> None:
    if int(plan["job_id"]) != int(resume["job_id"]):
        raise ValueError("Resume and Tailoring Plan belong to different jobs.")

    resume_job = resume.get("job_snapshot_binding")
    if not isinstance(resume_job, Mapping) or not resume.get("job_snapshot_bound"):
        raise ValueError("Resume is not bound to an exact Hunter job snapshot.")
    if str(resume_job.get("job_snapshot_sha256") or "") != str(plan["job_snapshot_sha256"]):
        raise ValueError("Resume and Tailoring Plan are bound to different job snapshots.")

    resume_truth = resume.get("candidate_truth_binding")
    if not isinstance(resume_truth, Mapping) or not resume.get("candidate_truth_bound"):
        raise ValueError("Resume is not bound to exact Candidate Truth.")
    plan_truth = plan["candidate_truth_binding"]
    for key in ("source_extraction_id", "profile_revision", "profile_digest"):
        if str(resume_truth.get(key) or "") != str(plan_truth.get(key) or ""):
            raise ValueError("Resume and Tailoring Plan are bound to different Candidate Truth states.")


def _requirement_links(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for ref in plan.get("requirement_refs") or []:
        if not isinstance(ref, Mapping):
            continue
        requirement_id = str(ref.get("requirement_id") or "")
        if not requirement_id:
            continue
        result[requirement_id] = {
            "requirement_id": requirement_id,
            "type": str(ref.get("type") or ""),
            "priority": str(ref.get("priority") or ""),
            "match_status": str(ref.get("match_status") or ""),
            "evidence_ids": sorted(
                {str(value).strip() for value in ref.get("evidence_ids") or [] if str(value).strip()}
            ),
        }
    return result


def build_trace(*, plan: Mapping[str, Any], resume: Mapping[str, Any]) -> dict[str, Any]:
    plan_value = planner.validate_plan(plan)
    _assert_binding_alignment(plan_value, resume)
    claims = _resume_claims(resume)
    requirement_map = _requirement_links(plan_value)
    do_not_claim = set(plan_value.get("do_not_claim_requirement_ids") or [])

    traced_claims: list[dict[str, Any]] = []
    linked_requirement_ids: set[str] = set()
    for claim in claims:
        claim_evidence = set(claim["evidence_ids"])
        requirement_ids = sorted(
            requirement_id
            for requirement_id, requirement in requirement_map.items()
            if requirement_id not in do_not_claim
            and claim_evidence
            and claim_evidence.intersection(requirement["evidence_ids"])
        )
        linked_requirement_ids.update(requirement_ids)
        if not claim["evidence_ids"]:
            support_status = "REVIEW_REQUIRED"
        elif requirement_ids:
            support_status = "EVIDENCE_SUPPORTED_JD_LINKED"
        else:
            support_status = "EVIDENCE_SUPPORTED_UNLINKED"
        traced_claims.append(
            {
                **claim,
                "requirement_ids": requirement_ids,
                "support_status": support_status,
            }
        )

    supported_requirement_ids = {
        str(ref["requirement_id"])
        for ref in plan_value.get("requirement_refs") or []
        if isinstance(ref, Mapping)
        and str(ref.get("match_status") or "") in {"DIRECT", "STRONG_TRANSFERABLE", "PARTIAL"}
    }
    unrepresented = sorted(supported_requirement_ids - linked_requirement_ids)
    binding = dict(plan_value["candidate_truth_binding"])
    payload = {
        "contract_version": TRACE_VERSION,
        "authority": "munshi-hr-hunter",
        "tenant_id": str(plan_value["tenant_id"]),
        "user_id": str(plan_value["user_id"]),
        "job_id": int(plan_value["job_id"]),
        "job_snapshot_sha256": str(plan_value["job_snapshot_sha256"]),
        "plan_id": str(plan_value["plan_id"]),
        "plan_digest": str(plan_value["plan_digest"]),
        "resume_version_id": str(resume["version_id"]),
        "rendered_resume_sha256": str(resume["html_sha256"]),
        "candidate_truth_binding": binding,
        "claims": traced_claims,
        "linked_requirement_ids": sorted(linked_requirement_ids),
        "supported_but_unrepresented_requirement_ids": unrepresented,
        "do_not_claim_requirement_ids": sorted(do_not_claim),
        "diagnostics": {
            "claim_count": len(traced_claims),
            "jd_linked_claim_count": sum(
                1
                for claim in traced_claims
                if claim["support_status"] == "EVIDENCE_SUPPORTED_JD_LINKED"
            ),
            "review_required_claim_count": sum(
                1 for claim in traced_claims if claim["support_status"] == "REVIEW_REQUIRED"
            ),
            "protected_candidate_facts_serialized": 0,
            "visible_resume_changed": False,
            "automatic_actions_executed": False,
        },
        "mutation_authority": False,
        "submission_authority": False,
    }
    digest = sha256_json(payload)
    return {
        **payload,
        "trace_id": f"jd-claim-trace-{digest[:24]}",
        "trace_digest": digest,
        "generated_at": _now(),
    }


def _digest_payload(trace: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in trace.items()
        if key not in {"trace_id", "trace_digest", "generated_at"}
    }


def validate_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(trace)
    if value.get("contract_version") != TRACE_VERSION:
        raise ValueError("Unsupported JD resume claim-trace version.")
    if value.get("submission_authority") is not False:
        raise ValueError("JD resume claim trace cannot have submission authority.")
    claims = value.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("JD resume claim trace must contain claims.")
    claim_ids: set[str] = set()
    forbidden = set(value.get("do_not_claim_requirement_ids") or [])
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise ValueError("Claim trace entry must be an object.")
        claim_id = str(claim.get("claim_id") or "")
        if not claim_id or claim_id in claim_ids:
            raise ValueError("Claim IDs must be non-empty and unique.")
        claim_ids.add(claim_id)
        if claim.get("support_status") not in SUPPORT_STATUSES:
            raise ValueError("Unsupported claim support status.")
        if forbidden.intersection(set(claim.get("requirement_ids") or [])):
            raise ValueError("A do-not-claim requirement cannot be linked to a resume claim.")
    digest = sha256_json(_digest_payload(value))
    if value.get("trace_digest") and str(value["trace_digest"]) != digest:
        raise ValueError("JD resume claim-trace digest mismatch.")
    return value


def trace_resume(
    *,
    plan_id: str,
    resume_version_id: str,
    persist: bool = True,
) -> dict[str, Any]:
    plan = planner.get_plan(plan_id)
    if not planner.plan_freshness(plan_id)["fresh"]:
        raise RuntimeError("Resume Tailoring Plan is stale. Recompute Stage B before tracing.")
    resume = resume_v4.get_version(resume_version_id)
    trace = build_trace(plan=plan, resume=resume)
    validate_trace(trace)
    if not persist:
        return trace

    # Re-read both immutable/artifact state and mutable plan freshness immediately
    # before persistence. Any changed job/profile state fails closed.
    if not planner.plan_freshness(plan_id)["fresh"]:
        raise RuntimeError("Resume Tailoring Plan became stale before trace persistence.")
    current_resume = resume_v4.get_version(resume_version_id)
    if str(current_resume.get("html_sha256") or "") != trace["rendered_resume_sha256"]:
        raise RuntimeError("Resume artifact changed before trace persistence.")
    _assert_binding_alignment(plan, current_resume)

    binding = trace["candidate_truth_binding"]
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        existing = connection.execute(
            """SELECT trace_json FROM jd_resume_claim_traces
               WHERE tenant_id=? AND user_id=? AND plan_digest=? AND resume_version_id=?
                 AND rendered_resume_sha256=? AND trace_version=?""",
            (
                owner.tenant_id,
                owner.user_id,
                trace["plan_digest"],
                trace["resume_version_id"],
                trace["rendered_resume_sha256"],
                TRACE_VERSION,
            ),
        ).fetchone()
        if existing is not None:
            return validate_trace(json.loads(existing["trace_json"]))

        connection.execute(
            """INSERT INTO jd_resume_claim_traces(
                   trace_id,tenant_id,user_id,job_id,job_snapshot_sha256,
                   plan_id,plan_digest,resume_version_id,rendered_resume_sha256,
                   source_extraction_id,profile_revision,profile_digest,
                   trace_version,trace_digest,trace_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trace["trace_id"],
                owner.tenant_id,
                owner.user_id,
                trace["job_id"],
                trace["job_snapshot_sha256"],
                trace["plan_id"],
                trace["plan_digest"],
                trace["resume_version_id"],
                trace["rendered_resume_sha256"],
                binding["source_extraction_id"],
                binding["profile_revision"],
                binding["profile_digest"],
                TRACE_VERSION,
                trace["trace_digest"],
                json.dumps(trace, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            ),
        )
        connection.commit()
        return trace
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_trace(trace_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT trace_json FROM jd_resume_claim_traces
               WHERE trace_id=? AND tenant_id=? AND user_id=?""",
            (str(trace_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("JD resume claim trace is unavailable.")
        return validate_trace(json.loads(row["trace_json"]))
    finally:
        connection.close()


def trace_freshness(trace_id: str) -> dict[str, Any]:
    trace = get_trace(trace_id)
    plan_state = planner.plan_freshness(trace["plan_id"])
    resume = resume_v4.get_version(trace["resume_version_id"])
    resume_same = str(resume.get("html_sha256") or "") == trace["rendered_resume_sha256"]
    try:
        _assert_binding_alignment(planner.get_plan(trace["plan_id"]), resume)
        bindings_same = True
    except (ValueError, RuntimeError, LookupError):
        bindings_same = False
    return {
        "trace_id": trace_id,
        "job_id": int(trace["job_id"]),
        "fresh": plan_state["fresh"] is True and resume_same and bindings_same,
        "plan_fresh": plan_state["fresh"] is True,
        "resume_artifact_same": resume_same,
        "bindings_same": bindings_same,
    }
