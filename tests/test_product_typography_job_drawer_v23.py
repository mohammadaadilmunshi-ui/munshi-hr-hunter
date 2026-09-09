from __future__ import annotations

from pathlib import Path


def test_product_ui_uses_manrope_with_system_fallbacks() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "product_ui.py").read_text(encoding="utf-8")

    assert "fonts.googleapis.com" in source
    assert "family=Manrope" in source
    assert '--m-font:"Manrope"' in source
    assert "font-family:var(--m-font)!important" in source
    assert "-webkit-font-smoothing:antialiased" in source


def test_job_card_typography_is_softer_and_more_geometric() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "product_ui.py").read_text(encoding="utf-8")

    assert ".job-title{" in source
    assert "font-weight:650" in source
    assert "letter-spacing:-.028em" in source
    assert ".job-company{font-weight:700" in source


def test_only_opportunity_dialog_becomes_liquid_glass_side_drawer() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "product_v22.py").read_text(encoding="utf-8")

    assert "job-detail-drawer-marker" in source
    assert 'body:has(.job-detail-drawer-marker) div[data-baseweb="modal"]' in source
    assert '[data-testid="stDialog"]:has(.job-detail-drawer-marker),' in source
    assert "background:transparent!important" in source
    assert "brightness(1.03)" in source
    assert "mix-blend-mode:screen" in source
    assert "position:fixed!important" in source
    assert "right:0!important" in source
    assert "height:100dvh!important" in source
    assert "width:min(640px,94vw)!important" in source
    assert "job_drawer_actions_" in source
    assert "position:sticky!important" in source


def test_job_drawer_keeps_existing_opportunity_authority_and_actions() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "product_v22.py").read_text(encoding="utf-8")

    dialog_start = source.index("def _opportunity_dialog(job_id: int) -> None:")
    dialog_end = source.find("\ndef ", dialog_start + 1)
    dialog = source[dialog_start:] if dialog_end == -1 else source[dialog_start:dialog_end]

    assert "_job_bundle(job_id)" in dialog
    assert 'st.session_state["last_viewed_job_id"] = int(job_id)' in dialog
    assert "_render_recruiter_contacts(job_id)" in dialog
    assert "_PAGES._toggle_job_saved" in dialog
    assert "_PAGES._toggle_job_skipped" in dialog
    assert "_PAGES._prepare_job" in dialog
    assert "Open original job" in dialog
    assert "final_submit" not in dialog
    assert "adapter.submit" not in dialog
