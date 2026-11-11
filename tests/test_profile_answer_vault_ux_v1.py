from __future__ import annotations

from pathlib import Path


def _profile_answer_sources() -> str:
    root = Path(__file__).resolve().parents[1]
    return "\n".join(
        [
            (root / "app" / "profile_workspace_v4.py").read_text(encoding="utf-8"),
            (root / "app" / "profile_answer_center_ui_v1.py").read_text(encoding="utf-8"),
        ]
    )


def test_profile_answer_vault_hides_catalog_from_normal_user_flow() -> None:
    source = _profile_answer_sources()

    assert "Recognition phrasings" not in source
    assert "Advanced: view the 100-phrase recognition catalog" not in source
    assert "Common phrasings" not in source
    assert "Canonical concepts" not in source
    assert "Prepare / refresh answers from Candidate Truth" in source
    assert "Reusable gaps" in source
    assert "Protected ready" in source
    assert "Provider wording stays internal" in source


def test_profile_answer_vault_only_surfaces_reusable_delta() -> None:
    source = _profile_answer_sources()

    assert "Complete reusable profile" in source
    assert "Save completed reusable answers" in source
    assert "Handled when an application needs it" in source
    assert "not mandatory onboarding" in source
