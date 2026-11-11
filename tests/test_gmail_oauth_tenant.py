from __future__ import annotations

import base64

import pytest

from app.gmail_oauth_tenant import (
    GMAIL_READONLY_SCOPE,
    GmailOAuthError,
    _consume_authorization_intent,
    _decrypt_for_integrity,
    account_status,
    begin_authorization_intent,
    create_account,
    record_email_evidence,
    store_token,
    transition_account,
)
from app.tenant_foundation import owner_context


def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUNSHI_GMAIL_OAUTH_VAULT_KEY", base64.urlsafe_b64encode(b"g" * 32).decode())


def _token_row(account_id: str):
    from app.database import get_connection
    connection = get_connection()
    return connection, connection.execute("SELECT * FROM gmail_oauth_tokens WHERE account_id=?", (account_id,)).fetchone()


def _connected(monkeypatch: pytest.MonkeyPatch):
    _key(monkeypatch)
    account = create_account(account_label="local", consent_revision="v1", scopes=[GMAIL_READONLY_SCOPE])
    status = transition_account(account_id=account["account_id"], next_state="AUTHORIZATION_REQUIRED", expected_revision=account["revision"], reason="AUTHORIZATION_STARTED")
    status = store_token(account_id=account["account_id"], token_kind="refresh_token", token="test-refresh-token", expected_revision=status["revision"], email_address="candidate@example.invalid")
    return account, status


def test_oauth_metadata_and_tokens_are_tenant_bound_write_only(hunter_db, monkeypatch):
    account, status = _connected(monkeypatch)
    assert status["state"] == "CONNECTED"
    assert "test-refresh-token" not in str(status)
    assert account_status(account_id=account["account_id"])["email_address"] == "candidate@example.invalid"
    connection, row = _token_row(account["account_id"])
    try:
        assert b"test-refresh-token" not in bytes(row["ciphertext"])
        assert _decrypt_for_integrity(connection=connection, account_id=account["account_id"], tenant_id="default", user_id="local-owner", token_kind="refresh_token", purpose="integrity-verification") == b"test-refresh-token"
    finally:
        connection.close()


@pytest.mark.parametrize("column", ["ciphertext", "nonce"])
def test_token_tampering_fails_closed(hunter_db, monkeypatch, column):
    account, _ = _connected(monkeypatch)
    connection, row = _token_row(account["account_id"])
    try:
        value = bytearray(row[column]); value[-1] ^= 1
        connection.execute(f"UPDATE gmail_oauth_tokens SET {column}=? WHERE account_id=?", (bytes(value), account["account_id"]))
        connection.commit()
        with pytest.raises(GmailOAuthError):
            _decrypt_for_integrity(connection=connection, account_id=account["account_id"], tenant_id="default", user_id="local-owner", token_kind="refresh_token", purpose="integrity-verification")
    finally:
        connection.close()


def test_token_aad_versions_wrong_key_and_purpose_fail_closed(hunter_db, monkeypatch):
    account, _ = _connected(monkeypatch)
    connection, _ = _token_row(account["account_id"])
    try:
        monkeypatch.setenv("MUNSHI_GMAIL_OAUTH_VAULT_KEY", base64.urlsafe_b64encode(b"h" * 32).decode())
        with pytest.raises(GmailOAuthError):
            _decrypt_for_integrity(connection=connection, account_id=account["account_id"], tenant_id="default", user_id="local-owner", token_kind="refresh_token", purpose="integrity-verification")
        _key(monkeypatch)
        for column, value in (("algorithm_version", "future-aead"), ("key_version", "future-key")):
            connection.execute(f"UPDATE gmail_oauth_tokens SET {column}=? WHERE account_id=?", (value, account["account_id"]))
            connection.commit()
            with pytest.raises(GmailOAuthError):
                _decrypt_for_integrity(connection=connection, account_id=account["account_id"], tenant_id="default", user_id="local-owner", token_kind="refresh_token", purpose="integrity-verification")
            connection.execute(f"UPDATE gmail_oauth_tokens SET {column}=? WHERE account_id=?", ("aes-gcm-v1" if column == "algorithm_version" else "gmail-oauth-env-v1", account["account_id"]))
        with pytest.raises(GmailOAuthError):
            _decrypt_for_integrity(connection=connection, account_id=account["account_id"], tenant_id="default", user_id="local-owner", token_kind="refresh_token", purpose="view-token")
    finally:
        connection.close()


def test_invalid_scope_passwords_and_stale_cas_are_rejected(hunter_db, monkeypatch):
    _key(monkeypatch)
    with pytest.raises(GmailOAuthError):
        create_account(account_label="bad", consent_revision="v1", scopes=["https://www.googleapis.com/auth/gmail.send"])
    account = create_account(account_label="local", consent_revision="v1")
    state = transition_account(account_id=account["account_id"], next_state="AUTHORIZATION_REQUIRED", expected_revision=account["revision"], reason="AUTHORIZATION_STARTED")
    with pytest.raises(GmailOAuthError):
        store_token(account_id=account["account_id"], token_kind="password", token="not-permitted", expected_revision=state["revision"])
    store_token(account_id=account["account_id"], token_kind="refresh_token", token="safe-token", expected_revision=state["revision"])
    with pytest.raises(GmailOAuthError):
        store_token(account_id=account["account_id"], token_kind="access_token", token="safe-token", expected_revision=state["revision"])


def test_authorization_intent_is_hashed_encrypted_expiring_and_single_use(hunter_db, monkeypatch):
    _key(monkeypatch)
    account = create_account(account_label="intent", consent_revision="v1")
    intent = begin_authorization_intent(account_id=account["account_id"], expected_revision=account["revision"])
    assert set(intent) == {"state", "code_challenge"}
    from app.database import get_connection
    connection = get_connection()
    try:
        row = connection.execute("SELECT state_hash,verifier_ciphertext,used_at FROM gmail_oauth_authorization_intents").fetchone()
        assert intent["state"] not in str(row) and b"test" not in bytes(row["verifier_ciphertext"])
        verifier = _consume_authorization_intent(connection=connection, state=intent["state"], account_id=account["account_id"], tenant_id="default", user_id="local-owner", purpose="oauth-token-exchange")
        assert verifier and intent["code_challenge"]
        with pytest.raises(GmailOAuthError):
            _consume_authorization_intent(connection=connection, state=intent["state"], account_id=account["account_id"], tenant_id="default", user_id="local-owner", purpose="oauth-token-exchange")
    finally:
        connection.close()


def test_legal_transitions_and_revocation_purges_tokens(hunter_db, monkeypatch):
    account, status = _connected(monkeypatch)
    with pytest.raises(GmailOAuthError):
        transition_account(account_id=account["account_id"], next_state="DISCONNECTED", expected_revision=status["revision"], reason="TOKEN_REVOKED")
    revoked = transition_account(account_id=account["account_id"], next_state="REVOKED", expected_revision=status["revision"], reason="TOKEN_REVOKED")
    assert revoked["state"] == "REVOKED"
    connection, row = _token_row(account["account_id"])
    try:
        assert row is None
    finally:
        connection.close()
    with pytest.raises(GmailOAuthError):
        store_token(account_id=account["account_id"], token_kind="refresh_token", token="safe-token", expected_revision=revoked["revision"])


def test_tenant_isolation_and_aad_owner_binding(hunter_db, monkeypatch):
    account, _ = _connected(monkeypatch)
    from app.database import get_connection
    connection = get_connection()
    try:
        connection.execute("INSERT INTO tenants VALUES('other','Other',CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO app_users VALUES('other-user','Other',CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO tenant_memberships VALUES('other','other-user','member',CURRENT_TIMESTAMP)")
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")
    with owner_context(tenant_id="other", user_id="other-user"):
        with pytest.raises(LookupError):
            account_status(account_id=account["account_id"])
        with pytest.raises(LookupError):
            store_token(account_id=account["account_id"], token_kind="refresh_token", token="safe-token", expected_revision=1)


def test_email_evidence_is_deduplicated_provenanced_and_never_changes_lifecycle(hunter_db, monkeypatch):
    account, _ = _connected(monkeypatch)
    event = record_email_evidence(account_id=account["account_id"], source_message_id="gmail-message-1", source_thread_id="thread-1", application_ref="application-1", subject="Application received", sender="jobs@example.invalid", snippet="Thank you for applying")
    assert event and event["event_type"] == "SUBMISSION_CONFIRMATION"
    assert event["provenance"] == "gmail_oauth_local_ingest" and event["confidence"] == 0.95
    assert "Thank you" not in str(event)
    assert record_email_evidence(account_id=account["account_id"], source_message_id="gmail-message-1", application_ref="application-1", subject="Application received") is None
    verification = record_email_evidence(account_id=account["account_id"], source_message_id="gmail-message-otp", application_ref=None, subject="Verification code", snippet="123456")
    assert verification and verification["event_type"] == "OTP_VERIFICATION" and "123456" not in str(verification)
