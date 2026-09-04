from __future__ import annotations

import base64
import os
import pytest

from app.ats_credentials import (
    CredentialError, _decrypt_for_integrity, account_status, create_account,
    policy, store_secret, transition_account,
)
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


def _secret_row(account_id):
    from app.database import get_connection
    c = get_connection()
    row = c.execute("SELECT * FROM ats_credential_secrets WHERE account_id=?", (account_id,)).fetchone()
    return c, row


@pytest.mark.parametrize("column", ["ciphertext", "nonce"])
def test_tampered_ciphertext_or_nonce_fails_closed(hunter_db, monkeypatch, column):
    _key(monkeypatch)
    a = create_account(provider="workday", account_scope="tamper-" + column, label="x", consent_version="v1")
    store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])
    c, row = _secret_row(a["account_id"])
    try:
        value = bytearray(row[column]); value[-1] ^= 1
        c.execute(f"UPDATE ats_credential_secrets SET {column}=? WHERE account_id=?", (bytes(value), a["account_id"])); c.commit()
        with pytest.raises(CredentialError):
            _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="default", user_id="local-owner", secret_kind="password", purpose="integrity-verification")
    finally: c.close()


def test_integrity_decrypt_is_owner_and_purpose_bound(hunter_db, monkeypatch):
    _key(monkeypatch)
    a = create_account(provider="lever", account_scope="bound", label="x", consent_version="v1")
    store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])
    c, _ = _secret_row(a["account_id"])
    try:
        assert _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="default", user_id="local-owner", secret_kind="password", purpose="integrity-verification") == b"test-only-secret"
        for kwargs in ({"tenant_id": "wrong"}, {"user_id": "wrong"}, {"account_id": "wrong"}, {"secret_kind": "otp"}, {"purpose": "view-password"}):
            args = {"connection": c, "account_id": a["account_id"], "tenant_id": "default", "user_id": "local-owner", "secret_kind": "password", "purpose": "integrity-verification"}; args.update(kwargs)
            with pytest.raises((CredentialError, LookupError)):
                _decrypt_for_integrity(**args)
    finally: c.close()


def test_wrong_key_and_unknown_versions_fail_closed(hunter_db, monkeypatch):
    _key(monkeypatch)
    a = create_account(provider="ashby", account_scope="versions", label="x", consent_version="v1")
    store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])
    c, _ = _secret_row(a["account_id"])
    try:
        monkeypatch.setenv("MUNSHI_VAULT_KEY", base64.urlsafe_b64encode(b"q" * 32).decode())
        with pytest.raises(CredentialError): _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="default", user_id="local-owner", secret_kind="password", purpose="integrity-verification")
        c.execute("UPDATE ats_credential_secrets SET key_version='tenant-env-v1' WHERE account_id=?", (a["account_id"],))
        c.execute("UPDATE ats_credential_accounts SET provider='WORKDAY' WHERE account_id=?", (a["account_id"],)); c.commit()
        with pytest.raises(CredentialError): _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="default", user_id="local-owner", secret_kind="password", purpose="integrity-verification")
        _key(monkeypatch); c.execute("UPDATE ats_credential_secrets SET key_version='future-v9' WHERE account_id=?", (a["account_id"],)); c.commit()
        with pytest.raises(CredentialError): _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="default", user_id="local-owner", secret_kind="password", purpose="integrity-verification")
    finally: c.close()


def test_cas_legal_transitions_and_block_purges_secret(hunter_db, monkeypatch):
    _key(monkeypatch)
    with pytest.raises(CredentialError): create_account(provider="workday", account_scope="bad", label="x", consent_version="v1", initial_state="AVAILABLE")
    a = create_account(provider="workday", account_scope="cas", label="x", consent_version="v1")
    with pytest.raises(CredentialError): transition_account(account_id=a["account_id"], next_state="AVAILABLE", expected_revision=a["revision"], reason="not a public transition")
    ready = store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])
    with pytest.raises(CredentialError): store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])
    blocked = transition_account(account_id=a["account_id"], next_state="BLOCKED", expected_revision=ready["revision"], reason="user revoked")
    assert blocked["state"] == "BLOCKED"
    c, row = _secret_row(a["account_id"])
    try: assert row is None
    finally: c.close()
    with pytest.raises(CredentialError): store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=blocked["revision"])


@pytest.mark.parametrize("state", ["NOT_REQUIRED", "BLOCKED", "NEEDS_VERIFICATION"])
def test_non_writable_states_reject_secret(hunter_db, monkeypatch, state):
    _key(monkeypatch)
    a = create_account(provider="workday", account_scope="state-" + state.lower(), label="x", consent_version="v1", initial_state=state)
    with pytest.raises(CredentialError): store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])


def test_cross_user_access_and_public_status_never_return_secret(hunter_db, monkeypatch):
    _key(monkeypatch)
    a = create_account(provider="workday", account_scope="owner", label="x", consent_version="v1")
    store_secret(account_id=a["account_id"], secret_kind="password", secret="never-in-status", expected_revision=a["revision"])
    assert "never-in-status" not in str(account_status(account_id=a["account_id"]))
    from app.database import get_connection
    c = get_connection()
    try:
        c.execute("INSERT INTO tenants VALUES('team-c','C',CURRENT_TIMESTAMP)"); c.execute("INSERT INTO app_users VALUES('member-c','C',CURRENT_TIMESTAMP)"); c.execute("INSERT INTO tenant_memberships VALUES('team-c','member-c','member',CURRENT_TIMESTAMP)"); c.commit()
    finally: c.close()
    monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")
    with owner_context(tenant_id="team-c", user_id="member-c"):
        with pytest.raises(LookupError): account_status(account_id=a["account_id"])
        with pytest.raises(LookupError): store_secret(account_id=a["account_id"], secret_kind="password", secret="never-in-status", expected_revision=2)
