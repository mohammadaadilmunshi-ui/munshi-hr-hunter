from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_prepare_selector_forces_full_app_rerun_from_dialog_fragment() -> None:
    source = _source("app/resume_engine_selector.py")
    assert 'st.rerun(scope="app")' in source
    assert "def _request_engine_choice(job_id: int)" in source
    request = source[
        source.index("def _request_engine_choice(job_id: int)"):
        source.index("\ndef _job_label", source.index("def _request_engine_choice(job_id: int)"))
    ]
    assert "_DIALOG_JOB_KEY" in request
    assert "_rerun_app()" in request


def test_job_drawer_native_close_is_forced_visible() -> None:
    source = _source("app/job_card_interaction_hotfix_v2.py")
    assert ".job-detail-drawer-marker" in source
    assert 'button[aria-label="Close"]' in source
    assert 'data-testid="stBaseButton-headerNoPadding"' in source
    assert "pointer-events:auto!important" in source
    assert "z-index:100000!important" in source


def test_product_shell_installs_job_drawer_hardening() -> None:
    shell = _source("app/product_shell.py")
    assert "inject_job_card_interaction_css()" in shell
    assert "install_job_card_runtime_hotfix()" in shell
    assert shell.index("install_product_v22(product_pages)") < shell.index(
        "install_job_card_runtime_hotfix()"
    ) < shell.index("install_resume_engine_selector(product_pages)")
