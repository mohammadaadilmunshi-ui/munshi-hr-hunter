from __future__ import annotations

from pathlib import Path

from app import native_resume_presentation_v1 as presentation
from app import profile_answer_center_ui_v3 as answer_v3


ROOT = Path(__file__).resolve().parents[1]


def test_profile_workspace_uses_first_class_navigation() -> None:
    source = (ROOT / "app" / "profile_workspace_v4.py").read_text(encoding="utf-8")
    assert "def _detail_navigation(" in source
    assert "Profile workspace" in source
    assert 'st.radio(\n        "Profile details section"' not in source
    assert "master_resume_visual_ui_v2" in source


def test_master_resume_card_has_no_paper_emoji() -> None:
    source = (ROOT / "app" / "master_resume_visual_ui_v2.py").read_text(
        encoding="utf-8"
    )
    assert "📄" not in source
    assert "MASTER RESUME AUTHORITY" in source
    assert "Preview resume" in source
    assert "Replace file" in source


def test_standard_and_jake_are_materially_distinct() -> None:
    standard = presentation._override_css(
        {
            "template": "Standard",
            "font": "Arial",
            "font_size": 10.5,
            "alignment": "Left",
        }
    )
    jake = presentation._override_css(
        {
            "template": "Jake",
            "font": "Computer Modern",
            "font_size": 10.5,
            "alignment": "Left",
        }
    )
    assert standard != jake
    assert "Arial, Helvetica, sans-serif" in standard
    assert "Computer Modern Serif" in jake
    assert ".46in .56in .48in" in standard
    assert ".43in .52in .45in" in jake


def test_master_source_uses_structured_professional_canvas() -> None:
    rendered = presentation.render_master_source_html(
        "Aadil Munshi\nNew Jersey | candidate@example.com\n\nWORK EXPERIENCE\nPeople Analytics Intern  Jul 2024 - Jan 2025\n• Built recruiting analytics dashboards.\n\nEDUCATION\nExample University | MS Human Resource Analytics",
        {"template": "Standard", "font": "Arial", "font_size": 10.5},
    )
    assert 'class="paper"' in rendered
    assert 'class="source-name"' in rendered
    assert "Aadil Munshi" in rendered
    assert 'class="source-entry-head"' in rendered
    assert "Jul 2024 - Jan 2025" in rendered
    assert 'class="source-bullets"' in rendered


def test_remaining_meanings_are_visible_when_reusable_delta_is_empty(monkeypatch) -> None:
    readiness = {
        "rows": [
            {
                "question_key": "eligibility.sponsorship_future",
                "family": "work_authorization",
                "sensitivity_class": "PROTECTED",
                "ready": False,
                "example_question": "Will you require sponsorship in the future?",
            },
            {
                "question_key": "experience.relevant_years",
                "family": "experience_skill",
                "sensitivity_class": "NORMAL",
                "ready": False,
                "example_question": "How many years of relevant experience do you have?",
            },
        ]
    }
    monkeypatch.setattr(
        answer_v3.v2.answer_autofill, "onboarding_delta", lambda _readiness: []
    )
    monkeypatch.setattr(answer_v3.v2, "_protected_configuration", lambda: {})

    rows = answer_v3._remaining_meanings(readiness)

    assert len(rows) == 2
    assert rows[0]["Meaning"] == "Sponsorship Future"
    assert rows[0]["Handling"].startswith("Protected/self-ID")
    assert rows[1]["Meaning"] == "Relevant Years"
    assert rows[1]["Handling"].startswith("Application-time")


def test_ats_identity_storage_lifecycle_baseline_is_preserved() -> None:
    for relative in (
        "app/ats_managed_identity.py",
        "app/ats_verification_orchestrator.py",
        "app/storage_lifecycle.py",
    ):
        assert (ROOT / relative).is_file(), relative
