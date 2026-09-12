from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from app.secure_vault import (
    VaultError,
    delete_secret,
    secret_metadata,
    store_secret,
    vault_readiness,
)


def _encoded_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def test_metadata_and_audit_never_contain_plaintext(hunter_db, monkeypatch) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _encoded_key())
    monkeypatch.delenv("MUNSHI_VAULT_KEY_FILE", raising=False)
    secret = "sk-test-value-that-must-never-appear-in-metadata"

    store_secret("autoapply_anthropic_api_key", secret, actor="pytest")
    metadata = secret_metadata("autoapply_anthropic_api_key")
    assert metadata is not None
    assert "ciphertext" not in metadata
    assert "nonce" not in metadata
    assert secret not in repr(metadata)

    from app.database import get_connection

    connection = get_connection()
    try:
        audit = connection.execute(
            "SELECT credential_type,account_label,action,actor,created_at "
            "FROM credential_secret_audit ORDER BY id"
        ).fetchall()
        serialized = repr([dict(row) for row in audit])
        assert secret not in serialized
        assert audit[-1]["action"] == "store"
        assert audit[-1]["actor"] == "pytest"
    finally:
        connection.close()

    store_secret("autoapply_anthropic_api_key", secret + "-rotated", actor="pytest")
    delete_secret("autoapply_anthropic_api_key", actor="pytest")

    connection = get_connection()
    try:
        actions = [
            row["action"]
            for row in connection.execute(
                "SELECT action FROM credential_secret_audit ORDER BY id"
            ).fetchall()
        ]
    finally:
        connection.close()
    assert actions[-3:] == ["store", "replace", "delete"]


def test_production_readiness_prefers_private_key_file(
    hunter_db, monkeypatch, tmp_path: Path
) -> None:
    pytest.importorskip("cryptography")
    key_file = tmp_path / "vault.key"
    key_file.write_text(_encoded_key() + "\n", encoding="ascii")
    key_file.chmod(0o600)

    monkeypatch.delenv("MUNSHI_VAULT_KEY", raising=False)
    monkeypatch.setenv("MUNSHI_VAULT_KEY_FILE", str(key_file))
    monkeypatch.setenv("MUNSHI_ENVIRONMENT", "production")

    readiness = vault_readiness()
    assert readiness["available"] is True
    assert readiness["production_ready"] is True
    assert readiness["key_source"] == "key-file"
    assert readiness["algorithm"] == "aes-gcm-v1"


def test_production_readiness_rejects_environment_only_master_key(
    hunter_db, monkeypatch
) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _encoded_key())
    monkeypatch.delenv("MUNSHI_VAULT_KEY_FILE", raising=False)
    monkeypatch.setenv("MUNSHI_ENVIRONMENT", "production")

    readiness = vault_readiness()
    assert readiness["available"] is True
    assert readiness["production_ready"] is False
    assert readiness["key_source"] == "environment"
    assert "requires MUNSHI_VAULT_KEY_FILE" in readiness["reason"]


def test_key_file_rejects_group_or_world_access(
    hunter_db, monkeypatch, tmp_path: Path
) -> None:
    pytest.importorskip("cryptography")
    key_file = tmp_path / "vault.key"
    key_file.write_text(_encoded_key(), encoding="ascii")
    key_file.chmod(0o644)

    monkeypatch.delenv("MUNSHI_VAULT_KEY", raising=False)
    monkeypatch.setenv("MUNSHI_VAULT_KEY_FILE", str(key_file))

    readiness = vault_readiness()
    assert readiness["available"] is False
    assert readiness["production_ready"] is False
    assert "permissions are unsafe" in readiness["reason"]


def test_dashboard_vault_is_write_only_by_source_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "secure_api_vault_ui.py").read_text(encoding="utf-8")

    assert "Secure API Key Vault" in source
    assert "Anthropic / Claude" in source
    assert "OpenAI" in source
    assert "Google Gemini" in source
    assert 'type="password"' in source
    assert "read_secret" not in source
    assert "ciphertext" not in source
    assert "nonce" not in source
    assert "Final submit" in source
    assert "disabled=True" in source


def test_initial_dashboard_installs_vault_after_v22() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "product_shell.py").read_text(encoding="utf-8")
    assert "from app.secure_api_vault_ui import install_secure_api_vault_ui" in source
    assert source.index("install_product_v22(product_pages)") < source.index(
        "install_secure_api_vault_ui(product_pages)"
    )


def test_invalid_secret_inputs_fail_without_echoing_value(hunter_db, monkeypatch) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _encoded_key())
    monkeypatch.delenv("MUNSHI_VAULT_KEY_FILE", raising=False)
    bad = "bad\nsecret-value"
    with pytest.raises(VaultError) as error:
        store_secret("openai_api_key", bad)
    assert bad not in str(error.value)
