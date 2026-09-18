from __future__ import annotations

import inspect
from pathlib import Path

from app import application_notification_ui_v1 as notifications_ui
from app import product_shell
from app import targeting_settings_ui_v1 as targeting_ui


ROOT = Path(__file__).resolve().parents[1]


class _DummyPages:
    def __init__(self) -> None:
        self.dashboard = lambda: None
        self.tracker = lambda: None
        self.settings = lambda: None


def test_advanced_deep_link_resolves_to_advanced_system() -> None:
    assert product_shell._resolved_subroute_state(
        "settings",
        section="advanced",
    ) == ("product_settings_section", "Advanced / System")


def test_application_email_notifications_are_first_class_advanced_ui() -> None:
    source = inspect.getsource(notifications_ui)
    assert 'id="application-email-notifications"' in source
    assert "### Application email notifications" in source
    assert 'product_settings_section") == "Advanced / System"' in source
    assert "Outbound application-email notifications are ready." in source
    assert "Save notification preferences" in source


def test_notification_installer_is_rerun_safe_and_does_not_wrap_settings() -> None:
    pages = _DummyPages()
    original_settings = pages.settings
    notifications_ui.install_application_notification_ui(pages)
    first = (pages.dashboard, pages.tracker)

    notifications_ui.install_application_notification_ui(pages)
    second = (pages.dashboard, pages.tracker)

    assert first == second
    assert pages.settings is original_settings


def test_targeting_card_uses_canonical_targeting_authority() -> None:
    source = inspect.getsource(targeting_ui)
    assert 'id="job-search-targeting-and-eligibility"' in source
    assert "### Job search targeting and eligibility" in source
    assert 'get_setting("targeting"' in source
    assert 'save_setting(\n            "targeting"' in source
    assert "Save job search targeting" in source
    assert 'product_settings_section") == "Advanced / System"' in source


def test_advanced_settings_renders_targeting_and_notifications_directly() -> None:
    from app import autoapply_settings_ui

    source = inspect.getsource(autoapply_settings_ui.settings_v23)
    targeting = "render_targeting_settings()"
    notifications = "render_application_notification_settings()"
    assert targeting in source
    assert notifications in source
    assert source.index(targeting) < source.index(notifications)


def test_initial_dashboard_contains_current_staging_feature_modules() -> None:
    required = (
        "app/application_workspace_page.py",
        "app/application_notification_ui_v1.py",
        "app/ats_account_readiness_ui_v1.py",
        "app/auto_prepare_readiness_ui_v1.py",
        "app/autoapply_settings_ui.py",
        "app/integration_credentials_ui_v1.py",
        "app/job_card_interaction_hotfix_v2.py",
        "app/job_card_runtime_hotfix_v1.py",
        "app/master_resume_visual_ui_v2.py",
        "app/n8n_live_console_v1.py",
        "app/native_resume_presentation_v1.py",
        "app/profile_answer_center_ui_v3.py",
        "app/profile_workspace_v4.py",
        "app/resume_studio_page_v6.py",
        "app/secure_api_vault_ui.py",
        "app/settings_profile_authority_ui_v1.py",
        "app/targeting_settings_ui_v1.py",
        "app/unified_job_workspaces_runtime_patch_v1.py",
    )
    missing = [relative for relative in required if not (ROOT / relative).is_file()]
    assert missing == []


def test_product_shell_contains_current_staging_feature_installers() -> None:
    source = (ROOT / "app" / "product_shell.py").read_text(encoding="utf-8")
    required = (
        "install_job_card_runtime_hotfix()",
        "install_autoapply_settings_ui(product_pages)",
        "install_secure_api_vault_ui(product_pages)",
        "install_settings_profile_authority_ui(product_pages)",
        "install_unified_job_workspaces(product_pages, application_workspace_page)",
        "install_application_notification_ui(product_pages)",
        "install_integration_credentials_ui(product_pages)",
        "install_ats_account_readiness_ui(product_pages)",
        "install_auto_prepare_readiness_ui(product_pages)",
        "render_n8n_live_console()",
    )
    for marker in required:
        assert marker in source, marker

    advanced_source = (
        ROOT / "app" / "autoapply_settings_ui.py"
    ).read_text(encoding="utf-8")
    assert "render_targeting_settings()" in advanced_source
    assert "render_application_notification_settings()" in advanced_source
