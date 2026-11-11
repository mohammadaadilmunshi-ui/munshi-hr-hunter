from __future__ import annotations

import ast
import re
from pathlib import Path


def _source(path: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / path).read_text(encoding="utf-8")


def test_product_ui_uses_manrope_with_system_fallbacks() -> None:
    source = _source("app/product_ui.py")
    assert "fonts.googleapis.com" in source
    assert "family=Manrope" in source
    assert '--m-font:"Manrope"' in source
    assert "font-family:var(--m-font)!important" in source
    assert "-webkit-font-smoothing:antialiased" in source


def test_job_card_palette_is_broad_and_unique() -> None:
    source = _source("app/product_ui.py")
    match = re.search(r"(?ms)^PASTELS\s*=\s*(\(.*?^\))", source)
    assert match, "PASTELS tuple missing"
    palette = ast.literal_eval(match.group(1))
    assert len(palette) >= 18
    assert len(set(palette)) == len(palette)
    assert "#DCEFE5" in palette
    assert "#EEE2F1" in palette
    assert "#E2E4F5" in palette


def test_job_card_typography_is_softer_and_more_geometric() -> None:
    source = _source("app/product_ui.py")
    assert ".job-title{" in source
    assert "font-weight:650" in source
    assert "letter-spacing:-.028em" in source
    assert ".job-company{font-weight:700" in source


def test_only_opportunity_dialog_uses_light_liquid_glass_overlay() -> None:
    source = _source("app/product_v22.py")
    assert "job-detail-drawer-marker" in source
    assert 'body:has(.job-detail-drawer-marker) div[data-baseweb="modal"]' in source
    assert "rgba(232,240,236,.20)!important" in source
    assert "blur(6px)" in source
    assert "saturate(1.12)" in source
    assert "brightness(1.01)" in source
    assert "rgba(255,255,255,.12)" in source
    assert "mix-blend-mode:screen" in source
    assert '[data-testid="stDialog"]:has(.job-detail-drawer-marker),' in source
    assert "background:transparent!important" in source
    assert "position:fixed!important" in source
    assert "right:0!important" in source
    assert "height:100dvh!important" in source
    assert "width:min(640px,94vw)!important" in source


def test_job_drawer_keeps_existing_opportunity_authority_and_actions() -> None:
    source = _source("app/product_v22.py")
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
