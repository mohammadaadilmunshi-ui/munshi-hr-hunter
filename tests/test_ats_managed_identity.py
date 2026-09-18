from __future__ import annotations

import base64
import re

from app.ats_credentials import _decrypt_for_integrity, create_account, store_secret
from app.ats_managed_identity import (
    ensure_application_identity,
    identity_status,
    prepare_managed_account,
)


def _vault(monkeypatch) -> None:
    monkeypatch.setenv(
        "MUNSHI_VAULT_KEY",
        base64.urlsafe_b64encode(b"m" * 32).decode(),
    )
    monkeypatch.setenv("MUNSHI_APPLICATION_MAIL_DOMAIN", "apply.munshi.systems")
    monkeypatch.delenv("MUNSHI_APPLICATION_MAIL_RELAY_ENABLED", raising=False)


def test_application_email_is_opaque_stable_and_unique_for_owner(hunter_db, monkeypatch):
    _vault(monkeypatch)
    first = ensure_application_identity()
    second = ensure_application_identity()

    assert first == second
    assert first["email_address"].endswith("@apply.munshi.systems")
    assert re.fullmatch(r"u_[a-z0-9]+@apply\.munshi\.systems", first["email_address"])
    assert "local-owner" not in first["email_address"]
    assert first["relay_state"] == "RESERVED"
    assert first["relay_ready"] is False


def test_mail_relay_readiness_changes_metadata_not_identity(hunter_db, monkeypatch):
    _vault(monkeypatch)
    before = ensure_application_identity()
    monkeypatch.setenv("MUNSHI_APPLICATION_MAIL_RELAY_ENABLED", "1")
    after = ensure_application_identity()

    assert before["identity_id"] == after["identity_id"]
    assert before["email_address"] == after["email_address"]
    assert after["relay_state"] == "ACTIVE"
    assert after["relay_ready"] is True


def test_new_managed_ats_account_gets_unique_write_only_password(hunter_db, monkeypatch):
    _vault(monkeypatch)
    result = prepare_managed_account(
        provider="workday",
        account_scope="acme.myworkdayjobs.com/acme",
        label="Acme Workday",
    )

    assert result["password_policy"] == "MUNSHI_GENERATED_UNIQUE"
    assert result["password_managed"] is True
    assert result["rotation_required"] is False
    assert result["credential_state"] == "AVAILABLE"
    assert result["remote_account_created"] is False
    assert result["browser_authority"] is False
    assert result["secret_ref"].startswith("ats-secret://")
    assert "password" not in {key.lower() for key in result if key != "password_policy"}

    from app.database import get_connection

    connection = get_connection()
    try:
        secret = connection.execute(
            "SELECT * FROM ats_credential_secrets WHERE account_id=? AND secret_kind='password'",
            (result["account_id"],),
        ).fetchone()
        binding = connection.execute(
            "SELECT * FROM ats_managed_account_bindings WHERE account_id=?",
            (result["account_id"],),
        ).fetchone()
        assert secret is not None
        assert binding is not None
        assert binding["password_mode"] == "MUNSHI_GENERATED_UNIQUE"
        assert len(bytes(secret["ciphertext"])) > 20
    finally:
        connection.close()


def test_managed_password_is_unique_per_ats_scope(hunter_db, monkeypatch):
    _vault(monkeypatch)
    first = prepare_managed_account(
        provider="workday",
        account_scope="tenant-one.example",
        label="One",
    )
    second = prepare_managed_account(
        provider="workday",
        account_scope="tenant-two.example",
        label="Two",
    )

    from app.database import get_connection

    connection = get_connection()
    try:
        one = _decrypt_for_integrity(
            connection=connection,
            account_id=first["account_id"],
            tenant_id="default",
            user_id="local-owner",
            secret_kind="password",
            purpose="integrity-verification",
        )
        two = _decrypt_for_integrity(
            connection=connection,
            account_id=second["account_id"],
            tenant_id="default",
            user_id="local-owner",
            secret_kind="password",
            purpose="integrity-verification",
        )
        assert one != two
        assert len(one) == 28
        assert len(two) == 28
    finally:
        connection.close()


def test_existing_unclassified_password_is_not_silently_replaced(hunter_db, monkeypatch):
    _vault(monkeypatch)
    account = create_account(
        provider="workday",
        account_scope="legacy.example",
        label="Legacy",
        consent_version="legacy-v1",
    )
    ready = store_secret(
        account_id=account["account_id"],
        secret_kind="password",
        secret="LegacyRemotePassword!123",
        expected_revision=account["revision"],
    )

    result = prepare_managed_account(
        provider="workday",
        account_scope="legacy.example",
        label="Legacy",
    )
    assert ready["state"] == "AVAILABLE"
    assert result["password_managed"] is False
    assert result["rotation_required"] is True
    assert result["password_policy"] == "LEGACY_REQUIRES_ROTATION"

    from app.database import get_connection

    connection = get_connection()
    try:
        plaintext = _decrypt_for_integrity(
            connection=connection,
            account_id=account["account_id"],
            tenant_id="default",
            user_id="local-owner",
            secret_kind="password",
            purpose="integrity-verification",
        )
        assert plaintext == b"LegacyRemotePassword!123"
    finally:
        connection.close()


def test_identity_status_declares_munshi_managed_product_policy(hunter_db, monkeypatch):
    _vault(monkeypatch)
    status = identity_status()
    assert status["password_policy"] == "MUNSHI_GENERATED_UNIQUE_PER_ATS_ACCOUNT"
    assert status["personal_email_required_for_ats"] is False
    assert status["inbound_verification_live"] is False
