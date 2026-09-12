from __future__ import annotations

from pathlib import Path


def test_profile_answer_vault_hides_catalog_from_normal_user_flow() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "profile_workspace_v4.py").read_text(encoding="utf-8")

    assert "Recognition phrasings" not in source
    assert "Advanced: view the 100-phrase recognition catalog" not in source
    assert "Common phrasings" not in source
    assert "Canonical concepts" not in source
    assert "Prepare answers automatically from Candidate Truth" in source
    assert "Reusable gaps" in source
    assert "Protected ready" in source
    assert "The provider-phrasing recognition catalog stays internal" in source


def test_profile_answer_vault_only_surfaces_reusable_delta() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "profile_workspace_v4.py").read_text(encoding="utf-8")

    assert "Optional: review {len(delta)} reusable gap(s)" in source
    assert "Ask only when needed" in source
    assert "You do not need to complete a questionnaire" in source
