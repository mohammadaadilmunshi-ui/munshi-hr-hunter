from __future__ import annotations

import pytest

from deploy.ops import verify_original_dashboard_learning as smoke


def _fake_request(_origin, path, headers=None):
    headers = headers or {}
    if path == "/health":
        return 200, {"success": True}
    if path.endswith("/application-smoke-does-not-exist"):
        if headers.get("X-Munshi-Status-Bridge-Secret") == "status-only":
            return 404, {"detail": "Not found"}
        return 401, {"detail": "Protected"}
    if not headers.get("X-Hunter-Secret"):
        return 401, {"detail": "Protected"}
    if path.endswith("/tsenta/status"):
        return 200, {"provider": "TSENTA",
                     "authenticated_backend": True,
                     "profile_read_verified": True,
                     "execution_ready": False}
    if path.endswith("/learning/cohort"):
        return 200, {"version": "munshi-provider-learning-cohort-v1",
                     "processed_applications_total": 12,
                     "cohort_processed": 12,
                     "phase": "EVALUATION_READY",
                     "unique_ats_in_cohort": 6,
                     "auto_upgrade_enabled": False}
    raise AssertionError("Unexpected endpoint: " + path)


def test_original_dashboard_postdeploy_smoke_is_strictly_readonly(monkeypatch):
    calls = []

    def spy(origin, path, headers=None):
        calls.append((origin, path, dict(headers or {})))
        return _fake_request(origin, path, headers)

    monkeypatch.setattr(smoke, "_request", spy)
    result = smoke.run_readonly_smoke(
        api_secret="synthetic-api", status_secret="status-only",
    )
    assert result["all_smoke_checks_passed"]
    assert result["ready_for_learning_observation"]
    assert result["learning"]["verified_processed_applications"] == 12
    assert result["learning"]["ats_platforms"] == 6
    assert result["tsenta"]["execution_ready"] is False
    assert result["live_submit_attempts"] == 0
    assert all(path.startswith("/") for _, path, _ in calls)
    assert all("submit" not in path and "sync" not in path
               and "oauth/start" not in path for _, path, _ in calls)
    assert all(origin == "http://127.0.0.1:8000" for origin, _, _ in calls)


def test_smoke_only_targets_loopback_and_never_forwards_secret_to_public_host():
    with pytest.raises(ValueError, match="loopback"):
        smoke.run_readonly_smoke(
            origin="https://dashboard.munshi.systems",
            api_secret="do-not-send-to-public",
        )


def test_smoke_can_report_disconnected_backend_without_fabricating_readiness(monkeypatch):
    monkeypatch.setattr(smoke, "_request", _fake_request)
    result = smoke.run_readonly_smoke()
    assert result["all_smoke_checks_passed"]
    assert result["ready_for_learning_observation"] is False
    assert result["learning"]["verified_processed_applications"] is None


def test_smoke_rejects_incorrect_learning_contract(monkeypatch):
    def forged(origin, path, headers=None):
        status, body = _fake_request(origin, path, headers)
        if path.endswith("/learning/cohort"):
            body["auto_upgrade_enabled"] = True
        return status, body

    monkeypatch.setattr(smoke, "_request", forged)
    result = smoke.run_readonly_smoke(api_secret="synthetic-api")
    assert not result["all_smoke_checks_passed"]
    assert "learning_cohort_contract" in result["failed"]


def test_smoke_flags_unexpected_auth_exposure(monkeypatch):
    def exposed(origin, path, headers=None):
        if path.endswith("/learning/cohort") and not headers:
            return 200, {"processed_applications_total": 12}
        return _fake_request(origin, path, headers)

    monkeypatch.setattr(smoke, "_request", exposed)
    result = smoke.run_readonly_smoke()
    assert not result["all_smoke_checks_passed"]
    assert "cohort_requires_auth" in result["failed"]




def test_unauthorized_redirect_never_forwards_hunter_secret():
    import urllib.request

    handler = smoke._RejectRedirect()
    request = urllib.request.Request(
        "http://127.0.0.1:8000/api/integrations/tsenta/status",
        headers={"X-Hunter-Secret": "synthetic-private-secret"},
    )
    assert handler.redirect_request(
        request, None, 302, "redirect",
        {"Location": "https://bad.example/steal"},
        "https://bad.example/steal",
    ) is None
