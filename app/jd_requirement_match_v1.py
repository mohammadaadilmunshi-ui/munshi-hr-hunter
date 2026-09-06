"""Stage B — Candidate ↔ JD evidence matching V1.

Consumes one immutable JD Intelligence snapshot and the current non-protected
Candidate Truth Profile to produce a content-addressed evidence-match snapshot.
Protected eligibility facts are deliberately not compared here; those requirements
remain explicit unknowns for a dedicated protected-fact resolver.

No resume generation, browser, ATS, Gmail, n8n, outreach, or submission authority.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from app import jd_intelligence_v1 as jd
from app import phase45_truth_binding as truth
from app.database import get_connection
from app.phase67_common import canonical_text, safe_owned_job_snapshot, sha256_json, tokens
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema

MATCH_VERSION = "stage-b-candidate-jd-match-v1"
SUBMISSION_AUTHORITY = False

MATCH_STATUSES = frozenset(
    {"DIRECT", "STRONG_TRANSFERABLE", "PARTIAL", "NO_EVIDENCE", "CONFLICT", "UNKNOWN"}
)

_PROTECTED_RESOLUTION_TYPES = frozenset(
    {"WORK_AUTHORIZATION", "SPONSORSHIP", "CITIZENSHIP", "CLEARANCE", "OTHER_ELIGIBILITY"}
)

_PRIORITY_WEIGHT = {
    "MUST_HAVE": 4.0,
    "CORE_RESPONSIBILITY": 3.0,
    "PREFERRED": 1.5,
    "BONUS": 1.0,
    "CONTEXT": 0.5,
    "UNKNOWN": 0.5,
}

_MATCH_VALUE = {
    "DIRECT": 1.0,
    "STRONG_TRANSFERABLE": 0.8,
    "PARTIAL": 0.45,
    "NO_EVIDENCE": 0.0,
}

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS candidate_job_match_snapshots (
        match_snapshot_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
        jd_snapshot_id TEXT NOT NULL,
        jd_snapshot_digest TEXT NOT NULL CHECK(length(jd_snapshot_digest)=64),
        source_extraction_id TEXT NOT NULL,
        profile_revision INTEGER NOT NULL CHECK(profile_revision >= 1),
        profile_digest TEXT NOT NULL CHECK(length(profile_digest)=64),
        matcher_version TEXT NOT NULL,
        match_digest TEXT NOT NULL CHECK(length(match_digest)=64),
        snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
        FOREIGN KEY(jd_snapshot_id) REFERENCES jd_intelligence_snapshots(snapshot_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        UNIQUE(
          tenant_id,user_id,job_id,job_snapshot_sha256,jd_snapshot_digest,
          source_extraction_id,profile_revision,profile_digest,matcher_version
        ),
        UNIQUE(tenant_id,user_id,match_digest)
    );""",
    """CREATE INDEX IF NOT EXISTS idx_candidate_job_match_owner_job
       ON candidate_job_match_snapshots(tenant_id,user_id,job_id,created_at DESC);""",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        jd.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _fact_text(value: Any) -> str:
    if isinstance(value, list):
        return " | ".join(canonical_text(item) for item in value if canonical_text(item))
    return canonical_text(value)


def _safe_evidence(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    context = truth.safe_resume_profile_context(dict(snapshot))
    result: list[dict[str, Any]] = []
    for fact in context.get("facts") or []:
        text = _fact_text(fact.get("value"))
        if not text:
            continue
        result.append(
            {
                "evidence_id": f"truth:{fact['fact_id']}",
                "fact_key": str(fact["key"]),
                "category": str(fact.get("category") or ""),
                "trust_level": str(fact.get("trust_level") or ""),
                "tokens": tokens(text),
                "normalized_text": canonical_text(text).casefold(),
            }
        )
    return result


def _requirement_evidence_match(
    requirement: Mapping[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    requirement_id = str(requirement["requirement_id"])
    req_type = str(requirement["type"])
    priority = str(requirement["priority"])
    if req_type in _PROTECTED_RESOLUTION_TYPES:
        return {
            "requirement_id": requirement_id,
            "type": req_type,
            "priority": priority,
            "match_status": "UNKNOWN",
            "match_score": None,
            "evidence_ids": [],
            "evidence_keys": [],
            "reason": "protected_candidate_fact_required",
        }

    req_text = canonical_text(requirement.get("exact_text")).casefold()
    req_tokens = tokens(req_text)
    if not req_tokens:
        return {
            "requirement_id": requirement_id,
            "type": req_type,
            "priority": priority,
            "match_status": "UNKNOWN",
            "match_score": None,
            "evidence_ids": [],
            "evidence_keys": [],
            "reason": "requirement_has_no_matchable_tokens",
        }

    ranked: list[tuple[float, bool, dict[str, Any]]] = []
    for item in evidence:
        evidence_tokens = item["tokens"]
        if not evidence_tokens:
            continue
        overlap = len(req_tokens & evidence_tokens)
        coverage = overlap / len(req_tokens)
        exact = req_text in item["normalized_text"] or item["normalized_text"] in req_text
        ranked.append((coverage, exact, item))

    ranked.sort(key=lambda value: (-value[0], not value[1], value[2]["evidence_id"]))
    best = ranked[:5]
    if not best or best[0][0] <= 0:
        return {
            "requirement_id": requirement_id,
            "type": req_type,
            "priority": priority,
            "match_status": "NO_EVIDENCE",
            "match_score": 0.0,
            "evidence_ids": [],
            "evidence_keys": [],
            "reason": "no_nonprotected_candidate_evidence_overlap",
        }

    _best_coverage, best_exact, _best_item = best[0]
    combined_tokens: set[str] = set()
    for coverage, _exact, item in best:
        if coverage > 0:
            combined_tokens |= item["tokens"]
    combined_coverage = len(req_tokens & combined_tokens) / len(req_tokens)

    if best_exact or (len(req_tokens) >= 2 and req_tokens <= combined_tokens):
        status = "DIRECT"
        score = 1.0
    elif combined_coverage >= 0.72:
        status = "STRONG_TRANSFERABLE"
        score = round(combined_coverage, 4)
    elif combined_coverage >= 0.40:
        status = "PARTIAL"
        score = round(combined_coverage, 4)
    else:
        status = "NO_EVIDENCE"
        score = round(combined_coverage, 4)

    supporting = [item for coverage, _exact, item in best if coverage > 0]
    return {
        "requirement_id": requirement_id,
        "type": req_type,
        "priority": priority,
        "match_status": status,
        "match_score": score,
        "evidence_ids": [item["evidence_id"] for item in supporting],
        "evidence_keys": [item["fact_key"] for item in supporting],
        "reason": "nonprotected_candidate_truth_overlap",
    }


def _coverage(matches: list[dict[str, Any]]) -> tuple[float | None, float]:
    known = [item for item in matches if item["match_status"] in _MATCH_VALUE]
    total_weight = sum(_PRIORITY_WEIGHT.get(item["priority"], 0.5) for item in known)
    if total_weight <= 0:
        return None, 0.0
    weighted = sum(
        _PRIORITY_WEIGHT.get(item["priority"], 0.5)
        * _MATCH_VALUE[item["match_status"]]
        for item in known
    )
    all_weight = sum(_PRIORITY_WEIGHT.get(item["priority"], 0.5) for item in matches)
    confidence = total_weight / all_weight if all_weight > 0 else 0.0
    return round((weighted / total_weight) * 100.0, 2), round(confidence, 4)


def build_match_snapshot(
    *,
    jd_snapshot: Mapping[str, Any],
    candidate_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    jd_value = jd.validate_snapshot(jd_snapshot)
    if (
        str(jd_value["tenant_id"]) != str(candidate_snapshot.get("tenant_id"))
        or str(jd_value["user_id"]) != str(candidate_snapshot.get("user_id"))
    ):
        raise ValueError("JD Intelligence and Candidate Truth owners do not match.")

    evidence = _safe_evidence(candidate_snapshot)
    matches = [
        _requirement_evidence_match(requirement, evidence)
        for requirement in jd_value["requirements"]
    ]
    coverage_score, score_confidence = _coverage(matches)

    unknowns = sorted(
        {
            item["requirement_id"]
            for item in matches
            if item["match_status"] == "UNKNOWN"
        }
    )
    unsupported_must_haves = sorted(
        {
            item["requirement_id"]
            for item in matches
            if item["priority"] == "MUST_HAVE" and item["match_status"] == "NO_EVIDENCE"
        }
    )

    payload = {
        "contract_version": MATCH_VERSION,
        "authority": "munshi-hr-hunter",
        "tenant_id": str(candidate_snapshot["tenant_id"]),
        "user_id": str(candidate_snapshot["user_id"]),
        "job_id": int(jd_value["job_id"]),
        "job_snapshot_sha256": str(jd_value["job_snapshot_sha256"]),
        "jd_snapshot_id": str(jd_value["snapshot_id"]),
        "jd_snapshot_digest": str(jd_value["snapshot_digest"]),
        "candidate_truth_binding": {
            "source_extraction_id": str(candidate_snapshot["source_extraction_id"]),
            "profile_revision": int(candidate_snapshot["profile_revision"]),
            "profile_digest": str(candidate_snapshot["profile_digest"]),
        },
        "requirement_matches": matches,
        "evidence_coverage_score": coverage_score,
        "score_confidence": score_confidence,
        "unknown_requirement_ids": unknowns,
        "unsupported_must_have_requirement_ids": unsupported_must_haves,
        "diagnostics": {
            "evidence_fact_count": len(evidence),
            "protected_candidate_facts_serialized": 0,
            "automatic_actions_executed": False,
        },
        "mutation_authority": False,
        "submission_authority": False,
    }
    digest = sha256_json(payload)
    return {
        **payload,
        "match_snapshot_id": f"candidate-job-match-{digest[:24]}",
        "match_digest": digest,
        "generated_at": _now(),
    }


def _digest_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in snapshot.items()
        if key not in {"match_snapshot_id", "match_digest", "generated_at"}
    }


def validate_match_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(snapshot)
    if value.get("contract_version") != MATCH_VERSION:
        raise ValueError("Unsupported Candidate ↔ JD match version.")
    if value.get("submission_authority") is not False:
        raise ValueError("Candidate ↔ JD match cannot have submission authority.")
    matches = value.get("requirement_matches")
    if not isinstance(matches, list):
        raise ValueError("Requirement matches must be a list.")
    for item in matches:
        if not isinstance(item, Mapping) or item.get("match_status") not in MATCH_STATUSES:
            raise ValueError("Invalid requirement match.")
    digest = sha256_json(_digest_payload(value))
    if value.get("match_digest") and str(value["match_digest"]) != digest:
        raise ValueError("Candidate ↔ JD match digest mismatch.")
    return value


def match_job(
    job_id: int,
    *,
    jd_snapshot_id: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    if jd_snapshot_id:
        jd_snapshot = jd.get_snapshot(jd_snapshot_id)
        if int(jd_snapshot["job_id"]) != int(job_id):
            raise ValueError("JD Intelligence snapshot belongs to a different job.")
    else:
        jd_snapshot = jd.analyze_job(int(job_id), persist=persist)

    current_job_before = safe_owned_job_snapshot(int(job_id))
    if current_job_before["job_snapshot_sha256"] != jd_snapshot["job_snapshot_sha256"]:
        raise RuntimeError("JD Intelligence is stale. Re-analyze the current job before matching.")

    candidate_before = truth.current_candidate_profile_snapshot()
    result = build_match_snapshot(
        jd_snapshot=jd_snapshot,
        candidate_snapshot=candidate_before,
    )
    validate_match_snapshot(result)
    if not persist:
        return result

    candidate_after = truth.current_candidate_profile_snapshot()
    binding_before = result["candidate_truth_binding"]
    if (
        str(candidate_after["source_extraction_id"]) != binding_before["source_extraction_id"]
        or int(candidate_after["profile_revision"]) != int(binding_before["profile_revision"])
        or str(candidate_after["profile_digest"]) != binding_before["profile_digest"]
    ):
        raise RuntimeError(
            "Candidate Truth changed during JD matching. Recompute from the current profile."
        )
    current_job = safe_owned_job_snapshot(int(job_id))
    if current_job["job_snapshot_sha256"] != result["job_snapshot_sha256"]:
        raise RuntimeError("The stored job changed during JD matching. Recompute Stage B.")

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        existing = connection.execute(
            """SELECT snapshot_json FROM candidate_job_match_snapshots
               WHERE tenant_id=? AND user_id=? AND job_id=? AND job_snapshot_sha256=?
                 AND jd_snapshot_digest=? AND source_extraction_id=? AND profile_revision=?
                 AND profile_digest=? AND matcher_version=?""",
            (
                owner.tenant_id,
                owner.user_id,
                int(job_id),
                result["job_snapshot_sha256"],
                result["jd_snapshot_digest"],
                binding_before["source_extraction_id"],
                binding_before["profile_revision"],
                binding_before["profile_digest"],
                MATCH_VERSION,
            ),
        ).fetchone()
        if existing is not None:
            return validate_match_snapshot(json.loads(existing["snapshot_json"]))

        connection.execute(
            """INSERT INTO candidate_job_match_snapshots(
                   match_snapshot_id,tenant_id,user_id,job_id,job_snapshot_sha256,
                   jd_snapshot_id,jd_snapshot_digest,source_extraction_id,profile_revision,
                   profile_digest,matcher_version,match_digest,snapshot_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                result["match_snapshot_id"],
                owner.tenant_id,
                owner.user_id,
                int(job_id),
                result["job_snapshot_sha256"],
                result["jd_snapshot_id"],
                result["jd_snapshot_digest"],
                binding_before["source_extraction_id"],
                binding_before["profile_revision"],
                binding_before["profile_digest"],
                MATCH_VERSION,
                result["match_digest"],
                json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            ),
        )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_match_snapshot(match_snapshot_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT snapshot_json FROM candidate_job_match_snapshots
               WHERE match_snapshot_id=? AND tenant_id=? AND user_id=?""",
            (str(match_snapshot_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Candidate ↔ JD match snapshot is unavailable.")
        return validate_match_snapshot(json.loads(row["snapshot_json"]))
    finally:
        connection.close()


def match_freshness(match_snapshot_id: str) -> dict[str, Any]:
    snapshot = get_match_snapshot(match_snapshot_id)
    candidate = truth.current_candidate_profile_snapshot()
    current_job = safe_owned_job_snapshot(int(snapshot["job_id"]))
    binding = snapshot["candidate_truth_binding"]
    fresh = (
        current_job["job_snapshot_sha256"] == snapshot["job_snapshot_sha256"]
        and str(candidate["source_extraction_id"]) == binding["source_extraction_id"]
        and int(candidate["profile_revision"]) == int(binding["profile_revision"])
        and str(candidate["profile_digest"]) == binding["profile_digest"]
    )
    return {
        "match_snapshot_id": match_snapshot_id,
        "job_id": int(snapshot["job_id"]),
        "fresh": fresh,
        "current_job_snapshot_sha256": current_job["job_snapshot_sha256"],
        "expected_job_snapshot_sha256": snapshot["job_snapshot_sha256"],
        "candidate_truth_binding": binding,
    }
