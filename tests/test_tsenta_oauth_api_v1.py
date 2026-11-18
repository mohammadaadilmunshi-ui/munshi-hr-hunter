from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from app import tsenta_oauth_v1


def test_api_oauth_start_and_status_use_no_account_secrets(monkeypatch):
    monkeypatch.setenv("HUNTER_API_SECRET","synthetic-api-secret")
    api=importlib.import_module("app.api")
    monkeypatch.setattr(
        tsenta_oauth_v1,"begin",
        lambda: {"authorization_url":"https://api.autojobs.me/oauth/authorize?synthetic=true",
                 "expires_in_seconds":"600"}
    )
    result=api.start_tsenta_authorization()
    assert result["authorization_url"].startswith("https://api.autojobs.me/")
    assert "access_token" not in result
    status=api.get_tsenta_connection_status()
    assert status["execution_ready"] is False


def test_api_callback_returns_only_dashboard_redirect(monkeypatch):
    api=importlib.import_module("app.api")
    def completed(*,state,code):
        assert state=="synthetic-state-long-enough-for-PKCE"
        assert code=="synthetic-code"
        return {"authorization_exchanged":True}
    monkeypatch.setattr(tsenta_oauth_v1,"complete",completed)
    response=api.complete_tsenta_authorization(
        state="synthetic-state-long-enough-for-PKCE",code="synthetic-code"
    )
    assert isinstance(response,RedirectResponse)
    assert response.status_code==303
    assert "synthetic-code" not in response.headers["location"]
    assert "synthetic-state" not in response.headers["location"]


def test_api_callback_errors_cannot_reflect_sensitive_input(monkeypatch):
    api=importlib.import_module("app.api")
    def failed(**_kwargs):
        raise tsenta_oauth_v1.TsentaOAuthError("private-token")
    monkeypatch.setattr(tsenta_oauth_v1,"complete",failed)
    with pytest.raises(HTTPException) as error:
        api.complete_tsenta_authorization(
            state="synthetic-state-long-enough-for-PKCE",code="synthetic-code"
        )
    assert error.value.status_code==400
    assert "private-token" not in str(error.value.detail)


def test_api_verify_failure_is_safe(monkeypatch):
    api=importlib.import_module("app.api")
    monkeypatch.setattr(
        tsenta_oauth_v1,"verify_profile",
        lambda: (_ for _ in ()).throw(
            tsenta_oauth_v1.TsentaOAuthError("internal-private-error")
        )
    )
    with pytest.raises(HTTPException) as error:
        api.verify_tsenta_authorization()
    assert error.value.status_code==409
    assert "internal-private-error" not in str(error.value.detail)



def test_compose_forwards_oauth_and_status_only_secret_fail_closed():
    from pathlib import Path

    source = Path("compose.yaml").read_text()
    for name in (
        "MUNSHI_HUNTER_STATUS_BRIDGE_SECRET",
        "MUNSHI_TSENTA_OAUTH_REDIRECT_URI",
        "MUNSHI_TSENTA_OAUTH_CLIENT_ID",
        "MUNSHI_TSENTA_OAUTH_CALLBACK_VERIFIED",
    ):
        assert f"      {name}: " in source
    prefix = chr(36)
    assert (
        "MUNSHI_TSENTA_OAUTH_CALLBACK_VERIFIED: "
        + prefix + "{MUNSHI_TSENTA_OAUTH_CALLBACK_VERIFIED:-false}"
    ) in source
    assert (
        "MUNSHI_HUNTER_STATUS_BRIDGE_SECRET: "
        + prefix + "{MUNSHI_HUNTER_STATUS_BRIDGE_SECRET:-}"
    ) in source
