from __future__ import annotations

from pathlib import Path


def _source(name: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / name).read_text(encoding="utf-8")


def test_autoapply_settings_layer_is_installed_after_v22() -> None:
    source = _source("app/product_shell.py")
    assert "from app.autoapply_settings_ui import install_autoapply_settings_ui" in source
    assert source.index("install_product_v22(product_pages)") < source.index(
        "install_autoapply_settings_ui(product_pages)"
    )


def test_credentials_page_exposes_autoapply_product_controls() -> None:
    source = _source("app/autoapply_settings_ui.py")
    required = (
        "Credentials & AutoApply",
        "Enable autonomous browser executor",
        "Claude authentication",
        "Browser-agent model",
        "Maximum agent turns per application",
        "Maximum AI cost per application (USD)",
        "Run browser headless when supported",
        "Permit final submit when the Application Plan also authorizes it",
        "Security checkpoint continuation",
        "User-assisted verification · automatic continuation",
    )
    for token in required:
        assert token in source


def test_autoapply_settings_keep_submit_and_security_authority_explicit() -> None:
    source = _source("app/autoapply_settings_ui.py")
    assert "Runtime submit authority remains a separate gate" in source
    assert "does not bypass Candidate Truth" in source
    assert "Application Plan" in source
    assert "independent submission verification" in source
    assert "CAPTCHA, MFA, OTP, identity" in source
    assert "resumes only after the extension" in source


def test_api_key_never_round_trips_to_ui() -> None:
    source = _source("app/autoapply_settings_ui.py")
    assert "type=\"password\"" in source
    assert "Stored value is never displayed" in source
    assert "store_secret(_ANTHROPIC_CREDENTIAL_TYPE" in source
    assert "read_secret" not in source
    assert "_vault_secret_exists" in source


def test_runtime_probe_is_read_only_health_only() -> None:
    source = _source("app/autoapply_settings_ui.py")
    assert '"http://apply:8000/health"' in source
    assert "httpx.get" in source
    assert "MUNSHI_APPLY_RUNTIME_HEALTH_URL" in source
    assert "timeout=1.25" in source
