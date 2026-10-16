"""Tenant-bound Tsenta OAuth connection and short-lived one-use PKCE handoff.

External provider operations occur only after explicit user-facing calls.
Never copy Codex OAuth tokens to Netcup. No production callback routing assumed.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.database import get_connection
from app.secure_vault import _aesgcm, _key
from app.tenant_foundation import current_owner, current_owner_readonly
from app.tenant_foundation import ensure_schema as ensure_tenants

OAUTH_BASE = "https://api.autojobs.me"
REDIRECT_ENV = "MUNSHI_TSENTA_OAUTH_REDIRECT_URI"
CLIENT_ID_ENV = "MUNSHI_TSENTA_OAUTH_CLIENT_ID"
INGRESS_ENV = "MUNSHI_TSENTA_OAUTH_CALLBACK_VERIFIED"
CALLBACK_PATH = "/api/integrations/tsenta/oauth/callback"
INTENT_LIFETIME = timedelta(minutes=10)
PROTOCOL_VERSION = "tsenta-oauth-pkce-v1"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS tsenta_oauth_intents_v1 (
        state_sha256 TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL,
        verifier_ciphertext BLOB NOT NULL, verifier_nonce BLOB NOT NULL,
        expires_at TEXT NOT NULL, consumed_at TEXT, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tsenta_oauth_connections_v1 (
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, client_id TEXT NOT NULL,
        redirect_uri TEXT NOT NULL, access_ciphertext BLOB NOT NULL,
        access_nonce BLOB NOT NULL, refresh_ciphertext BLOB,
        refresh_nonce BLOB, expires_at TEXT, profile_verified_at TEXT,
        connected_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(tenant_id,user_id)
    )""",
)

class TsentaOAuthError(RuntimeError):
    """Non-sensitive external authorization failure."""


def _now() -> datetime:
    return datetime.now(UTC)


def _seal(raw: str, aad: bytes) -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    return _aesgcm()(_key()).encrypt(nonce, raw.encode(), aad), nonce


def _open(cipher: bytes, nonce: bytes, aad: bytes) -> str:
    try:
        return _aesgcm()(_key()).decrypt(nonce, cipher, aad).decode()
    except Exception as error:
        raise TsentaOAuthError("Tsenta OAuth credentials could not be decrypted.") from error


def _aad(tenant: str, user: str, kind: str, identity: str = "") -> bytes:
    return f"{PROTOCOL_VERSION}:{tenant}:{user}:{kind}:{identity}".encode()


def _redirect_uri() -> str:
    if str(os.getenv(INGRESS_ENV) or "").strip().lower() not in {"1", "true", "yes"}:
        raise TsentaOAuthError("The dashboard OAuth callback has not passed ingress verification.")
    value = str(os.getenv(REDIRECT_ENV) or "").strip()
    parsed = urlsplit(value)
    # A Netcup OAuth callback requires known HTTPS routing, not an HTTP tunnel.
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"dashboard.munshi.systems", "staging-dashboard.munshi.systems"}
        or parsed.path != CALLBACK_PATH
        or parsed.query or parsed.fragment or parsed.username or parsed.password
    ):
        raise TsentaOAuthError("A verified dashboard HTTPS callback must be configured.")
    return value


def ensure_schema(conn: sqlite3.Connection) -> None:
    ensure_tenants(conn)
    for ddl in SCHEMA:
        conn.execute(ddl)


def _provider_post(client: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    # Reject redirects and enforce provider hostname; no arbitrary URL accepted.
    response = client.post(
        OAUTH_BASE + path,
        data=payload if path.endswith("/token") else None,
        json=payload if not path.endswith("/token") else None,
        headers={"Accept": "application/json"},
        timeout=15,
        follow_redirects=False,
    )
    if response.status_code not in (200, 201):
        raise TsentaOAuthError("Tsenta authorization server rejected the request.")
    if len(response.content) > 50_000:
        raise TsentaOAuthError("Tsenta authorization response exceeded safe limits.")
    try:
        data = response.json()
    except ValueError as error:
        raise TsentaOAuthError("Tsenta authorization server returned invalid JSON.") from error
    if not isinstance(data, dict):
        raise TsentaOAuthError("Tsenta authorization server returned invalid metadata.")
    return data


def begin(*, client: httpx.Client | None = None) -> dict[str, str]:
    """Explicit account connection initiation; DCR occurs only on this call."""
    redirect = _redirect_uri()
    # Check local crypto and tenant membership BEFORE dynamic client registration.
    # A misconfigured vault must never create an orphaned provider client.
    _key()
    conn = get_connection()
    try:
        ensure_schema(conn)
        current_owner(conn)
        conn.commit()
    finally:
        conn.close()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    owns_client = client is None
    client = client or httpx.Client()
    try:
        client_id = str(os.getenv(CLIENT_ID_ENV) or "").strip()
        if not client_id:
            registered = _provider_post(
                client, "/oauth/register", {
                    "client_name": "MUNSHI Hunter Dashboard",
                    "redirect_uris": [redirect],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
            client_id = str(registered.get("client_id") or "").strip()
        if not client_id or len(client_id) > 300:
            raise TsentaOAuthError("Tsenta OAuth client registration was not verified.")
        conn = get_connection()
        try:
            ensure_schema(conn)
            owner = current_owner(conn)
            digest = hashlib.sha256(state.encode()).hexdigest()
            ciphertext, nonce = _seal(
                verifier, _aad(owner.tenant_id, owner.user_id, "verifier", digest)
            )
            now = _now()
            conn.execute(
                """INSERT INTO tsenta_oauth_intents_v1(
                    state_sha256,tenant_id,user_id,client_id,redirect_uri,
                    verifier_ciphertext,verifier_nonce,expires_at,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (digest,owner.tenant_id,owner.user_id,client_id,redirect,ciphertext,nonce,
                 (now+INTENT_LIFETIME).isoformat(),now.isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        auth = OAUTH_BASE + "/oauth/authorize?" + urlencode({
            "response_type": "code", "client_id": client_id,
            "redirect_uri": redirect, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        return {"authorization_url": auth, "expires_in_seconds": "600"}
    finally:
        if owns_client:
            client.close()


def complete(*, state: str, code: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """One-time state-based callback; caller has no need for a reusable API key."""
    state = str(state or "").strip()
    code = str(code or "").strip()
    if not (20 <= len(state) <= 200 and 2 <= len(code) <= 4096):
        raise TsentaOAuthError("Tsenta authorization callback is incomplete.")
    digest = hashlib.sha256(state.encode()).hexdigest()
    conn = get_connection()
    try:
        installed = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='tsenta_oauth_intents_v1'"
        ).fetchone()
        if installed is None:
            raise TsentaOAuthError("Tsenta authorization was not initiated.")
        initial = conn.execute(
            "SELECT consumed_at,expires_at FROM tsenta_oauth_intents_v1 "
            "WHERE state_sha256=?", (digest,)
        ).fetchone()
        # Unknown callback state is read-only. Do not acquire a write lock.
        if initial is None or initial["consumed_at"] is not None:
            raise TsentaOAuthError("Tsenta authorization state is invalid or used.")
        if datetime.fromisoformat(initial["expires_at"]) <= _now():
            raise TsentaOAuthError("Tsenta authorization state has expired.")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM tsenta_oauth_intents_v1 WHERE state_sha256=?", (digest,)
        ).fetchone()
        if row is None or row["consumed_at"] is not None:
            raise TsentaOAuthError("Tsenta authorization state is invalid or used.")
        if datetime.fromisoformat(row["expires_at"]) <= _now():
            raise TsentaOAuthError("Tsenta authorization state has expired.")
        conn.execute(
            "UPDATE tsenta_oauth_intents_v1 SET consumed_at=? "
            "WHERE state_sha256=? AND consumed_at IS NULL",
            (_now().isoformat(),digest),
        )
        conn.commit()  # Consume before network call; uncertain retry must start afresh.
        owner_tenant,owner_user = str(row["tenant_id"]),str(row["user_id"])
        verifier = _open(
            bytes(row["verifier_ciphertext"]),bytes(row["verifier_nonce"]),
            _aad(owner_tenant,owner_user,"verifier",digest),
        )
        owns_client = client is None
        client = client or httpx.Client()
        try:
            response = _provider_post(client, "/oauth/token", {
                "grant_type": "authorization_code", "code": code,
                "client_id": str(row["client_id"]),
                "redirect_uri": str(row["redirect_uri"]),
                "code_verifier": verifier,
            })
        finally:
            if owns_client:
                client.close()
        access = response.get("access_token")
        refresh = response.get("refresh_token")
        if not isinstance(access,str) or len(access) < 12:
            raise TsentaOAuthError("Tsenta did not return a usable access token.")
        if refresh is not None and not isinstance(refresh,str):
            raise TsentaOAuthError("Tsenta returned invalid refresh token metadata.")
        access_cipher,access_nonce = _seal(access,_aad(owner_tenant,owner_user,"access"))
        refresh_cipher,refresh_nonce = (None,None)
        if refresh:
            refresh_cipher,refresh_nonce = _seal(
                refresh,_aad(owner_tenant,owner_user,"refresh")
            )
        lifetime = response.get("expires_in")
        expires_at = None
        if isinstance(lifetime,(int,float)) and 0 < lifetime <= 365*86400:
            expires_at = (_now()+timedelta(seconds=int(lifetime))).isoformat()
        now = _now().isoformat()
        conn.execute(
            """INSERT INTO tsenta_oauth_connections_v1(
                tenant_id,user_id,client_id,redirect_uri,access_ciphertext,access_nonce,
                refresh_ciphertext,refresh_nonce,expires_at,profile_verified_at,
                connected_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id,user_id) DO UPDATE SET
                client_id=excluded.client_id,redirect_uri=excluded.redirect_uri,
                access_ciphertext=excluded.access_ciphertext,
                access_nonce=excluded.access_nonce,refresh_ciphertext=excluded.refresh_ciphertext,
                refresh_nonce=excluded.refresh_nonce,expires_at=excluded.expires_at,
                profile_verified_at=NULL,updated_at=excluded.updated_at""",
            (owner_tenant,owner_user,row["client_id"],row["redirect_uri"],
             access_cipher,access_nonce,refresh_cipher,refresh_nonce,expires_at,None,now,now),
        )
        conn.commit()
        return {"authorization_exchanged": True, "profile_read_verified": False}
    finally:
        conn.close()


def status() -> dict[str, Any]:
    conn = get_connection()
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tsenta_oauth_connections_v1'"
        ).fetchone() is None:
            return {"authorized": False,"profile_read_verified": False,
                    "reason_code": "BACKEND_OAUTH_NOT_CONFIGURED"}
        owner = current_owner_readonly(conn)
        row = conn.execute(
            """SELECT profile_verified_at,expires_at,refresh_ciphertext
               FROM tsenta_oauth_connections_v1 WHERE tenant_id=? AND user_id=?""",
            (owner.tenant_id,owner.user_id),
        ).fetchone()
        if not row:
            return {"authorized": False,"profile_read_verified": False,
                    "reason_code": "BACKEND_OAUTH_NOT_CONFIGURED"}
        verified = bool(row["profile_verified_at"])
        refreshable = bool(row["refresh_ciphertext"])
        expiry = row["expires_at"]
        usable = not expiry or datetime.fromisoformat(expiry) > _now()+timedelta(minutes=2)
        return {
            "authorized": True, "profile_read_verified": verified,
            "execution_ready": verified and (usable or refreshable),
            "reason_code": "CONNECTED_VERIFIED" if verified else "PROFILE_READ_PENDING",
        }
    finally:
        conn.close()


def disconnect() -> dict[str, bool]:
    """Local disconnect; remote grant revocation requires verified provider support."""
    conn=get_connection()
    try:
        ensure_schema(conn)
        owner=current_owner(conn)
        conn.execute(
            "DELETE FROM tsenta_oauth_connections_v1 WHERE tenant_id=? AND user_id=?",
            (owner.tenant_id,owner.user_id),
        )
        conn.commit()
        return {"disconnected": True}
    finally:
        conn.close()



def _token_for_current_owner(client: httpx.Client | None = None) -> str:
    """Resolve/refresh current tenant's encrypted access token in memory."""
    conn=get_connection()
    owns_client=client is None
    client=client or httpx.Client()
    try:
        owner=current_owner_readonly(conn)
        row=conn.execute(
            "SELECT * FROM tsenta_oauth_connections_v1 WHERE tenant_id=? AND user_id=?",
            (owner.tenant_id,owner.user_id),
        ).fetchone()
        if row is None:
            raise TsentaOAuthError("Tsenta backend account is not connected.")
        access=_open(bytes(row["access_ciphertext"]),bytes(row["access_nonce"]),
                     _aad(owner.tenant_id,owner.user_id,"access"))
        expires=row["expires_at"]
        if not expires or datetime.fromisoformat(expires)>_now()+timedelta(minutes=2):
            return access
        if not row["refresh_ciphertext"] or not row["refresh_nonce"]:
            raise TsentaOAuthError("Tsenta account requires fresh authorization.")
        refresh=_open(bytes(row["refresh_ciphertext"]),bytes(row["refresh_nonce"]),
                      _aad(owner.tenant_id,owner.user_id,"refresh"))
        response=_provider_post(client,"/oauth/token",{
            "grant_type":"refresh_token","refresh_token":refresh,
            "client_id":str(row["client_id"]),
        })
        replacement=response.get("access_token")
        if not isinstance(replacement,str) or len(replacement)<12:
            raise TsentaOAuthError("Tsenta token refresh returned an invalid access token.")
        next_refresh=response.get("refresh_token") or refresh
        if not isinstance(next_refresh,str):
            raise TsentaOAuthError("Tsenta refresh token was invalid.")
        lifetime=response.get("expires_in")
        if not isinstance(lifetime,(int,float)) or not 60 <= lifetime <= 365*86400:
            raise TsentaOAuthError("Tsenta token refresh lacked safe expiry metadata.")
        ac,an=_seal(replacement,_aad(owner.tenant_id,owner.user_id,"access"))
        rc,rn=_seal(next_refresh,_aad(owner.tenant_id,owner.user_id,"refresh"))
        conn.execute(
            """UPDATE tsenta_oauth_connections_v1 SET
                    access_ciphertext=?,access_nonce=?,refresh_ciphertext=?,
                    refresh_nonce=?,expires_at=?,updated_at=?
                WHERE tenant_id=? AND user_id=?""",
            (ac,an,rc,rn,(_now()+timedelta(seconds=int(lifetime))).isoformat(),
             _now().isoformat(),owner.tenant_id,owner.user_id),
        )
        conn.commit()
        return replacement
    finally:
        conn.close()
        if owns_client:
            client.close()


def verify_profile(*, client: httpx.Client | None = None) -> dict[str,bool]:
    """Authenticated read only: never persist or log raw Tsenta candidate data."""
    from app.tsenta_mcp_adapter_v1 import _unwrap
    from app.tsenta_mcp_http_v1 import TsentaMcpReadClient

    token=_token_for_current_owner(client)
    with TsentaMcpReadClient(token,client=client) as mcp:
        mcp.initialize()
        result=_unwrap(mcp.call_read("get_profile_status"),tool_name="get_profile_status")
    if not isinstance(result,dict) or not (
        "onboardingComplete" in result or "hasResume" in result
    ):
        raise TsentaOAuthError("Tsenta account profile was not verified.")
    conn=get_connection()
    try:
        owner=current_owner_readonly(conn)
        updated=conn.execute(
            """UPDATE tsenta_oauth_connections_v1 SET
                profile_verified_at=?,updated_at=?
               WHERE tenant_id=? AND user_id=?""",
            (_now().isoformat(),_now().isoformat(),owner.tenant_id,owner.user_id),
        ).rowcount
        conn.commit()
        if not updated:
            raise TsentaOAuthError("The verified Tsenta connection is no longer present.")
    finally:
        conn.close()
    return {"authenticated_backend":True,"profile_read_verified":True}
