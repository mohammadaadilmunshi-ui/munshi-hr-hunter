from __future__ import annotations

from pathlib import Path

from app.resume_engine_selector import _job_label, native_resume_v5_enabled


def test_native_job_label_matches_resume_studio_selector_format() -> None:
    row = {
        "id": 42,
        "company_name": "Example Co",
        "title": "People Analytics Analyst",
        "hunter_score": 91.4,
    }
    assert _job_label(row) == "#42 · Example Co · People Analytics Analyst · 91% match"


def test_native_v5_is_available_by_default_but_can_be_explicitly_disabled(
    monkeypatch,
) -> None:
    monkeypatch.delenv("MUNSHI_NATIVE_RESUME_V5_ENABLED", raising=False)
    assert native_resume_v5_enabled() is True

    monkeypatch.setenv("MUNSHI_NATIVE_RESUME_V5_ENABLED", "false")
    assert native_resume_v5_enabled() is False

    monkeypatch.setenv("MUNSHI_NATIVE_RESUME_V5_ENABLED", "true")
    assert native_resume_v5_enabled() is True


def test_product_shell_preserves_selector_without_intercepting_prepare() -> None:
    root = Path(__file__).resolve().parents[1]
    shell = (root / "app" / "product_shell.py").read_text(encoding="utf-8")
    selector = (root / "app" / "resume_engine_selector.py").read_text(
        encoding="utf-8"
    )

    assert "install_product_v22(product_pages)" in shell
    assert "install_resume_engine_selector(product_pages)" in shell
    assert shell.index("install_product_v22(product_pages)") < shell.index(
        "install_resume_engine_selector(product_pages)"
    )
    assert "render_resume_engine_selector()" in shell

    assert "_resume_engine_original_n8n_prepare = pages_module._prepare_job" not in selector
    assert "pages_module._prepare_job = _request_engine_choice" not in selector
    assert "_run_n8n(job_id)" in selector

    assert "from app.native_resume_service_v5 import writer_status" in selector
    assert "from app import resume_one_page_guard_v1 as one_page_guard" in selector
    assert "from app import native_resume_quality_hotfix_v2 as quality_hotfix" in selector
    assert "quality_hotfix.install()" in selector
    assert "one_page_guard.generate_checked(" in selector
    assert "rewrite_mode=native_mode" in selector
    assert "artifacts.materialize" not in selector
    assert "current_candidate_profile_snapshot" in selector
    assert "record.get(\"stage_b_bound\") is not True" in selector
    assert "status.get('model')" not in selector
    assert 'status.get("model")' not in selector
    assert "MUNSHI Native Resume" in selector


def test_resume_studio_entrypoint_uses_candidate_truth_first_v4_page() -> None:
    root = Path(__file__).resolve().parents[1]
    entrypoint = (root / "app" / "resume_studio_page.py").read_text(
        encoding="utf-8"
    )
    page = (root / "app" / "resume_studio_page_v4.py").read_text(
        encoding="utf-8"
    )

    assert "from app.resume_studio_page_v4 import render" in entrypoint
    assert "resume_studio_page_v3" not in entrypoint
    assert "native_resume_service_v4" not in entrypoint

    assert "current_candidate_profile_snapshot" in page
    assert "file_uploader" not in page
    assert "extract_profile" not in page
    assert "save_confirmed_source" not in page
    assert "Native profile extractor" not in page
    assert "Advanced resume settings" in page
    assert "artifacts.materialize" in page
    assert '"Download DOCX"' in page
    assert '"Download PDF"' in page
