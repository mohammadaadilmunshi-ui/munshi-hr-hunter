from __future__ import annotations

from pathlib import Path


def test_resume_studio_has_one_profile_authority_and_no_duplicate_intake() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "app" / "resume_studio_page_v4.py").read_text(
        encoding="utf-8"
    )
    truth = (root / "app" / "profile_snapshot_projection.py").read_text(
        encoding="utf-8"
    )

    assert "current_candidate_profile_snapshot" in page
    assert "Master Resume extraction and profile editing stay in Profile" in page
    assert "file_uploader" not in page
    assert "_profile_workspace" not in page

    assert "overrides.resolve_profile(extracted)" in truth
    assert "load_candidate_profile_details_envelope" in truth
    assert "authority" in truth


def test_model_and_api_details_are_advanced_only() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "app" / "resume_studio_page_v4.py").read_text(
        encoding="utf-8"
    )
    selector = (root / "app" / "resume_engine_selector.py").read_text(
        encoding="utf-8"
    )

    assert "Advanced resume settings" in page
    assert "_writer_settings_panel()" in page
    assert 'with st.expander("JD match details"' in page
    assert '"Open system diagnostics"' in page
    assert 'with st.expander("Advanced diagnostics"' not in page
    assert "status.get('model')" not in selector
    assert 'status.get("model")' not in selector
