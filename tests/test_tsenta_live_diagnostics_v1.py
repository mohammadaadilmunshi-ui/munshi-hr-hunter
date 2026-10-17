"""No-network diagnostic contract tests. Never send an application."""
from __future__ import annotations

from types import SimpleNamespace

from app import tsenta_live_diagnostics_v1 as diag
from app.tsenta_mcp_adapter_v1 import TsentaAccountSnapshot


class FakeDB:
    def __init__(self, alias: str | None = "random@example.net"):
        self.alias = alias
        self.closed = False

    def execute(self, sql, args=()):
        if sql.startswith("PRAGMA"):
            return SimpleNamespace(fetchone=lambda: None)
        assert "ats_application_identities" in sql
        assert len(args) == 2
        return SimpleNamespace(
            fetchone=lambda: {"email_address": self.alias} if self.alias else None
        )

    def close(self):
        self.closed = True


class FakeClient:
    tools = (
        {"name": "upload-custom-resume"},
        {"name": "check-custom-resume"},
        {"name": "apply-with-custom-resume"},
    )
    called: tuple[str, ...] = ()

    def __init__(self, token):
        assert token == "fake-token"
        self._tools = {
            "get_profile_status": "get-profile-status",
            "list_resume_profiles": "list-resume-profiles",
            "get_resume_profile": "get-resume-profile",
            "get_apply_preferences": "get-apply-preferences",
            "get_auto_apply_state": "get-auto-apply-state",
        }

    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def initialize(self): return None

    def call_read(self, name, args=None):
        self.called += (name,)
        assert name not in {"upload_custom_resume", "apply_with_custom_resume"}
        return {}

    def _send(self, name, args):
        assert name == "tools/list"
        return {"tools": self.tools}


def _setup(monkeypatch, *, stored_email="random@example.net", alias="random@example.net"):
    from app import (
        mailbox_verification_service,
        tsenta_mcp_adapter_v1,
        tsenta_mcp_http_v1,
        tsenta_oauth_v1,
    )

    db = FakeDB(alias=alias)
    monkeypatch.setattr(diag, "get_connection", lambda: db)
    monkeypatch.setattr(diag, "current_owner_readonly",
        lambda connection: SimpleNamespace(tenant_id="t", user_id="u"))
    monkeypatch.setattr(mailbox_verification_service, "runtime_health",
        lambda: {"ready": True})
    monkeypatch.setattr(tsenta_oauth_v1, "_token_for_current_owner",
        lambda: "fake-token")
    monkeypatch.setattr(tsenta_mcp_http_v1, "TsentaMcpReadClient", FakeClient)
    account = TsentaAccountSnapshot(
        profile_status={"onboardingComplete": True, "hasResume": True},
        resume_profiles=({"id": "one", "isDefault": True},),
        selected_resume_profile={"data": {"personalInformation": {
            "email": stored_email,
        }}},
        apply_preferences={},
        auto_apply_state={},
    )
    monkeypatch.setattr(tsenta_mcp_adapter_v1.TsentaMcpAdapter,
        "read_account_snapshot", lambda *_: account)
    return db


def test_verified_status_preserves_non_submission_and_no_private_email(monkeypatch):
    db = _setup(monkeypatch)
    response = diag.diagnose_submission_setup()
    assert db.closed
    assert response["profile_verified"] is True
    assert response["managed_email_matches_tsenta"] is True
    assert response["custom_resume_tools_present"] is True
    assert response["execution_ready"] is False
    assert "random@example.net" not in str(response)
    assert "ONE_USE_DISPATCH_WORKER_NOT_VALIDATED" in response["reason_codes"]


def test_live_profile_email_mismatch_is_a_visible_reason(monkeypatch):
    _setup(monkeypatch, stored_email="different@example.net")
    response = diag.diagnose_submission_setup()
    assert response["managed_email_matches_tsenta"] is False
    assert "TSENTA_PROFILE_EMAIL_DIFFERS_FROM_MUNSHI_ALIAS" in response["reason_codes"]


def test_managed_alias_not_provisioned_stays_blocked(monkeypatch):
    _setup(monkeypatch, alias=None)
    response = diag.diagnose_submission_setup()
    assert response["managed_email_active"] is False
    assert "MUNSHI_ACTIVE_APPLICATION_ALIAS_MISSING" in response["reason_codes"]


def test_dashboard_exposes_explicit_readonly_diagnostic(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from app import tsenta_oauth_v1
    from app.application_provider_ui_v1 import render_connection_panel

    monkeypatch.setattr(tsenta_oauth_v1, "status",
        lambda: {"authorized": True, "profile_read_verified": True})
    a = AppTest.from_function(render_connection_panel).run()
    assert not list(a.exception)
    assert any(button.label == "Check Tsenta application readiness"
               for button in a.button)
