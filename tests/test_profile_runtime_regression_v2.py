from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from app import native_resume_service as resume_v1


ROOT = Path(__file__).resolve().parents[1]


def test_profile_details_with_master_resume_renders_without_duplicate_keys(hunter_db) -> None:
    """Reproduce the staging state that exposed StreamlitDuplicateElementKey.

    The generic dashboard smoke test had no active Master Resume, so the file-card
    widgets never existed and CI missed the duplicate mount. This fixture creates
    the exact prerequisite before rendering Profile Details.
    """
    resume_v1.save_confirmed_source(
        content_text=(
            "Candidate Example\n"
            "candidate@example.com\n"
            "EXPERIENCE\n"
            "Example Company | HR Analyst | 2025\n"
            "Supported recruiting and analytics workflows.\n"
            "EDUCATION\n"
            "Example University | MS Human Resource Analytics"
        ),
        label="Master Resume.pdf",
        source_kind="text_upload",
    )

    app = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
    app.session_state["product_view"] = "profile"
    app.session_state["product_profile_tab"] = "Profile details"
    app.run()

    assert not app.exception


def test_master_resume_interactive_surface_is_mounted_once() -> None:
    workspace = (ROOT / "app" / "profile_workspace_v4.py").read_text(encoding="utf-8")
    assert workspace.count("master_resume_ui.render_compact(authority)") == 1
    assert "master_resume_ui.render_detail(authority)" not in workspace
    assert "def _master_resume_details(" in workspace


def test_answer_center_does_not_mount_second_application_defaults_form() -> None:
    answer_center = (ROOT / "app" / "profile_answer_center_ui_v2.py").read_text(encoding="utf-8")
    assert "profile_v3._edit_application_defaults" not in answer_center
    assert "Open encrypted protected defaults editor" in answer_center
    assert "profile_v3_edit_section" in answer_center


def test_runtime_safe_answer_center_keeps_existing_capabilities() -> None:
    answer_center = (ROOT / "app" / "profile_answer_center_ui_v2.py").read_text(encoding="utf-8")
    for marker in (
        "Prepare / refresh answers from Candidate Truth",
        "Complete reusable profile",
        "Save completed reusable answers",
        "Protected & self-ID",
        "Teach MUNSHI for future use",
        "Save for future applications",
        "What does the remaining coverage mean?",
    ):
        assert marker in answer_center
