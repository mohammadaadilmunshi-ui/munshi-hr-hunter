"""Cross-platform encrypted secret vault for server-side integrations.

Secrets are AES-GCM ciphertext only.  The master key is supplied exclusively by
``MUNSHI_VAULT_KEY`` at runtime and is never copied into settings, SQLite logs,
or Streamlit output.
"""
from __future__ import annotations

import base64
import os
import sqlite3
from typing import Any

from app.database import get_connection

ALGORITHM_VERSION = "aes-gcm-v1"
KEY_VERSION = "env-v1"


class VaultError(RuntimeError):
    """A deliberately non-sensitive vault failure."""


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as error:
        raise VaultError("Encrypted credential storage is unavailable: cryptography is not installed.") from error
    return AESGCM


def _key() -> bytes:
    raw = str(os.getenv("MUNSHI_VAULT_KEY") or "").strip()
    if not raw:
        raise VaultError("Encrypted credential storage is not configured.")
    try:
        key = base64.urlsafe_b64decode(raw.encode("ascii"))
    except Exception as error:
        raise VaultError("Encrypted credential storage is not configured.") from error
    if len(key) != 32:
        raise VaultError("Encrypted credential storage is not configured.")
    return key


def vault_available() -> bool:
    try:
        _key(); _aesgcm()
        return True
    except VaultError:
        return False


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS credential_secret (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            credential_type TEXT NOT NULL,
            account_label TEXT NOT NULL DEFAULT 'default',
            ciphertext BLOB NOT NULL,
            nonce BLOB NOT NULL,
            algorithm_version TEXT NOT NULL,
            key_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TEXT,
            UNIQUE(credential_type, account_label)
        );
        """)
        if owns_connection: connection.commit()
    finally:
        if owns_connection: connection.close()


def store_secret(credential_type: str, secret: str, *, account_label: str = "default") -> None:
    if not credential_type.strip() or not secret:
        raise VaultError("A credential type and non-empty secret are required.")
    AESGCM = _aesgcm(); key = _key(); nonce = os.urandom(12)
    # Binding the record identity prevents ciphertext swapping between accounts.
    aad = f"{credential_type}:{account_label}:{ALGORITHM_VERSION}".encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), aad)
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("""INSERT INTO credential_secret(credential_type,account_label,ciphertext,nonce,algorithm_version,key_version)
            VALUES (?,?,?,?,?,?) ON CONFLICT(credential_type,account_label) DO UPDATE SET ciphertext=excluded.ciphertext,nonce=excluded.nonce,algorithm_version=excluded.algorithm_version,key_version=excluded.key_version,updated_at=CURRENT_TIMESTAMP""", (credential_type, account_label, ciphertext, nonce, ALGORITHM_VERSION, KEY_VERSION))
        connection.commit()
    finally: connection.close()


def read_secret(credential_type: str, *, account_label: str = "default") -> str | None:
    AESGCM = _aesgcm(); key = _key()
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute("SELECT ciphertext,nonce,algorithm_version FROM credential_secret WHERE credential_type=? AND account_label=?", (credential_type, account_label)).fetchone()
        if row is None: return None
        if row["algorithm_version"] != ALGORITHM_VERSION:
            raise VaultError("Credential uses an unsupported encryption version.")
        aad = f"{credential_type}:{account_label}:{ALGORITHM_VERSION}".encode("utf-8")
        try:
            plaintext = AESGCM(key).decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), aad).decode("utf-8")
        except Exception as error:
            raise VaultError("Credential could not be decrypted.") from error
        connection.execute("UPDATE credential_secret SET last_used_at=CURRENT_TIMESTAMP WHERE credential_type=? AND account_label=?", (credential_type, account_label)); connection.commit()
        return plaintext
    finally: connection.close()


def delete_secret(credential_type: str, *, account_label: str = "default") -> bool:
    connection = get_connection()
    try:
        ensure_schema(connection)
        cursor = connection.execute("DELETE FROM credential_secret WHERE credential_type=? AND account_label=?", (credential_type, account_label)); connection.commit()
        return bool(cursor.rowcount)
    finally: connection.close()
