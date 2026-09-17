"""Phase 10 tenant-bound, write-only ATS credential foundation.

There is no login, browser, network, n8n, Apply, or submission authority here.
The private decrypt helper is only for owner-and-purpose-bound integrity checks.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
from typing import Any
from uuid import uuid4

from app.tenant_foundation import current_owner, ensure_schema as ensure_tenants

ALGORITHM = "aes-gcm-v1"
KEY_VERSION = "tenant-env-v1"
STATES = frozenset({"NOT_REQUIRED", "REQUIRED", "AVAILABLE", "NEEDS_LOGIN", "NEEDS_VERIFICATION", "BLOCKED"})
_INITIAL_STATES = frozenset({"NOT_REQUIRED", "REQUIRED", "NEEDS_LOGIN", "NEEDS_VERIFICATION", "BLOCKED"})
_SECRET_WRITABLE_STATES = frozenset({"REQUIRED", "NEEDS_LOGIN", "AVAILABLE"})
_TRANSITIONS = {
    "REQUIRED": frozenset({"NEEDS_LOGIN", "NEEDS_VERIFICATION", "BLOCKED"}),
    "AVAILABLE": frozenset({"NEEDS_LOGIN", "NEEDS_VERIFICATION", "BLOCKED"}),
    "NEEDS_LOGIN": frozenset({"REQUIRED", "NEEDS_VERIFICATION", "BLOCKED"}),
    "NEEDS_VERIFICATION": frozenset({"REQUIRED", "NEEDS_LOGIN", "BLOCKED"}),
    "NOT_REQUIRED": frozenset({"REQUIRED", "BLOCKED"}),
    "BLOCKED": frozenset(),
}
_TRANSITION_REASONS = frozenset({
    "ACCOUNT_CREATED", "CONSENT_UPDATED", "CREDENTIAL_UPDATED", "LOGIN_REQUIRED",
    "VERIFICATION_REQUIRED", "USER_REVOKED", "PROVIDER_BLOCKED",
})
_SECRET_KIND = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
POLICIES = {"GREENHOUSE": "ACCOUNTLESS_POSSIBLE", "LEVER": "ACCOUNTLESS_POSSIBLE", "ASHBY": "ACCOUNTLESS_POSSIBLE", "SMARTRECRUITERS": "VARIABLE", "WORKDAY": "ACCOUNT_COMMON"}


class CredentialError(RuntimeError):
    """Deliberately non-sensitive credential boundary failure."""


def _key() -> bytes:
    try:
        key = base64.urlsafe_b64decode(str(os.getenv("MUNSHI_VAULT_KEY") or "").encode())
    except Exception as error:
        raise CredentialError("Credential vault is unavailable.") from error
    if len(key) != 32:
        raise CredentialError("Credential vault is unavailable.")
    return key


def _aes():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError as error:
        raise CredentialError("Credential vault is unavailable.") from error


def _aad(*, tenant_id: str, user_id: str, account_id: str, provider: str, secret_kind: str) -> bytes:
    return json.dumps({"v": 1, "tenant": tenant_id, "user": user_id, "account": account_id, "provider": provider, "kind": secret_kind, "algorithm": ALGORITHM, "key": KEY_VERSION}, sort_keys=True, separators=(",", ":")).encode()


def _valid_secret_kind(value: str) -> str:
    kind = str(value or "").strip().lower()
    if not _SECRET_KIND.fullmatch(kind):
        raise CredentialError("Credential secret kind is invalid.")
    return kind


def _valid_transition_reason(value: str) -> str:
    """Only stable non-sensitive reason codes belong in the event ledger."""
    reason = str(value or "").strip().upper()
    if reason not in _TRANSITION_REASONS:
        raise CredentialError("Credential transition is invalid.")
    return reason


def ensure_schema(connection: sqlite3.Connection) -> None:
    ensure_tenants(connection)
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS ats_credential_accounts(
      account_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, provider TEXT NOT NULL, account_scope TEXT NOT NULL, label TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('NOT_REQUIRED','REQUIRED','AVAILABLE','NEEDS_LOGIN','NEEDS_VERIFICATION','BLOCKED')), consent_version TEXT, consented_at TEXT,
      revision INTEGER NOT NULL DEFAULT 1, evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(tenant_id,user_id,provider,account_scope), FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id));
    CREATE TABLE IF NOT EXISTS ats_credential_secrets(
      secret_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, secret_kind TEXT NOT NULL, ciphertext BLOB NOT NULL, nonce BLOB NOT NULL,
      algorithm_version TEXT NOT NULL, key_version TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(account_id,secret_kind), FOREIGN KEY(account_id) REFERENCES ats_credential_accounts(account_id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS ats_credential_events(
      event_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, prior_state TEXT, next_state TEXT NOT NULL, actor TEXT NOT NULL,
      reason TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    """)


def policy(provider: str) -> dict[str, Any]:
    key = str(provider or "").strip().upper()
    return {"provider": key, "account_policy": POLICIES.get(key, "UNSUPPORTED"), "live_login": False, "account_creation": False, "submission": False}


def _event(c: sqlite3.Connection, account_id: str, tenant_id: str, user_id: str, prior: str | None, next_state: str, reason: str) -> None:
    c.execute("INSERT INTO ats_credential_events(event_id,account_id,tenant_id,user_id,prior_state,next_state,actor,reason) VALUES(?,?,?,?,?,?,?,?)", (str(uuid4()), account_id, tenant_id, user_id, prior, next_state, "local", reason[:120]))


def _owned_account(c: sqlite3.Connection, account_id: str, tenant_id: str, user_id: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM ats_credential_accounts WHERE account_id=? AND tenant_id=? AND user_id=?", (account_id, tenant_id, user_id)).fetchone()
    if row is None:
        raise LookupError("Credential account is unavailable.")
    return row


def create_account(*, provider: str, account_scope: str, label: str, consent_version: str, initial_state: str = "REQUIRED") -> dict[str, Any]:
    from app.database import get_connection
    p = policy(provider)
    if p["account_policy"] == "UNSUPPORTED": initial_state = "BLOCKED"
    if initial_state not in _INITIAL_STATES or not str(consent_version or "").strip():
        raise CredentialError("Credential consent and a permitted initial state are required.")
    owner = current_owner(); scope = str(account_scope).strip().lower()
    if not scope or len(scope) > 240: raise CredentialError("Credential account scope is required.")
    c = get_connection()
    try:
        ensure_schema(c)
        prior = c.execute("SELECT * FROM ats_credential_accounts WHERE tenant_id=? AND user_id=? AND provider=? AND account_scope=?", (owner.tenant_id, owner.user_id, p["provider"], scope)).fetchone()
        if prior: return dict(prior)
        account_id = str(uuid4())
        c.execute("INSERT INTO ats_credential_accounts(account_id,tenant_id,user_id,provider,account_scope,label,state,consent_version,consented_at) VALUES(?,?,?,?,?,?,?, ?,CURRENT_TIMESTAMP)", (account_id, owner.tenant_id, owner.user_id, p["provider"], scope, str(label or "Credential")[:120], initial_state, consent_version[:80]))
        _event(c, account_id, owner.tenant_id, owner.user_id, None, initial_state, "account_created"); c.commit()
        return dict(c.execute("SELECT * FROM ats_credential_accounts WHERE account_id=?", (account_id,)).fetchone())
    finally: c.close()


def _status_from_connection(c: sqlite3.Connection, account_id: str, tenant_id: str, user_id: str) -> dict[str, Any]:
    row = c.execute("SELECT account_id,provider,account_scope,label,state,revision,consent_version,consented_at,created_at,updated_at FROM ats_credential_accounts WHERE account_id=? AND tenant_id=? AND user_id=?", (account_id, tenant_id, user_id)).fetchone()
    if row is None: raise LookupError("Credential account is unavailable.")
    return dict(row)


def store_secret(*, account_id: str, secret_kind: str, secret: str, expected_revision: int) -> dict[str, Any]:
    """Atomically encrypt a secret. No public function decrypts credentials."""
    from app.database import get_connection
    if not isinstance(secret, str) or not secret: raise CredentialError("Credential secret is required.")
    kind = _valid_secret_kind(secret_kind); owner = current_owner(); c = get_connection()
    try:
        ensure_schema(c); c.execute("BEGIN IMMEDIATE")
        row = _owned_account(c, account_id, owner.tenant_id, owner.user_id)
        if row["state"] not in _SECRET_WRITABLE_STATES: raise CredentialError("Credential update is not permitted.")
        nonce = os.urandom(12); cipher = _aes()(_key()).encrypt(nonce, secret.encode(), _aad(tenant_id=owner.tenant_id, user_id=owner.user_id, account_id=account_id, provider=row["provider"], secret_kind=kind))
        changed = c.execute("UPDATE ats_credential_accounts SET state='AVAILABLE',revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND tenant_id=? AND user_id=? AND revision=? AND state IN ('REQUIRED','NEEDS_LOGIN','AVAILABLE')", (account_id, owner.tenant_id, owner.user_id, int(expected_revision))).rowcount
        if changed != 1: raise CredentialError("Credential update is not permitted.")
        c.execute("INSERT INTO ats_credential_secrets(secret_id,account_id,tenant_id,user_id,secret_kind,ciphertext,nonce,algorithm_version,key_version) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,secret_kind) DO UPDATE SET ciphertext=excluded.ciphertext,nonce=excluded.nonce,algorithm_version=excluded.algorithm_version,key_version=excluded.key_version,updated_at=CURRENT_TIMESTAMP", (str(uuid4()), account_id, owner.tenant_id, owner.user_id, kind, cipher, nonce, ALGORITHM, KEY_VERSION))
        _event(c, account_id, owner.tenant_id, owner.user_id, row["state"], "AVAILABLE", "secret_written"); c.commit()
        return _status_from_connection(c, account_id, owner.tenant_id, owner.user_id)
    except Exception:
        c.rollback(); raise
    finally: c.close()


def transition_account(*, account_id: str, next_state: str, expected_revision: int, reason: str) -> dict[str, Any]:
    """Legal metadata transition only; blocking irrevocably purges stored slots."""
    from app.database import get_connection
    if next_state not in STATES: raise CredentialError("Credential transition is invalid.")
    reason_code = _valid_transition_reason(reason)
    owner = current_owner(); c = get_connection()
    try:
        ensure_schema(c); c.execute("BEGIN IMMEDIATE"); row = _owned_account(c, account_id, owner.tenant_id, owner.user_id)
        if next_state not in _TRANSITIONS[row["state"]]: raise CredentialError("Credential transition is not permitted.")
        changed = c.execute("UPDATE ats_credential_accounts SET state=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND tenant_id=? AND user_id=? AND revision=? AND state=?", (next_state, account_id, owner.tenant_id, owner.user_id, int(expected_revision), row["state"])).rowcount
        if changed != 1: raise CredentialError("Credential transition is not permitted.")
        if next_state == "BLOCKED": c.execute("DELETE FROM ats_credential_secrets WHERE account_id=? AND tenant_id=? AND user_id=?", (account_id, owner.tenant_id, owner.user_id))
        _event(c, account_id, owner.tenant_id, owner.user_id, row["state"], next_state, reason_code); c.commit()
        return _status_from_connection(c, account_id, owner.tenant_id, owner.user_id)
    except Exception:
        c.rollback(); raise
    finally: c.close()


def account_status(*, account_id: str) -> dict[str, Any]:
    from app.database import get_connection
    owner = current_owner(); c = get_connection()
    try:
        ensure_schema(c); return _status_from_connection(c, account_id, owner.tenant_id, owner.user_id)
    finally: c.close()


def _decrypt_for_integrity(*, connection: sqlite3.Connection, account_id: str, tenant_id: str, user_id: str, secret_kind: str, purpose: str) -> bytes:
    """Private integrity verifier; not an API/UI password-view capability."""
    if purpose != "integrity-verification": raise CredentialError("Credential integrity verification is unavailable.")
    owner = current_owner(connection)
    if owner.tenant_id != tenant_id or owner.user_id != user_id:
        raise CredentialError("Credential integrity verification is unavailable.")
    kind = _valid_secret_kind(secret_kind)
    row = connection.execute("SELECT s.*,a.provider FROM ats_credential_secrets s JOIN ats_credential_accounts a ON a.account_id=s.account_id WHERE s.account_id=? AND s.tenant_id=? AND s.user_id=? AND s.secret_kind=? AND a.tenant_id=? AND a.user_id=? AND a.state='AVAILABLE'", (account_id, tenant_id, user_id, kind, tenant_id, user_id)).fetchone()
    if row is None or row["algorithm_version"] != ALGORITHM or row["key_version"] != KEY_VERSION: raise CredentialError("Credential integrity verification failed.")
    try:
        return _aes()(_key()).decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), _aad(tenant_id=tenant_id, user_id=user_id, account_id=account_id, provider=row["provider"], secret_kind=kind))
    except Exception as error:
        raise CredentialError("Credential integrity verification failed.") from error
