from __future__ import annotations

import inspect

from app import master_resume_file_ui_v1 as master_ui
from app import profile_answer_center_ui_v3 as answer_v3
from app import resume_visual_studio_v7 as visual_v7


def test_master_resume_card_no_longer_uses_paper_emoji() -> None:
    source = inspect.getsource(master_ui.render_compact)
    assert "📄" not in source
    assert "MASTER RESUME AUTHORITY" in source


def test_resume_v7_exposes_only_product_templates() -> None:
    assert visual_v7._TEMPLATES == ("Standard", "Jake")
    assert "Computer Modern" in visual_v7._FONTS


def test_standard_and_jake_are_materially_distinct() -> None:
    standard = visual_v7._theme_css(
        {
            "template": "Standard",
            "font": "Arial",
            "font_size": 10.5,
            "alignment": "Left",
        }
    )
    jake = visual_v7._theme_css(
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
    assert "44px 55px 46px" in standard
    assert "42px 51px 43px" in jake


def test_master_source_entry_splits_date_tail() -> None:
    left, date = visual_v7._split_date_tail(
        "Toyota | Human Resource Recruitment & Operations Intern  Jul 2024 - Jan 2025"
    )
    assert "Toyota" in left
    assert "Human Resource Recruitment" in left
    assert date == "Jul 2024 - Jan 2025"


def test_remaining_meanings_are_exposed_even_with_zero_reusable_gaps(monkeypatch) -> None:
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
    monkeypatch.setattr(answer_v3.v2.answer_autofill, "onboarding_delta", lambda _readiness: [])
    monkeypatch.setattr(answer_v3.v2, "_protected_configuration", lambda: {})

    rows = answer_v3._remaining_meanings(readiness)

    assert len(rows) == 2
    assert rows[0]["Meaning"] == "Sponsorship Future"
    assert rows[0]["Handling"].startswith("Protected/self-ID")
    assert rows[1]["Meaning"] == "Relevant Years"
    assert rows[1]["Handling"].startswith("Application-time")
