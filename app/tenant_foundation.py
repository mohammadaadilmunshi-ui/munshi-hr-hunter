"""Additive local user/tenant ownership foundation.

The current Hunter is a deliberately singleton product.  This module records
ownership for new user-owned records without changing existing singleton reads,
job discovery, dispatch, or callback authority.  A future authenticated entry
point may install an owner context; until then every write resolves to the
stable local owner and default tenant.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Iterator

from app.database import get_connection


DEFAULT_TENANT_ID = "default"
DEFAULT_USER_ID = "local-owner"
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,119}$")
_owner_context: ContextVar["OwnerContext | None"] = ContextVar(
    "munshi_owner_context", default=None
)

TENANT_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);""",

    """CREATE TABLE IF NOT EXISTS app_users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);""",

    """CREATE TABLE IF NOT EXISTS tenant_memberships (
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, user_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (user_id) REFERENCES app_users(user_id) ON DELETE RESTRICT
);""",

    """CREATE TABLE IF NOT EXISTS owned_record_owners (
    record_domain TEXT NOT NULL,
    record_key TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (record_domain, record_key),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (user_id) REFERENCES app_users(user_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE RESTRICT
);""",
    """CREATE INDEX IF NOT EXISTS idx_owned_record_owners_user
    ON owned_record_owners(tenant_id, user_id, record_domain);""",
)


@dataclass(frozen=True)
class OwnerContext:
    tenant_id: str
    user_id: str


def tenant_foundation_enabled() -> bool:
    """Whether a future authenticated caller may override the singleton owner."""
    return str(os.getenv("MUNSHI_TENANT_FOUNDATION_ENABLED") or "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def _valid_identifier(value: str) -> str:
    candidate = str(value or "").strip().casefold()
    if not _IDENTIFIER.fullmatch(candidate):
        raise ValueError("Tenant and user identifiers must be 1-120 lowercase URL-safe characters.")
    return candidate


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    """Install additive tables and the deterministic legacy singleton principal."""
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        # Do not use executescript here.  sqlite3.executescript implicitly
        # commits a pending transaction, while individual DDL statements remain
        # part of the caller's transaction.  Owner lookup can occur mid-write.
        for statement in TENANT_SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT OR IGNORE INTO tenants(tenant_id, display_name) VALUES (?, ?)",
            (DEFAULT_TENANT_ID, "Default workspace"),
        )
        connection.execute(
            "INSERT OR IGNORE INTO app_users(user_id, display_name) VALUES (?, ?)",
            (DEFAULT_USER_ID, "Local owner"),
        )
        connection.execute(
            "INSERT OR IGNORE INTO tenant_memberships(tenant_id, user_id, role) VALUES (?, ?, 'owner')",
            (DEFAULT_TENANT_ID, DEFAULT_USER_ID),
        )
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def current_owner(connection: sqlite3.Connection | None = None) -> OwnerContext:
    """Return the current owner, falling back to the stable singleton principal.

    Context override is intentionally inert until the feature flag is enabled.
    It never creates users or memberships: an authenticated future boundary must
    provision those explicitly before using a context.
    """
    candidate = _owner_context.get()
    if not tenant_foundation_enabled() or candidate is None:
        return OwnerContext(DEFAULT_TENANT_ID, DEFAULT_USER_ID)
    tenant_id = _valid_identifier(candidate.tenant_id)
    user_id = _valid_identifier(candidate.user_id)
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        membership = connection.execute(
            "SELECT 1 FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
            (tenant_id, user_id),
        ).fetchone()
        if membership is None:
            raise LookupError("Current user is not a member of the current tenant.")
        return OwnerContext(tenant_id, user_id)
    finally:
        if owns_connection:
            connection.close()


@contextmanager
def owner_context(*, tenant_id: str, user_id: str) -> Iterator[None]:
    """Install a request-local owner context for a future authenticated boundary."""
    token: Token[OwnerContext | None] = _owner_context.set(
        OwnerContext(_valid_identifier(tenant_id), _valid_identifier(user_id))
    )
    try:
        yield
    finally:
        _owner_context.reset(token)


def associate_owned_record(
    connection: sqlite3.Connection,
    *,
    record_domain: str,
    record_key: str | int,
    owner: OwnerContext | None = None,
) -> OwnerContext:
    """Attach a newly-created user-owned record to exactly one owner.

    Existing associations are intentionally preserved.  This makes retries and
    legacy singleton call paths deterministic rather than silently reassigning
    data when a request context changes.
    """
    domain = str(record_domain or "").strip().casefold()
    key = str(record_key or "").strip()
    if not domain or len(domain) > 120 or not key or len(key) > 240:
        raise ValueError("Owned record domain and key are required and bounded.")
    # Direct callers still get a safe lazy install on an older database.  The
    # initializer uses transactional individual DDL statements, not executescript.
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='owned_record_owners'"
    ).fetchone()
    if exists is None:
        ensure_schema(connection)
    resolved = owner or current_owner(connection)
    connection.execute(
        """INSERT OR IGNORE INTO owned_record_owners(
               record_domain, record_key, tenant_id, user_id
           ) VALUES (?, ?, ?, ?)""",
        (domain, key, resolved.tenant_id, resolved.user_id),
    )
    return resolved
