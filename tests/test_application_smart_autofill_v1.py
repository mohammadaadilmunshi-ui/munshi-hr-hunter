from __future__ import annotations

from typing import Any

from app import application_answer_autofill_v1 as autofill
from app import application_field_intelligence_v1 as fields
from app import application_plan_question_bridge_v1 as bridge


def _snapshot() -> dict[str, Any]:
    return {
        "profile_revision": 7,
        "profile_digest": "a" * 64,
        "source_extraction_id": "extract-1",
        "facts": [
            {
                "key": "contact.full_name",
                "value": "Mohammad Aadil Vasim Munshi",
                "protected": False,
                "trust_level": "DOCUMENT_CONFIRMED",
                "source": "master-resume-extraction:extract-1",
            },
            {
                "key": "contact.email",
                "value": "candidate@example.com",
                "protected": False,
                "trust_level": "DOCUMENT_CONFIRMED",
                "source": "master-resume-extraction:extract-1",
            },
            {
                "key": "contact.location",
                "value": "Example City, NJ 08000, United States",
                "protected": False,
                "trust_level": "USER_CONFIRMED",
                "source": "candidate-profile-overrides-v1:r2",
            },
            {
                "key": "application_defaults.authorized_to_work",
                "protected": True,
                "trust_level": "USER_CONFIRMED",
                "source": "candidate-profile-details-v31:r1",
                "value_reference": "hunter-vault://candidate-profile-details-v31/application_defaults.authorized_to_work",
            },
        ],
    }


def test_name_split_is_generic_and_does_not_use_candidate_specific_rules() -> None:
    result = fields.split_person_name("Mohammad Aadil Vasim Munshi")
    assert result == {
        "full_name": "Mohammad Aadil Vasim Munshi",
        "first_name": "Mohammad",
        "middle_name": "Aadil Vasim",
        "last_name": "Munshi",
        "suffix": "",
    }


def test_html_autocomplete_has_priority_for_first_and_last_name() -> None:
    first = fields.field_intent(
        {"label": "Name", "autocomplete": "section-applicant given-name"}
    )
    last = fields.field_intent(
        {"label": "Name", "autocomplete": "section-applicant family-name"}
    )
    assert first is not None and first.key == "contact.first_name"
    assert last is not None and last.key == "contact.last_name"


def test_label_variants_map_without_provider_specific_hardcoding() -> None:
    given = fields.field_intent({"label": "Given name"})
    surname = fields.field_intent({"aria_label": "Surname"})
    mobile = fields.field_intent({"placeholder": "Mobile number"})
    assert given is not None and given.key == "contact.first_name"
    assert surname is not None and surname.key == "contact.last_name"
    assert mobile is not None and mobile.key == "contact.phone"


def test_unusual_screening_question_is_not_misclassified_as_structural_field() -> None:
    assert fields.field_intent({"label": "Why do you want this role?"}) is None


def test_projection_resolves_first_name_from_confirmed_truth(monkeypatch) -> None:
    monkeypatch.setattr(fields.truth_binding, "current_candidate_profile_snapshot", _snapshot)
    monkeypatch.setattr(
        fields.truth_binding,
        "public_binding_state",
        lambda snapshot: {
            "profile_revision": snapshot["profile_revision"],
            "profile_digest": snapshot["profile_digest"],
        },
    )
    resolved = fields.resolve_profile_projection(
        profile_fact_key="contact.full_name",
        projection="first_name",
    )
    assert resolved["status"] == "ANSWERED"
    assert resolved["answer"]["canonical_answer"] == "Mohammad"
    assert resolved["answer"]["autofill_allowed"] is True
    assert resolved["answer"]["user_confirmed"] is True


def test_field_requirement_is_bound_to_current_profile_digest(monkeypatch) -> None:
    monkeypatch.setattr(
        autofill.truth_binding, "current_candidate_profile_snapshot", _snapshot
    )
    requirement = bridge.normalize_visible_question(
        {
            "label": "First name",
            "autocomplete": "given-name",
            "field_id": "first-name",
            "source": "munshi_apply",
        }
    )
    assert requirement["question_key"] == "contact.first_name"
    assert requirement["profile_fact_key"] == "contact.full_name"
    assert requirement["conditions"]["profile_projection"] == "first_name"
    assert requirement["conditions"]["candidate_profile_revision"] == 7
    assert requirement["conditions"]["candidate_profile_digest"] == "a" * 64


def test_munshi_apply_and_autoapply_use_identical_structural_semantics(monkeypatch) -> None:
    monkeypatch.setattr(
        autofill.truth_binding, "current_candidate_profile_snapshot", _snapshot
    )
    munshi = bridge.normalize_visible_question(
        {
            "label": "Surname",
            "autocomplete": "family-name",
            "source": "munshi_apply",
        }
    )
    auto = bridge.normalize_visible_question(
        {
            "label": "Last name",
            "autocomplete": "family-name",
            "source": "autoapply",
        }
    )
    for key in (
        "question_key",
        "question_family",
        "profile_fact_key",
        "conditions",
        "sensitivity_class",
    ):
        assert munshi[key] == auto[key]
    assert munshi["question_key"] == "contact.last_name"


def test_prepare_from_candidate_truth_never_copies_protected_plaintext(monkeypatch) -> None:
    snapshot = _snapshot()
    monkeypatch.setattr(
        autofill.truth_binding, "current_candidate_profile_snapshot", lambda: snapshot
    )
    monkeypatch.setattr(
        fields.truth_binding, "current_candidate_profile_snapshot", lambda: snapshot
    )
    monkeypatch.setattr(
        fields.truth_binding,
        "public_binding_state",
        lambda current: {
            "profile_revision": current["profile_revision"],
            "profile_digest": current["profile_digest"],
        },
    )

    direct: list[dict[str, Any]] = []
    derived: list[dict[str, Any]] = []
    monkeypatch.setattr(
        autofill.answer_brain_v2,
        "save_profile_answer",
        lambda **kwargs: direct.append(dict(kwargs)) or "direct-id",
    )
    monkeypatch.setattr(
        autofill.answer_brain_v2,
        "save_profile_projection_answer",
        lambda **kwargs: derived.append(dict(kwargs)) or "derived-id",
    )

    result = autofill.prepare_from_candidate_truth()
    assert result["prepared_count"] >= 3
    assert any(item["question_key"] == "contact.full_name" for item in direct)
    assert any(item["question_key"] == "contact.email" for item in direct)
    assert any(item["question_key"] == "contact.first_name" for item in derived)
    assert any(item["question_key"] == "contact.last_name" for item in derived)
    assert all("authorized_to_work" not in str(item) for item in direct + derived)


def test_onboarding_delta_excludes_job_specific_and_protected_questions() -> None:
    readiness = {
        "rows": [
            {
                "question_key": "availability.notice_period",
                "family": "availability",
                "sensitivity_class": "NORMAL",
                "ready": False,
            },
            {
                "question_key": "motivation.why_role",
                "family": "open_ended_job_specific",
                "sensitivity_class": "NORMAL",
                "ready": False,
            },
            {
                "question_key": "eligibility.work_authorized_us",
                "family": "work_authorization",
                "sensitivity_class": "PROTECTED",
                "ready": False,
            },
        ]
    }
    delta = autofill.onboarding_delta(readiness)
    assert [row["question_key"] for row in delta] == ["availability.notice_period"]
