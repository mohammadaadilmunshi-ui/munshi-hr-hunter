"""Strengthened Phase 6 Opportunity Intelligence.

V2 turns the existing safe career-policy foundation into a reproducible, owned-job
advisory evaluation. Every persisted evaluation is bound to the exact Hunter job
snapshot, Candidate Truth Profile state, and preference/policy contents used.

This module never dispatches, generates a resume, logs into an ATS, accesses Gmail,
controls a browser, sends outreach, or submits an application.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Mapping
from uuid import uuid4

from app import career_policy
from app import phase45_truth_binding
from app.database import get_connection
from app.phase67_common import (
    bounded_ratio,
    canonical_text,
    safe_mapping_digest,
    safe_owned_job_snapshot,
    sha256_json,
    tokens,
)
from app.tenant_foundation import current_owner

OPPORTUNITY_INTELLIGENCE_VERSION = "opportunity-intelligence-v2-evidence-bound"
SCORE_WEIGHTS = {
    "skill_fit": 40.0,
    "evidence_backed_experience_fit": 35.0,
    "career_direction_fit": 25.0,
}
MIN_SCORE_CONFIDENCE = 0.60
_SPLIT_SKILLS = re.compile(r"[,;|\n]+")

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS opportunity_intelligence_evaluations (
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
        result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64),
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_opportunity_evaluations_owner_job
       ON opportunity_intelligence_evaluations(tenant_id,user_id,job_id,created_at DESC);""",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        career_policy.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def opportunity_intelligence_enabled() -> bool:
    return career_policy.career_policy_enabled()


def _normal(value: Any) -> str:
    return canonical_text(value).casefold()


def _text_matches(value: str | None, choices: list[str]) -> bool:
    if not value:
        return False
    haystack = _normal(value)
    haystack_tokens = tokens(haystack)
    for choice in choices:
        needle = _normal(choice)
        if not needle:
            continue
        if needle == haystack or needle in haystack or haystack in needle:
            return True
        needle_tokens = tokens(needle)
        if needle_tokens and needle_tokens <= haystack_tokens:
            return True
    return False


def _job_location(job: Mapping[str, Any]) -> str | None:
    parts = [job.get("location_raw"), job.get("city"), job.get("state"), job.get("country")]
    value = " | ".join(canonical_text(item) for item in parts if canonical_text(item))
    return value or None


def _workplace(job: Mapping[str, Any]) -> str | None:
    raw = _normal(job.get("remote_type"))
    if not raw:
        return None
    if "hybrid" in raw:
        return "hybrid"
    if "remote" in raw:
        return "remote"
    if "onsite" in raw or "on-site" in raw or "on site" in raw:
        return "onsite"
    return None


def _candidate_skill_phrases(snapshot: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for fact in snapshot.get("facts") or []:
        key = str(fact.get("key") or "")
        if fact.get("protected") is True or not key.startswith("skills.") or not key.endswith(".skills"):
            continue
        value = fact.get("value")
        items = value if isinstance(value, list) else [value]
        for item in items:
            normalized = _normal(item)
            if normalized:
                values.add(normalized)
    return values


def _required_skill_phrases(job: Mapping[str, Any]) -> list[str]:
    raw = canonical_text(job.get("skills_keywords"))
    if not raw:
        return []
    values: list[str] = []
    for item in _SPLIT_SKILLS.split(raw):
        normalized = _normal(item)
        if normalized and normalized not in values:
            values.append(normalized)
    return values[:80]


def _skill_fit(job: Mapping[str, Any], snapshot: Mapping[str, Any]) -> tuple[float | None, dict[str, Any]]:
    required = _required_skill_phrases(job)
    candidate = _candidate_skill_phrases(snapshot)
    if not required or not candidate:
        return None, {"required": required, "candidate_skill_count": len(candidate), "matched": []}
    candidate_tokens = set().union(*(tokens(item) for item in candidate)) if candidate else set()
    matched: list[str] = []
    for requirement in required:
        req_tokens = tokens(requirement)
        if (
            requirement in candidate
            or any(requirement in skill or skill in requirement for skill in candidate)
            or (req_tokens and req_tokens <= candidate_tokens)
        ):
            matched.append(requirement)
    return bounded_ratio(len(matched), len(required)), {
        "required": required,
        "candidate_skill_count": len(candidate),
        "matched": matched,
    }


def _experience_evidence_text(snapshot: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    for fact in snapshot.get("facts") or []:
        key = str(fact.get("key") or "")
        if fact.get("protected") is True:
            continue
        if not (key.startswith("experience.") or key.startswith("projects.")):
            continue
        value = fact.get("value")
        if isinstance(value, list):
            chunks.extend(canonical_text(item) for item in value if canonical_text(item))
        elif canonical_text(value):
            chunks.append(canonical_text(value))
    return " ".join(chunks)


def _experience_fit(job: Mapping[str, Any], snapshot: Mapping[str, Any]) -> tuple[float | None, dict[str, Any]]:
    requirement_text = " ".join(
        canonical_text(job.get(field))
        for field in ("responsibilities", "qualifications", "preferred_qualifications", "preferred_skills")
        if canonical_text(job.get(field))
    )
    evidence_text = _experience_evidence_text(snapshot)
    requirement_tokens = tokens(requirement_text)
    evidence_tokens = tokens(evidence_text)
    if not requirement_tokens or not evidence_tokens:
        return None, {
            "requirement_token_count": len(requirement_tokens),
            "evidence_token_count": len(evidence_tokens),
            "matched_tokens": [],
        }
    matched = sorted(requirement_tokens & evidence_tokens)
    # Coverage asks: how much of the job's meaningful language is present in
    # evidence-backed candidate experience? Missing evidence remains unknown;
    # a known low overlap is allowed to be low rather than silently boosted.
    value = bounded_ratio(len(matched), len(requirement_tokens))
    return value, {
        "requirement_token_count": len(requirement_tokens),
        "evidence_token_count": len(evidence_tokens),
        "matched_tokens": matched[:80],
    }


def _role_similarity(job_label: str, target: str) -> float:
    left, right = _normal(job_label), _normal(target)
    if not left or not right:
        return 0.0
    if left == right or left in right or right in left:
        return 1.0
    left_tokens, right_tokens = tokens(left), tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return bounded_ratio(intersection, union)


def _career_direction_fit(job: Mapping[str, Any], preferences: Mapping[str, Any]) -> tuple[float | None, dict[str, Any]]:
    targets = list(preferences.get("target_roles") or []) + list(preferences.get("preferred_roles") or [])
    targets = list(dict.fromkeys(canonical_text(item) for item in targets if canonical_text(item)))
    label = canonical_text(job.get("target_track") or job.get("title"))
    if not label or not targets:
        return None, {"job_role": label, "targets": targets, "best_target": None}
    ranked = sorted(
        ((target, _role_similarity(label, target)) for target in targets),
        key=lambda item: (-item[1], item[0].casefold()),
    )
    best_target, best_value = ranked[0]
    return best_value, {"job_role": label, "targets": targets, "best_target": best_target}


def _hard_policy_gate(
    job: Mapping[str, Any],
    preferences: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[list[str], list[str], dict[str, Any]]:
    failures: list[str] = []
    unknowns: list[str] = []
    role = canonical_text(job.get("target_track") or job.get("title")) or None
    company = canonical_text(job.get("company_name")) or None
    location = _job_location(job)
    workplace = _workplace(job)
    employment_type = canonical_text(job.get("employment_type")) or None

    if policy.get("allowed_role_families"):
        if role is None:
            unknowns.append("role_family")
        elif not _text_matches(role, list(policy["allowed_role_families"])):
            failures.append("role_not_allowed")
    if policy.get("company_exclusions") and company and _text_matches(company, list(policy["company_exclusions"])):
        failures.append("company_excluded")
    if policy.get("allowed_locations"):
        if location is None:
            unknowns.append("location")
        elif not _text_matches(location, list(policy["allowed_locations"])):
            failures.append("location_not_allowed")
    if policy.get("employment_types"):
        if employment_type is None:
            unknowns.append("employment_type")
        elif not _text_matches(employment_type, list(policy["employment_types"])):
            failures.append("employment_type_not_allowed")

    # The canonical jobs table currently stores normalized hourly compensation,
    # while career policy salary floors may represent a different cadence. We do
    # not fabricate a conversion. A hard salary gate therefore remains unresolved
    # until a future compensation-normalization contract provides comparable units.
    if policy.get("hard_salary_floor") is not None or preferences.get("minimum_salary") is not None:
        unknowns.append("comparable_salary_max")

    if workplace is None:
        unknowns.append("workplace")
    elif not bool(preferences.get(f"{workplace}_allowed", False)):
        failures.append("workplace_not_allowed")

    if role and _text_matches(role, list(preferences.get("excluded_roles") or [])):
        failures.append("role_excluded")
    if location and _text_matches(location, list(preferences.get("excluded_locations") or [])):
        failures.append("location_excluded")
    if preferences.get("allowed_locations"):
        if location is None:
            unknowns.append("preference_location")
        elif not _text_matches(location, list(preferences["allowed_locations"])):
            failures.append("preference_location_not_allowed")

    return sorted(set(failures)), sorted(set(unknowns)), {
        "role_family": role,
        "company_name": company,
        "location": location,
        "workplace": workplace,
        "employment_type": employment_type,
    }


def _score_components(job: Mapping[str, Any], snapshot: Mapping[str, Any], preferences: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    raw = {
        "skill_fit": _skill_fit(job, snapshot),
        "evidence_backed_experience_fit": _experience_fit(job, snapshot),
        "career_direction_fit": _career_direction_fit(job, preferences),
    }
    components: list[dict[str, Any]] = []
    unknowns: list[str] = []
    for name in SCORE_WEIGHTS:
        value, evidence = raw[name]
        if value is None:
            unknowns.append(name)
            continue
        components.append(
            {
                "input": name,
                "weight": SCORE_WEIGHTS[name],
                "value": round(float(value), 4),
                "weighted_points": round(float(value) * SCORE_WEIGHTS[name], 4),
                "evidence": evidence,
            }
        )
    return components, unknowns


def _normalized_score(components: list[dict[str, Any]]) -> tuple[float | None, float]:
    known_weight = sum(float(item["weight"]) for item in components)
    if known_weight <= 0:
        return None, 0.0
    points = sum(float(item["weighted_points"]) for item in components)
    return round((points / known_weight) * 100.0, 2), round(known_weight / 100.0, 4)


def _pursuit_strategy(
    *,
    score: float | None,
    score_confidence: float,
    hard_failures: list[str],
    unknowns: list[str],
) -> dict[str, Any]:
    if hard_failures:
        state = "IGNORE"
        reason = "One or more user hard-policy gates failed."
    elif unknowns or score is None or score_confidence < MIN_SCORE_CONFIDENCE:
        state = "WATCH"
        reason = "More evidence is required before a stronger pursuit recommendation is justified."
    elif score >= 85:
        state = "FULL PURSUIT"
        reason = "High evidence-backed fit with no unresolved hard-policy gate."
    elif score >= 70:
        state = "NETWORK + APPLY"
        reason = "Strong evidence-backed fit; relationship intelligence may refine networking priority."
    elif score >= 50:
        state = "APPLY"
        reason = "Moderate evidence-backed fit with no unresolved hard-policy gate."
    else:
        state = "WATCH"
        reason = "Evidence-backed fit is currently below the apply recommendation band."
    return {
        "pursuit_state": state,
        "reason": reason,
        "required_resolutions": sorted(set(unknowns)),
        "hard_failures": sorted(set(hard_failures)),
        "advisory_only": True,
        "automatic_actions_executed": False,
    }


def _evaluation_core(job_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    if not opportunity_intelligence_enabled():
        raise RuntimeError("Opportunity intelligence is disabled because career policy is disabled.")

    job_snapshot = safe_owned_job_snapshot(job_id)
    job = job_snapshot["job"]
    candidate_snapshot = phase45_truth_binding.current_candidate_profile_snapshot()
    if (
        candidate_snapshot["tenant_id"] != job_snapshot["tenant_id"]
        or candidate_snapshot["user_id"] != job_snapshot["user_id"]
    ):
        raise ValueError("Candidate Truth Profile and opportunity owner do not match.")

    preferences = career_policy.get_preferences()
    policy = career_policy.get_autopilot_policy()
    preferences_sha = safe_mapping_digest(preferences, label="Career preferences")
    policy_sha = safe_mapping_digest(policy, label="Career policy")

    hard_failures, hard_unknowns, normalized_job = _hard_policy_gate(job, preferences, policy)
    components, fit_unknowns = _score_components(job, candidate_snapshot, preferences)
    score, score_confidence = _normalized_score(components)
    unknowns = sorted(set(hard_unknowns + fit_unknowns))

    minimum = float(policy.get("minimum_opportunity_score") or 0)
    if minimum > 0:
        if score is None or score_confidence < MIN_SCORE_CONFIDENCE:
            unknowns = sorted(set(unknowns + ["opportunity_score_confidence"]))
        elif score < minimum:
            hard_failures = sorted(set(hard_failures + ["opportunity_score_below_policy"]))

    # Work-authorization language is visible job evidence but is not enough to
    # infer candidate eligibility. Candidate authorization facts are protected,
    # so any explicit requirement remains an unresolved review item here.
    if canonical_text(job.get("work_authorization")):
        unknowns = sorted(set(unknowns + ["work_authorization_match"]))

    strategy = _pursuit_strategy(
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
        "candidate_truth_binding": phase45_truth_binding.public_binding_state(candidate_snapshot),
        "preferences_sha256": preferences_sha,
        "policy_sha256": policy_sha,
        "normalized_job": normalized_job,
        "status": status,
        "opportunity_score": score,
        "score_confidence": score_confidence,
        "score_explanation": components,
        "hard_failures": hard_failures,
        "unknowns": unknowns,
        "pursuit_strategy": strategy,
        "autonomy_readiness": career_policy.autonomy_readiness(
            policy=policy,
            hard_failures=hard_failures,
            unknowns=unknowns,
        ),
    }
    binding = {
        "tenant_id": candidate_snapshot["tenant_id"],
        "user_id": candidate_snapshot["user_id"],
        "source_extraction_id": candidate_snapshot["source_extraction_id"],
        "profile_revision": int(candidate_snapshot["profile_revision"]),
        "profile_digest": candidate_snapshot["profile_digest"],
        "preferences_sha256": preferences_sha,
        "policy_sha256": policy_sha,
        "job_snapshot_sha256": job_snapshot["job_snapshot_sha256"],
    }
    return result, binding


def evaluate_job(job_id: int, *, persist: bool = True) -> dict[str, Any]:
    """Evaluate one owned Hunter job and optionally persist an immutable snapshot."""
    result, binding = _evaluation_core(job_id)
    if not persist:
        return result

    evaluation_id = f"opportunity-eval-{uuid4()}"
    result_with_id = {**result, "evaluation_id": evaluation_id}
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
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM opportunity_intelligence_evaluations
               WHERE evaluation_id=? AND tenant_id=? AND user_id=?""",
            (str(evaluation_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Opportunity evaluation is unavailable to the current user.")
        result = json.loads(str(row["result_json"]))
        if sha256_json(result) != row["result_sha256"]:
            raise RuntimeError("Stored opportunity evaluation digest does not match its payload.")
        result["result_sha256"] = row["result_sha256"]
        return result
    finally:
        connection.close()


def evaluation_freshness(evaluation_id: str) -> dict[str, Any]:
    """Compare a persisted evaluation with current truth/policy/job state."""
    stored = get_evaluation(evaluation_id)
    current, _ = _evaluation_core(int(stored["job_id"]))
    changed: list[str] = []
    for field in (
        "job_snapshot_sha256",
        "preferences_sha256",
        "policy_sha256",
    ):
        if stored.get(field) != current.get(field):
            changed.append(field)
    stored_binding = stored.get("candidate_truth_binding") or {}
    current_binding = current.get("candidate_truth_binding") or {}
    for field in ("source_extraction_id", "profile_revision", "profile_digest"):
        if stored_binding.get(field) != current_binding.get(field):
            changed.append(f"candidate_truth_binding.{field}")
    return {
        "evaluation_id": evaluation_id,
        "fresh": not changed,
        "changed_bindings": sorted(changed),
        "automatic_actions_executed": False,
    }
