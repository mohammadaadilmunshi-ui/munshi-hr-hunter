from __future__ import annotations

from pathlib import Path


def _source(path: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / path).read_text(encoding="utf-8")


def test_settings_profile_uses_unified_candidate_truth_authority() -> None:
    source = _source("app/settings_profile_authority_ui_v1.py")
    assert "profile_authority.authority_snapshot()" in source
    assert "draft_fact_count" in source
    assert "structured_fact_count" in source
    assert "Candidate Truth" in source
    assert "extracted value(s) ready for review" in source
    assert "Open Profile details" in source


def test_profile_settings_adapter_delegates_non_profile_sections() -> None:
    source = _source("app/settings_profile_authority_ui_v1.py")
    assert 'if section == "Profile & defaults"' in source
    assert "original_settings()" in source


def test_product_shell_installs_profile_settings_adapter_after_vault() -> None:
    source = _source("app/product_shell.py")
    assert "install_settings_profile_authority_ui" in source
    assert source.index("install_secure_api_vault_ui(product_pages)") < source.index(
        "install_settings_profile_authority_ui(product_pages)"
    )


def test_normal_profile_ui_does_not_expose_recognition_catalog() -> None:
    source = _source("app/profile_workspace_v4.py")
    for forbidden in (
        "Recognition phrasings",
        "Common phrasings",
        "Canonical concepts",
        "Advanced: view the 100-phrase recognition catalog",
    ):
        assert forbidden not in source
    assert "Reusable gaps" in source
    assert "Protected ready" in source
