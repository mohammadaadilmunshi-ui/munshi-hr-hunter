from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_job_card_uses_local_widget_instead_of_browser_navigation() -> None:
    source = _source("app/product_pages.py")
    start = source.index("def _job_card(")
    end = source.index("\ndef _clear_job_query", start)
    card = source[start:end]
    assert "_PRODUCT_JOB_DIALOG_STATE_KEY" in source
    assert "def _open_job_detail(job_id: int)" in source
    assert 'key=f"job_open_{key_prefix}_{job_id}"' in card
    assert "on_click=_open_job_detail" in card
    assert 'href="?view=' not in card
    assert "&amp;job=" not in card


def test_v22_prefers_one_shot_local_job_state_before_deep_link() -> None:
    source = _source("app/product_v22.py")
    start = source.index("def _job_detail_v22() -> None:")
    end = source.index("\ndef _pipeline_list_v22", start)
    detail = source[start:end]
    assert '"_PRODUCT_JOB_DIALOG_STATE_KEY"' in detail
    assert "st.session_state.pop(state_key, None)" in detail
    assert "_opportunity_dialog(int(queued))" in detail
    assert '_consume_query_id("job", "_product_v22_job_once")' in detail


def test_open_button_is_transparent_overlay_on_colored_card_only() -> None:
    source = _source("app/product_ui.py")
    assert '[class*="st-key-product_card_"]{position:relative!important' in source
    assert '[class*="st-key-job_open_"]{' in source
    assert "height:224px!important" in source
    assert "opacity:0!important" in source
