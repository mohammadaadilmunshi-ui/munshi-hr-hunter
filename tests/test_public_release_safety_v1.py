"""Public Hunter releases must not weaken checkout credential protections."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app import relay_inbox_receipt_v1


def _module():
    path=Path(__file__).resolve().parents[1]/".github/verify_public_release_safety_v1.py"
    spec=importlib.util.spec_from_file_location("public_release_security",path)
    assert spec is not None and spec.loader is not None
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_public_secret_check_rejects_real_credential_shaped_strings():
    guard=_module()
    assert "openai_key" in guard.check_blob("app/config.py",("sk-"+"a"*38).encode())
    assert "github_token" in guard.check_blob("README.md",("ghp_"+"b"*38).encode())
    assert "private_key" in guard.check_blob(
        "tests/fake.py",("-----BEGIN "+"OPENSSH PRIVATE KEY"+"-----").encode()
    )


def test_public_release_rejects_tracked_database_and_real_env():
    guard=_module()
    for f in ("data/recruiter.sqlite","config/.env","keys/ssh.pem","id_ed25519"):
        assert guard.forbidden_path(f)
    for f in (".env.example",".env.netcup.shadow.example","tests/sample.py"):
        assert not guard.forbidden_path(f)


def test_fake_test_key_does_not_exempt_any_other_file():
    guard=_module()
    mock=("sk-"+"a"*38).encode()
    assert guard.check_blob("tests/test_secure_api_vault.py",mock)
    assert guard.check_blob("app/secure_vault.py",mock)


def test_universal_inbox_reader_is_read_only_and_has_no_secret_dependencies():
    assert not hasattr(relay_inbox_receipt_v1,"apply_to_job")
    assert not hasattr(relay_inbox_receipt_v1,"send_email")
    assert relay_inbox_receipt_v1._verified_destination_host("https://jobs.example.com/role")=="example.com"
