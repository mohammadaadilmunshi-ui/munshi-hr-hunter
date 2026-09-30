from __future__ import annotations

from app import application_field_intelligence_v1 as fields
from app import candidate_ontology_v1 as ontology


def _snapshot(*facts):
    return {
        "tenant_id": "tenant-test",
        "user_id": "user-test",
        "source_extraction_id": "extract-test",
        "profile_revision": 7,
        "profile_digest": "a" * 64,
        "facts": list(facts),
    }


def _fact(key: str, value: str, *, trust: str = "USER_CONFIRMED"):
    return {
        "key": key,
        "value": value,
        "protected": False,
        "trust_level": trust,
        "source": "test:confirmed-profile",
    }


def test_segmented_phone_fields_are_distinct_universal_intents() -> None:
    device = fields.field_intent({"label": "Phone Device Type"})
    country_code = fields.field_intent({"label": "Country Phone Code"})
    number = fields.field_intent({"label": "Phone Number"})
    extension = fields.field_intent({"label": "Phone Extension"})

    assert device is not None
    assert country_code is not None
    assert number is not None
    assert extension is not None

    assert device.key == "application.phone_device_type"
    assert country_code.key == "contact.phone_country_code"
    assert number.key == "contact.phone"
    assert extension.key == "contact.phone_extension"

    assert len({device.key, country_code.key, number.key, extension.key}) == 4
    assert extension.profile_fact_key != number.profile_fact_key


def test_apply_semantics_keep_phone_metadata_separate_from_phone_number() -> None:
    assert fields.field_intent(
        {"semanticType": "PHONE_DEVICE_TYPE"}
    ).key == "application.phone_device_type"
    assert fields.field_intent(
        {"semanticType": "PHONE_COUNTRY_CODE"}
    ).key == "contact.phone_country_code"
    assert fields.field_intent(
        {"semanticType": "PHONE"}
    ).key == "contact.phone"
    assert fields.field_intent(
        {"semanticType": "PHONE_EXTENSION"}
    ).key == "contact.phone_extension"


def test_structured_legal_name_parts_take_precedence(monkeypatch) -> None:
    snapshot = _snapshot(
        _fact("contact.full_name", "Ada Mary Lovelace"),
        _fact("contact.first_name", "Ada"),
        _fact("contact.middle_name", "Mary"),
        _fact("contact.last_name", "Lovelace"),
    )
    monkeypatch.setattr(
        fields.truth_binding,
        "current_candidate_profile_snapshot",
        lambda: snapshot,
    )
    monkeypatch.setattr(
        fields.truth_binding,
        "public_binding_state",
        lambda value: {
            "source_extraction_id": value["source_extraction_id"],
            "profile_revision": value["profile_revision"],
            "profile_digest": value["profile_digest"],
        },
    )

    first = fields.resolve_profile_projection(
        profile_fact_key="contact.first_name",
        projection="identity",
    )
    middle = fields.resolve_profile_projection(
        profile_fact_key="contact.middle_name",
        projection="identity",
    )
    last = fields.resolve_profile_projection(
        profile_fact_key="contact.last_name",
        projection="identity",
    )

    assert first["answer"]["canonical_answer"] == "Ada"
    assert middle["answer"]["canonical_answer"] == "Mary"
    assert last["answer"]["canonical_answer"] == "Lovelace"
    assert first["answer"]["source_fact_key"] == "contact.first_name"


def test_legacy_full_name_fallback_is_conservative(monkeypatch) -> None:
    snapshot = _snapshot(_fact("contact.full_name", "Ada Mary Lovelace"))
    monkeypatch.setattr(
        fields.truth_binding,
        "current_candidate_profile_snapshot",
        lambda: snapshot,
    )
    monkeypatch.setattr(
        fields.truth_binding,
        "public_binding_state",
        lambda value: {
            "source_extraction_id": value["source_extraction_id"],
            "profile_revision": value["profile_revision"],
            "profile_digest": value["profile_digest"],
        },
    )

    first = fields.resolve_profile_projection(
        profile_fact_key="contact.first_name",
        projection="identity",
    )
    middle = fields.resolve_profile_projection(
        profile_fact_key="contact.middle_name",
        projection="identity",
    )
    last = fields.resolve_profile_projection(
        profile_fact_key="contact.last_name",
        projection="identity",
    )

    assert first["answer"]["canonical_answer"] == "Ada"
    assert middle["answer"]["canonical_answer"] == "Mary"
    assert last["answer"]["canonical_answer"] == "Lovelace"
    assert first["answer"]["source_fact_key"] == "contact.full_name"


def test_workday_universal_concepts_preserve_sensitivity_boundaries() -> None:
    phone_device_type = ontology.require_concept("application.phone_device_type")
    phone_country_code = ontology.require_concept("contact.phone_country_code")
    phone_extension = ontology.require_concept("contact.phone_extension")
    location_eligibility = ontology.require_concept("work.location_eligibility")
    hispanic_latino = ontology.require_concept("self_id.hispanic_latino")
    employee_id = ontology.require_concept("employment.employee_id")
    legal = ontology.require_concept("legal.attestation")

    assert phone_device_type.sensitivity == "NORMAL"
    assert phone_device_type.autofill_default is False
    assert phone_country_code.sensitivity == "NORMAL"
    assert phone_country_code.autofill_default is False
    assert phone_extension.sensitivity == "NORMAL"
    assert phone_extension.autofill_default is False

    assert location_eligibility.sensitivity == "PROTECTED"
    assert location_eligibility.autofill_default is False

    assert hispanic_latino.sensitivity == "SELF_ID"
    assert hispanic_latino.autofill_default is False

    assert employee_id.sensitivity == "POST_OFFER"
    assert employee_id.autofill_default is False

    assert legal.sensitivity == "NORMAL"
    assert legal.answer_mode == "LEGAL_ATTESTATION_POLICY"
    assert legal.autofill_default is False


def test_dynamic_application_defaults_do_not_downgrade_sensitive_domains() -> None:
    assert ontology.require_concept(
        "application_defaults.hispanic_latino"
    ).sensitivity == "SELF_ID"
    assert ontology.require_concept(
        "application_defaults.location_work_eligible"
    ).sensitivity == "PROTECTED"
    assert ontology.require_concept(
        "application_defaults.employee_id"
    ).sensitivity == "POST_OFFER"


def test_age_threshold_is_protected_and_not_derived_from_birth_date() -> None:
    age = ontology.require_concept("eligibility.age_18_plus")
    assert age.sensitivity == "PROTECTED"
    assert age.answer_mode == "PROTECTED_POLICY"
    assert age.autofill_default is False
    assert age.candidate_fact_paths == ("application_defaults.age_18_plus",)


def test_browser_semantics_apply_strict_sensitivity_floors() -> None:
    from app import universal_question_resolver_v1 as questions

    age = questions.resolve_question({
        "rawText": "Are you at least 18 years of age?",
        "semanticType": "AGE_18_PLUS",
        "inputType": "select",
    })
    assert age["state"] == "RESOLVED"
    assert age["sensitivity"] == "PROTECTED"

    hispanic = questions.resolve_question({
        "rawText": "Do you identify as Hispanic or Latino?",
        "semanticType": "HISPANIC_LATINO",
        "inputType": "select",
    })
    assert hispanic["state"] == "RESOLVED"
    assert hispanic["sensitivity"] == "SELF_ID"

    employee_id = questions.resolve_question({
        "rawText": "Employee ID (if applicable)",
        "semanticType": "EMPLOYEE_ID",
        "inputType": "text",
    })
    assert employee_id["state"] == "RESOLVED"
    assert employee_id["sensitivity"] == "POST_OFFER"
