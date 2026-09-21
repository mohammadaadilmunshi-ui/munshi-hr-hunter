from __future__ import annotations

import inspect

from app import application_workspace_page
from app import ats_account_readiness_ui_v1 as ats_ui
from app import auto_prepare_readiness_ui_v1 as auto_ready
from app import master_resume_visual_ui_v2 as master_ui
from app import native_resume_presentation_v1 as presentation
from app import product_pages
from app import product_shell
from app import resume_studio_page_v6 as studio_v6


def test_native_preview_section_order_and_visibility_are_real() -> None:
    base = """<!doctype html><html><head></head><body>
<main class="resume"><header>HEADER</header>
<h2 class="section-heading">PROFESSIONAL SUMMARY</h2><p>summary</p>
<h2 class="section-heading">WORK EXPERIENCE</h2><section>experience</section>
<h2 class="section-heading">EDUCATION</h2><section>education</section>
<h2 class="section-heading">SKILLS</h2><p>skills</p>
<h2 class="section-heading">PROJECTS</h2><section>projects</section>
</main></body></html>"""

    rendered = presentation._reorder_native_resume_html(
        base,
        {
            "section_order": "Skills first",
            "visible_sections": ["Summary", "Skills", "Experience", "Projects"],
        },
    )

    assert "EDUCATION" not in rendered
    assert rendered.index("PROFESSIONAL SUMMARY") < rendered.index("SKILLS")
    assert rendered.index("SKILLS") < rendered.index("WORK EXPERIENCE")
    assert rendered.index("WORK EXPERIENCE") < rendered.index("PROJECTS")


def test_master_source_section_order_and_visibility_are_real() -> None:
    sections = [
        ("Professional Summary", ["summary"]),
        ("Work Experience", ["experience"]),
        ("Education", ["education"]),
        ("Skills", ["skills"]),
        ("Projects", ["projects"]),
    ]
    rendered = presentation._ordered_source_sections(
        sections,
        {
            "section_order": "Education first",
            "visible_sections": ["Summary", "Education", "Experience"],
        },
    )

    assert [row[0] for row in rendered] == [
        "Professional Summary",
        "Education",
        "Work Experience",
    ]


def test_resume_studio_v7_controls_are_presentation_only() -> None:
    source = inspect.getsource(studio_v6._presentation_settings)
    assert "SECTION_ORDER_PRESETS" in source
    assert "visible_sections" in source
    assert "Section visibility & order" in source
    assert "immutable Standard V5 artifact" in source

    renderer = inspect.getsource(presentation.render_document_html)
    assert "_reorder_native_resume_html" in renderer
    assert "resume_contract.render_ats_html" in renderer


def test_master_resume_uses_same_section_controls() -> None:
    source = inspect.getsource(master_ui._preview_settings)
    assert "SECTION_ORDER_PRESETS" in source
    assert "available_source_sections" in source
    assert "visible_sections" in source


def test_ats_readiness_surface_is_read_only_and_non_secret() -> None:
    module_source = inspect.getsource(ats_ui)
    forbidden = (
        "read_secret(",
        "_decrypt_for_integrity(",
        "prepare_managed_account(",
        "ensure_application_identity(",
        "store_secret(",
        "delete_secret(",
    )
    for marker in forbidden:
        assert marker not in module_source

    assert "Remote creation" in module_source
    assert "Only after Apply browser confirmation" in module_source
    assert "never claims a remote account exists" in module_source


def test_auto_prepare_readiness_surfaces_dispatcher_required_keys(monkeypatch) -> None:
    monkeypatch.setattr(auto_ready, "get_setting", lambda *_args, **_kwargs: {})
    snapshot = auto_ready.readiness_snapshot()
    assert snapshot["ready"] is False
    assert snapshot["missing"] == [
        "auto_n8n_threshold",
        "daily_auto_n8n_limit",
        "daily_manual_n8n_limit",
    ]

    monkeypatch.setattr(
        auto_ready,
        "get_setting",
        lambda *_args, **_kwargs: {
            "auto_n8n_threshold": 93,
            "daily_auto_n8n_limit": 7,
            "daily_manual_n8n_limit": 25,
        },
    )
    snapshot = auto_ready.readiness_snapshot()
    assert snapshot["ready"] is True
    assert snapshot["missing"] == []


def test_product_shell_rebinds_settings_wrappers_after_autoapply_reset() -> None:
    source = inspect.getsource(product_shell.render)
    reset = "product_pages._integration_credentials_ui_v1_installed = False"
    install_integration = "install_integration_credentials_ui(product_pages)"
    install_ats = "install_ats_account_readiness_ui(product_pages)"
    install_auto = "install_auto_prepare_readiness_ui(product_pages)"

    assert reset in source
    assert source.index(reset) < source.index(install_integration)
    assert source.index(install_integration) < source.index(install_ats)
    assert install_auto in source


def test_application_workspace_live_progress_is_fragment_only() -> None:
    source = inspect.getsource(application_workspace_page._render_complete_loop_live)
    assert 'st.fragment(run_every=run_every)' in source
    assert '_LIVE_PROGRESS_INTERVAL' in source
    assert 'st.rerun(scope="app")' in source
    assert 'start_prepare_for_job' not in source
    assert 'approve_and_submit_for_job' not in source
    assert 'subprocess' not in source
    assert 'docker' not in source


def test_auto_prepare_live_activity_is_read_only_projection() -> None:
    source = inspect.getsource(product_pages._render_auto_prepare_live_activity)
    assert 'st.fragment(run_every="5s")' in source
    assert 'activity_summary()' in source
    assert 'pipeline_rows(limit=20)' in source
    assert 'start_prepare_for_job' not in source
    assert 'approve_and_submit_for_job' not in source
    assert 'set_lane_enabled' not in source
    assert 'save_volume_policy' not in source
    assert 'subprocess' not in source
    assert 'docker' not in source


def test_failed_prepare_does_not_navigate_to_not_started_workspace() -> None:
    source = inspect.getsource(product_pages._prepare_job)
    error_index = source.index('except Exception as error:')
    return_index = source.index('return', error_index)
    navigate_index = source.index(
        'st.session_state["product_view"] = "prepare-application"'
    )
    assert error_index < return_index < navigate_index
