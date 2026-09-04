"""Phase 10 tenant-bound ATS credential metadata and encrypted secret slots.

This local foundation has no login, browser, network, or submission code.
Secrets are deliberately write-only: only a future separately-authorized
executor may add a narrowly scoped internal decryptor.
"""
from __future__ import annotations

import base64, json, os, sqlite3
from typing import Any
from uuid import uuid4

from app.tenant_foundation import current_owner, ensure_schema as ensure_tenants

ALGORITHM = "aes-gcm-v1"
KEY_VERSION = "tenant-env-v1"
STATES = frozenset({"NOT_REQUIRED", "REQUIRED", "AVAILABLE", "NEEDS_LOGIN", "NEEDS_VERIFICATION", "BLOCKED"})
POLICIES = {
    "GREENHOUSE": "ACCOUNTLESS_POSSIBLE", "LEVER": "ACCOUNTLESS_POSSIBLE",
    "ASHBY": "ACCOUNTLESS_POSSIBLE", "SMARTRECRUITERS": "VARIABLE",
    "WORKDAY": "ACCOUNT_COMMON",
}

class CredentialError(RuntimeError): pass

def _key() -> bytes:
    try: key = base64.urlsafe_b64decode(str(os.getenv("MUNSHI_VAULT_KEY") or "").encode())
    except Exception as error: raise CredentialError("Credential vault is unavailable.") from error
    if len(key) != 32: raise CredentialError("Credential vault is unavailable.")
    return key

def _aes():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError as error: raise CredentialError("Credential vault is unavailable.") from error

def _aad(*, tenant_id: str, user_id: str, account_id: str, provider: str, secret_kind: str) -> bytes:
    return json.dumps({"v": 1, "tenant": tenant_id, "user": user_id, "account": account_id, "provider": provider, "kind": secret_kind, "algorithm": ALGORITHM, "key": KEY_VERSION}, sort_keys=True, separators=(",", ":")).encode()

def ensure_schema(connection: sqlite3.Connection) -> None:
    ensure_tenants(connection)
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS ats_credential_accounts(
      account_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
      provider TEXT NOT NULL, account_scope TEXT NOT NULL, label TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('NOT_REQUIRED','REQUIRED','AVAILABLE','NEEDS_LOGIN','NEEDS_VERIFICATION','BLOCKED')),
      consent_version TEXT, consented_at TEXT, revision INTEGER NOT NULL DEFAULT 1,
      evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(tenant_id,user_id,provider,account_scope), FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id));
    CREATE TABLE IF NOT EXISTS ats_credential_secrets(
      secret_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, secret_kind TEXT NOT NULL,
      ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, algorithm_version TEXT NOT NULL, key_version TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(account_id,secret_kind), FOREIGN KEY(account_id) REFERENCES ats_credential_accounts(account_id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS ats_credential_events(
      event_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
      prior_state TEXT, next_state TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    """)

def policy(provider: str) -> dict[str, Any]:
    key = str(provider or "").strip().upper()
    return {"provider": key, "account_policy": POLICIES.get(key, "UNSUPPORTED"), "live_login": False, "account_creation": False, "submission": False}

def create_account(*, provider: str, account_scope: str, label: str, consent_version: str, initial_state: str = "REQUIRED") -> dict[str, Any]:
    from app.database import get_connection
    p = policy(provider)
    if p["account_policy"] == "UNSUPPORTED": initial_state = "BLOCKED"
    if initial_state not in STATES or not consent_version.strip(): raise CredentialError("Credential consent and state are required.")
    owner = current_owner(); scope = str(account_scope).strip().lower()
    if not scope or len(scope) > 240: raise CredentialError("Credential account scope is required.")
    c = get_connection()
    try:
        ensure_schema(c); prior = c.execute("SELECT * FROM ats_credential_accounts WHERE tenant_id=? AND user_id=? AND provider=? AND account_scope=?", (owner.tenant_id,owner.user_id,p["provider"],scope)).fetchone()
        if prior: return dict(prior)
        account_id=str(uuid4()); c.execute("INSERT INTO ats_credential_accounts(account_id,tenant_id,user_id,provider,account_scope,label,state,consent_version,consented_at) VALUES(?,?,?,?,?,?,?, ?,CURRENT_TIMESTAMP)", (account_id,owner.tenant_id,owner.user_id,p["provider"],scope,str(label or "Credential")[:120],initial_state,consent_version[:80]))
        c.execute("INSERT INTO ats_credential_events(event_id,account_id,tenant_id,user_id,next_state,actor,reason) VALUES(?,?,?,?,?,?,?)", (str(uuid4()),account_id,owner.tenant_id,owner.user_id,initial_state,"local","account_created")); c.commit()
        return dict(c.execute("SELECT * FROM ats_credential_accounts WHERE account_id=?",(account_id,)).fetchone())
    finally: c.close()

def store_secret(*, account_id: str, secret_kind: str, secret: str, expected_revision: int) -> dict[str, Any]:
    from app.database import get_connection
    if not secret or not secret_kind.strip(): raise CredentialError("Credential secret is required.")
    owner=current_owner(); c=get_connection()
    try:
        ensure_schema(c); row=c.execute("SELECT * FROM ats_credential_accounts WHERE account_id=? AND tenant_id=? AND user_id=?",(account_id,owner.tenant_id,owner.user_id)).fetchone()
        if not row or row["state"] in {"NOT_REQUIRED","BLOCKED","NEEDS_VERIFICATION"} or int(row["revision"]) != int(expected_revision): raise CredentialError("Credential update is not permitted.")
        nonce=os.urandom(12); cipher=_aes()(_key()).encrypt(nonce,secret.encode(),_aad(tenant_id=owner.tenant_id,user_id=owner.user_id,account_id=account_id,provider=row["provider"],secret_kind=secret_kind))
        c.execute("INSERT INTO ats_credential_secrets(secret_id,account_id,tenant_id,user_id,secret_kind,ciphertext,nonce,algorithm_version,key_version) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,secret_kind) DO UPDATE SET ciphertext=excluded.ciphertext,nonce=excluded.nonce,algorithm_version=excluded.algorithm_version,key_version=excluded.key_version,updated_at=CURRENT_TIMESTAMP",(str(uuid4()),account_id,owner.tenant_id,owner.user_id,secret_kind,cipher,nonce,ALGORITHM,KEY_VERSION))
        c.execute("UPDATE ats_credential_accounts SET state='AVAILABLE',revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE account_id=?",(account_id,)); c.execute("INSERT INTO ats_credential_events(event_id,account_id,tenant_id,user_id,prior_state,next_state,actor,reason) VALUES(?,?,?,?,?,?,?,?)",(str(uuid4()),account_id,owner.tenant_id,owner.user_id,row["state"],"AVAILABLE","local","secret_written")); c.commit()
        return account_status(account_id=account_id)
    finally: c.close()

def account_status(*, account_id: str) -> dict[str, Any]:
    from app.database import get_connection
    owner=current_owner(); c=get_connection()
    try:
        ensure_schema(c); row=c.execute("SELECT account_id,provider,account_scope,label,state,revision,consent_version,consented_at,created_at,updated_at FROM ats_credential_accounts WHERE account_id=? AND tenant_id=? AND user_id=?",(account_id,owner.tenant_id,owner.user_id)).fetchone()
        if not row: raise LookupError("Credential account is unavailable.")
        return dict(row)
    finally: c.close()
