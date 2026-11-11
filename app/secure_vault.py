"""Cross-platform encrypted secret vault for server-side integrations.

Secrets are encrypted with AES-256-GCM before they enter SQLite. The master key
is supplied at runtime from a root-managed key file (preferred for staging and
production) or ``MUNSHI_VAULT_KEY`` (development/test compatibility). Secret
values are never returned by metadata APIs, copied into settings/history rows,
or written to Streamlit output.
"""
from __future__ import annotations

import base64
import os
import sqlite3
from pathlib import Path
from typing import Any

from app.database import get_connection

ALGORITHM_VERSION = "aes-gcm-v1"
KEY_VERSION = "master-v1"
_KEY_FILE_ENV = "MUNSHI_VAULT_KEY_FILE"
_KEY_ENV = "MUNSHI_VAULT_KEY"


class VaultError(RuntimeError):
    """A deliberately non-sensitive vault failure."""


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as error:
        raise VaultError(
            "Encrypted credential storage is unavailable: cryptography is not installed."
        ) from error
    return AESGCM


def _decode_key(raw: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(raw.strip().encode("ascii"))
    except Exception as error:
        raise VaultError("Encrypted credential storage is not configured.") from error
    if len(key) != 32:
        raise VaultError("Encrypted credential storage is not configured.")
    return key


def _key_material() -> tuple[bytes, str]:
    """Return the AES-256 key and a non-secret source label.

    A key file is preferred because it can be mounted read-only and avoids
    placing the master key directly in process environment/config output. Key
    files must not be group/world accessible.
    """
    configured_file = str(os.getenv(_KEY_FILE_ENV) or "").strip()
    if configured_file:
        path = Path(configured_file)
        try:
            if not path.is_absolute() or not path.is_file():
                raise VaultError("Encrypted credential storage is not configured.")
            mode = path.stat().st_mode & 0o777
            if mode & 0o077:
                raise VaultError("Encrypted credential key file permissions are unsafe.")
            raw = path.read_text(encoding="ascii").strip()
        except VaultError:
            raise
        except (OSError, UnicodeError) as error:
            raise VaultError("Encrypted credential storage is not configured.") from error
        return _decode_key(raw), "key-file"

    raw = str(os.getenv(_KEY_ENV) or "").strip()
    if not raw:
        raise VaultError("Encrypted credential storage is not configured.")
    return _decode_key(raw), "environment"


def _key() -> bytes:
    return _key_material()[0]


def vault_available() -> bool:
    try:
        key, _ = _key_material()
        AESGCM = _aesgcm()
        nonce = os.urandom(12)
        aad = b"munshi:vault:self-test:aes-gcm-v1"
        ciphertext = AESGCM(key).encrypt(nonce, b"vault-self-test", aad)
        if AESGCM(key).decrypt(nonce, ciphertext, aad) != b"vault-self-test":
            return False
        return True
    except (VaultError, Exception):
        return False


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    """Ensure encrypted-secret and metadata-only audit tables."""
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        connection.execute(
            """
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
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS credential_secret_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credential_type TEXT NOT NULL,
                account_label TEXT NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_credential_secret_audit_type_time
            ON credential_secret_audit(credential_type, created_at DESC)
            """
        )
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def _normalized_identity(credential_type: str, account_label: str) -> tuple[str, str]:
    credential_type = str(credential_type or "").strip()
    account_label = str(account_label or "default").strip() or "default"
    if not credential_type or len(credential_type) > 120 or len(account_label) > 120:
        raise VaultError("A valid credential type and account label are required.")
    return credential_type, account_label


def _validate_secret(secret: str) -> str:
    secret = str(secret or "").strip()
    if not secret:
        raise VaultError("A non-empty secret is required.")
    if len(secret) > 16384:
        raise VaultError("Credential value is too large.")
    if any(ord(character) < 32 for character in secret):
        raise VaultError("Credential contains unsupported control characters.")
    return secret


def _audit(
    connection: sqlite3.Connection,
    *,
    credential_type: str,
    account_label: str,
    action: str,
    actor: str,
) -> None:
    connection.execute(
        """
        INSERT INTO credential_secret_audit(
            credential_type, account_label, action, actor
        ) VALUES (?, ?, ?, ?)
        """,
        (
            credential_type,
            account_label,
            str(action or "unknown")[:40],
            str(actor or "application")[:120],
        ),
    )


def store_secret(
    credential_type: str,
    secret: str,
    *,
    account_label: str = "default",
    actor: str = "application",
) -> None:
    credential_type, account_label = _normalized_identity(
        credential_type, account_label
    )
    secret = _validate_secret(secret)
    AESGCM = _aesgcm()
    key = _key()
    nonce = os.urandom(12)
    aad = f"{credential_type}:{account_label}:{ALGORITHM_VERSION}".encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), aad)

    connection = get_connection()
    try:
        ensure_schema(connection)
        existed = connection.execute(
            "SELECT 1 FROM credential_secret WHERE credential_type=? AND account_label=?",
            (credential_type, account_label),
        ).fetchone() is not None
        connection.execute(
            """
            INSERT INTO credential_secret(
                credential_type, account_label, ciphertext, nonce,
                algorithm_version, key_version
            ) VALUES (?,?,?,?,?,?)
            ON CONFLICT(credential_type,account_label) DO UPDATE SET
                ciphertext=excluded.ciphertext,
                nonce=excluded.nonce,
                algorithm_version=excluded.algorithm_version,
                key_version=excluded.key_version,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                credential_type,
                account_label,
                ciphertext,
                nonce,
                ALGORITHM_VERSION,
                KEY_VERSION,
            ),
        )
        _audit(
            connection,
            credential_type=credential_type,
            account_label=account_label,
            action="replace" if existed else "store",
            actor=actor,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def read_secret(credential_type: str, *, account_label: str = "default") -> str | None:
    credential_type, account_label = _normalized_identity(
        credential_type, account_label
    )
    AESGCM = _aesgcm()
    key = _key()
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute(
            """
            SELECT ciphertext,nonce,algorithm_version
            FROM credential_secret
            WHERE credential_type=? AND account_label=?
            """,
            (credential_type, account_label),
        ).fetchone()
        if row is None:
            return None
        if row["algorithm_version"] != ALGORITHM_VERSION:
            raise VaultError("Credential uses an unsupported encryption version.")
        aad = f"{credential_type}:{account_label}:{ALGORITHM_VERSION}".encode("utf-8")
        try:
            plaintext = AESGCM(key).decrypt(
                bytes(row["nonce"]), bytes(row["ciphertext"]), aad
            ).decode("utf-8")
        except Exception as error:
            raise VaultError("Credential could not be decrypted.") from error
        connection.execute(
            """
            UPDATE credential_secret
            SET last_used_at=CURRENT_TIMESTAMP
            WHERE credential_type=? AND account_label=?
            """,
            (credential_type, account_label),
        )
        connection.commit()
        return plaintext
    finally:
        connection.close()


def delete_secret(
    credential_type: str,
    *,
    account_label: str = "default",
    actor: str = "application",
) -> bool:
    credential_type, account_label = _normalized_identity(
        credential_type, account_label
    )
    connection = get_connection()
    try:
        ensure_schema(connection)
        cursor = connection.execute(
            "DELETE FROM credential_secret WHERE credential_type=? AND account_label=?",
            (credential_type, account_label),
        )
        deleted = bool(cursor.rowcount)
        if deleted:
            _audit(
                connection,
                credential_type=credential_type,
                account_label=account_label,
                action="delete",
                actor=actor,
            )
        connection.commit()
        return deleted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def secret_metadata(
    credential_type: str, *, account_label: str = "default"
) -> dict[str, Any] | None:
    """Return non-secret metadata only. Ciphertext and nonce are never returned."""
    credential_type, account_label = _normalized_identity(
        credential_type, account_label
    )
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute(
            """
            SELECT credential_type,account_label,algorithm_version,key_version,
                   created_at,updated_at,last_used_at
            FROM credential_secret
            WHERE credential_type=? AND account_label=?
            """,
            (credential_type, account_label),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        connection.close()


def list_secret_metadata() -> list[dict[str, Any]]:
    """List configured credential identities without decrypting any secret."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        rows = connection.execute(
            """
            SELECT credential_type,account_label,algorithm_version,key_version,
                   created_at,updated_at,last_used_at
            FROM credential_secret
            ORDER BY credential_type,account_label
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def vault_readiness() -> dict[str, Any]:
    """Return non-secret readiness diagnostics for UI and deployment gates."""
    try:
        key, source = _key_material()
        AESGCM = _aesgcm()
        nonce = os.urandom(12)
        aad = b"munshi:vault:readiness:aes-gcm-v1"
        ciphertext = AESGCM(key).encrypt(nonce, b"readiness", aad)
        round_trip = AESGCM(key).decrypt(nonce, ciphertext, aad) == b"readiness"
    except VaultError as error:
        return {
            "available": False,
            "production_ready": False,
            "key_source": "none",
            "algorithm": ALGORITHM_VERSION,
            "reason": str(error),
            "secret_count": 0,
        }

    environment = str(
        os.getenv("MUNSHI_ENVIRONMENT")
        or os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or "development"
    ).strip().casefold()
    require_file = (
        str(os.getenv("MUNSHI_VAULT_REQUIRE_KEY_FILE") or "").strip().casefold()
        in {"1", "true", "yes", "on"}
        or environment == "production"
    )
    production_ready = bool(round_trip and (source == "key-file" or not require_file))
    reason = "ready"
    if round_trip and require_file and source != "key-file":
        reason = "Production policy requires MUNSHI_VAULT_KEY_FILE."

    try:
        secret_count = len(list_secret_metadata())
    except sqlite3.Error:
        secret_count = 0

    return {
        "available": bool(round_trip),
        "production_ready": production_ready,
        "key_source": source,
        "algorithm": ALGORITHM_VERSION,
        "reason": reason,
        "secret_count": secret_count,
    }
