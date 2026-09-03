"""Explicit Gmail integration: read-only, click-triggered, and vault-backed."""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from app.database import get_connection
from app.secure_vault import delete_secret, read_secret, store_secret, vault_available

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_STATE_TTL_SECONDS = 600
_PKCE_CREDENTIAL_TYPE = "gmail_oauth_pkce_verifier"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS gmail_integration_state (
          account_label TEXT PRIMARY KEY, email_address TEXT, sync_cursor TEXT,
          last_sync_at TEXT, last_error_code TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS gmail_oauth_state (
          state_hash TEXT PRIMARY KEY, account_label TEXT NOT NULL, expires_at TEXT NOT NULL,
          used_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_gmail_oauth_state_expiry ON gmail_oauth_state(expires_at);
        """)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def gmail_configuration_status() -> dict[str, Any]:
    client_id = str(os.getenv("MUNSHI_GMAIL_CLIENT_ID") or "").strip()
    client_secret = str(os.getenv("MUNSHI_GMAIL_CLIENT_SECRET") or "").strip()
    redirect = str(os.getenv("MUNSHI_GMAIL_REDIRECT_URI") or "").strip()
    return {
        "ready": bool(client_id and client_secret and redirect and vault_available()),
        "oauth_client": "Configured" if client_id and client_secret and redirect else "Not configured",
        "vault": "Configured" if vault_available() else "Not configured",
        "scope": GMAIL_READONLY_SCOPE,
    }


def authorization_url(state: str, code_challenge: str) -> str:
    if not gmail_configuration_status()["ready"]:
        raise RuntimeError("Gmail OAuth is not configured.")
    return OAUTH_AUTHORIZE_URL + "?" + urlencode({
        "client_id": os.environ["MUNSHI_GMAIL_CLIENT_ID"], "redirect_uri": os.environ["MUNSHI_GMAIL_REDIRECT_URI"],
        "response_type": "code", "scope": GMAIL_READONLY_SCOPE, "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": code_challenge, "code_challenge_method": "S256",
    })


def begin_authorization(account_label: str = "default") -> str:
    """Create a one-time OAuth state plus encrypted PKCE verifier after a click."""
    if not gmail_configuration_status()["ready"]:
        raise RuntimeError("Gmail OAuth is not configured.")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    digest = _state_hash(state)
    store_secret(_PKCE_CREDENTIAL_TYPE, verifier, account_label=digest)
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("DELETE FROM gmail_oauth_state WHERE expires_at < ? OR used_at IS NOT NULL", (_now().isoformat(),))
        connection.execute("INSERT INTO gmail_oauth_state(state_hash,account_label,expires_at) VALUES (?,?,?)", (digest, str(account_label or "default")[:120], (_now() + timedelta(seconds=OAUTH_STATE_TTL_SECONDS)).isoformat()))
        connection.commit()
    except Exception:
        delete_secret(_PKCE_CREDENTIAL_TYPE, account_label=digest)
        raise
    finally:
        connection.close()
    return authorization_url(state, _challenge(verifier))


def _consume_state(state: str) -> tuple[str, str]:
    if not state or len(state) > 512:
        raise RuntimeError("OAuth authorization could not be validated.")
    digest = _state_hash(state)
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute("SELECT account_label,expires_at,used_at FROM gmail_oauth_state WHERE state_hash=?", (digest,)).fetchone()
        if row is None or row["used_at"] is not None or str(row["expires_at"]) <= _now().isoformat():
            raise RuntimeError("OAuth authorization could not be validated.")
        changed = connection.execute("UPDATE gmail_oauth_state SET used_at=CURRENT_TIMESTAMP WHERE state_hash=? AND used_at IS NULL", (digest,)).rowcount
        connection.commit()
        verifier = read_secret(_PKCE_CREDENTIAL_TYPE, account_label=digest)
        if changed != 1 or not verifier:
            raise RuntimeError("OAuth authorization could not be validated.")
        return str(row["account_label"]), verifier
    finally:
        delete_secret(_PKCE_CREDENTIAL_TYPE, account_label=digest)
        connection.close()


def save_refresh_token(account_label: str, refresh_token: str, email_address: str | None = None) -> None:
    if not refresh_token:
        raise ValueError("OAuth authorization did not return a refresh token.")
    store_secret("gmail_refresh_token", refresh_token, account_label=account_label)
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("INSERT INTO gmail_integration_state(account_label,email_address,updated_at) VALUES (?,?,CURRENT_TIMESTAMP) ON CONFLICT(account_label) DO UPDATE SET email_address=excluded.email_address,updated_at=CURRENT_TIMESTAMP", (account_label, email_address))
        connection.commit()
    finally:
        connection.close()


def exchange_authorization_code(code: str, *, account_label: str, code_verifier: str) -> None:
    if not code.strip() or not code_verifier or not gmail_configuration_status()["ready"]:
        raise RuntimeError("Gmail OAuth is not configured.")
    import httpx
    response = httpx.post(OAUTH_TOKEN_URL, data={
        "code": code, "client_id": os.environ["MUNSHI_GMAIL_CLIENT_ID"], "client_secret": os.environ["MUNSHI_GMAIL_CLIENT_SECRET"],
        "redirect_uri": os.environ["MUNSHI_GMAIL_REDIRECT_URI"], "grant_type": "authorization_code", "code_verifier": code_verifier,
    }, timeout=20)
    response.raise_for_status()
    token = str(response.json().get("refresh_token") or "")
    if not token:
        raise RuntimeError("OAuth authorization did not return a refresh token.")
    save_refresh_token(account_label, token)


def complete_authorization(code: str, state: str) -> None:
    account_label, verifier = _consume_state(state)
    exchange_authorization_code(code, account_label=account_label, code_verifier=verifier)


def connection_status(account_label: str = "default") -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute("SELECT email_address,last_sync_at,last_error_code FROM gmail_integration_state WHERE account_label=?", (account_label,)).fetchone()
    finally:
        connection.close()
    connected = bool(row and read_secret("gmail_refresh_token", account_label=account_label))
    email = str(row["email_address"] or "") if row else ""
    masked = email[:1] + "•••" + email[email.find("@"):] if "@" in email else "Connected account" if connected else ""
    return {"connected": connected, "account": masked, "last_sync_at": row["last_sync_at"] if row else None, "last_error_code": row["last_error_code"] if row else None}


def _access_token(account_label: str) -> str:
    refresh_token = read_secret("gmail_refresh_token", account_label=account_label)
    if not refresh_token:
        raise RuntimeError("Gmail is not connected.")
    import httpx
    response = httpx.post(OAUTH_TOKEN_URL, data={"client_id": os.environ["MUNSHI_GMAIL_CLIENT_ID"], "client_secret": os.environ["MUNSHI_GMAIL_CLIENT_SECRET"], "refresh_token": refresh_token, "grant_type": "refresh_token"}, timeout=20)
    response.raise_for_status()
    token = str(response.json().get("access_token") or "")
    if not token:
        raise RuntimeError("Gmail token refresh returned no access token.")
    return token


def _record_sync_error(account_label: str) -> None:
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("INSERT INTO gmail_integration_state(account_label,last_error_code,updated_at) VALUES (?,'sync_failed',CURRENT_TIMESTAMP) ON CONFLICT(account_label) DO UPDATE SET last_error_code='sync_failed',updated_at=CURRENT_TIMESTAMP", (account_label,))
        connection.commit()
    finally:
        connection.close()


def sync_messages(account_label: str = "default", *, max_messages: int = 25) -> int:
    """Explicit bounded sync; access tokens exist only for this call."""
    if not gmail_configuration_status()["ready"]:
        raise RuntimeError("Gmail OAuth is not configured.")
    import httpx
    token = _access_token(account_label)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages", headers=headers, params={"maxResults": max(1, min(int(max_messages), 100))}, timeout=20)
        response.raise_for_status()
        inserted = 0
        for item in response.json().get("messages", []):
            message_id = str(item.get("id") or "")
            if not message_id:
                continue
            detail = httpx.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}", headers=headers, params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]}, timeout=20)
            detail.raise_for_status()
            data = detail.json()
            by_name = {str(header.get("name") or "").casefold(): str(header.get("value") or "") for header in data.get("payload", {}).get("headers", [])}
            inserted += int(upsert_message({"id": message_id, "thread_id": data.get("threadId"), "subject": by_name.get("subject"), "sender": by_name.get("from"), "received_at": by_name.get("date"), "snippet": data.get("snippet")}))
    except Exception:
        _record_sync_error(account_label)
        raise RuntimeError("Gmail sync did not complete.") from None
    finally:
        token = ""
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("INSERT INTO gmail_integration_state(account_label,last_sync_at,updated_at) VALUES (?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) ON CONFLICT(account_label) DO UPDATE SET last_sync_at=CURRENT_TIMESTAMP,last_error_code=NULL,updated_at=CURRENT_TIMESTAMP", (account_label,))
        connection.commit()
    finally:
        connection.close()
    return inserted


def disconnect(account_label: str = "default") -> bool:
    deleted = delete_secret("gmail_refresh_token", account_label=account_label)
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("DELETE FROM gmail_integration_state WHERE account_label=?", (account_label,))
        connection.commit()
    finally:
        connection.close()
    return deleted


def classify_message(subject: str, sender: str, snippet: str) -> tuple[str, str]:
    text = f"{subject} {sender} {snippet}".casefold()
    categories = (("interview", ("interview", "schedule a call")), ("assessment", ("assessment", "coding challenge", "complete this test")), ("offer", ("offer letter", "we are pleased to offer")), ("rejection", ("not moving forward", "regret to inform", "not selected")), ("verification", ("verify your email", "confirm your email")), ("reminder", ("reminder", "action required")), ("applied", ("application received", "thank you for applying")))
    for category, phrases in categories:
        phrase = next((item for item in phrases if item in text), None)
        if phrase:
            return category, f"Matched explicit phrase: {phrase}"
    return "unclassified", "No deterministic application-message phrase matched."


def upsert_message(message: dict[str, Any]) -> bool:
    message_id = str(message.get("id") or "").strip()
    if not message_id:
        raise ValueError("Gmail message id is required.")
    category, evidence = classify_message(str(message.get("subject") or ""), str(message.get("sender") or ""), str(message.get("snippet") or ""))
    connection = get_connection()
    try:
        from app.product_state import ensure_schema as ensure_product_schema
        ensure_product_schema(connection)
        cursor = connection.execute("INSERT OR IGNORE INTO gmail_messages(gmail_message_id,thread_id,category,subject,sender,received_at,snippet,body_text,classification_evidence) VALUES (?,?,?,?,?,?,?,?,?)", (message_id, message.get("thread_id"), category, message.get("subject"), message.get("sender"), message.get("received_at"), message.get("snippet"), message.get("body_text"), evidence))
        connection.commit()
        return bool(cursor.rowcount)
    finally:
        connection.close()


def stored_messages(*, category: str = "All", query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    from app.product_state import ensure_schema as ensure_product_schema
    connection = get_connection()
    try:
        ensure_product_schema(connection)
        where, params = ["1=1"], []
        if category and category != "All":
            where.append("category=?")
            params.append(category)
        if query.strip():
            needle = f"%{query.strip()}%"
            where.append("(subject LIKE ? COLLATE NOCASE OR sender LIKE ? COLLATE NOCASE OR snippet LIKE ? COLLATE NOCASE)")
            params.extend((needle, needle, needle))
        rows = connection.execute(f"SELECT category,subject,sender,received_at,snippet FROM gmail_messages WHERE {' AND '.join(where)} ORDER BY received_at DESC, id DESC LIMIT ?", (*params, max(1, min(int(limit), 250)))).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
