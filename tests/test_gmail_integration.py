from __future__ import annotations

import base64
import os

import pytest

from app.gmail_integration import (
    _state_hash, begin_authorization, classify_message, complete_authorization,
    connection_status, gmail_configuration_status, upsert_message,
)


def test_gmail_classification_is_conservative() -> None:
    assert classify_message("Interview invitation", "", "")[0] == "interview"
    assert classify_message("A note", "", "hello")[0] == "unclassified"


def test_gmail_message_deduplication(hunter_db) -> None:
    message = {"id": "gmail-message-1", "subject": "Application received", "sender": "jobs@example.test", "snippet": "Thank you for applying"}
    assert upsert_message(message) is True
    assert upsert_message(message) is False


def test_gmail_stays_not_configured_without_server_secrets(hunter_db, monkeypatch) -> None:
    for key in ("MUNSHI_GMAIL_CLIENT_ID", "MUNSHI_GMAIL_CLIENT_SECRET", "MUNSHI_GMAIL_REDIRECT_URI", "MUNSHI_VAULT_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert gmail_configuration_status()["ready"] is False


def test_oauth_state_is_hashed_single_use_and_pkce_backed(hunter_db, monkeypatch) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("MUNSHI_GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setenv("MUNSHI_GMAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MUNSHI_GMAIL_REDIRECT_URI", "https://hunter.example.test/api/gmail/oauth/callback")
    url = begin_authorization()
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly" in url
    assert "code_challenge_method=S256" in url
    state = url.split("state=", 1)[1].split("&", 1)[0]
    from urllib.parse import unquote
    state = unquote(state)
    from app.database import get_connection
    connection = get_connection()
    try:
        row = connection.execute("SELECT state_hash,used_at FROM gmail_oauth_state").fetchone()
        assert row["state_hash"] == _state_hash(state) and state.encode() not in str(dict(row)).encode()
    finally: connection.close()
    monkeypatch.setattr("app.gmail_integration.exchange_authorization_code", lambda code, *, account_label, code_verifier: None)
    complete_authorization("authorization-code", state)
    with pytest.raises(RuntimeError, match="could not be validated"):
        complete_authorization("authorization-code", state)
    assert connection_status()["connected"] is False
