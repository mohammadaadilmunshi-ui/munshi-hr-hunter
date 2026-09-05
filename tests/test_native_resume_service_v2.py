from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from app import database
from app.native_resume_service_v2 import (
    MODEL_OPTIONS,
    delete_personal_api_key,
    ensure_schema,
    rewrite_policy,
    save_personal_api_key,
    save_writer_settings,
    writer_status,
)


def _vault_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def test_writer_status_uses_server_key_without_exposing_it(hunter_db, monkeypatch) -> None:
    monkeypatch.delenv("MUNSHI_VAULT_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-secret-value-for-tests")
    ensure_schema()
    status = writer_status()
    assert status["configured"] is True
    assert status["key_source"] == "server_environment"
    assert "sk-server-secret-value-for-tests" not in json.dumps(status)


def test_personal_openai_key_is_encrypted_and_takes_precedence(hunter_db, monkeypatch) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _vault_key())
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-fallback-value-for-tests")
    ensure_schema()
    personal = "sk-personal-secret-value-for-resume-tests"
    save_personal_api_key(personal)

    status = writer_status()
    assert status["configured"] is True
    assert status["personal_key_saved"] is True
    assert status["key_source"] == "personal_encrypted"
    assert personal not in json.dumps(status)

    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT ciphertext,account_label FROM credential_secret WHERE credential_type='openai_resume_api_key'"
        ).fetchone()
        assert row is not None
        assert personal.encode("utf-8") not in bytes(row["ciphertext"])
        assert ":resume-studio" in str(row["account_label"])
    finally:
        connection.close()

    assert delete_personal_api_key() is True
    assert writer_status()["key_source"] == "server_environment"


def test_writer_settings_persist_candidate_constraints(hunter_db, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-secret-value-for-tests")
    ensure_schema()
    saved = save_writer_settings(
        model_name="gpt-5.6-luna",
        reasoning_effort="low",
        max_output_tokens=3500,
        max_calls_per_generation=1,
    )
    assert saved["model"] == "gpt-5.6-luna"
    assert saved["reasoning_effort"] == "low"
    assert saved["max_output_tokens"] == 3500
    assert saved["max_calls_per_generation"] == 1
    assert "gpt-5.6-terra" in MODEL_OPTIONS


def test_writer_settings_reject_unsafe_or_unbounded_values(hunter_db) -> None:
    ensure_schema()
    with pytest.raises(ValueError, match="Model ID"):
        save_writer_settings(model_name="bad model with spaces")
    with pytest.raises(ValueError, match="between 2,000 and 12,000"):
        save_writer_settings(model_name="gpt-5.6-terra", max_output_tokens=50000)
    with pytest.raises(ValueError, match="must be 1 or 2"):
        save_writer_settings(model_name="gpt-5.6-terra", max_calls_per_generation=3)


def test_rewrite_strengths_are_explicit_and_never_relax_truth_boundary(hunter_db) -> None:
    slight = rewrite_policy("slight")
    medium = rewrite_policy("medium")
    aggressive = rewrite_policy("aggressive")
    assert "light-touch" in slight
    assert "balanced" in medium
    assert "strictly truthful" in aggressive
    assert "never permits invention" in aggressive.casefold()
    with pytest.raises(ValueError, match="Slight, Medium, or Aggressive"):
        rewrite_policy("maximum")


def test_container_wires_writer_secrets_only_from_runtime_environment() -> None:
    root = Path(__file__).resolve().parents[1]
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    example = (root / ".env.example").read_text(encoding="utf-8")
    shadow_example = (root / ".env.netcup.shadow.example").read_text(encoding="utf-8")

    assert "MUNSHI_VAULT_KEY: ${MUNSHI_VAULT_KEY:-}" in compose
    assert "OPENAI_API_KEY: ${OPENAI_API_KEY:-}" in compose
    assert "MUNSHI_RESUME_MODEL: ${MUNSHI_RESUME_MODEL:-gpt-5.6-terra}" in compose
    assert "MUNSHI_VAULT_KEY=\n" in example
    assert "OPENAI_API_KEY=\n" in example
    assert "MUNSHI_VAULT_KEY=\n" in shadow_example
    assert "OPENAI_API_KEY=\n" in shadow_example
    assert "sk-" not in example
    assert "sk-" not in shadow_example
