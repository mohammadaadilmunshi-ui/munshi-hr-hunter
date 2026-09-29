from __future__ import annotations

from app import application_field_intelligence_v1 as intelligence
from app import application_plan_question_bridge_v1 as bridge


def test_apply_semantic_first_name_maps_to_candidate_truth_projection() -> None:
    requirement = intelligence.requirement_from_field(
        {
            "semanticType": "FIRST_NAME",
            "rawText": "Given name",
            "controlId": "given-name-control",
        }
    )
    assert requirement is not None
    assert requirement["question_key"] == "contact.first_name"
    assert requirement["profile_fact_key"] == "contact.first_name"
    assert requirement["conditions"] == {"profile_projection": "identity"}
    assert requirement["field_intelligence"]["classification_source"] == (
        "munshi_apply_semantic_type"
    )


def test_apply_semantic_last_name_and_browser_metadata_share_one_requirement() -> None:
    apply_requirement = bridge.normalize_visible_question(
        {
            "semanticType": "LAST_NAME",
            "rawText": "Surname",
            "controlId": "surname-1",
            "required": True,
        }
    )
    browser_requirement = bridge.normalize_visible_question(
        {
            "label": "Family name",
            "autocomplete": "family-name",
            "field_id": "surname-2",
            "required": True,
        }
    )
    for key in (
        "question_key",
        "question_family",
        "profile_fact_key",
        "conditions",
        "sensitivity_class",
    ):
        assert apply_requirement[key] == browser_requirement[key]
    assert apply_requirement["question_key"] == "contact.last_name"


def test_apply_semantic_profile_fields_do_not_hardcode_candidate_values() -> None:
    source = open(intelligence.__file__, encoding="utf-8").read()
    assert "Aadil" not in source
    assert "Munshi" not in source
    assert "candidate value" in intelligence.__doc__.casefold()


def test_protected_apply_semantic_hint_is_not_downgraded_to_normal_projection() -> None:
    # The semantic hint alone is not enough to create a NORMAL structural field.
    assert intelligence.field_intent(
        {
            "semanticType": "WORK_AUTHORIZATION_CURRENT",
            "rawText": "Are you authorized to work in the United States?",
        }
    ) is None

    requirement = bridge.normalize_visible_question(
        {
            "semanticType": "WORK_AUTHORIZATION_CURRENT",
            "rawText": "Are you legally authorized to work in the United States?",
            "required": True,
        }
    )
    assert requirement["question_key"] == "eligibility.work_authorized_us"
    assert requirement["sensitivity_class"] == "PROTECTED"


def test_apply_camelcase_aria_metadata_is_understood() -> None:
    requirement = intelligence.requirement_from_field(
        {
            "semanticType": "UNKNOWN",
            "ariaLabel": "Mobile number",
            "controlId": "mobile-control",
        }
    )
    assert requirement is not None
    assert requirement["question_key"] == "contact.phone"
