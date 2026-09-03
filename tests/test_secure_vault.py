from __future__ import annotations

import base64
import os

import pytest

from app.secure_vault import VaultError, delete_secret, read_secret, store_secret


def test_vault_fails_closed_without_key(hunter_db, monkeypatch) -> None:
    monkeypatch.delenv("MUNSHI_VAULT_KEY", raising=False)
    with pytest.raises(VaultError) as error:
        store_secret("gmail_refresh_token", "secret-value-must-not-leak")
    assert "secret-value-must-not-leak" not in str(error.value)


def test_vault_fails_closed_for_malformed_key(hunter_db, monkeypatch) -> None:
    monkeypatch.setenv("MUNSHI_VAULT_KEY", "definitely-not-a-valid-aes-key")
    with pytest.raises(VaultError, match="not configured"):
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


def test_vault_rejects_tampering_and_cross_account_swaps(hunter_db, monkeypatch) -> None:
    pytest.importorskip("cryptography")
    key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", key)
    store_secret("gmail_refresh_token", "first-secret", account_label="first")
    store_secret("gmail_refresh_token", "second-secret", account_label="second")
    from app.database import get_connection
    connection = get_connection()
    try:
        first = connection.execute("SELECT ciphertext,nonce FROM credential_secret WHERE account_label='first'").fetchone()
        connection.execute("UPDATE credential_secret SET ciphertext=?,nonce=? WHERE account_label='second'", (first["ciphertext"], first["nonce"]))
        connection.commit()
    finally: connection.close()
    with pytest.raises(VaultError, match="could not be decrypted"):
        read_secret("gmail_refresh_token", account_label="second")
    connection = get_connection()
    try:
        value = bytes(connection.execute("SELECT ciphertext FROM credential_secret WHERE account_label='first'").fetchone()["ciphertext"])
        connection.execute("UPDATE credential_secret SET ciphertext=? WHERE account_label='first'", (value[:-1] + bytes([value[-1] ^ 1]),))
        connection.commit()
    finally: connection.close()
    with pytest.raises(VaultError, match="could not be decrypted"):
        read_secret("gmail_refresh_token", account_label="first")
