from __future__ import annotations

from app import application_answer_autofill_v1 as answer_autofill
from app import application_answer_router_v1 as answer_router
from app import application_question_catalog_v1 as catalog


def test_generic_skill_years_alias_stays_on_reusable_concept() -> None:
    question = "How many years of experience do you have with this skill?"

    match = catalog.match_question(question)
    requirement = catalog.requirement_from_question(question)
    routed = answer_router.requirement_from_question(question)

    assert match["matched"] is True
    assert match["question_key"] == "experience.skill_years"
    assert match["canonical_concept"] == "experience.skill_years"
    assert match["conditions"] == {}

    assert requirement["question_key"] == "experience.skill_years"
    assert requirement["conditions"] == {}
    assert routed["question_key"] == "experience.skill_years"
    assert routed["conditions"] == {}


def test_generic_technology_alias_stays_on_reusable_concept() -> None:
    question = "How many years have you used this technology professionally?"

    requirement = catalog.requirement_from_question(question)

    assert requirement["question_key"] == "experience.skill_years"
    assert requirement["conditions"] == {}


def test_concrete_skill_years_question_remains_dynamic() -> None:
    question = "How many years of experience do you have with Power BI?"

    match = catalog.match_question(question)
    requirement = answer_router.requirement_from_question(question)

    assert match["matched"] is True
    assert match["question_key"] == "experience.skill_years.power.bi"
    assert match["canonical_concept"] == "experience.skill_years"
    assert match["conditions"] == {"skill": "Power BI"}

    assert requirement["question_key"] == "experience.skill_years.power.bi"
    assert requirement["conditions"] == {"skill": "Power BI"}


def test_contextual_experience_questions_do_not_block_onboarding_progress() -> None:
    readiness = {
        "rows": [
            {
                "question_key": "experience.skill_years",
                "family": "experience_skill",
                "sensitivity_class": "NORMAL",
                "ready": False,
                "example_question": "How many years of experience do you have with this skill?",
            },
            {
                "question_key": "experience.relevant_years",
                "family": "experience_skill",
                "sensitivity_class": "NORMAL",
                "ready": False,
                "example_question": "How many years of relevant experience do you have?",
            },
            {
                "question_key": "experience.total_years",
                "family": "experience_skill",
                "sensitivity_class": "NORMAL",
                "ready": False,
                "example_question": "How many total years of professional experience do you have?",
            },
        ]
    }

    delta = answer_autofill.onboarding_delta(readiness)
    keys = [row["question_key"] for row in delta]

    assert "experience.skill_years" not in keys
    assert "experience.relevant_years" not in keys
    assert "experience.total_years" in keys
