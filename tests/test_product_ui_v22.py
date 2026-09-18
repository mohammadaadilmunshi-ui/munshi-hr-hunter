from __future__ import annotations

from pathlib import Path


def test_v22_brand_assets_and_shell_are_wired() -> None:
    root = Path(__file__).resolve().parents[1]
    shell = (root / "app" / "product_shell.py").read_text(encoding="utf-8")
    dashboard = (root / "app" / "dashboard.py").read_text(encoding="utf-8")

    assert "brand_logo_data_uri" in shell
    assert "install_product_v22(product_pages)" in shell
    assert "munshi_crown_favicon.png" in dashboard
    assert (root / "app" / "assets" / "munshi_crown.png").is_file()
    assert (root / "app" / "assets" / "munshi_crown_favicon.png").is_file()


def test_v22_job_dialog_is_one_shot_and_hides_raw_json() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "product_v22.py").read_text(encoding="utf-8")

    assert 'del st.query_params[parameter]' in source
    assert 'st.session_state.pop(state_key, None)' in source
    assert "Open prepared resume" in source
    assert "Prepared package" in source
    assert "Raw machine evidence" not in source
    assert "Raw extractor JSON is kept out" in source


def test_v22_tracker_is_clickable_and_humanized() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "product_v22.py").read_text(encoding="utf-8")

    assert "pipeline-row-link" in source
    assert "pipeline_job" in source
    assert "No resume generated" in source
    assert "completed_without_writer" in source
    assert "completed_with_warnings" in source
    assert 'return "Review status"' in source
    assert "Workflow: Completed" not in source


def test_v22_profile_and_settings_explain_source_boundaries() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "product_v22.py").read_text(encoding="utf-8")

    assert "MUNSHI Apply profile bridge" in source
    assert "browser-local" in source
    assert "Master resume" in source
    assert "Opportunities scanned" in source
    assert "Engineering diagnostics" in source
    assert "system-stat" in source
