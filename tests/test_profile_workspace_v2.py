from pathlib import Path


def test_profile_v2_exposes_extracted_preview_before_confirmation() -> None:
    source = Path("app/profile_workspace_v2.py").read_text(encoding="utf-8")
    assert "latest_profile_for_source" in source
    assert "_render_profile_details" in source
    assert "Profile preview" in source
    assert "Confirm as permanent profile" in source
    assert "confirm_profile_extract" in source


def test_profile_v2_can_build_from_saved_master_resume() -> None:
    source = Path("app/profile_workspace_v2.py").read_text(encoding="utf-8")
    assert "Build profile from Master Resume" in source
    assert "extract_profile_from_source" in source
    assert "confirmed Master Resume" in source


def test_product_shell_preserves_v1_route_contract_while_installing_v2_handoff() -> None:
    source = Path("app/product_shell.py").read_text(encoding="utf-8")
    assert "from app import product_pages, profile_workspace_v1, resume_studio_page" in source
    assert "from app import profile_workspace_v2" in source
    assert "profile_workspace_v1.render = profile_workspace_v2.render" in source
    assert '"profile": profile_workspace_v1.render' in source


def test_v2_reuses_v1_logo_resolved_layout_instead_of_reimplementing_it() -> None:
    source = Path("app/profile_workspace_v2.py").read_text(encoding="utf-8")
    v1 = Path("app/profile_workspace_v1.py").read_text(encoding="utf-8")
    assert "v1._render_profile_details" in source
    assert "resolve_brand_assets" in v1
    assert "profile-logo" in v1
