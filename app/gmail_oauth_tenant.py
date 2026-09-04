"""Additive, tenant-bound Gmail OAuth and email-intelligence foundation.

This module deliberately does not call Google, send mail, or alter application
state.  It is a local persistence boundary for a future explicitly-authorized
OAuth transport.  The legacy singleton Gmail integration remains untouched.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.tenant_foundation import current_owner, ensure_schema as ensure_tenants

ALGORITHM = "aes-gcm-v1"
KEY_VERSION = "gmail-oauth-env-v1"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
ALLOWED_SCOPES = frozenset({GMAIL_READONLY_SCOPE})
TOKEN_KINDS = frozenset({"refresh_token", "access_token"})
_PKCE_KIND = "pkce_verifier"
STATES = frozenset({
    "DISCONNECTED", "AUTHORIZATION_REQUIRED", "CONNECTED", "REFRESH_REQUIRED", "REVOKED", "ERROR",
})
_INITIAL_STATES = frozenset({"DISCONNECTED", "AUTHORIZATION_REQUIRED"})
_TRANSITIONS = {
    "DISCONNECTED": frozenset({"AUTHORIZATION_REQUIRED"}),
    "AUTHORIZATION_REQUIRED": frozenset({"CONNECTED", "REVOKED", "ERROR"}),
    "CONNECTED": frozenset({"REFRESH_REQUIRED", "REVOKED", "ERROR"}),
    "REFRESH_REQUIRED": frozenset({"CONNECTED", "REVOKED", "ERROR"}),
    "REVOKED": frozenset({"AUTHORIZATION_REQUIRED"}),
    "ERROR": frozenset({"AUTHORIZATION_REQUIRED", "REVOKED"}),
}
_REASONS = frozenset({
    "AUTHORIZATION_STARTED", "TOKEN_STORED", "TOKEN_REFRESH_REQUIRED", "TOKEN_REVOKED", "OAUTH_ERROR", "TOKEN_ROTATED",
})
_CLASSIFIERS = (
    ("SUBMISSION_CONFIRMATION", ("application received", "thank you for applying", "application confirmation"), 0.95),
    ("ASSESSMENT", ("assessment", "coding challenge", "complete this test"), 0.90),
    ("INTERVIEW", ("interview", "schedule a call", "schedule time"), 0.90),
    ("REJECTION", ("not moving forward", "regret to inform", "not selected"), 0.95),
    ("RECRUITER_RESPONSE", ("recruiter", "talent acquisition", "hiring manager"), 0.70),
    ("OTP_VERIFICATION", ("verification code", "one-time code", "verify your email"), 0.90),
)


class GmailOAuthError(RuntimeError):
    """Non-sensitive failure at the local OAuth persistence boundary."""


def _key() -> bytes:
    try:
        key = base64.urlsafe_b64decode(str(os.getenv("MUNSHI_GMAIL_OAUTH_VAULT_KEY") or "").encode())
    except Exception as error:
        raise GmailOAuthError("Gmail OAuth vault is unavailable.") from error
    if len(key) != 32:
        raise GmailOAuthError("Gmail OAuth vault is unavailable.")
    return key


def _aes():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError as error:
        raise GmailOAuthError("Gmail OAuth vault is unavailable.") from error


def _aad(*, tenant_id: str, user_id: str, account_id: str, token_kind: str) -> bytes:
    return json.dumps({
        "v": 1, "tenant": tenant_id, "user": user_id, "account": account_id,
        "kind": token_kind, "algorithm": ALGORITHM, "key": KEY_VERSION,
    }, sort_keys=True, separators=(",", ":")).encode()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def _scopes(scopes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({str(scope or "").strip() for scope in scopes if str(scope or "").strip()}))
    if not normalized or not set(normalized) <= ALLOWED_SCOPES:
        raise GmailOAuthError("Gmail OAuth scopes are not permitted.")
    return normalized


def _reason(value: str) -> str:
    result = str(value or "").strip().upper()
    if result not in _REASONS:
        raise GmailOAuthError("Gmail OAuth transition is invalid.")
    return result


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Install additive tenant OAuth/evidence tables; no legacy table mutation."""
    ensure_tenants(connection)
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS gmail_oauth_accounts(
      account_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
      account_label TEXT NOT NULL, email_address TEXT, state TEXT NOT NULL
        CHECK(state IN ('DISCONNECTED','AUTHORIZATION_REQUIRED','CONNECTED','REFRESH_REQUIRED','REVOKED','ERROR')),
      scopes_json TEXT NOT NULL, consent_revision TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(tenant_id,user_id,account_label),
      FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id)
    );
    CREATE TABLE IF NOT EXISTS gmail_oauth_tokens(
      token_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
      token_kind TEXT NOT NULL CHECK(token_kind IN ('refresh_token','access_token')), ciphertext BLOB NOT NULL, nonce BLOB NOT NULL,
      algorithm_version TEXT NOT NULL, key_version TEXT NOT NULL, expires_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(account_id,token_kind), FOREIGN KEY(account_id) REFERENCES gmail_oauth_accounts(account_id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS gmail_oauth_events(
      event_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
      prior_state TEXT, next_state TEXT NOT NULL, reason TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS gmail_oauth_authorization_intents(
      intent_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
      state_hash TEXT NOT NULL UNIQUE, verifier_ciphertext BLOB NOT NULL, verifier_nonce BLOB NOT NULL,
      algorithm_version TEXT NOT NULL, key_version TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(account_id) REFERENCES gmail_oauth_accounts(account_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_gmail_oauth_intents_expiry
      ON gmail_oauth_authorization_intents(expires_at);
    CREATE TABLE IF NOT EXISTS gmail_email_evidence(
      evidence_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
      application_ref TEXT, source_message_id TEXT NOT NULL, source_thread_id TEXT, event_type TEXT NOT NULL,
      provenance TEXT NOT NULL, confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
      evidence_code TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(tenant_id,user_id,source_message_id,event_type),
      FOREIGN KEY(account_id) REFERENCES gmail_oauth_accounts(account_id) ON DELETE RESTRICT
    );
    CREATE INDEX IF NOT EXISTS idx_gmail_email_evidence_application
      ON gmail_email_evidence(tenant_id,user_id,application_ref,created_at DESC);
    """)


def _owned(connection: sqlite3.Connection, account_id: str, tenant_id: str, user_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM gmail_oauth_accounts WHERE account_id=? AND tenant_id=? AND user_id=?",
        (account_id, tenant_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("Gmail OAuth account is unavailable.")
    return row


def _event(connection: sqlite3.Connection, row: sqlite3.Row, next_state: str, reason: str) -> None:
    connection.execute(
        "INSERT INTO gmail_oauth_events(event_id,account_id,tenant_id,user_id,prior_state,next_state,reason) VALUES(?,?,?,?,?,?,?)",
        (str(uuid4()), row["account_id"], row["tenant_id"], row["user_id"], row["state"], next_state, reason),
    )


def _status(connection: sqlite3.Connection, account_id: str, tenant_id: str, user_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT account_id,account_label,email_address,state,scopes_json,consent_revision,revision,created_at,updated_at "
        "FROM gmail_oauth_accounts WHERE account_id=? AND tenant_id=? AND user_id=?",
        (account_id, tenant_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("Gmail OAuth account is unavailable.")
    result = dict(row)
    result["scopes"] = json.loads(result.pop("scopes_json"))
    return result


def create_account(*, account_label: str, consent_revision: str, scopes: list[str] | tuple[str, ...] = (GMAIL_READONLY_SCOPE,), initial_state: str = "DISCONNECTED") -> dict[str, Any]:
    """Create only account metadata; an OAuth account never accepts passwords."""
    if initial_state not in _INITIAL_STATES or not str(consent_revision or "").strip():
        raise GmailOAuthError("Gmail OAuth consent and initial state are required.")
    label = str(account_label or "").strip()
    if not label or len(label) > 120:
        raise GmailOAuthError("Gmail OAuth account label is required.")
    allowed_scopes = _scopes(scopes)
    from app.database import get_connection
    owner = current_owner()
    connection = get_connection()
    try:
        ensure_schema(connection)
        prior = connection.execute(
            "SELECT * FROM gmail_oauth_accounts WHERE tenant_id=? AND user_id=? AND account_label=?",
            (owner.tenant_id, owner.user_id, label),
        ).fetchone()
        if prior is not None:
            return _status(connection, str(prior["account_id"]), owner.tenant_id, owner.user_id)
        account_id = str(uuid4())
        connection.execute(
            "INSERT INTO gmail_oauth_accounts(account_id,tenant_id,user_id,account_label,state,scopes_json,consent_revision) VALUES(?,?,?,?,?,?,?)",
            (account_id, owner.tenant_id, owner.user_id, label, initial_state, json.dumps(allowed_scopes), str(consent_revision)[:80]),
        )
        connection.commit()
        return _status(connection, account_id, owner.tenant_id, owner.user_id)
    finally:
        connection.close()


def transition_account(*, account_id: str, next_state: str, expected_revision: int, reason: str) -> dict[str, Any]:
    """Atomically perform a legal OAuth state transition, purging on revocation."""
    if next_state not in STATES:
        raise GmailOAuthError("Gmail OAuth transition is invalid.")
    reason_code = _reason(reason)
    from app.database import get_connection
    owner = current_owner()
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = _owned(connection, account_id, owner.tenant_id, owner.user_id)
        if next_state not in _TRANSITIONS[row["state"]]:
            raise GmailOAuthError("Gmail OAuth transition is not permitted.")
        changed = connection.execute(
            "UPDATE gmail_oauth_accounts SET state=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP "
            "WHERE account_id=? AND tenant_id=? AND user_id=? AND revision=? AND state=?",
            (next_state, account_id, owner.tenant_id, owner.user_id, int(expected_revision), row["state"]),
        ).rowcount
        if changed != 1:
            raise GmailOAuthError("Gmail OAuth transition is not permitted.")
        if next_state == "REVOKED":
            connection.execute("DELETE FROM gmail_oauth_tokens WHERE account_id=? AND tenant_id=? AND user_id=?", (account_id, owner.tenant_id, owner.user_id))
        _event(connection, row, next_state, reason_code)
        connection.commit()
        return _status(connection, account_id, owner.tenant_id, owner.user_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def begin_authorization_intent(*, account_id: str, expected_revision: int, ttl_seconds: int = 600) -> dict[str, str]:
    """Create a one-use, expiring local OAuth intent without constructing a URL or calling Google.

    Only state and its PKCE challenge cross this public boundary.  The verifier
    stays encrypted until the private consume step validates state ownership.
    """
    if not 60 <= int(ttl_seconds) <= 900:
        raise GmailOAuthError("Gmail OAuth authorization intent is invalid.")
    from app.database import get_connection
    owner = current_owner()
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = _owned(connection, account_id, owner.tenant_id, owner.user_id)
        if row["state"] not in {"DISCONNECTED", "ERROR", "REVOKED"}:
            raise GmailOAuthError("Gmail OAuth authorization intent is not permitted.")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        changed = connection.execute(
            "UPDATE gmail_oauth_accounts SET state='AUTHORIZATION_REQUIRED',revision=revision+1,updated_at=CURRENT_TIMESTAMP "
            "WHERE account_id=? AND tenant_id=? AND user_id=? AND revision=? AND state IN ('DISCONNECTED','ERROR','REVOKED')",
            (account_id, owner.tenant_id, owner.user_id, int(expected_revision)),
        ).rowcount
        if changed != 1:
            raise GmailOAuthError("Gmail OAuth authorization intent is not permitted.")
        nonce = os.urandom(12)
        cipher = _aes()(_key()).encrypt(nonce, verifier.encode(), _aad(tenant_id=owner.tenant_id, user_id=owner.user_id, account_id=account_id, token_kind=_PKCE_KIND))
        connection.execute("DELETE FROM gmail_oauth_authorization_intents WHERE account_id=? AND (expires_at < ? OR used_at IS NOT NULL)", (account_id, _now().isoformat()))
        connection.execute(
            "INSERT INTO gmail_oauth_authorization_intents(intent_id,account_id,tenant_id,user_id,state_hash,verifier_ciphertext,verifier_nonce,algorithm_version,key_version,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (str(uuid4()), account_id, owner.tenant_id, owner.user_id, hashlib.sha256(state.encode()).hexdigest(), cipher, nonce, ALGORITHM, KEY_VERSION, (_now() + timedelta(seconds=int(ttl_seconds))).isoformat()),
        )
        _event(connection, row, "AUTHORIZATION_REQUIRED", "AUTHORIZATION_STARTED")
        connection.commit()
        return {"state": state, "code_challenge": _challenge(verifier)}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _consume_authorization_intent(*, connection: sqlite3.Connection, state: str, account_id: str, tenant_id: str, user_id: str, purpose: str) -> bytes:
    """Private one-use PKCE verifier retrieval for a future authorized exchanger."""
    if purpose != "oauth-token-exchange" or not state or len(state) > 512:
        raise GmailOAuthError("Gmail OAuth authorization validation failed.")
    owner = current_owner(connection)
    if owner.tenant_id != tenant_id or owner.user_id != user_id:
        raise GmailOAuthError("Gmail OAuth authorization validation failed.")
    digest = hashlib.sha256(state.encode()).hexdigest()
    row = connection.execute(
        "SELECT * FROM gmail_oauth_authorization_intents WHERE state_hash=? AND account_id=? AND tenant_id=? AND user_id=? AND used_at IS NULL AND expires_at > ?",
        (digest, account_id, tenant_id, user_id, _now().isoformat()),
    ).fetchone()
    if row is None or row["algorithm_version"] != ALGORITHM or row["key_version"] != KEY_VERSION:
        raise GmailOAuthError("Gmail OAuth authorization validation failed.")
    try:
        verifier = _aes()(_key()).decrypt(bytes(row["verifier_nonce"]), bytes(row["verifier_ciphertext"]), _aad(tenant_id=tenant_id, user_id=user_id, account_id=account_id, token_kind=_PKCE_KIND))
    except Exception as error:
        raise GmailOAuthError("Gmail OAuth authorization validation failed.") from error
    changed = connection.execute("UPDATE gmail_oauth_authorization_intents SET used_at=CURRENT_TIMESTAMP WHERE intent_id=? AND used_at IS NULL", (row["intent_id"],)).rowcount
    if changed != 1:
        raise GmailOAuthError("Gmail OAuth authorization validation failed.")
    # Persist consumption before returning the verifier: an OAuth callback retry
    # must never be able to reuse an otherwise-valid state.
    connection.commit()
    return verifier


def store_token(*, account_id: str, token_kind: str, token: str, expected_revision: int, expires_at: str | None = None, email_address: str | None = None) -> dict[str, Any]:
    """Encrypt OAuth token material.  This is write-only at every public boundary."""
    if token_kind not in TOKEN_KINDS or not isinstance(token, str) or not token:
        raise GmailOAuthError("Gmail OAuth token material is invalid.")
    if email_address is not None and (not str(email_address).strip() or len(str(email_address)) > 320):
        raise GmailOAuthError("Gmail OAuth account metadata is invalid.")
    from app.database import get_connection
    owner = current_owner()
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = _owned(connection, account_id, owner.tenant_id, owner.user_id)
        if row["state"] not in {"AUTHORIZATION_REQUIRED", "CONNECTED", "REFRESH_REQUIRED"}:
            raise GmailOAuthError("Gmail OAuth token update is not permitted.")
        nonce = os.urandom(12)
        ciphertext = _aes()(_key()).encrypt(nonce, token.encode(), _aad(tenant_id=owner.tenant_id, user_id=owner.user_id, account_id=account_id, token_kind=token_kind))
        changed = connection.execute(
            "UPDATE gmail_oauth_accounts SET state='CONNECTED',email_address=COALESCE(?,email_address),revision=revision+1,updated_at=CURRENT_TIMESTAMP "
            "WHERE account_id=? AND tenant_id=? AND user_id=? AND revision=? AND state IN ('AUTHORIZATION_REQUIRED','CONNECTED','REFRESH_REQUIRED')",
            (str(email_address).strip() if email_address is not None else None, account_id, owner.tenant_id, owner.user_id, int(expected_revision)),
        ).rowcount
        if changed != 1:
            raise GmailOAuthError("Gmail OAuth token update is not permitted.")
        connection.execute(
            "INSERT INTO gmail_oauth_tokens(token_id,account_id,tenant_id,user_id,token_kind,ciphertext,nonce,algorithm_version,key_version,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(account_id,token_kind) DO UPDATE SET ciphertext=excluded.ciphertext,nonce=excluded.nonce,algorithm_version=excluded.algorithm_version,key_version=excluded.key_version,expires_at=excluded.expires_at,updated_at=CURRENT_TIMESTAMP",
            (str(uuid4()), account_id, owner.tenant_id, owner.user_id, token_kind, ciphertext, nonce, ALGORITHM, KEY_VERSION, expires_at),
        )
        _event(connection, row, "CONNECTED", "TOKEN_ROTATED" if row["state"] == "CONNECTED" else "TOKEN_STORED")
        connection.commit()
        return _status(connection, account_id, owner.tenant_id, owner.user_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def account_status(*, account_id: str) -> dict[str, Any]:
    from app.database import get_connection
    owner = current_owner()
    connection = get_connection()
    try:
        ensure_schema(connection)
        return _status(connection, account_id, owner.tenant_id, owner.user_id)
    finally:
        connection.close()


def _decrypt_for_integrity(*, connection: sqlite3.Connection, account_id: str, tenant_id: str, user_id: str, token_kind: str, purpose: str) -> bytes:
    """Private owner/purpose-bound verifier; never a UI/API token-read capability."""
    if purpose != "integrity-verification":
        raise GmailOAuthError("Gmail OAuth integrity verification is unavailable.")
    owner = current_owner(connection)
    if owner.tenant_id != tenant_id or owner.user_id != user_id or token_kind not in TOKEN_KINDS:
        raise GmailOAuthError("Gmail OAuth integrity verification is unavailable.")
    row = connection.execute(
        "SELECT t.*,a.state FROM gmail_oauth_tokens t JOIN gmail_oauth_accounts a ON a.account_id=t.account_id "
        "WHERE t.account_id=? AND t.tenant_id=? AND t.user_id=? AND t.token_kind=? AND a.tenant_id=? AND a.user_id=? AND a.state='CONNECTED'",
        (account_id, tenant_id, user_id, token_kind, tenant_id, user_id),
    ).fetchone()
    if row is None or row["algorithm_version"] != ALGORITHM or row["key_version"] != KEY_VERSION:
        raise GmailOAuthError("Gmail OAuth integrity verification failed.")
    try:
        return _aes()(_key()).decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), _aad(tenant_id=tenant_id, user_id=user_id, account_id=account_id, token_kind=token_kind))
    except Exception as error:
        raise GmailOAuthError("Gmail OAuth integrity verification failed.") from error


def classify_email(*, subject: str, sender: str, snippet: str) -> tuple[str, float, str]:
    """Conservative local classifier.  It retains a rule code, never message bodies."""
    text = f"{subject} {sender} {snippet}".casefold()
    for event_type, phrases, confidence in _CLASSIFIERS:
        if any(phrase in text for phrase in phrases):
            return event_type, confidence, f"deterministic:{event_type.casefold()}"
    return "UNCLASSIFIED", 0.0, "deterministic:no-match"


def record_email_evidence(*, account_id: str, source_message_id: str, application_ref: str | None, subject: str = "", sender: str = "", snippet: str = "", source_thread_id: str | None = None) -> dict[str, Any] | None:
    """Record a deduplicated local observation; it never changes application lifecycle."""
    message_id = str(source_message_id or "").strip()
    if not message_id or len(message_id) > 500:
        raise GmailOAuthError("Gmail source message identity is required.")
    app_ref = str(application_ref).strip() if application_ref is not None else None
    if app_ref is not None and (not app_ref or len(app_ref) > 240):
        raise GmailOAuthError("Application reference is invalid.")
    event_type, confidence, code = classify_email(subject=subject, sender=sender, snippet=snippet)
    if event_type == "UNCLASSIFIED":
        return None
    from app.database import get_connection
    owner = current_owner()
    connection = get_connection()
    try:
        ensure_schema(connection)
        _owned(connection, account_id, owner.tenant_id, owner.user_id)
        evidence_id = str(uuid4())
        cursor = connection.execute(
            "INSERT OR IGNORE INTO gmail_email_evidence(evidence_id,account_id,tenant_id,user_id,application_ref,source_message_id,source_thread_id,event_type,provenance,confidence,evidence_code) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (evidence_id, account_id, owner.tenant_id, owner.user_id, app_ref, message_id, str(source_thread_id or "")[:500] or None, event_type, "gmail_oauth_local_ingest", confidence, code),
        )
        connection.commit()
        if not cursor.rowcount:
            return None
        return dict(connection.execute("SELECT evidence_id,account_id,application_ref,source_message_id,source_thread_id,event_type,provenance,confidence,evidence_code,created_at FROM gmail_email_evidence WHERE evidence_id=?", (evidence_id,)).fetchone())
    finally:
        connection.close()
