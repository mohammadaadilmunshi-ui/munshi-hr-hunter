from __future__ import annotations

import base64
import os
import pytest

from app.ats_credentials import CredentialError, account_status, create_account, policy, store_secret
from app.tenant_foundation import owner_context

def _key(monkeypatch): monkeypatch.setenv("MUNSHI_VAULT_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())

def test_write_only_encrypted_owner_bound_account(hunter_db, monkeypatch):
    _key(monkeypatch)
    a=create_account(provider="workday",account_scope="tenant.example",label="Workday",consent_version="v1")
    out=store_secret(account_id=a["account_id"],secret_kind="password",secret="not-visible",expected_revision=a["revision"])
    assert out["state"] == "AVAILABLE" and "not-visible" not in str(out)
    from app.database import get_connection
    c=get_connection()
    try:
        row=c.execute("SELECT ciphertext FROM ats_credential_secrets").fetchone()
        assert b"not-visible" not in bytes(row[0])
    finally: c.close()

def test_tenant_isolation_and_fail_closed_policy(hunter_db, monkeypatch):
    _key(monkeypatch); a=create_account(provider="lever",account_scope="x",label="x",consent_version="v1")
    from app.database import get_connection
    c=get_connection()
    try:
        c.execute("INSERT INTO tenants VALUES('team-b','B',CURRENT_TIMESTAMP)"); c.execute("INSERT INTO app_users VALUES('member-b','B',CURRENT_TIMESTAMP)"); c.execute("INSERT INTO tenant_memberships VALUES('team-b','member-b','member',CURRENT_TIMESTAMP)"); c.commit()
    finally: c.close()
    monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED","1")
    with owner_context(tenant_id="team-b",user_id="member-b"):
        with pytest.raises(LookupError): account_status(account_id=a["account_id"])
    assert policy("unknown")["account_policy"] == "UNSUPPORTED"
    assert all(not policy("workday")[k] for k in ("live_login","account_creation","submission"))

def test_missing_key_and_not_required_reject_secret(hunter_db, monkeypatch):
    a=create_account(provider="ashby",account_scope="x",label="x",consent_version="v1",initial_state="NOT_REQUIRED")
    with pytest.raises(CredentialError): store_secret(account_id=a["account_id"],secret_kind="password",secret="x",expected_revision=a["revision"])
