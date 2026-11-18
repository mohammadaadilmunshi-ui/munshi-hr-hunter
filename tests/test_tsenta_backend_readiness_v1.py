from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException

from app import tsenta_backend_readiness_v1


def test_backend_never_confuses_local_codex_auth_with_server_authorization(monkeypatch):
    monkeypatch.setenv("MUNSHI_TSENTA_EXTERNAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("MUNSHI_EXTERNAL_APPLICATION_EXECUTION_ENABLED", "true")
    monkeypatch.setattr("app.tsenta_oauth_v1.status", lambda: {"authorized": False, "profile_read_verified": False})
    state = tsenta_backend_readiness_v1.connection_status()
    assert state["authenticated_backend"] is False
    assert state["execution_ready"] is False
    assert state["local_codex_session_reused"] is False


def test_tsenta_provider_endpoint_fails_closed_before_creating_route(monkeypatch):
    monkeypatch.setenv("HUNTER_API_SECRET", "synthetic-only-api-secret")
    api = importlib.import_module("app.api")
    payload = api.ApplicationProviderSelectionRequest(
        provider="TSENTA",
        verified_employer_url="https://jobs.lever.co/example/role",
        preflight_package_id="synthetic-preflight",
        idempotency_key="synthetic-idempotency",
    )
    with pytest.raises(HTTPException) as result:
        api.select_job_application_provider(7, payload)
    assert result.value.status_code == 503
    assert "not yet authorized" in str(result.value.detail)
