"""Feature-gated, tenant-owned career preferences and opportunity policy.

This is an internal decision-support ledger only.  It deliberately does not
read or alter Hunter scoring/targeting, dispatch, n8n, Apply, email, resumes,
or submission workflows.  A caller must explicitly enable the local feature
and write preferences/policy; no model or inference path writes these records.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Mapping

from app.database import get_connection
from app.tenant_foundation import OwnerContext, current_owner, ensure_schema as ensure_tenant_schema


CAREER_POLICY_VERSION = "career-policy-v1"
AUTOPILOT_MODES = frozenset({"copilot", "guarded_autopilot", "full_autopilot"})
PURSUIT_STATES = frozenset({"IGNORE", "WATCH", "APPLY", "NETWORK + APPLY", "FULL PURSUIT"})
WORKPLACE_VALUES = frozenset({"remote", "hybrid", "onsite"})
SENSITIVE_FIELD_MARKERS = frozenset({
    "race", "ethnicity", "gender", "disability", "veteran", "religion",
    "sexual_orientation", "self_identification", "self_id",
})

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS career_preferences (
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        preferences_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (tenant_id, user_id),
        FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE RESTRICT
    );""",
    """CREATE TABLE IF NOT EXISTS career_autopilot_policies (
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        policy_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (tenant_id, user_id),
        FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE RESTRICT
    );""",
)

DEFAULT_PREFERENCES: dict[str, Any] = {
    "target_roles": [], "preferred_roles": [], "excluded_roles": [],
    "minimum_salary": None, "preferred_salary": None,
    "remote_allowed": True, "hybrid_allowed": True, "onsite_allowed": True,
    "allowed_locations": [], "preferred_locations": [], "excluded_locations": [],
    "relocation_allowed": False, "maximum_travel": None,
    "target_industries": [], "excluded_industries": [],
}
DEFAULT_AUTOPILOT_POLICY: dict[str, Any] = {
    "minimum_opportunity_score": 0, "allowed_role_families": [],
    "hard_salary_floor": None, "allowed_locations": [], "employment_types": [],
    "company_exclusions": [], "daily_application_mode": "copilot",
    "daily_application_limit": 0, "resume_generation_permission": False,
    "ats_account_creation_permission": False, "submission_permission": False,
    "email_access_permission": False, "recruiter_outreach_permission": False,
}


def career_policy_enabled() -> bool:
    return str(os.getenv("MUNSHI_CAREER_POLICY_ENABLED") or "").strip().casefold() in {"1", "true", "yes", "on"}


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    own = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if own:
            connection.commit()
    finally:
        if own:
            connection.close()


def _owner(connection: sqlite3.Connection) -> OwnerContext:
    if not career_policy_enabled():
        raise RuntimeError("Career policy is disabled.")
    return current_owner(connection)


def _list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list.")
    result: list[str] = []
    for item in value:
        item = str(item or "").strip()
        if not item or len(item) > 160:
            raise ValueError(f"{label} contains an invalid value.")
        if item.casefold() not in {entry.casefold() for entry in result}:
            result.append(item)
    return result


def _optional_number(value: Any, label: str, *, minimum: float = 0, maximum: float = 10_000_000) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be numeric.") from error
    if not minimum <= result <= maximum:
        raise ValueError(f"{label} is outside its permitted range.")
    return result


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be true or false.")
    return value


def _normalise_preferences(values: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise ValueError("Preferences must be an object.")
    unknown = set(values) - set(DEFAULT_PREFERENCES)
    if unknown:
        raise ValueError("Unsupported preference fields.")
    result = dict(DEFAULT_PREFERENCES)
    for key in ("target_roles", "preferred_roles", "excluded_roles", "allowed_locations", "preferred_locations", "excluded_locations", "target_industries", "excluded_industries"):
        if key in values:
            result[key] = _list(values[key], key)
    for key in ("minimum_salary", "preferred_salary", "maximum_travel"):
        if key in values:
            result[key] = _optional_number(values[key], key)
    for key in ("remote_allowed", "hybrid_allowed", "onsite_allowed", "relocation_allowed"):
        if key in values:
            result[key] = _boolean(values[key], key)
    if result["preferred_salary"] is not None and result["minimum_salary"] is not None and result["preferred_salary"] < result["minimum_salary"]:
        raise ValueError("preferred_salary cannot be below minimum_salary.")
    return result


def _normalise_policy(values: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise ValueError("AutoPilot policy must be an object.")
    unknown = set(values) - set(DEFAULT_AUTOPILOT_POLICY)
    if unknown:
        raise ValueError("Unsupported AutoPilot policy fields.")
    result = dict(DEFAULT_AUTOPILOT_POLICY)
    for key in ("allowed_role_families", "allowed_locations", "employment_types", "company_exclusions"):
        if key in values:
            result[key] = _list(values[key], key)
    for key in ("minimum_opportunity_score", "hard_salary_floor", "daily_application_limit"):
        if key in values:
            result[key] = _optional_number(values[key], key, maximum=100 if key == "minimum_opportunity_score" else 10_000_000)
    if result["daily_application_limit"] is None:
        result["daily_application_limit"] = 0
    if int(result["daily_application_limit"]) != result["daily_application_limit"]:
        raise ValueError("daily_application_limit must be a whole number.")
    result["daily_application_limit"] = int(result["daily_application_limit"])
    if "daily_application_mode" in values:
        mode = str(values["daily_application_mode"] or "").strip().casefold()
        if mode not in AUTOPILOT_MODES:
            raise ValueError("Unsupported daily_application_mode.")
        result["daily_application_mode"] = mode
    for key in ("resume_generation_permission", "ats_account_creation_permission", "submission_permission", "email_access_permission", "recruiter_outreach_permission"):
        if key in values:
            result[key] = _boolean(values[key], key)
    return result


def _save(table: str, field: str, value: dict[str, Any]) -> None:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
        connection.execute(
            f"INSERT INTO {table}(tenant_id,user_id,{field}) VALUES (?,?,?) "
            f"ON CONFLICT(tenant_id,user_id) DO UPDATE SET {field}=excluded.{field},updated_at=CURRENT_TIMESTAMP",
            (owner.tenant_id, owner.user_id, payload),
        )
        connection.commit()
    finally:
        connection.close()


def save_preferences(values: Mapping[str, Any]) -> dict[str, Any]:
    result = _normalise_preferences(values)
    _save("career_preferences", "preferences_json", result)
    return result


def save_autopilot_policy(values: Mapping[str, Any]) -> dict[str, Any]:
    result = _normalise_policy(values)
    _save("career_autopilot_policies", "policy_json", result)
    return result


def _load(table: str, field: str, default: dict[str, Any]) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = _owner(connection)
        row = connection.execute(f"SELECT {field} FROM {table} WHERE tenant_id=? AND user_id=?", (owner.tenant_id, owner.user_id)).fetchone()
        return dict(default) if row is None else json.loads(str(row[field]))
    finally:
        connection.close()


def get_preferences() -> dict[str, Any]:
    return _load("career_preferences", "preferences_json", DEFAULT_PREFERENCES)


def get_autopilot_policy() -> dict[str, Any]:
    return _load("career_autopilot_policies", "policy_json", DEFAULT_AUTOPILOT_POLICY)


def _known_text(opportunity: Mapping[str, Any], field: str) -> str | None:
    value = opportunity.get(field)
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _matches(value: str | None, allowed: list[str]) -> bool:
    return bool(value) and value.casefold() in {item.casefold() for item in allowed}


def _has_sensitive_input(opportunity: Mapping[str, Any]) -> bool:
    return any(marker in str(key).casefold() for key in opportunity for marker in SENSITIVE_FIELD_MARKERS)


def _fit(value: Any, label: str) -> float | None:
    if value is None:
        return None
    return _optional_number(value, label, minimum=0, maximum=1)


def evaluate_opportunity(opportunity: Mapping[str, Any]) -> dict[str, Any]:
    """Produce a deterministic advisory decision from caller-supplied, safe facts.

    Missing facts stay visible as unknowns.  This function has no side effects
    outside its own feature-gated preference/policy ledger.
    """
    if not isinstance(opportunity, Mapping):
        raise ValueError("Opportunity must be an object.")
    if _has_sensitive_input(opportunity):
        raise ValueError("Sensitive self-identification cannot be used for opportunity evaluation.")
    preferences, policy = get_preferences(), get_autopilot_policy()
    role = _known_text(opportunity, "role_family")
    company = _known_text(opportunity, "company_name")
    location = _known_text(opportunity, "location")
    workplace = _known_text(opportunity, "workplace")
    employment_type = _known_text(opportunity, "employment_type")
    salary_max = _optional_number(opportunity.get("salary_max"), "salary_max")
    hard_failures: list[str] = []
    unknowns: list[str] = []
    if policy["allowed_role_families"]:
        if role is None: unknowns.append("role_family")
        elif not _matches(role, policy["allowed_role_families"]): hard_failures.append("role_not_allowed")
    if policy["company_exclusions"]:
        if company is None: unknowns.append("company_name")
        elif _matches(company, policy["company_exclusions"]): hard_failures.append("company_excluded")
    if policy["allowed_locations"]:
        if location is None: unknowns.append("location")
        elif not _matches(location, policy["allowed_locations"]): hard_failures.append("location_not_allowed")
    if policy["employment_types"]:
        if employment_type is None: unknowns.append("employment_type")
        elif not _matches(employment_type, policy["employment_types"]): hard_failures.append("employment_type_not_allowed")
    if policy["hard_salary_floor"] is not None:
        if salary_max is None: unknowns.append("salary_max")
        elif salary_max < policy["hard_salary_floor"]: hard_failures.append("salary_below_hard_floor")
    if workplace is not None:
        kind = workplace.casefold()
        if kind not in WORKPLACE_VALUES: hard_failures.append("unsupported_workplace")
        elif not preferences[f"{kind}_allowed"]: hard_failures.append("workplace_not_allowed")
    else:
        unknowns.append("workplace")
    if role is not None and _matches(role, preferences["excluded_roles"]): hard_failures.append("role_excluded")
    if company is not None and _matches(company, policy["company_exclusions"]): hard_failures.append("company_excluded")
    if location is not None and _matches(location, preferences["excluded_locations"]): hard_failures.append("location_excluded")
    if salary_max is not None and preferences["minimum_salary"] is not None and salary_max < preferences["minimum_salary"]: hard_failures.append("salary_below_preference_floor")
    fits = [("skill_fit", _fit(opportunity.get("skill_fit"), "skill_fit"), 35), ("evidence_backed_experience_fit", _fit(opportunity.get("evidence_backed_experience_fit"), "evidence_backed_experience_fit"), 35), ("career_direction_fit", _fit(opportunity.get("career_direction_fit"), "career_direction_fit"), 20), ("eligibility_fit", _fit(opportunity.get("eligibility_fit"), "eligibility_fit"), 10)]
    score_parts = [{"input": name, "weight": weight, "value": value, "points": round(value * weight, 2)} for name, value, weight in fits if value is not None]
    score = round(sum(part["points"] for part in score_parts), 2)
    # An absent fit signal is unknown, not a zero.  Treating it as zero would
    # fabricate a negative assessment and could silently turn incomplete data
    # into an exclusion.
    if not score_parts:
        unknowns.append("opportunity_fit_inputs")
    elif policy["minimum_opportunity_score"] and score < policy["minimum_opportunity_score"]:
        hard_failures.append("opportunity_score_below_policy")
    hard_failures = sorted(set(hard_failures)); unknowns = sorted(set(unknowns))
    if hard_failures: pursuit = "IGNORE"
    elif unknowns: pursuit = "WATCH"
    elif score >= 85: pursuit = "FULL PURSUIT"
    elif score >= 70: pursuit = "NETWORK + APPLY"
    elif score >= 50: pursuit = "APPLY"
    else: pursuit = "WATCH"
    readiness = autonomy_readiness(policy=policy, hard_failures=hard_failures, unknowns=unknowns)
    return {"version": CAREER_POLICY_VERSION, "status": "PASS" if not hard_failures and not unknowns else ("FAIL" if hard_failures else "NEEDS_INPUT"), "pursuit_state": pursuit, "opportunity_score": score, "score_explanation": score_parts, "hard_failures": hard_failures, "unknowns": unknowns, "autonomy_readiness": readiness}


def autonomy_readiness(*, policy: Mapping[str, Any] | None = None, hard_failures: list[str] | None = None, unknowns: list[str] | None = None) -> dict[str, Any]:
    """Report prerequisites; it does not grant or execute any authority."""
    policy = dict(policy) if policy is not None else get_autopilot_policy()
    blockers = list(hard_failures or []) + [f"unknown:{item}" for item in (unknowns or [])]
    permissions = ("resume_generation_permission", "ats_account_creation_permission", "submission_permission", "email_access_permission", "recruiter_outreach_permission")
    blockers.extend(f"permission_disabled:{name}" for name in permissions if not bool(policy.get(name)))
    if policy.get("daily_application_mode", "copilot") == "copilot": blockers.append("autopilot_mode_copilot")
    if int(policy.get("daily_application_limit", 0)) <= 0: blockers.append("daily_application_limit_zero")
    return {"ready": False, "mode": str(policy.get("daily_application_mode", "copilot")), "blockers": sorted(set(blockers)), "automatic_actions_executed": False}
