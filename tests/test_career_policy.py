from __future__ import annotations

import ast
import importlib
import sqlite3
from pathlib import Path

import pytest

from app import database
from app.career_policy import (
    autonomy_readiness, evaluate_opportunity, get_preferences,
    save_autopilot_policy, save_preferences,
)
from app.tenant_foundation import owner_context


def _enable(monkeypatch: pytest.MonkeyPatch, *, tenants: bool = False) -> None:
    monkeypatch.setenv("MUNSHI_CAREER_POLICY_ENABLED", "1")
    if tenants:
        monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")


def _member() -> None:
    connection = database.get_connection()
    try:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES ('team-a','Team A')")
        connection.execute("INSERT INTO app_users(user_id,display_name) VALUES ('member-a','Member A')")
        connection.execute("INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES ('team-a','member-a','member')")
        connection.commit()
    finally:
        connection.close()


def test_disabled_by_default_has_no_writes(hunter_db) -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        save_preferences({"target_roles": ["HR Analyst"]})
    connection = database.get_connection()
    try:
        assert connection.execute("SELECT COUNT(*) FROM career_preferences").fetchone()[0] == 0
    finally:
        connection.close()


def test_validation_and_separate_defaults(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    assert get_preferences()["target_roles"] == []
    with pytest.raises(ValueError, match="Unsupported preference"):
        save_preferences({"submission_permission": True})
    with pytest.raises(ValueError, match="below"):
        save_preferences({"minimum_salary": 90_000, "preferred_salary": 80_000})
    with pytest.raises(ValueError, match="whole number"):
        save_autopilot_policy({"daily_application_limit": 1.5})
    saved = save_autopilot_policy({"allowed_role_families": ["HR", "hr"], "hard_salary_floor": 80_000})
    assert saved["allowed_role_families"] == ["HR"]
    assert saved["submission_permission"] is False


def test_owner_isolation(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch, tenants=True)
    save_preferences({"target_roles": ["HR Analyst"]})
    _member()
    with owner_context(tenant_id="team-a", user_id="member-a"):
        assert get_preferences()["target_roles"] == []
        save_preferences({"target_roles": ["People Ops"]})
    assert get_preferences()["target_roles"] == ["HR Analyst"]


def test_deterministic_hard_constraints_score_and_readiness(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    save_preferences({"remote_allowed": True, "hybrid_allowed": False, "onsite_allowed": False, "excluded_roles": ["Sales"]})
    save_autopilot_policy({"allowed_role_families": ["HR"], "hard_salary_floor": 80_000, "allowed_locations": ["New York"], "company_exclusions": ["Bad Co"], "minimum_opportunity_score": 60})
    result = evaluate_opportunity({"role_family": "HR", "company_name": "Good Co", "location": "New York", "workplace": "remote", "salary_max": 95_000, "skill_fit": .8, "evidence_backed_experience_fit": .8, "career_direction_fit": .8, "eligibility_fit": 1})
    assert result["status"] == "PASS"
    assert result["pursuit_state"] == "NETWORK + APPLY"
    assert result["opportunity_score"] == 82
    assert [item["input"] for item in result["score_explanation"]] == ["skill_fit", "evidence_backed_experience_fit", "career_direction_fit", "eligibility_fit"]
    assert result["autonomy_readiness"]["ready"] is False
    assert result["autonomy_readiness"]["automatic_actions_executed"] is False
    denied = evaluate_opportunity({"role_family": "Sales", "company_name": "Bad Co", "location": "Boston", "workplace": "hybrid", "salary_max": 70_000})
    assert denied["pursuit_state"] == "IGNORE"
    assert {"role_not_allowed", "company_excluded", "location_not_allowed", "workplace_not_allowed", "salary_below_hard_floor"} <= set(denied["hard_failures"])
    unknown = evaluate_opportunity({"role_family": "HR"})
    assert unknown["status"] == "NEEDS_INPUT"
    assert unknown["pursuit_state"] == "WATCH"
    assert "unknown:workplace" in unknown["autonomy_readiness"]["blockers"]


def test_sensitive_exclusion_and_no_authority_surface(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    with pytest.raises(ValueError, match="Sensitive"):
        evaluate_opportunity({"role_family": "HR", "race": "private"})
    source = (Path(__file__).resolve().parent.parent / "app" / "career_policy.py").read_text(encoding="utf-8")
    imports = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) and node.module}
    assert not {"app.answer_brain", "app.n8n_dispatch", "app.gmail_integration", "app.native_resume_shadow", "app.scoring"} & imports
    assert autonomy_readiness()["ready"] is False


def test_migration_is_additive(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "upgraded.sqlite")
    try:
        importlib.import_module("migrations.020_career_preferences_policy").apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert {"career_preferences", "career_autopilot_policies"} <= tables
