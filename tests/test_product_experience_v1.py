from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_prepare_from_job_card_opens_application_workspace() -> None:
    pages = _source("app/product_pages.py")
    workspace = _source("app/application_workspace_page.py")
    drawer = _source("app/product_v22.py")

    assert 'st.query_params["view"] = "prepare-application"' in pages
    assert 'st.query_params["job"] = str(resolved_job_id)' in pages
    assert 'st.session_state["application_workspace_notice"]' in pages
    assert 'notice = st.session_state.pop("application_workspace_notice", None)' in workspace
    assert 'on_click=_PAGES._prepare_job' in drawer


def test_unanswered_questions_have_direct_actions() -> None:
    profile = _source("app/profile_workspace_v4.py")
    answers = _source("app/profile_answer_center_ui_v3.py")
    answer_v2 = _source("app/profile_answer_center_ui_v2.py")

    assert "Review {remaining} unanswered" in profile
    assert 'st.session_state["profile_v4_detail_section"] = "Application Answers"' in profile
    assert "#### Unanswered questions · {len(rows)}" in answers
    assert '"Answer now"' in answers
    assert '"Edit secure default"' in answers
    assert '"Teach if reusable"' in answers
    assert 'st.session_state["answer_center_v2_teach_question"]' in answers
    assert 'key="answer_center_v2_teach_question"' in answer_v2


def test_resume_studio_hides_internal_repair_ancestors_from_normal_history() -> None:
    resume = _source("app/resume_studio_page_v4.py")

    assert "_ONE_PAGE_REPAIR_PREFIX" in resume
    assert "def _user_facing_versions" in resume
    assert "repair_parent_ids" in resume
    assert "intermediate one-page repair pass(es) are hidden" in resume
    assert 'st.markdown("### Current resume")' in resume
    assert "Revision history · {len(prior_versions)} prior" in resume


def test_resume_studio_does_not_render_raw_engineering_diagnostics() -> None:
    resume = _source("app/resume_studio_page_v4.py")

    assert 'with st.expander("Advanced diagnostics"' not in resume
    assert 'with st.expander("JD match details"' in resume
    assert '"Open system diagnostics"' in resume
    assert '"writer_model": diagnostics.get("writer_model")' not in resume
    assert '"writer_reasoning": diagnostics.get("writer_reasoning_effort")' not in resume


def test_shared_theme_keeps_baseweb_controls_light() -> None:
    ui = _source("app/product_ui.py")

    assert '[data-testid="stExpander"] summary' in ui
    assert '[data-testid="stNumberInput"] input' in ui
    assert '[role="listbox"]' in ui
    assert '[class*="st-key-munshi_surface_"]' in ui



def test_resume_repair_lineage_keeps_genuine_user_revisions_visible() -> None:
    from app.resume_studio_page_v4 import _user_facing_versions

    repair_prefix = (
        "Create a new compact revision of this exact resume. The current version "
        "failed the strict one-page guard"
    )
    rows = [
        {
            "version_id": "v4",
            "parent_version_id": "v3",
            "version_number": 4,
            "instruction": "Move Power BI earlier.",
        },
        {
            "version_id": "v3",
            "parent_version_id": "v2",
            "version_number": 3,
            "instruction": repair_prefix + " (renderer overflow)",
        },
        {
            "version_id": "v2",
            "parent_version_id": "v1",
            "version_number": 2,
            "instruction": repair_prefix + " (density)",
        },
        {
            "version_id": "v1",
            "parent_version_id": None,
            "version_number": 1,
            "instruction": "Initial tailored generation",
        },
    ]

    visible, hidden = _user_facing_versions(rows)

    assert [row["version_id"] for row in visible] == ["v4", "v3"]
    assert hidden == 2
