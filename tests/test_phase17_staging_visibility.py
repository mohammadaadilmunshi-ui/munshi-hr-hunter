from __future__ import annotations

import inspect
import os
import sqlite3
from pathlib import Path

from app import application_workspace_page, product_shell, staging_feature_policy
from scripts import phase17_staging_migrate


def _staging_env(monkeypatch) -> None:
    values = {
        "CLOUD_SHADOW_MODE": "true",
        "PRODUCTION_STATE_IMPORTED": "false",
        "PRODUCTION_CALLBACKS_ENABLED": "false",
        "HUNTER_ENABLE_TELEGRAM": "false",
        "HUNTER_ENABLE_DISCOVERY_SCHEDULER": "false",
        "HUNTER_ENABLE_COORDINATOR": "false",
        "MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED": "false",
        "MUNSHI_CAREER_POLICY_ENABLED": "false",
        "MUNSHI_RELATIONSHIP_INTELLIGENCE_ENABLED": "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_isolated_staging_promotes_only_preparation_features(monkeypatch) -> None:
    _staging_env(monkeypatch)
    assert staging_feature_policy.isolated_staging() is True
    assert staging_feature_policy.activate_isolated_staging_preparation_features() is True
    assert os.environ["MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED"] == "true"
    assert os.environ["MUNSHI_CAREER_POLICY_ENABLED"] == "true"
    assert os.environ["MUNSHI_RELATIONSHIP_INTELLIGENCE_ENABLED"] == "true"
    assert os.environ["HUNTER_ENABLE_TELEGRAM"] == "false"
    assert os.environ["HUNTER_ENABLE_DISCOVERY_SCHEDULER"] == "false"
    assert os.environ["HUNTER_ENABLE_COORDINATOR"] == "false"
    assert os.environ["PRODUCTION_CALLBACKS_ENABLED"] == "false"


def test_production_contract_never_gets_staging_promotion(monkeypatch) -> None:
    _staging_env(monkeypatch)
    monkeypatch.setenv("PRODUCTION_STATE_IMPORTED", "true")
    assert staging_feature_policy.isolated_staging() is False
    assert staging_feature_policy.activate_isolated_staging_preparation_features() is False
    assert os.environ["MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED"] == "false"
    assert os.environ["MUNSHI_CAREER_POLICY_ENABLED"] == "false"
    assert os.environ["MUNSHI_RELATIONSHIP_INTELLIGENCE_ENABLED"] == "false"


def test_product_shell_exposes_candidate_facing_phase17_routes() -> None:
    navigation = dict(product_shell.NAVIGATION)
    assert navigation["profile"] == "Profile"
    assert navigation["resume-studio"] == "Resume Studio"
    assert navigation["prepare-application"] == "Prepare Application"
    assert product_shell.PREPARE_APPLICATION_VIEW in product_shell._SPECIAL_VIEWS


def test_application_workspace_is_preparation_only_surface() -> None:
    source = inspect.getsource(application_workspace_page)
    assert "Opportunity intelligence" in source
    assert "Application answers" in source
    assert "People and relationship intelligence" in source
    assert "Application readiness" in source
    assert "Submission authority: OFF" in source
    assert "gmail_integration" not in source
    assert "playwright" not in source.casefold()
    assert "selenium" not in source.casefold()
    assert "send_email" not in source
    assert "submit_application" not in source


def test_hunter_image_contains_phase17_migration_runner() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert (
        "COPY scripts/phase17_staging_migrate.py ./scripts/phase17_staging_migrate.py"
        in dockerfile
    )


def test_phase17_staging_migration_is_additive_and_verified(hunter_db, monkeypatch) -> None:
    _staging_env(monkeypatch)
    result = phase17_staging_migrate.apply()
    assert result["quick_check"] == "ok"

    connection = sqlite3.connect(hunter_db)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert phase17_staging_migrate.REQUIRED_TABLES <= tables
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        # Canonical opportunity storage is preserved; migration does not replace jobs.
        assert "jobs" in tables
    finally:
        connection.close()


def test_phase17_migration_refuses_production_like_runtime(hunter_db, monkeypatch) -> None:
    _staging_env(monkeypatch)
    monkeypatch.setenv("PRODUCTION_CALLBACKS_ENABLED", "true")
    try:
        phase17_staging_migrate.apply()
    except RuntimeError as error:
        assert "restricted" in str(error).casefold() or "refusing" in str(error).casefold()
    else:
        raise AssertionError("Production-like runtime must be rejected before migration.")
