from __future__ import annotations

import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import device_auth


@pytest.fixture(autouse=True)
def isolated_device_key(monkeypatch: pytest.MonkeyPatch, tmp_path):
    key_file = tmp_path / "dashboard-device-auth.key"
    monkeypatch.setenv("MUNSHI_DEVICE_AUTH_KEY_FILE", str(key_file))
    monkeypatch.setenv("MUNSHI_DEVICE_AUTH_TTL_SECONDS", "2592000")
    device_auth.reset_signing_key_cache()
    yield key_file
    device_auth.reset_signing_key_cache()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(device_auth.router)
    return TestClient(app, base_url="https://dashboard.munshi.systems")


def test_signed_device_token_survives_process_cache_reset(isolated_device_key):
    token = device_auth.mint_device_token("aadil", now=1_000_000)
    assert device_auth.verify_device_token(token, now=1_000_001)["sub"] == "aadil"

    device_auth.reset_signing_key_cache()
    assert device_auth.verify_device_token(token, now=1_000_002)["sub"] == "aadil"
    assert isolated_device_key.exists()
    assert stat.S_IMODE(isolated_device_key.stat().st_mode) == 0o600


def test_device_token_expires_and_tampering_fails_closed():
    token = device_auth.mint_device_token("aadil", now=10_000, ttl_seconds=3600)
    assert device_auth.verify_device_token(token, now=13_599) is not None
    assert device_auth.verify_device_token(token, now=13_600) is None

    payload, signature = token.split(".", 1)
    replacement = "A" if signature[0] != "A" else "B"
    tampered = payload + "." + replacement + signature[1:]
    assert device_auth.verify_device_token(tampered, now=10_001) is None


def test_login_requires_edge_authenticated_user_and_sets_persistent_secure_cookie():
    client = _client()
    denied = client.get("/_munshi-auth/login", follow_redirects=False)
    assert denied.status_code == 401

    response = client.get(
        "/_munshi-auth/login?next=%2Fbrowse%3Fview%3Dsaved",
        headers={"X-Munshi-Auth-User": "aadil"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/browse?view=saved"
    cookie = response.headers["set-cookie"].lower()
    assert "__host-munshi_device_session=" in cookie
    assert "max-age=2592000" in cookie
    assert "path=/" in cookie
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert response.headers["cache-control"] == "no-store, private"


def test_valid_cookie_authorizes_forward_auth_without_password_reprompt():
    client = _client()
    enrolled = client.get(
        "/_munshi-auth/login",
        headers={"X-Munshi-Auth-User": "aadil"},
        follow_redirects=False,
    )
    assert enrolled.status_code == 303

    verified = client.get(
        "/_munshi-auth/verify",
        headers={"X-Munshi-Original-URI": "/browse"},
        follow_redirects=False,
    )
    assert verified.status_code == 204
    assert verified.headers["x-munshi-auth-user"] == "aadil"


def test_missing_cookie_redirects_to_password_enrollment_with_local_return_path():
    client = _client()
    response = client.get(
        "/_munshi-auth/verify",
        headers={"X-Munshi-Original-URI": "/tracker?state=review"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == (
        "/_munshi-auth/login?next=%2Ftracker%3Fstate%3Dreview"
    )


def test_open_redirects_and_auth_loops_are_rejected():
    assert device_auth.safe_next_path("https://evil.example/") == "/"
    assert device_auth.safe_next_path("//evil.example/") == "/"
    assert device_auth.safe_next_path("/_munshi-auth/login") == "/"
    assert device_auth.safe_next_path("/dashboard") == "/dashboard"


def test_logout_clears_device_cookie():
    client = _client()
    client.get(
        "/_munshi-auth/login",
        headers={"X-Munshi-Auth-User": "aadil"},
        follow_redirects=False,
    )
    response = client.get("/_munshi-auth/logout", follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "__host-munshi_device_session=" in cookie
    assert "max-age=0" in cookie
