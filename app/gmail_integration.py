"""Conservative Gmail integration foundation; no render-time network activity."""
from __future__ import annotations

import os
import sqlite3
from urllib.parse import urlencode
from typing import Any

from app.database import get_connection
from app.secure_vault import delete_secret, read_secret, store_secret, vault_available

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None; connection = connection or get_connection()
    try:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS gmail_integration_state (
          account_label TEXT PRIMARY KEY, email_address TEXT, sync_cursor TEXT,
          last_sync_at TEXT, last_error_code TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)
        if owns_connection: connection.commit()
    finally:
        if owns_connection: connection.close()


def gmail_configuration_status() -> dict[str, Any]:
    client_id = str(os.getenv("MUNSHI_GMAIL_CLIENT_ID") or "").strip()
    client_secret = str(os.getenv("MUNSHI_GMAIL_CLIENT_SECRET") or "").strip()
    redirect = str(os.getenv("MUNSHI_GMAIL_REDIRECT_URI") or "").strip()
    ready = bool(client_id and client_secret and redirect and vault_available())
    return {"ready": ready, "oauth_client": "Configured" if client_id and client_secret and redirect else "Not configured", "vault": "Configured" if vault_available() else "Not configured", "scope": GMAIL_READONLY_SCOPE}


def authorization_url(state: str) -> str:
    """Build an explicit OAuth consent URL; callers must validate ``state`` server-side."""
    status = gmail_configuration_status()
    if not status["ready"]: raise RuntimeError("Gmail OAuth is not configured.")
    return OAUTH_AUTHORIZE_URL + "?" + urlencode({"client_id": os.environ["MUNSHI_GMAIL_CLIENT_ID"], "redirect_uri": os.environ["MUNSHI_GMAIL_REDIRECT_URI"], "response_type": "code", "scope": GMAIL_READONLY_SCOPE, "access_type": "offline", "prompt": "consent", "state": state})


def save_refresh_token(account_label: str, refresh_token: str, email_address: str | None = None) -> None:
    """Called only after OAuth callback validation by a future authenticated endpoint."""
    if not refresh_token: raise ValueError("OAuth response did not include a refresh token.")
    store_secret("gmail_refresh_token", refresh_token, account_label=account_label)
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("INSERT INTO gmail_integration_state(account_label,email_address,updated_at) VALUES (?,?,CURRENT_TIMESTAMP) ON CONFLICT(account_label) DO UPDATE SET email_address=excluded.email_address,updated_at=CURRENT_TIMESTAMP", (account_label, email_address)); connection.commit()
    finally: connection.close()


def exchange_authorization_code(code: str, *, state_validated: bool, account_label: str = "default") -> None:
    """Exchange an explicit OAuth callback code after the host validates state.

    This function is intentionally not wired to page rendering. A future
    authenticated callback route must validate the state/PKCE verifier before
    invoking it.
    """
    if not state_validated:
        raise RuntimeError("OAuth state validation is required.")
    if not code.strip() or not gmail_configuration_status()["ready"]:
        raise RuntimeError("Gmail OAuth is not configured.")
    import httpx
    response = httpx.post(OAUTH_TOKEN_URL, data={"code": code, "client_id": os.environ["MUNSHI_GMAIL_CLIENT_ID"], "client_secret": os.environ["MUNSHI_GMAIL_CLIENT_SECRET"], "redirect_uri": os.environ["MUNSHI_GMAIL_REDIRECT_URI"], "grant_type": "authorization_code"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    token = str(payload.get("refresh_token") or "")
    if not token:
        raise RuntimeError("OAuth authorization did not return a refresh token.")
    save_refresh_token(account_label, token)


def _access_token(account_label: str) -> str:
    refresh_token = read_secret("gmail_refresh_token", account_label=account_label)
    if not refresh_token:
        raise RuntimeError("Gmail is not connected.")
    import httpx
    response = httpx.post(OAUTH_TOKEN_URL, data={"client_id": os.environ["MUNSHI_GMAIL_CLIENT_ID"], "client_secret": os.environ["MUNSHI_GMAIL_CLIENT_SECRET"], "refresh_token": refresh_token, "grant_type": "refresh_token"}, timeout=20)
    response.raise_for_status()
    token = str(response.json().get("access_token") or "")
    if not token: raise RuntimeError("Gmail token refresh returned no access token.")
    return token


def sync_messages(account_label: str = "default", *, max_messages: int = 25) -> int:
    """Explicit, bounded Gmail read-only sync entrypoint (never called on render)."""
    if not gmail_configuration_status()["ready"]:
        raise RuntimeError("Gmail OAuth is not configured.")
    import httpx
    token = _access_token(account_label)
    headers = {"Authorization": f"Bearer {token}"}
    response = httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages", headers=headers, params={"maxResults": max(1, min(int(max_messages), 100))}, timeout=20)
    response.raise_for_status()
    inserted = 0
    for item in response.json().get("messages", []):
        message_id = str(item.get("id") or "")
        if not message_id: continue
        detail = httpx.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}", headers=headers, params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]}, timeout=20)
        detail.raise_for_status(); data = detail.json()
        headers_by_name = {str(header.get("name") or "").casefold(): str(header.get("value") or "") for header in data.get("payload", {}).get("headers", [])}
        if upsert_message({"id": message_id, "thread_id": data.get("threadId"), "subject": headers_by_name.get("subject"), "sender": headers_by_name.get("from"), "received_at": headers_by_name.get("date"), "snippet": data.get("snippet")}) : inserted += 1
    connection = get_connection()
    try:
        ensure_schema(connection); connection.execute("INSERT INTO gmail_integration_state(account_label,last_sync_at,updated_at) VALUES (?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) ON CONFLICT(account_label) DO UPDATE SET last_sync_at=CURRENT_TIMESTAMP,last_error_code=NULL,updated_at=CURRENT_TIMESTAMP", (account_label,)); connection.commit()
    finally: connection.close()
    return inserted


def disconnect(account_label: str = "default") -> bool:
    deleted = delete_secret("gmail_refresh_token", account_label=account_label)
    connection = get_connection()
    try:
        ensure_schema(connection); connection.execute("DELETE FROM gmail_integration_state WHERE account_label=?", (account_label,)); connection.commit()
    finally: connection.close()
    return deleted


def classify_message(subject: str, sender: str, snippet: str) -> tuple[str, str]:
    """Deterministic, deliberately conservative classification with evidence."""
    text = f"{subject} {sender} {snippet}".casefold()
    categories = (("interview", ("interview", "schedule a call")), ("assessment", ("assessment", "coding challenge", "complete this test")), ("offer", ("offer letter", "we are pleased to offer")), ("rejection", ("not moving forward", "regret to inform", "not selected")), ("verification", ("verify your email", "confirm your email")), ("reminder", ("reminder", "action required")), ("applied", ("application received", "thank you for applying")))
    for category, phrases in categories:
        phrase = next((item for item in phrases if item in text), None)
        if phrase: return category, f"Matched explicit phrase: {phrase}"
    return "unclassified", "No deterministic application-message phrase matched."


def upsert_message(message: dict[str, Any]) -> bool:
    """Persist a fetched Gmail record idempotently. Fetching/sync scheduling is external."""
    message_id = str(message.get("id") or "").strip()
    if not message_id: raise ValueError("Gmail message id is required.")
    category, evidence = classify_message(str(message.get("subject") or ""), str(message.get("sender") or ""), str(message.get("snippet") or ""))
    connection = get_connection()
    try:
        from app.product_state import ensure_schema as ensure_product_schema
        ensure_product_schema(connection)
        cursor = connection.execute("INSERT OR IGNORE INTO gmail_messages(gmail_message_id,thread_id,category,subject,sender,received_at,snippet,body_text,classification_evidence) VALUES (?,?,?,?,?,?,?,?,?)", (message_id, message.get("thread_id"), category, message.get("subject"), message.get("sender"), message.get("received_at"), message.get("snippet"), message.get("body_text"), evidence)); connection.commit()
        return bool(cursor.rowcount)
    finally: connection.close()
