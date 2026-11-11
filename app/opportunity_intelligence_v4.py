"""Stage B-aware Opportunity Intelligence V4.

V4 replaces the legacy raw JD token-bag fit components with the exact Stage B
Candidate ↔ JD evidence-match snapshot while preserving the proven career-policy
hard gates, advisory-only pursuit semantics, and fail-closed unknown handling.

It has no resume-generation, browser, ATS, Gmail, n8n, outreach, or submission
authority.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping
from uuid import uuid4

from app import career_policy
from app import jd_requirement_match_v1 as matcher
from app import opportunity_intelligence_v2 as v2
from app.database import get_connection
from app.phase67_common import safe_mapping_digest, safe_owned_job_snapshot, sha256_json
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema

OPPORTUNITY_INTELLIGENCE_VERSION = "opportunity-intelligence-v4-stage-b-evidence"
SUBMISSION_AUTHORITY = False
MIN_SCORE_CONFIDENCE = v2.MIN_SCORE_CONFIDENCE

SCORE_WEIGHTS = {
    "must_have_evidence_fit": 40.0,
    "core_responsibility_evidence_fit": 30.0,
    "preferred_evidence_fit": 10.0,
    "career_direction_fit": 20.0,
}

_RESUME_RELEVANT_TYPES = frozenset(
    {
        "RESPONSIBILITY",
        "SKILL",
        "TOOL",
        "PROCESS",
        "DOMAIN_KNOWLEDGE",
        "EXPERIENCE",
        "EDUCATION",
        "CERTIFICATION",
        "LANGUAGE",
        "LICENSE",
    }
)
_PROTECTED_ELIGIBILITY_TYPES = frozenset(
    {"WORK_AUTHORIZATION", "SPONSORSHIP", "CITIZENSHIP", "CLEARANCE", "OTHER_ELIGIBILITY"}
)
_MATCH_VALUE = {
    "DIRECT": 1.0,
    "STRONG_TRANSFERABLE": 0.80,
    "PARTIAL": 0.45,
    "NO_EVIDENCE": 0.0,
    "CONFLICT": 0.0,
}
_BINDING_FIELDS = (
    "tenant_id",
    "user_id",
    "source_extraction_id",
    "profile_revision",
    "profile_digest",
    "preferences_sha256",
    "policy_sha256",
    "job_snapshot_sha256",
    "jd_snapshot_id",
    "jd_snapshot_digest",
    "match_snapshot_id",
    "match_digest",
)

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS opportunity_intelligence_v4_evaluations (
        evaluation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        source_extraction_id TEXT NOT NULL,
        profile_revision INTEGER NOT NULL CHECK(profile_revision >= 1),
        profile_digest TEXT NOT NULL CHECK(length(profile_digest)=64),
        preferences_sha256 TEXT NOT NULL CHECK(length(preferences_sha256)=64),
        policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256)=64),
        job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
        jd_snapshot_id TEXT NOT NULL,
        jd_snapshot_digest TEXT NOT NULL CHECK(length(jd_snapshot_digest)=64),
        match_snapshot_id TEXT NOT NULL,
        match_digest TEXT NOT NULL CHECK(length(match_digest)=64),
        result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64),
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
        FOREIGN KEY(jd_snapshot_id) REFERENCES jd_intelligence_snapshots(snapshot_id) ON DELETE RESTRICT,
        FOREIGN KEY(match_snapshot_id)
          REFERENCES candidate_job_match_snapshots(match_snapshot_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        UNIQUE(tenant_id,user_id,job_id,match_digest,preferences_sha256,policy_sha256),
        UNIQUE(tenant_id,user_id,result_sha256)
    );""",
    """CREATE INDEX IF NOT EXISTS idx_opportunity_v4_owner_job
       ON opportunity_intelligence_v4_evaluations(tenant_id,user_id,job_id,created_at DESC);""",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        career_policy.ensure_schema(connection)
        matcher.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def opportunity_intelligence_enabled() -> bool:
    return v2.opportunity_intelligence_enabled()


def _group_component(
    matches: list[dict[str, Any]],
    *,
    name: str,
    priorities: frozenset[str],
    nominal_weight: float,
) -> tuple[dict[str, Any] | None, list[str]]:
    relevant = [
        item
        for item in matches
        if str(item.get("priority") or "") in priorities
        and str(item.get("type") or "") in _RESUME_RELEVANT_TYPES
    ]
    if not relevant:
        return None, []

    known = [item for item in relevant if str(item.get("match_status") or "") in _MATCH_VALUE]
    unknown = [
        str(item["requirement_id"])
        for item in relevant
        if str(item.get("match_status") or "") not in _MATCH_VALUE
    ]
    if not known:
        return {
            "input": name,
            "weight": nominal_weight,
            "effective_weight": 0.0,
            "value": None,
            "weighted_points": 0.0,
            "evidence": {
                "requirement_ids": [str(item["requirement_id"]) for item in relevant],
                "known_requirement_ids": [],
                "unknown_requirement_ids": unknown,
            },
        }, unknown

    value = sum(_MATCH_VALUE[str(item["match_status"])] for item in known) / len(known)
    known_ratio = len(known) / len(relevant)
    effective_weight = nominal_weight * known_ratio
    return {
        "input": name,
        "weight": nominal_weight,
        "effective_weight": round(effective_weight, 4),
        "value": round(value, 4),
        "weighted_points": round(value * effective_weight, 4),
        "evidence": {
            "requirement_ids": [str(item["requirement_id"]) for item in relevant],
            "known_requirement_ids": [str(item["requirement_id"]) for item in known],
            "unknown_requirement_ids": unknown,
            "direct_requirement_ids": [
                str(item["requirement_id"])
                for item in known
                if item["match_status"] == "DIRECT"
            ],
            "unsupported_requirement_ids": [
                str(item["requirement_id"])
                for item in known
                if item["match_status"] in {"NO_EVIDENCE", "CONFLICT"}
            ],
        },
    }, unknown


def _career_component(
    job: Mapping[str, Any], preferences: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, list[str]]:
    value, evidence = v2._career_direction_fit(job, preferences)
    if value is None:
        return None, ["career_direction_fit"]
    return {
        "input": "career_direction_fit",
        "weight": SCORE_WEIGHTS["career_direction_fit"],
        "effective_weight": SCORE_WEIGHTS["career_direction_fit"],
        "value": round(float(value), 4),
        "weighted_points": round(float(value) * SCORE_WEIGHTS["career_direction_fit"], 4),
        "evidence": evidence,
    }, []


def _score_components(
    *,
    job: Mapping[str, Any],
    match_snapshot: Mapping[str, Any],
    preferences: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    matches = [dict(item) for item in match_snapshot.get("requirement_matches") or []]
    components: list[dict[str, Any]] = []
    unknowns: list[str] = []

    for name, priorities in (
        ("must_have_evidence_fit", frozenset({"MUST_HAVE"})),
        ("core_responsibility_evidence_fit", frozenset({"CORE_RESPONSIBILITY"})),
        ("preferred_evidence_fit", frozenset({"PREFERRED", "BONUS"})),
    ):
        component, ids = _group_component(
            matches,
            name=name,
            priorities=priorities,
            nominal_weight=SCORE_WEIGHTS[name],
        )
        if component is not None:
            components.append(component)
        unknowns.extend(f"jd_requirement:{requirement_id}" for requirement_id in ids)

    career, career_unknowns = _career_component(job, preferences)
    if career is not None:
        components.append(career)
    unknowns.extend(career_unknowns)
    return components, sorted(set(unknowns))


def _normalized_score(components: list[dict[str, Any]]) -> tuple[float | None, float]:
    active_nominal_weight = sum(float(item["weight"]) for item in components)
    effective_weight = sum(float(item["effective_weight"]) for item in components)
    if effective_weight <= 0 or active_nominal_weight <= 0:
        return None, 0.0
    points = sum(float(item["weighted_points"]) for item in components)
    return (
        round((points / effective_weight) * 100.0, 2),
        round(effective_weight / active_nominal_weight, 4),
    )


def _eligibility_unknowns(match_snapshot: Mapping[str, Any]) -> list[str]:
    unresolved: list[str] = []
    for item in match_snapshot.get("requirement_matches") or []:
        if not isinstance(item, Mapping):
            continue
        if (
            str(item.get("type") or "") in _PROTECTED_ELIGIBILITY_TYPES
            and str(item.get("priority") or "") == "MUST_HAVE"
            and str(item.get("match_status") or "") in {"UNKNOWN", "CONFLICT"}
        ):
            unresolved.append(f"eligibility_requirement:{item['requirement_id']}")
    return sorted(set(unresolved))


def _evaluation_core(job_id: int, *, persist_stage_b: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    if not opportunity_intelligence_enabled():
        raise RuntimeError("Opportunity intelligence is disabled because career policy is disabled.")

    job_snapshot = safe_owned_job_snapshot(int(job_id))
    job = job_snapshot["job"]
    match_snapshot = matcher.match_job(int(job_id), persist=persist_stage_b)
    if match_snapshot["job_snapshot_sha256"] != job_snapshot["job_snapshot_sha256"]:
        raise RuntimeError("Stage B candidate/job match is stale for the current job.")

    truth_binding = dict(match_snapshot["candidate_truth_binding"])
    preferences = career_policy.get_preferences()
    policy = career_policy.get_autopilot_policy()
    preferences_sha = safe_mapping_digest(preferences, label="Career preferences")
    policy_sha = safe_mapping_digest(policy, label="Career policy")

    hard_failures, hard_unknowns, normalized_job = v2._hard_policy_gate(job, preferences, policy)
    components, fit_unknowns = _score_components(
        job=job,
        match_snapshot=match_snapshot,
        preferences=preferences,
    )
    score, score_confidence = _normalized_score(components)
    unknowns = sorted(
        set(hard_unknowns + fit_unknowns + _eligibility_unknowns(match_snapshot))
    )

    minimum = float(policy.get("minimum_opportunity_score") or 0)
    if minimum > 0:
        if score is None or score_confidence < MIN_SCORE_CONFIDENCE:
            unknowns = sorted(set(unknowns + ["opportunity_score_confidence"]))
        elif score < minimum:
            hard_failures = sorted(set(hard_failures + ["opportunity_score_below_policy"]))

    strategy = v2._pursuit_strategy(
        score=score,
        score_confidence=score_confidence,
        hard_failures=hard_failures,
        unknowns=unknowns,
    )
    status = "FAIL" if hard_failures else ("NEEDS_INPUT" if unknowns else "PASS")

    result = {
        "version": OPPORTUNITY_INTELLIGENCE_VERSION,
        "job_id": int(job_id),
        "job_snapshot_sha256": job_snapshot["job_snapshot_sha256"],
        "candidate_truth_binding": truth_binding,
        "preferences_sha256": preferences_sha,
        "policy_sha256": policy_sha,
        "stage_b_binding": {
            "jd_snapshot_id": match_snapshot["jd_snapshot_id"],
            "jd_snapshot_digest": match_snapshot["jd_snapshot_digest"],
            "match_snapshot_id": match_snapshot["match_snapshot_id"],
            "match_digest": match_snapshot["match_digest"],
        },
        "normalized_job": normalized_job,
        "status": status,
        "opportunity_score": score,
        "score_confidence": score_confidence,
        "score_explanation": components,
        "hard_failures": hard_failures,
        "unknowns": unknowns,
        "unsupported_must_have_requirement_ids": list(
            match_snapshot.get("unsupported_must_have_requirement_ids") or []
        ),
        "pursuit_strategy": strategy,
        "autonomy_readiness": career_policy.autonomy_readiness(
            policy=policy,
            hard_failures=hard_failures,
            unknowns=unknowns,
        ),
        "advisory_only": True,
        "automatic_actions_executed": False,
        "submission_authority": False,
    }
    binding = {
        "tenant_id": str(match_snapshot["tenant_id"]),
        "user_id": str(match_snapshot["user_id"]),
        "source_extraction_id": truth_binding["source_extraction_id"],
        "profile_revision": int(truth_binding["profile_revision"]),
        "profile_digest": truth_binding["profile_digest"],
        "preferences_sha256": preferences_sha,
        "policy_sha256": policy_sha,
        "job_snapshot_sha256": job_snapshot["job_snapshot_sha256"],
        "jd_snapshot_id": match_snapshot["jd_snapshot_id"],
        "jd_snapshot_digest": match_snapshot["jd_snapshot_digest"],
        "match_snapshot_id": match_snapshot["match_snapshot_id"],
        "match_digest": match_snapshot["match_digest"],
    }
    return result, binding


def _binding_changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    return [field for field in _BINDING_FIELDS if before.get(field) != after.get(field)]


def evaluate_job(job_id: int, *, persist: bool = True) -> dict[str, Any]:
    """Evaluate one owned job from exact Stage B evidence and optionally persist."""
    result, binding = _evaluation_core(int(job_id), persist_stage_b=persist)
    if not persist:
        return result

    current_result, current_binding = _evaluation_core(int(job_id), persist_stage_b=True)
    changed = _binding_changed(binding, current_binding)
    if changed:
        raise RuntimeError(
            "Opportunity V4 inputs changed during evaluation; recompute before persistence: "
            + ", ".join(changed)
        )
    # Semantic output must also remain stable across the optimistic recapture.
    if sha256_json(result) != sha256_json(current_result):
        raise RuntimeError("Opportunity V4 result changed during optimistic recapture.")

    evaluation_id = f"opportunity-v4-eval-{uuid4()}"
    result_with_id = {
        **result,
        "evaluation_id": evaluation_id,
        "persistence_revalidated": True,
    }
    result_sha = sha256_json(result_with_id)
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if owner.tenant_id != binding["tenant_id"] or owner.user_id != binding["user_id"]:
            raise ValueError("Opportunity V4 owner changed before persistence.")
        connection.execute(
            """INSERT INTO opportunity_intelligence_v4_evaluations(
                   evaluation_id,tenant_id,user_id,job_id,source_extraction_id,
                   profile_revision,profile_digest,preferences_sha256,policy_sha256,
                   job_snapshot_sha256,jd_snapshot_id,jd_snapshot_digest,
                   match_snapshot_id,match_digest,result_sha256,result_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                binding["jd_snapshot_id"],
                binding["jd_snapshot_digest"],
                binding["match_snapshot_id"],
                binding["match_digest"],
                result_sha,
                json.dumps(result_with_id, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {**result_with_id, "result_sha256": result_sha}


def get_evaluation(evaluation_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT result_sha256,result_json FROM opportunity_intelligence_v4_evaluations
               WHERE evaluation_id=? AND tenant_id=? AND user_id=?""",
            (str(evaluation_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Opportunity V4 evaluation is unavailable to the current user.")
        result = json.loads(str(row["result_json"]))
        if sha256_json(result) != row["result_sha256"]:
            raise RuntimeError("Stored Opportunity V4 digest does not match its payload.")
        result["result_sha256"] = row["result_sha256"]
        return result
    finally:
        connection.close()


def evaluation_freshness(evaluation_id: str) -> dict[str, Any]:
    stored = get_evaluation(evaluation_id)
    current, _binding = _evaluation_core(int(stored["job_id"]), persist_stage_b=False)
    changed: list[str] = []
    for field in ("job_snapshot_sha256", "preferences_sha256", "policy_sha256"):
        if stored.get(field) != current.get(field):
            changed.append(field)
    stored_truth = stored.get("candidate_truth_binding") or {}
    current_truth = current.get("candidate_truth_binding") or {}
    for field in ("source_extraction_id", "profile_revision", "profile_digest"):
        if stored_truth.get(field) != current_truth.get(field):
            changed.append(f"candidate_truth_binding.{field}")
    stored_stage_b = stored.get("stage_b_binding") or {}
    current_stage_b = current.get("stage_b_binding") or {}
    for field in ("jd_snapshot_id", "jd_snapshot_digest", "match_snapshot_id", "match_digest"):
        if stored_stage_b.get(field) != current_stage_b.get(field):
            changed.append(f"stage_b_binding.{field}")
    return {
        "evaluation_id": evaluation_id,
        "job_id": int(stored["job_id"]),
        "fresh": not changed,
        "changed_bindings": sorted(set(changed)),
    }
