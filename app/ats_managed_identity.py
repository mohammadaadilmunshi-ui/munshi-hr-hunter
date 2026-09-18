"""MUNSHI-managed ATS identity and password policy.

This module makes the product policy explicit without granting browser/network
execution authority:

* every owner receives an opaque MUNSHI application email identity;
* every new managed ATS account receives a unique MUNSHI-generated password;
* password material is written directly into the existing tenant-bound ATS
  credential vault and is never returned from this API;
* existing legacy credentials are never silently replaced. They are marked as
  requiring managed rotation so the remote ATS account cannot be desynchronised;
* email relay readiness is metadata only. No inbound-mail capability is claimed
  until the relay is configured separately.
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import string
from typing import Any
from uuid import uuid4

from app.ats_credentials import (
    CredentialError,
    account_status,
    create_account,
    ensure_schema as ensure_credential_schema,
    store_secret,
)
from app.tenant_foundation import current_owner

_DEFAULT_MAIL_DOMAIN = "mail.munshi.systems"
_MAIL_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_PASSWORD_SAFE_SYMBOLS = "!@#$%_-+="
_PASSWORD_LENGTH = 28
_PASSWORD_MODE_MANAGED = "MUNSHI_GENERATED_UNIQUE"
_PASSWORD_MODE_LEGACY = "LEGACY_REQUIRES_ROTATION"


class ManagedIdentityError(RuntimeError):
    """Non-sensitive managed ATS identity failure."""


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def application_mail_domain() -> str:
    value = str(os.getenv("MUNSHI_APPLICATION_MAIL_DOMAIN") or _DEFAULT_MAIL_DOMAIN)
    domain = value.strip().lower().rstrip(".")
    if not _MAIL_DOMAIN.fullmatch(domain):
        raise ManagedIdentityError("Application mail domain is invalid.")
    return domain


def mail_relay_ready() -> bool:
    """Metadata-only readiness flag; it does not probe or send network traffic."""
    return _truthy(os.getenv("MUNSHI_APPLICATION_MAIL_RELAY_ENABLED"))


def _opaque_local_part(prefix: str, *, bytes_count: int = 12) -> str:
    token = secrets.token_urlsafe(bytes_count).lower().replace("-", "").replace("_", "")
    return f"{prefix}_{token[:18]}"


def _generated_password(length: int = _PASSWORD_LENGTH) -> str:
    if length < 20:
        raise ManagedIdentityError("Managed ATS passwords must be at least 20 characters.")
    random = secrets.SystemRandom()
    required = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice(_PASSWORD_SAFE_SYMBOLS),
    ]
    alphabet = string.ascii_letters + string.digits + _PASSWORD_SAFE_SYMBOLS
    required.extend(random.choice(alphabet) for _ in range(length - len(required)))
    random.shuffle(required)
    return "".join(required)


def ensure_schema(connection: sqlite3.Connection) -> None:
    ensure_credential_schema(connection)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS ats_application_identities(
          identity_id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          email_domain TEXT NOT NULL,
          local_part TEXT NOT NULL,
          email_address TEXT NOT NULL,
          relay_state TEXT NOT NULL CHECK(relay_state IN ('RESERVED','ACTIVE','SUSPENDED')),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(tenant_id,user_id,email_domain),
          UNIQUE(email_address),
          FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id)
        );

        CREATE TABLE IF NOT EXISTS ats_managed_account_bindings(
          account_id TEXT PRIMARY KEY,
          identity_id TEXT NOT NULL,
          password_mode TEXT NOT NULL CHECK(password_mode IN ('MUNSHI_GENERATED_UNIQUE','LEGACY_REQUIRES_ROTATION')),
          password_version INTEGER NOT NULL DEFAULT 1 CHECK(password_version >= 1),
          secret_ref TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(account_id) REFERENCES ats_credential_accounts(account_id) ON DELETE CASCADE,
          FOREIGN KEY(identity_id) REFERENCES ats_application_identities(identity_id) ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_ats_application_identity_owner
        ON ats_application_identities(tenant_id,user_id,relay_state);

        CREATE INDEX IF NOT EXISTS idx_ats_managed_binding_identity
        ON ats_managed_account_bindings(identity_id,password_mode);
        """
    )


def _identity_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "identity_id": row["identity_id"],
        "email_address": row["email_address"],
        "email_domain": row["email_domain"],
        "relay_state": row["relay_state"],
        "relay_ready": row["relay_state"] == "ACTIVE",
        "managed_by_munshi": True,
    }


def ensure_application_identity() -> dict[str, Any]:
    """Return the stable opaque ATS email identity for the current owner."""
    from app.database import get_connection

    owner = current_owner()
    domain = application_mail_domain()
    connection = get_connection()
    try:
        ensure_schema(connection)
        requested_state = "ACTIVE" if mail_relay_ready() else "RESERVED"
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM ats_application_identities
            WHERE tenant_id=? AND user_id=? AND email_domain=?
            """,
            (owner.tenant_id, owner.user_id, domain),
        ).fetchone()
        if row is None:
            for _ in range(8):
                local_part = _opaque_local_part("u")
                email = f"{local_part}@{domain}"
                try:
                    identity_id = str(uuid4())
                    connection.execute(
                        """
                        INSERT INTO ats_application_identities(
                          identity_id,tenant_id,user_id,email_domain,local_part,email_address,relay_state
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            identity_id,
                            owner.tenant_id,
                            owner.user_id,
                            domain,
                            local_part,
                            email,
                            requested_state,
                        ),
                    )
                    row = connection.execute(
                        "SELECT * FROM ats_application_identities WHERE identity_id=?",
                        (identity_id,),
                    ).fetchone()
                    break
                except sqlite3.IntegrityError:
                    continue
            if row is None:
                raise ManagedIdentityError("Unable to reserve a unique application email identity.")
        elif row["relay_state"] != "SUSPENDED" and row["relay_state"] != requested_state:
            connection.execute(
                """
                UPDATE ats_application_identities
                SET relay_state=?,updated_at=CURRENT_TIMESTAMP
                WHERE identity_id=?
                """,
                (requested_state, row["identity_id"]),
            )
            row = connection.execute(
                "SELECT * FROM ats_application_identities WHERE identity_id=?",
                (row["identity_id"],),
            ).fetchone()
        connection.commit()
        if row is None:
            raise ManagedIdentityError("Application email identity is unavailable.")
        return _identity_from_row(row)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _secret_exists(connection: sqlite3.Connection, account_id: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM ats_credential_secrets WHERE account_id=? AND secret_kind='password' LIMIT 1",
        (account_id,),
    ).fetchone()
    return row is not None


def _binding(connection: sqlite3.Connection, account_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM ats_managed_account_bindings WHERE account_id=?",
        (account_id,),
    ).fetchone()


def managed_secret_ref(account_id: str) -> str:
    """Opaque reference for a later secure execution resolver; never plaintext."""
    value = str(account_id or "").strip()
    if not value:
        raise ManagedIdentityError("ATS account id is required.")
    return f"ats-secret://{value}/password"


def prepare_managed_account(
    *,
    provider: str,
    account_scope: str,
    label: str,
    consent_version: str = "managed-ats-v1",
) -> dict[str, Any]:
    """Prepare MUNSHI-owned ATS credentials without performing remote account creation.

    A new credential slot receives a unique generated password immediately. If an
    older slot already contains an unclassified password, it is *not* overwritten:
    the binding is marked LEGACY_REQUIRES_ROTATION so browser automation can rotate
    the remote ATS password before MUNSHI claims compliance.
    """
    from app.database import get_connection

    identity = ensure_application_identity()
    account = create_account(
        provider=provider,
        account_scope=account_scope,
        label=label,
        consent_version=consent_version,
    )

    connection = get_connection()
    try:
        ensure_schema(connection)
        existing_binding = _binding(connection, account["account_id"])
        secret_exists = _secret_exists(connection, account["account_id"])
    finally:
        connection.close()

    if existing_binding is None and not secret_exists:
        if account["state"] not in {"REQUIRED", "NEEDS_LOGIN", "AVAILABLE"}:
            raise ManagedIdentityError("ATS account is not writable for managed-password setup.")
        password = _generated_password()
        try:
            status = store_secret(
                account_id=account["account_id"],
                secret_kind="password",
                secret=password,
                expected_revision=int(account["revision"]),
            )
        finally:
            password = ""  # best-effort lifetime reduction; Python strings are immutable.
        mode = _PASSWORD_MODE_MANAGED
        version = 1
    else:
        status = account_status(account_id=account["account_id"])
        if existing_binding is not None:
            mode = str(existing_binding["password_mode"])
            version = int(existing_binding["password_version"])
        else:
            mode = _PASSWORD_MODE_LEGACY
            version = 1

    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO ats_managed_account_bindings(
              account_id,identity_id,password_mode,password_version,secret_ref
            ) VALUES(?,?,?,?,?)
            ON CONFLICT(account_id) DO UPDATE SET
              identity_id=excluded.identity_id,
              password_mode=excluded.password_mode,
              password_version=excluded.password_version,
              secret_ref=excluded.secret_ref,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                account["account_id"],
                identity["identity_id"],
                mode,
                version,
                managed_secret_ref(account["account_id"]),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "account_id": account["account_id"],
        "provider": status["provider"],
        "account_scope": status["account_scope"],
        "credential_state": status["state"],
        "application_email": identity["email_address"],
        "email_relay_state": identity["relay_state"],
        "password_policy": mode,
        "password_managed": mode == _PASSWORD_MODE_MANAGED,
        "rotation_required": mode == _PASSWORD_MODE_LEGACY,
        "secret_ref": managed_secret_ref(account["account_id"]),
        "remote_account_created": False,
        "browser_authority": False,
    }


def identity_status() -> dict[str, Any]:
    identity = ensure_application_identity()
    return {
        **identity,
        "password_policy": "MUNSHI_GENERATED_UNIQUE_PER_ATS_ACCOUNT",
        "personal_email_required_for_ats": False,
        "inbound_verification_live": mail_relay_ready(),
    }
