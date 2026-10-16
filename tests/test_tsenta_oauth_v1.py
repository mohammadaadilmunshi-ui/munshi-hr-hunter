from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app import tsenta_oauth_v1 as auth

KEY = bytes(range(32))


@pytest.fixture(autouse=True)
def oauth_sandbox(monkeypatch, hunter_db):
    del hunter_db
    monkeypatch.setattr(auth,"_key",lambda:KEY)
    monkeypatch.setenv(auth.REDIRECT_ENV,
        "https://staging-dashboard.munshi.systems/api/integrations/tsenta/oauth/callback")
    monkeypatch.setenv(auth.CLIENT_ID_ENV,"synthetic-client-id")
    monkeypatch.setenv(auth.INGRESS_ENV,"true")


def make_client(*, code="code-synthetic-value", expires=3600):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.path=="/oauth/token":
            return httpx.Response(200,json={
                "access_token":"private-access-token-long",
                "refresh_token":"private-refresh-token-long",
                "expires_in":expires,
                "token_type":"Bearer",
            })
        raise AssertionError("unexpected URL "+str(request.url.path))
    return httpx.Client(transport=httpx.MockTransport(handler)),calls


def test_oauth_begin_uses_one_time_secret_pkce_and_no_raw_verifier_in_db(monkeypatch):
    client,calls=make_client()
    data=auth.begin(client=client)
    assert not calls
    parsed=urlsplit(data["authorization_url"])
    assert parsed.hostname=="api.autojobs.me"
    assert parsed.path=="/oauth/authorize"
    values=parse_qs(parsed.query)
    assert values["code_challenge_method"]==["S256"]
    assert values["client_id"]==["synthetic-client-id"]
    assert "code_verifier" not in parsed.query
    assert data["expires_in_seconds"]=="600"

    from app.database import get_connection
    with get_connection() as c:
        row=c.execute("SELECT * FROM tsenta_oauth_intents_v1").fetchone()
        assert row["state_sha256"] == hashlib.sha256(values["state"][0].encode()).hexdigest()
        assert "code_verifier" not in str(dict(row))
    result=auth.complete(state=values["state"][0],code="synthetic-code",client=client)
    assert result["authorization_exchanged"] is True
    assert auth.status()["authorized"] is True
    assert auth.status()["profile_read_verified"] is False
    with pytest.raises(auth.TsentaOAuthError, match="used"):
        auth.complete(state=values["state"][0],code="synthetic-code",client=client)
    with get_connection() as c:
        row=c.execute("SELECT * FROM tsenta_oauth_connections_v1").fetchone()
        assert b"private-access-token-long" not in bytes(row["access_ciphertext"])
        assert b"private-refresh-token-long" not in bytes(row["refresh_ciphertext"])
        assert row["profile_verified_at"] is None
    assert all(r.url.host=="api.autojobs.me" for r in calls)


def test_oauth_refuses_insecure_or_offdomain_callbacks(monkeypatch):
    for value in ["http://dashboard.munshi.systems"+auth.CALLBACK_PATH,
                  "https://evil.example"+auth.CALLBACK_PATH,
                  "https://dashboard.munshi.systems/other"]:
        monkeypatch.setenv(auth.REDIRECT_ENV,value)
        with pytest.raises(auth.TsentaOAuthError):
            auth.begin(client=make_client()[0])


def test_oauth_no_live_dcr_for_synthetic_preconfigured_client():
    client,calls=make_client()
    auth.begin(client=client)
    assert calls==[]


def test_local_disconnection_clears_only_connection(hunter_db):
    del hunter_db
    client,_=make_client()
    url=auth.begin(client=client)["authorization_url"]
    state=parse_qs(urlsplit(url).query)["state"][0]
    auth.complete(state=state,code="synthetic-code",client=client)
    assert auth.disconnect()=={"disconnected":True}
    assert auth.status()["authorized"] is False


def test_invalid_callback_is_rejected_before_network():
    client,calls=make_client()
    with pytest.raises(auth.TsentaOAuthError):
        auth.complete(state="invalid",code="invalid",client=client)
    assert not calls



def test_backend_profile_verification_uses_actual_mcp_read_without_persisting_profile():
    client,_calls=make_client()
    url=auth.begin(client=client)["authorization_url"]
    state=parse_qs(urlsplit(url).query)["state"][0]
    auth.complete(state=state,code="synthetic-code",client=client)
    assert auth.status()["profile_read_verified"] is False

    mcp_calls=[]
    def mcp_handler(request):
        mcp_calls.append(request)
        payload=json.loads(request.content)
        if payload["method"]=="notifications/initialized":
            return httpx.Response(202)
        if payload["method"]=="initialize":
            return httpx.Response(200,headers={"Mcp-Session-Id":"synthetic-test-session"},json={
                "jsonrpc":"2.0","id":payload["id"],
                "result":{"protocolVersion":"2025-11-25","serverInfo":{"name":"test","version":"0"},"capabilities":{}}
            })
        if payload["method"]=="tools/list":
            return httpx.Response(200,json={"jsonrpc":"2.0","id":payload["id"],
                "result":{"tools":[{"name":"get-profile-status","inputSchema":{"type":"object"}}]}})
        if payload["method"]=="tools/call":
            assert payload["params"]["name"]=="get-profile-status"
            return httpx.Response(200,json={"jsonrpc":"2.0","id":payload["id"],
                "result":{"structuredContent":{"result":{"onboardingComplete":True,"hasResume":True}}}})
        raise AssertionError("unexpected MCP method")

    with httpx.Client(transport=httpx.MockTransport(mcp_handler)) as mcp_client:
        assert auth.verify_profile(client=mcp_client)=={
            "authenticated_backend":True,"profile_read_verified":True}
    assert auth.status()["profile_read_verified"] is True
    assert auth.status()["execution_ready"] is False
    assert auth.status()["profile_token_usable_or_refreshable"] is True
    assert len(mcp_calls)==4
    assert all(x.url.host=="api.autojobs.me" for x in mcp_calls)
    assert all("Bearer" in x.headers.get("authorization","") for x in mcp_calls)

    from app.database import get_connection
    with get_connection() as c:
        row=c.execute("SELECT * FROM tsenta_oauth_connections_v1").fetchone()
        assert "onboardingComplete" not in str(dict(row))
        assert b"private-access-token" not in bytes(row["access_ciphertext"])


def test_expired_access_token_refreshes_with_form_post(monkeypatch):
    client,_=make_client(expires=1)
    state=parse_qs(urlsplit(auth.begin(client=client)["authorization_url"]).query)["state"][0]
    auth.complete(state=state,code="synthetic-code",client=client)
    with httpx.Client(transport=httpx.MockTransport(lambda req:
        httpx.Response(200,json={
            "access_token":"updated-access-token-long","refresh_token":"updated-refresh-token-long",
            "expires_in":3600})
    )) as test_client:
        token=auth._token_for_current_owner(test_client)
    assert token=="updated-access-token-long"
    assert auth.status()["authorized"] is True



def test_oauth_cannot_start_until_callback_ingress_is_approved(monkeypatch):
    monkeypatch.delenv(auth.INGRESS_ENV,raising=False)
    client,calls=make_client()
    with pytest.raises(auth.TsentaOAuthError,match="ingress verification"):
        auth.begin(client=client)
    assert calls==[]



def test_dynamic_client_registration_only_occurs_on_explicit_begin(monkeypatch):
    monkeypatch.delenv(auth.CLIENT_ID_ENV)
    calls=[]
    def register(request):
        calls.append(request)
        assert request.url.path=="/oauth/register"
        body=json.loads(request.content)
        assert body["token_endpoint_auth_method"]=="none"
        assert body["redirect_uris"] == [
            "https://staging-dashboard.munshi.systems/api/integrations/tsenta/oauth/callback"
        ]
        return httpx.Response(201,json={"client_id":"new-synthetic-client-id"})
    with httpx.Client(transport=httpx.MockTransport(register)) as client:
        result=auth.begin(client=client)
    assert len(calls)==1
    assert parse_qs(urlsplit(result["authorization_url"]).query)["client_id"]==[
        "new-synthetic-client-id"
    ]


def test_expired_code_state_does_not_call_provider(monkeypatch):
    client,calls=make_client()
    url=auth.begin(client=client)["authorization_url"]
    state=parse_qs(urlsplit(url).query)["state"][0]
    from app.database import get_connection
    with get_connection() as conn:
        conn.execute(
            "UPDATE tsenta_oauth_intents_v1 SET expires_at=?",
            ("2000-01-01T00:00:00+00:00",)
        )
        conn.commit()
    with pytest.raises(auth.TsentaOAuthError,match="expired"):
        auth.complete(state=state,code="synthetic-code",client=client)
    assert calls==[]



def test_oauth_migration_059_is_additive_and_idempotent(hunter_db):
    del hunter_db
    import importlib.util
    from pathlib import Path

    from app.database import get_connection

    file=Path("migrations/059_tsenta_backend_oauth_v1.py")
    spec=importlib.util.spec_from_file_location("tsenta_migration_059",file)
    assert spec and spec.loader
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with get_connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS migration_sentinel (marker TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO migration_sentinel(marker) VALUES ('keep')")
        module.apply(conn)
        module.apply(conn)
        conn.commit()
        tables={r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "tsenta_oauth_intents_v1" in tables
        assert "tsenta_oauth_connections_v1" in tables
        assert conn.execute("SELECT marker FROM migration_sentinel").fetchone()[0]=="keep"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0]=="ok"



def test_unknown_public_callback_never_installs_tables_or_touches_provider(hunter_db):
    del hunter_db
    from app.database import get_connection

    client,calls=make_client()
    with get_connection() as conn:
        before={row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "tsenta_oauth_intents_v1" not in before
    with pytest.raises(auth.TsentaOAuthError,match="not initiated"):
        auth.complete(state="synthetic-long-invalid-callback-state",
                      code="synthetic-code",client=client)
    with get_connection() as conn:
        after={row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert after==before
    assert not calls



def test_oauth_registration_response_is_rejected_before_unbounded_buffering(monkeypatch):
    monkeypatch.delenv(auth.CLIENT_ID_ENV)
    calls=[]
    def oversized(request):
        calls.append(request)
        return httpx.Response(
            200,content=b"{" + b"x"*60_000 + b"}",
            headers={"content-type":"application/json"},
        )
    with (
        httpx.Client(transport=httpx.MockTransport(oversized)) as client,
        pytest.raises(auth.TsentaOAuthError, match="safe limits"),
    ):
        auth.begin(client=client)
    assert len(calls)==1
