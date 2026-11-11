from pathlib import Path

from app import profile_workspace_v1 as workspace


def test_profile_tabs_match_product_deep_links() -> None:
    assert workspace.PROFILE_TABS == {
        "Resume": "resume",
        "Cover Letter": "cover-letter",
        "Profile Details": "details",
    }


def test_product_shell_routes_profile_to_permanent_workspace() -> None:
    source = Path("app/product_shell.py").read_text(encoding="utf-8")
    assert "from app import product_pages, profile_workspace_v1, resume_studio_page" in source
    assert '"profile": profile_workspace_v1.render' in source


def test_profile_workspace_keeps_resume_studio_as_edit_source() -> None:
    source = Path("app/profile_workspace_v1.py").read_text(encoding="utf-8")
    assert "?view=resume-studio" in source
    assert "_latest_confirmed_profile" in source
    assert "status='CONFIRMED'" in source
    assert "resolve_brand_assets" in source


def test_logo_resolver_does_not_import_candidate_profile_or_sensitive_fields() -> None:
    source = Path("app/profile_brand_resolver.py").read_text(encoding="utf-8")
    assert "candidate_profile" not in source.casefold()
    assert "gender" not in source.casefold()
    assert "ethnicity" not in source.casefold()
    assert "work_authorization" not in source.casefold()
