from __future__ import annotations

import base64
import os

import pytest

from app.secure_vault import VaultError, delete_secret, read_secret, store_secret


def test_vault_fails_closed_without_key(hunter_db, monkeypatch) -> None:
    monkeypatch.delenv("MUNSHI_VAULT_KEY", raising=False)
    with pytest.raises(VaultError):
        store_secret("gmail_refresh_token", "secret")


def test_vault_encrypts_round_trip_and_delete(hunter_db, monkeypatch) -> None:
    pytest.importorskip("cryptography")
    key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", key)
    store_secret("gmail_refresh_token", "sensitive-token")
    from app.database import get_connection
    connection = get_connection()
    try:
        row = connection.execute("SELECT ciphertext FROM credential_secret").fetchone()
        assert b"sensitive-token" not in bytes(row["ciphertext"])
    finally: connection.close()
    assert read_secret("gmail_refresh_token") == "sensitive-token"
    monkeypatch.setenv("MUNSHI_VAULT_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"))
    with pytest.raises(VaultError):
        read_secret("gmail_refresh_token")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", key)
    assert delete_secret("gmail_refresh_token") is True
    assert read_secret("gmail_refresh_token") is None
