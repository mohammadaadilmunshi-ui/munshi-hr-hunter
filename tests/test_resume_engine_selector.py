from __future__ import annotations

from pathlib import Path

from app.resume_engine_selector import _job_label


def test_native_job_label_matches_resume_studio_selector_format() -> None:
    row = {
        "id": 42,
        "company_name": "Example Co",
        "title": "People Analytics Analyst",
        "hunter_score": 91.4,
    }
    assert _job_label(row) == "#42 · Example Co · People Analytics Analyst · 91% match"


def test_product_shell_installs_engine_selector_after_v22() -> None:
    root = Path(__file__).resolve().parents[1]
    shell = (root / "app" / "product_shell.py").read_text(encoding="utf-8")
    selector = (root / "app" / "resume_engine_selector.py").read_text(encoding="utf-8")

    assert "install_product_v22(product_pages)" in shell
    assert "install_resume_engine_selector(product_pages)" in shell
    assert shell.index("install_product_v22(product_pages)") < shell.index(
        "install_resume_engine_selector(product_pages)"
    )
    assert "render_resume_engine_selector()" in shell

    # Existing n8n behavior is preserved behind the wrapper rather than deleted.
    assert "_resume_engine_original_n8n_prepare = pages_module._prepare_job" in selector
    assert "pages_module._prepare_job = _request_engine_choice" in selector
    assert "_run_n8n(job_id)" in selector
    assert "n8n workflow · proven current pipeline" in selector

    # Native execution is explicit and still preparation-only.
    assert "generate_resume(" in selector
    assert "Native Resume Studio · built into MUNSHI" in selector
    assert "Neither choice marks an application Submitted" in selector


def test_native_resume_studio_exposes_pdf_and_docx_outputs() -> None:
    root = Path(__file__).resolve().parents[1]
    compatibility_page = (root / "app" / "resume_studio_page.py").read_text(encoding="utf-8")
    page = (root / "app" / "resume_studio_page_v2.py").read_text(encoding="utf-8")
    service = (root / "app" / "native_resume_service.py").read_text(encoding="utf-8")

    # V2 owns the current UI while the original module remains a stable entry point.
    assert "from app.resume_studio_page_v2 import render" in compatibility_page
    assert '"Download DOCX"' in page
    assert '"Build PDF"' in page
    assert '"Download PDF"' in page
    assert "version_docx(" in page
    assert "version_pdf(" in page
    assert "def render_docx_bytes" in service
    assert "def render_pdf_bytes" in service
