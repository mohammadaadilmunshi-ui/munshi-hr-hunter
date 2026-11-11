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
        _key(monkeypatch)
        c.execute("UPDATE ats_credential_accounts SET provider='WORKDAY' WHERE account_id=?", (a["account_id"],)); c.commit()
        with pytest.raises(CredentialError): _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="default", user_id="local-owner", secret_kind="password", purpose="integrity-verification")
        c.execute("UPDATE ats_credential_accounts SET provider='ASHBY' WHERE account_id=?", (a["account_id"],))
        c.execute("UPDATE ats_credential_secrets SET algorithm_version='future-aead-v9' WHERE account_id=?", (a["account_id"],)); c.commit()
        with pytest.raises(CredentialError): _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="default", user_id="local-owner", secret_kind="password", purpose="integrity-verification")
        c.execute("UPDATE ats_credential_secrets SET algorithm_version='aes-gcm-v1', key_version='future-v9' WHERE account_id=?", (a["account_id"],)); c.commit()
        with pytest.raises(CredentialError): _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="default", user_id="local-owner", secret_kind="password", purpose="integrity-verification")
    finally: c.close()


def test_cas_legal_transitions_and_block_purges_secret(hunter_db, monkeypatch):
    _key(monkeypatch)
    with pytest.raises(CredentialError): create_account(provider="workday", account_scope="bad", label="x", consent_version="v1", initial_state="AVAILABLE")
    a = create_account(provider="workday", account_scope="cas", label="x", consent_version="v1")
    with pytest.raises(CredentialError): transition_account(account_id=a["account_id"], next_state="AVAILABLE", expected_revision=a["revision"], reason="CREDENTIAL_UPDATED")
    ready = store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])
    with pytest.raises(CredentialError): store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])
    blocked = transition_account(account_id=a["account_id"], next_state="BLOCKED", expected_revision=ready["revision"], reason="USER_REVOKED")
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


def test_integrity_verifier_requires_current_owner_and_available_state(hunter_db, monkeypatch):
    _key(monkeypatch)
    a = create_account(provider="workday", account_scope="internal-only", label="x", consent_version="v1")
    ready = store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])
    c, _ = _secret_row(a["account_id"])
    try:
        monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")
        with owner_context(tenant_id="default", user_id="local-owner"):
            assert _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="default", user_id="local-owner", secret_kind="password", purpose="integrity-verification") == b"test-only-secret"
            with pytest.raises(CredentialError):
                _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="other", user_id="other", secret_kind="password", purpose="integrity-verification")
        c.execute("UPDATE ats_credential_accounts SET state='NEEDS_LOGIN' WHERE account_id=?", (a["account_id"],)); c.commit()
        with pytest.raises(CredentialError):
            _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="default", user_id="local-owner", secret_kind="password", purpose="integrity-verification")
    finally:
        c.close()


def test_aad_identity_mutations_fail_closed(hunter_db, monkeypatch):
    _key(monkeypatch)
    a = create_account(provider="workday", account_scope="aad-a", label="x", consent_version="v1")
    b = create_account(provider="workday", account_scope="aad-b", label="x", consent_version="v1")
    store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])
    c, _ = _secret_row(a["account_id"])
    try:
        # Rebinding a ciphertext to a different account reaches AES-GCM and fails its tag.
        c.execute("UPDATE ats_credential_accounts SET state='AVAILABLE' WHERE account_id=?", (b["account_id"],))
        c.execute("UPDATE ats_credential_secrets SET account_id=? WHERE account_id=?", (b["account_id"], a["account_id"])); c.commit()
        with pytest.raises(CredentialError):
            _decrypt_for_integrity(connection=c, account_id=b["account_id"], tenant_id="default", user_id="local-owner", secret_kind="password", purpose="integrity-verification")
    finally:
        c.close()


def test_secret_kind_tamper_and_sensitive_reason_fail_closed(hunter_db, monkeypatch):
    _key(monkeypatch)
    a = create_account(provider="lever", account_scope="aad-kind", label="x", consent_version="v1")
    store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])
    c, _ = _secret_row(a["account_id"])
    try:
        c.execute("UPDATE ats_credential_secrets SET secret_kind='otp' WHERE account_id=?", (a["account_id"],)); c.commit()
        with pytest.raises(CredentialError):
            _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id="default", user_id="local-owner", secret_kind="otp", purpose="integrity-verification")
    finally:
        c.close()
    clean = create_account(provider="lever", account_scope="reason", label="x", consent_version="v1")
    with pytest.raises(CredentialError):
        transition_account(account_id=clean["account_id"], next_state="NEEDS_LOGIN", expected_revision=clean["revision"], reason="test-only-secret")


@pytest.mark.parametrize(
    ("new_tenant", "new_user"),
    [("other-tenant", "local-owner"), ("default", "other-user")],
)
def test_tenant_or_user_metadata_tamper_reaches_aead_and_fails_closed(hunter_db, monkeypatch, new_tenant, new_user):
    _key(monkeypatch)
    a = create_account(provider="ashby", account_scope=f"aad-{new_tenant}-{new_user}", label="x", consent_version="v1")
    store_secret(account_id=a["account_id"], secret_kind="password", secret="test-only-secret", expected_revision=a["revision"])
    c, _ = _secret_row(a["account_id"])
    try:
        c.execute("INSERT OR IGNORE INTO tenants VALUES(?, 'Other', CURRENT_TIMESTAMP)", (new_tenant,))
        c.execute("INSERT OR IGNORE INTO app_users VALUES(?, 'Other', CURRENT_TIMESTAMP)", (new_user,))
        c.execute("INSERT OR IGNORE INTO tenant_memberships VALUES(?, ?, 'member', CURRENT_TIMESTAMP)", (new_tenant, new_user))
        c.execute("UPDATE ats_credential_accounts SET tenant_id=?,user_id=? WHERE account_id=?", (new_tenant, new_user, a["account_id"]))
        c.execute("UPDATE ats_credential_secrets SET tenant_id=?,user_id=? WHERE account_id=?", (new_tenant, new_user, a["account_id"])); c.commit()
        monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")
        with owner_context(tenant_id=new_tenant, user_id=new_user):
            with pytest.raises(CredentialError):
                _decrypt_for_integrity(connection=c, account_id=a["account_id"], tenant_id=new_tenant, user_id=new_user, secret_kind="password", purpose="integrity-verification")
    finally:
        c.close()
