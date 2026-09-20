from __future__ import annotations

from datetime import date
import importlib

import pytest

from app import application_semantic_binding_v1 as bindings
from app import application_plan_v2 as plans
from app import application_question_catalog_v2 as catalog
from app import candidate_derivation_engine_v1 as derivation
from app import candidate_knowledge_graph_v1 as graph
from app import candidate_knowledge_ingestion_v1 as ingestion
from app import candidate_knowledge_center_v1 as knowledge_center
from app import candidate_ontology_v1 as ontology
from app import universal_answer_resolver_v1 as answers
from app import universal_question_resolver_v1 as questions
from app import universal_semantic_embedding_v1 as semantic_embedding
from app import universal_open_ended_generator_v1 as open_ended


def test_ontology_contains_every_current_catalog_concept_and_never_autofills_protected() -> None:
    ids = {item.concept_id for item in ontology.all_concepts()}
    assert {item.key for item in catalog.CONCEPTS}.issubset(ids)
    for item in ontology.all_concepts():
        if item.sensitivity != "NORMAL":
            assert item.autofill_default is False


def test_ontology_preserves_repeated_entity_identity() -> None:
    first = ontology.require_concept("experience.0.employer")
    second = ontology.require_concept("experience.1.employer")
    assert first.repeated_entity is True
    assert second.repeated_entity is True
    assert first.concept_id != second.concept_id


def test_state_new_jersey_resolves_to_region_without_candidate_value_reasoning() -> None:
    result = questions.resolve_question(
        {
            "rawText": "State New Jersey",
            "sectionHeading": "Contact Address",
            "inputType": "select",
            "role": "combobox",
            "options": ["New Jersey", "New York", "Pennsylvania", "Delaware"],
            "semanticType": "UNKNOWN",
            "controlId": "state-control",
        }
    )
    assert result["state"] == "RESOLVED"
    assert result["concept_id"] == "contact.region"
    assert result["classification_lane"] in {
        "DETERMINISTIC_STRUCTURAL", "ONTOLOGY_RETRIEVAL", "ONTOLOGY_CONTEXT"
    }
    assert result["candidate_values_exposed"] is False


def test_context_lane_recovers_region_when_legacy_field_classifier_cannot() -> None:
    result = questions.resolve_question(
        {
            "rawText": "New Jersey",
            "nearbyText": "State",
            "sectionHeading": "Contact Address",
            "inputType": "select",
            "role": "combobox",
            "options": [
                "Alabama", "Alaska", "Arizona", "California", "Delaware",
                "Florida", "New Jersey", "New York", "Pennsylvania", "Texas",
            ],
            "semanticType": "UNKNOWN",
            "controlId": "ctl-17",
        }
    )
    assert result["state"] == "RESOLVED"
    assert result["concept_id"] == "contact.region"
    assert result["classification_lane"] == "ONTOLOGY_CONTEXT"


def test_repeated_employment_and_education_fields_get_entity_scoped_concepts() -> None:
    employer = questions.resolve_question({
        "rawText": "Employer", "semanticType": "EMPLOYER_NAME",
        "repeatGroupId": "employment", "repeatIndex": 1,
    })
    assert employer["concept_id"] == "experience.1.employer"
    school = questions.resolve_question({
        "rawText": "School", "semanticType": "SCHOOL_NAME",
        "repeatGroupId": "education", "repeatIndex": 2,
    })
    assert school["concept_id"] == "education.2.institution"


def test_protected_semantic_cannot_be_downgraded_by_normal_metadata() -> None:
    result = questions.resolve_question(
        {
            "rawText": "Are you legally authorized to work in the United States?",
            "label": "Work authorization",
            "semanticType": "WORK_AUTHORIZATION_CURRENT",
            "inputType": "radio",
        }
    )
    assert result["state"] == "RESOLVED"
    assert result["sensitivity"] == "PROTECTED"


def test_unknown_normal_question_stays_unknown_instead_of_guessing() -> None:
    result = questions.resolve_question(
        {"rawText": "What is your favorite ice cream flavor?", "inputType": "text"}
    )
    assert result["state"] in {"UNKNOWN", "AMBIGUOUS_CONCEPT"}


def test_derivation_merges_overlapping_experience_and_never_rounds_up() -> None:
    profile = {
        "experience": [
            {"start_date": "Jan 2024", "end_date": "Dec 2024"},
            {"start_date": "Jul 2024", "end_date": "Jun 2025"},
        ]
    }
    result = derivation.total_experience(profile, as_of=date(2025, 6, 30))
    assert result["status"] == "DERIVED"
    # Jan 2024 through Jun 2025 is about 1.49 years, not two overlapping years.
    assert 1.48 <= result["value"] <= 1.50


def test_capability_requires_explicit_evidence() -> None:
    absent = derivation.extract_profile_claims(
        {"skills": [{"category": "Analytics", "skills": ["Excel"]}], "experience": []}
    )
    assert derivation.capability_boolean("workday", absent)["status"] == "MISSING_FACT"
    present = derivation.extract_profile_claims(
        {"skills": [{"category": "HRIS", "skills": ["Workday"]}], "experience": []}
    )
    result = derivation.capability_boolean("workday", present)
    assert result["status"] == "DERIVED"
    assert result["value"] is True
    assert result["evidence_ids"]


def test_knowledge_graph_rejects_protected_plaintext(hunter_db) -> None:
    with pytest.raises(ValueError, match="reference-only"):
        graph.store_fact(
            concept_id="eligibility.visa_status",
            value="plaintext visa value",
            source_type="PROTECTED_POLICY",
            source_ref="test:protected",
            trust_level="USER_CONFIRMED",
            confidence=1.0,
            sensitivity="PROTECTED",
            autofill_policy="POLICY",
        )
    stored = graph.store_fact(
        concept_id="eligibility.visa_status",
        value_ref="hunter-vault://candidate-profile-details-v31/application_defaults.visa_or_permit",
        source_type="PROTECTED_POLICY",
        source_ref="test:protected",
        trust_level="USER_CONFIRMED",
        confidence=1.0,
        sensitivity="PROTECTED",
        autofill_policy="POLICY",
    )
    assert "value" not in stored
    assert stored["value_ref"].startswith("hunter-vault://")


def test_equal_authority_conflicting_facts_fail_closed(hunter_db) -> None:
    graph.store_fact(
        concept_id="contact.city", value="City One", source_type="USER_DASHBOARD",
        source_ref="dashboard:a", trust_level="USER_CONFIRMED", confidence=1.0,
        sensitivity="NORMAL", autofill_policy="ALLOW",
    )
    graph.store_fact(
        concept_id="contact.city", value="City Two", source_type="USER_DASHBOARD",
        source_ref="dashboard:b", trust_level="USER_CONFIRMED", confidence=1.0,
        sensitivity="NORMAL", autofill_policy="ALLOW",
    )
    resolved = graph.resolve_fact("contact.city")
    assert resolved["status"] == "CONFLICT"
    assert graph.open_conflicts("contact.city")


def test_truth_preview_keeps_protected_value_reference_only() -> None:
    snapshot = {
        "profile_revision": 7,
        "profile_digest": "a" * 64,
        "facts": [
            {
                "fact_id": "f1", "key": "contact.region", "protected": False,
                "value": "New Jersey", "trust_level": "USER_CONFIRMED",
                "source": "candidate-profile-overrides-v1:r1",
            },
            {
                "fact_id": "f2", "key": "application_defaults.visa_or_permit", "protected": True,
                "value_reference": "hunter-vault://candidate-profile-details-v31/application_defaults.visa_or_permit",
                "trust_level": "USER_CONFIRMED", "source": "candidate-profile-details-v31:r1",
            },
        ],
    }
    rows = ingestion.preview_candidate_truth(snapshot)
    protected = [row for row in rows if row["sensitivity"] != "NORMAL"]
    assert protected
    assert all("value" not in row for row in protected)
    assert all(str(row.get("value_ref") or "").startswith("hunter-vault://") for row in protected)


def test_answer_resolver_searches_complete_view_after_semantic_resolution() -> None:
    question = questions.resolve_question(
        {"rawText": "State New Jersey", "sectionHeading": "Address", "role": "combobox"}
    )
    view = {
        "profile_revision": 27,
        "facts": [
            {
                "concept_id": "contact.region", "value": "New Jersey",
                "source_type": "USER_DASHBOARD", "source_ref": "dashboard:contact.region",
                "trust_level": "USER_CONFIRMED", "confidence": 1.0,
                "autofill_policy": "ALLOW", "source_fact_id": "source-fact-1",
            }
        ],
        "answer_memory": [], "claims": [], "profile": {},
    }
    resolved = answers.resolve_concept(question, knowledge_view=view)
    assert resolved["answer_status"] == "ANSWERED"
    assert resolved["value"] == "New Jersey"
    assert resolved["autofill_allowed"] is True
    assert resolved["value_ref"].startswith("hunter-value://")
    public = answers.public_trace(resolved)
    assert "value" not in public
    assert public["value_exposed_in_trace"] is False


def test_answer_resolver_only_reports_missing_after_knowledge_and_derivation_exhausted() -> None:
    question = questions.resolve_question(
        {"rawText": "How many total years of professional experience do you have?"}
    )
    resolved = answers.resolve_concept(
        question,
        knowledge_view={"profile_revision": 1, "facts": [], "answer_memory": [], "claims": [], "profile": {"experience": []}},
    )
    assert resolved["answer_status"] == "MISSING_FACT"
    assert resolved["autofill_allowed"] is False


def test_semantic_binding_survives_control_id_change(hunter_db) -> None:
    first = {
        "rawText": "State", "label": "State", "sectionHeading": "Address",
        "role": "combobox", "options": ["New Jersey", "New York"], "controlId": "ctl-old",
    }
    row = bindings.bind(
        application_id="app-1", session_id="session-1", observation=first,
        concept_id="contact.region", classification_source="ONTOLOGY", confidence=0.99,
        plan_id="plan-1",
    )
    assert row["concept_id"] == "contact.region"
    rerendered = {**first, "controlId": "ctl-new", "semanticType": "UNKNOWN"}
    found = bindings.lookup(application_id="app-1", session_id="session-1", observation=rerendered)
    assert found is not None
    assert found["concept_id"] == "contact.region"
    rebound = bindings.bind(
        application_id="app-1", session_id="session-1", observation=rerendered,
        concept_id="contact.region", classification_source="ONTOLOGY", confidence=0.99,
        plan_id="plan-2",
    )
    assert rebound["binding_revision"] > row["binding_revision"]
    assert rebound["control_id"] == "ctl-new"


def test_repeat_group_without_index_is_not_durably_bindable() -> None:
    with pytest.raises(ValueError, match="repeat index"):
        bindings.fingerprint_parts(
            {"label": "Employer", "repeatGroupId": "employment", "controlId": "employer"}
        )


def test_migration_is_additive_and_idempotent(hunter_db) -> None:
    from app import database

    migration = importlib.import_module("migrations.057_universal_brain_v1")
    connection = database.get_connection()
    try:
        migration.apply(connection)
        migration.apply(connection)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'candidate_knowledge_%'"
            ).fetchall()
        }
        assert "candidate_knowledge_facts" in tables
        assert "candidate_knowledge_evidence" in tables
        assert "candidate_knowledge_conflicts" in tables
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='application_semantic_bindings'"
        ).fetchone()
    finally:
        connection.close()


def test_application_plan_preserves_validated_universal_semantic_metadata() -> None:
    requirement = plans._normalize_requirement(
        {
            "question_key": "unknown.abc",
            "question_family": "unknown",
            "normalized_question": "State New Jersey",
            "required": True,
            "conditions": {},
            "sensitivity_class": "NORMAL",
            "universal_concept_id": "contact.region",
            "universal_semantic_confidence": 0.99,
            "universal_classification_lane": "ONTOLOGY_CONTEXT",
            "universal_reason_code": "ADDRESS_REGION_CONTEXT",
            "universal_sensitivity": "NORMAL",
            "universal_binding_fingerprint": "a" * 64,
        }
    )
    assert requirement["universal_concept_id"] == "contact.region"
    assert requirement["universal_semantic_confidence"] == 0.99
    assert requirement["universal_binding_fingerprint"] == "a" * 64


def test_application_plan_universal_normal_canary_uses_opaque_ref_and_trusted_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requirement = plans._normalize_requirement(
        {
            "question_key": "unknown.abc",
            "question_family": "unknown",
            "normalized_question": "State New Jersey",
            "required": True,
            "conditions": {},
            "sensitivity_class": "NORMAL",
            "universal_concept_id": "contact.region",
            "universal_semantic_confidence": 0.99,
            "universal_classification_lane": "ONTOLOGY_CONTEXT",
            "universal_reason_code": "ADDRESS_REGION_CONTEXT",
            "universal_sensitivity": "NORMAL",
            "universal_binding_fingerprint": "b" * 64,
        }
    )
    monkeypatch.setattr(plans.universal_graph, "universal_normal_autofill_enabled", lambda: True)
    monkeypatch.setattr(
        plans.universal_answers,
        "resolve_concept",
        lambda _q: {
            "answer_status": "ANSWERED",
            "answer_mode": "DIRECT_FACT",
            "value": "New Jersey",
            "value_ref": "hunter-value://universal-brain/ref-1",
            "knowledge_revision": 27,
            "confidence": 0.99,
            "autofill_allowed": True,
            "answer_source": "USER_DASHBOARD",
            "reason": "USER_CONFIRMED",
            "evidence_ids": ["evidence-1"],
        },
    )
    answer, reason = plans._resolve_requirement(
        requirement,
        permissions={"normal_answer_autofill": True},
        secure_refs={},
    )
    assert reason is None
    assert answer["execution_value"] == "New Jersey"
    assert answer["universal_value_ref"] == "hunter-value://universal-brain/ref-1"
    assert answer["universal_concept_id"] == "contact.region"
    assert answer["autofill_allowed"] is True


def test_application_plan_universal_conflict_fails_closed_without_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requirement = plans._normalize_requirement(
        {
            "question_key": "unknown.xyz",
            "question_family": "unknown",
            "normalized_question": "Current city",
            "required": True,
            "conditions": {},
            "sensitivity_class": "NORMAL",
            "universal_concept_id": "contact.city",
            "universal_semantic_confidence": 0.98,
            "universal_classification_lane": "ONTOLOGY_RETRIEVAL",
            "universal_reason_code": "ALIAS_OR_SEMANTIC_RETRIEVAL",
            "universal_sensitivity": "NORMAL",
        }
    )
    monkeypatch.setattr(plans.universal_graph, "universal_normal_autofill_enabled", lambda: True)
    monkeypatch.setattr(
        plans.universal_answers,
        "resolve_concept",
        lambda _q: {
            "answer_status": "CONFLICT",
            "answer_mode": "DIRECT_FACT",
            "value_ref": None,
            "knowledge_revision": 28,
            "confidence": 0.98,
            "autofill_allowed": False,
            "reason": "top_authority_candidate_facts_conflict",
            "evidence_ids": [],
        },
    )
    monkeypatch.setattr(
        plans.answer_brain_v2,
        "resolve_answer",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("legacy fallback must not run")),
    )
    answer, reason = plans._resolve_requirement(
        requirement,
        permissions={"normal_answer_autofill": True},
        secure_refs={},
    )
    assert answer["execution_value"] is None
    assert answer["universal_answer_status"] == "CONFLICT"
    assert answer["autofill_allowed"] is False
    assert reason == "top_authority_candidate_facts_conflict"


def test_knowledge_center_masks_protected_values_and_exposes_provenance(hunter_db, monkeypatch):
    monkeypatch.setattr(
        knowledge_center.ingestion,
        "read_only_knowledge_view",
        lambda: {
            "profile_revision": 27,
            "profile_digest": "d" * 64,
            "persisted_graph_fact_count": 0,
            "facts": [
                {
                    "concept_id": "contact.region", "value": "New Jersey",
                    "source_type": "CANDIDATE_TRUTH", "source_ref": "profile:region",
                    "trust_level": "USER_CONFIRMED", "confidence": 1.0,
                    "sensitivity": "NORMAL", "autofill_policy": "ALLOW",
                },
                {
                    "concept_id": "eligibility.visa_status",
                    "value_ref": "hunter-vault://visa/ref",
                    "source_type": "PROTECTED_POLICY", "source_ref": "protected:visa",
                    "trust_level": "USER_CONFIRMED", "confidence": 1.0,
                    "sensitivity": "PROTECTED", "autofill_policy": "POLICY",
                },
            ],
            "claims": [], "answer_memory": [], "profile": {},
        },
    )
    view = knowledge_center.snapshot()
    identity = next(row for row in view["sections"]["Identity"] if row["concept_id"] == "contact.region")
    protected = next(row for row in view["sections"]["Work authorization"] if row["concept_id"] == "eligibility.visa_status")
    assert identity["value"] == "New Jersey"
    assert identity["source_type"] == "CANDIDATE_TRUTH"
    assert protected["value"] is None
    assert protected["value_reference_available"] is True


def test_dashboard_canonical_correction_wins_future_universal_resolution(hunter_db):
    knowledge_center.save_normal_fact(concept_id="contact.region", value="Pennsylvania")
    view = {
        "profile_revision": 27,
        "facts": [
            *graph.current_facts(),
            {
                "concept_id": "contact.region", "value": "New Jersey",
                "source_type": "CANDIDATE_TRUTH", "source_ref": "profile:region",
                "trust_level": "USER_CONFIRMED", "confidence": 1.0,
                "autofill_policy": "ALLOW",
            },
        ],
        "answer_memory": [], "claims": [], "profile": {},
    }
    question = questions.resolve_question({"rawText": "State / Province", "sectionHeading": "Address"})
    resolved = answers.resolve_concept(question, knowledge_view=view)
    assert resolved["answer_status"] == "ANSWERED"
    assert resolved["value"] == "Pennsylvania"
    assert resolved["answer_source"] == "USER_DASHBOARD"


def test_bounded_model_semantics_can_resolve_weird_wording_without_candidate_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUNSHI_UNIVERSAL_MODEL_SEMANTICS_ENABLED", "true")
    seen = {}

    def interpret(payload):
        seen.update(payload)
        assert payload["candidate_values"] is None
        assert "value" not in payload["observation"]
        assert payload["ontology_candidates"]
        return {
            "concept_id": "contact.region",
            "confidence": 0.97,
            "reason_code": "HOME_BASE_REGION_CONTEXT",
        }

    result = questions.resolve_question(
        {
            "rawText": "Your home base jurisdiction",
            "label": "Home base",
            "sectionHeading": "Personal details",
            "inputType": "select",
            "role": "combobox",
            "options": ["New Jersey", "New York", "Pennsylvania"],
            "semanticType": "UNKNOWN",
        },
        model_interpreter=interpret,
    )
    assert result["state"] == "RESOLVED"
    assert result["concept_id"] == "contact.region"
    assert result["classification_lane"] == "BOUNDED_MODEL_INTERPRETATION"
    assert result["candidate_values_exposed"] is False
    assert seen["candidate_values"] is None


def test_arbitrary_explicit_resume_skill_becomes_evidence_concept_without_hardcoding() -> None:
    claims = derivation.extract_profile_claims(
        {
            "skills": [{"category": "Data", "skills": ["Snowflake"]}],
            "experience": [],
            "projects": [],
        },
        extraction_id="extract-generic-skill",
    )
    matches = [claim for claim in claims if claim["concept"] == "experience.skill.snowflake"]
    assert len(matches) == 1
    assert matches[0]["evidence_text"] == "Snowflake"
    assert matches[0]["confidence"] == 0.99


def test_dynamic_capability_question_resolves_arbitrary_explicit_skill_without_skill_regex() -> None:
    result = questions.resolve_question(
        {
            "rawText": "Have you ever used Snowflake?",
            "label": "Have you ever used Snowflake?",
            "semanticType": "UNKNOWN",
            "inputType": "radio",
        }
    )
    assert result["state"] == "RESOLVED"
    assert result["concept_id"] == "experience.skill.snowflake"
    assert result["classification_lane"] == "ONTOLOGY_DYNAMIC_CAPABILITY"


def test_dynamic_capability_question_cannot_reclassify_protected_wording_as_normal_skill() -> None:
    result = questions.resolve_question(
        {
            "rawText": "Do you have experience with work authorization?",
            "label": "Do you have experience with work authorization?",
            "semanticType": "UNKNOWN",
        }
    )
    assert result.get("concept_id") != "experience.skill.work_authorization"


def test_dynamic_skill_answer_requires_actual_extracted_evidence() -> None:
    question = questions.resolve_question(
        {"rawText": "Have you ever used Snowflake?", "semanticType": "UNKNOWN"}
    )
    missing = answers.resolve_concept(
        question,
        knowledge_view={
            "profile_revision": 1,
            "facts": [],
            "answer_memory": [],
            "claims": [],
            "profile": {},
        },
    )
    assert missing["answer_status"] == "MISSING_FACT"


def test_semantic_binding_application_fallback_is_opt_in_and_exact(hunter_db) -> None:
    observation = {
        "rawText": "State",
        "label": "State",
        "sectionHeading": "Address",
        "role": "combobox",
        "options": ["New Jersey", "New York"],
        "controlId": "state-session-one",
    }
    first = bindings.bind(
        application_id="app-cross-session",
        session_id="session-one",
        observation=observation,
        concept_id="contact.region",
        classification_source="ONTOLOGY",
        confidence=0.99,
    )
    rerendered = {**observation, "controlId": "state-session-two", "semanticType": "UNKNOWN"}
    assert bindings.lookup(
        application_id="app-cross-session",
        session_id="session-two",
        observation=rerendered,
    ) is None
    recovered = bindings.lookup(
        application_id="app-cross-session",
        session_id="session-two",
        observation=rerendered,
        allow_application_fallback=True,
    )
    assert recovered is not None
    assert recovered["binding_id"] == first["binding_id"]
    assert recovered["concept_id"] == "contact.region"
    assert recovered["cross_session"] is True


def test_dynamic_arbitrary_skill_autofill_is_enabled_only_with_explicit_evidence_and_derivation_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUNSHI_UNIVERSAL_DERIVATION_ENABLED", "true")
    claims = derivation.extract_profile_claims(
        {"skills": [{"category": "Data", "skills": ["Snowflake"]}], "experience": []},
        extraction_id="extract-snowflake",
    )
    question = questions.resolve_question(
        {"rawText": "Have you ever used Snowflake?", "semanticType": "UNKNOWN"}
    )
    resolved = answers.resolve_concept(
        question,
        knowledge_view={
            "profile_revision": 8,
            "facts": [],
            "answer_memory": [],
            "claims": claims,
            "profile": {},
        },
    )
    assert resolved["answer_status"] == "DERIVED"
    assert resolved["answer_mode"] == "EXPERIENCE_EVIDENCE_BOOLEAN"
    assert resolved["value"] is True
    assert resolved["evidence_ids"]
    assert resolved["autofill_allowed"] is True


def test_knowledge_center_surfaces_reusable_answer_memory_and_missing_safe_concepts(
    hunter_db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        knowledge_center.ingestion,
        "read_only_knowledge_view",
        lambda: {
            "profile_revision": 3,
            "profile_digest": "a" * 64,
            "persisted_graph_fact_count": 0,
            "facts": [
                {
                    "concept_id": "contact.region",
                    "value": "New Jersey",
                    "source_type": "CANDIDATE_TRUTH",
                    "source_ref": "profile:region",
                    "trust_level": "USER_CONFIRMED",
                    "confidence": 1.0,
                    "sensitivity": "NORMAL",
                    "autofill_policy": "ALLOW",
                }
            ],
            "answer_memory": [
                {
                    "concept_id": "work.relocation",
                    "value": True,
                    "source_type": "ANSWER_MEMORY",
                    "source_ref": "answer-vault:relocate",
                    "trust_level": "USER_CONFIRMED",
                    "confidence": 1.0,
                    "sensitivity": "NORMAL",
                    "autofill_policy": "ALLOW",
                }
            ],
            "claims": [],
            "profile": {},
        },
    )
    view = knowledge_center.snapshot()
    assert view["answer_memory_count"] == 1
    assert any(
        row["concept_id"] == "work.relocation"
        for row in view["sections"]["Career preferences"]
    )
    available = {row["concept_id"] for row in view["available_to_add"]}
    assert "contact.region" not in available
    assert "contact.city" in available


def test_dashboard_can_add_arbitrary_explicit_skill_as_canonical_user_truth(hunter_db) -> None:
    stored = knowledge_center.save_explicit_skill(label="Rippling")
    assert stored["concept_id"] == "experience.skill.rippling"
    assert stored["value"] is True
    assert stored["source_type"] == "USER_DASHBOARD"
    assert stored["autofill_policy"] == "ALLOW"
    question = questions.resolve_question(
        {"rawText": "Have you worked with Rippling?", "semanticType": "UNKNOWN"}
    )
    resolved = answers.resolve_concept(
        question,
        knowledge_view={
            "profile_revision": 1,
            "facts": graph.current_facts(),
            "answer_memory": [],
            "claims": [],
            "profile": {},
        },
    )
    assert resolved["answer_status"] == "ANSWERED"
    assert resolved["concept_id"] == "experience.skill.rippling"
    assert resolved["value"] is True
    assert resolved["autofill_allowed"] is True


def test_knowledge_center_add_inventory_excludes_protected_and_repeated_entities() -> None:
    rows = knowledge_center.editable_concept_inventory()
    assert rows
    assert all(row["protected"] is False for row in rows)
    ids = {row["concept_id"] for row in rows}
    assert "contact.region" in ids
    assert "work.authorization.us" not in ids
    assert not any(concept_id.startswith("experience.0.") for concept_id in ids)


def test_knowledge_center_can_add_arbitrary_user_confirmed_skill_without_code_change(hunter_db) -> None:
    stored = knowledge_center.save_explicit_skill(label="Rippling")
    assert stored["concept_id"] == "experience.skill.rippling"
    assert stored["value"] is True
    assert stored["source_type"] == "USER_DASHBOARD"
    assert stored["autofill_policy"] == "ALLOW"
    resolved = graph.resolve_fact("experience.skill.rippling")
    assert resolved["status"] == "ANSWERED"
    assert resolved["fact"]["value"] is True


def test_knowledge_center_normal_editor_cannot_write_protected_concept(hunter_db) -> None:
    with pytest.raises(ValueError, match="Protected"):
        knowledge_center.save_normal_fact(
            concept_id="work.authorization.us",
            value=True,
        )


def test_dynamic_enumeration_scopes_to_candidate_provided_skill_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUNSHI_UNIVERSAL_DERIVATION_ENABLED", "true")
    claims = derivation.extract_profile_claims(
        {
            "skills": [
                {"category": "HRIS / ATS", "skills": ["Workday", "Greenhouse", "Rippling"]},
                {"category": "Analytics", "skills": ["Python", "Tableau"]},
            ],
            "experience": [],
            "projects": [],
        },
        extraction_id="enumeration-category",
    )
    question = questions.resolve_question(
        {"rawText": "Which HRIS / ATS systems have you used?", "semanticType": "UNKNOWN"}
    )
    assert question["state"] == "RESOLVED"
    assert question["concept_id"] == "experience.capabilities"
    assert question["classification_lane"] == "ONTOLOGY_DYNAMIC_ENUMERATION"
    resolved = answers.resolve_concept(
        question,
        knowledge_view={
            "profile_revision": 1,
            "facts": [],
            "answer_memory": [],
            "claims": claims,
            "profile": {},
        },
    )
    assert resolved["answer_status"] == "DERIVED"
    assert resolved["answer_mode"] == "ENUMERATION"
    assert resolved["value"] == ["Greenhouse", "Rippling", "Workday"]
    assert "Python" not in resolved["value"]


def test_dynamic_enumeration_without_category_returns_all_explicit_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUNSHI_UNIVERSAL_DERIVATION_ENABLED", "true")
    claims = derivation.extract_profile_claims(
        {
            "skills": [{"category": "Tools", "skills": ["Snowflake", "Jira"]}],
            "experience": [],
            "projects": [],
        },
        extraction_id="enumeration-all",
    )
    question = questions.resolve_question(
        {"rawText": "What tools and technologies have you used?", "semanticType": "UNKNOWN"}
    )
    assert question["concept_id"] == "experience.capabilities"
    resolved = answers.resolve_concept(
        question,
        knowledge_view={
            "profile_revision": 1,
            "facts": [],
            "answer_memory": [],
            "claims": claims,
            "profile": {},
        },
    )
    assert resolved["answer_status"] == "DERIVED"
    assert resolved["value"] == ["Jira", "Snowflake"]


def test_enumeration_includes_dashboard_added_arbitrary_skill_from_graph_evidence(
    hunter_db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUNSHI_UNIVERSAL_DERIVATION_ENABLED", "true")
    knowledge_center.save_explicit_skill(label="Rippling")
    persisted = graph.current_facts()
    for fact in persisted:
        fact["evidence"] = graph.evidence_for_fact(str(fact["fact_id"]))
    question = questions.resolve_question(
        {"rawText": "Which tools have you used?", "semanticType": "UNKNOWN"}
    )
    resolved = answers.resolve_concept(
        question,
        knowledge_view={
            "profile_revision": 1,
            "facts": persisted,
            "answer_memory": [],
            "claims": [],
            "profile": {},
        },
    )
    assert resolved["answer_status"] == "DERIVED"
    assert resolved["value"] == ["Rippling"]


def _open_ended_claims() -> list[dict[str, object]]:
    return derivation.extract_profile_claims(
        {
            "skills": [
                {"category": "Analytics", "skills": ["Python", "Power BI"]},
                {"category": "Recruiting", "skills": ["Recruiting", "Onboarding"]},
            ],
            "experience": [],
            "projects": [],
        },
        extraction_id="open-ended-evidence",
    )


def test_open_ended_why_role_is_generated_only_from_exact_job_and_candidate_evidence() -> None:
    question = questions.resolve_question(
        {"rawText": "Why are you interested in this position?", "semanticType": "UNKNOWN"}
    )
    resolved = answers.resolve_concept(
        question,
        knowledge_view={
            "profile_revision": 1,
            "facts": [],
            "answer_memory": [],
            "claims": _open_ended_claims(),
            "profile": {},
        },
        open_ended_generator=open_ended.generate,
        job_context={
            "company_name": "Example Company",
            "title": "People Analytics Specialist",
            "responsibilities": "Build dashboards and analyze recruiting and workforce data.",
            "qualifications": "Experience with analytics and recruiting operations.",
        },
    )
    assert resolved["answer_status"] == "GENERATED"
    assert resolved["autofill_allowed"] is True
    assert "People Analytics Specialist" in resolved["value"]
    assert resolved["evidence_ids"]
    assert "culture" not in resolved["value"].casefold()


def test_open_ended_why_company_does_not_invent_company_attributes() -> None:
    question = questions.resolve_question(
        {"rawText": "Why do you want to work for our company?", "semanticType": "UNKNOWN"}
    )
    resolved = answers.resolve_concept(
        question,
        knowledge_view={
            "profile_revision": 1,
            "facts": [],
            "answer_memory": [],
            "claims": _open_ended_claims(),
            "profile": {},
        },
        open_ended_generator=open_ended.generate,
        job_context={
            "company_name": "Example Company",
            "title": "HR Analyst",
            "responsibilities": "Support HR reporting and recruiting analytics.",
        },
    )
    assert resolved["answer_status"] == "GENERATED"
    assert resolved["autofill_allowed"] is True
    assert "Example Company" in resolved["value"]
    prohibited = ("leading company", "great culture", "mission", "values", "industry leader")
    assert not any(term in resolved["value"].casefold() for term in prohibited)


def test_relevant_experience_open_ended_concept_is_provider_neutral_and_evidence_grounded() -> None:
    question = questions.resolve_question(
        {"rawText": "Describe your relevant experience", "semanticType": "UNKNOWN"}
    )
    assert question["concept_id"] == "motivation.relevant_experience"
    resolved = answers.resolve_concept(
        question,
        knowledge_view={
            "profile_revision": 1,
            "facts": [],
            "answer_memory": [],
            "claims": _open_ended_claims(),
            "profile": {},
        },
        open_ended_generator=open_ended.generate,
        job_context={"title": "People Analyst", "skills_keywords": "analytics recruiting"},
    )
    assert resolved["answer_status"] == "GENERATED"
    assert resolved["evidence_ids"]
    assert resolved["autofill_allowed"] is True


def test_reason_for_leaving_is_never_invented_by_open_ended_generator() -> None:
    question = questions.resolve_question(
        {"rawText": "Why are you looking to leave your current role?", "semanticType": "UNKNOWN"}
    )
    resolved = answers.resolve_concept(
        question,
        knowledge_view={
            "profile_revision": 1,
            "facts": [],
            "answer_memory": [],
            "claims": _open_ended_claims(),
            "profile": {},
        },
        open_ended_generator=open_ended.generate,
        job_context={"company_name": "Example Company", "title": "HR Analyst"},
    )
    assert resolved["answer_status"] == "BLOCKED_BY_POLICY"
    assert resolved["autofill_allowed"] is False
    assert "value" not in resolved


def test_embedding_lane_resolves_weird_wording_with_high_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUNSHI_UNIVERSAL_EMBEDDING_ENABLED", "true")
    monkeypatch.setenv("MUNSHI_UNIVERSAL_EMBEDDING_MIN_SCORE", "0.84")
    monkeypatch.setenv("MUNSHI_UNIVERSAL_EMBEDDING_MARGIN", "0.05")
    monkeypatch.setattr(
        semantic_embedding,
        "retrieve_fail_closed",
        lambda observation, top_k=8: [
            {
                "concept_id": "contact.region",
                "score": 0.94,
                "sensitivity": "NORMAL",
                "answer_mode": "DIRECT_FACT",
                "category": "CONTACT",
                "description": "Candidate state or province of residence.",
            },
            {
                "concept_id": "contact.city",
                "score": 0.76,
                "sensitivity": "NORMAL",
                "answer_mode": "DIRECT_FACT",
                "category": "CONTACT",
                "description": "Candidate city of residence.",
            },
        ],
    )
    result = questions.resolve_question(
        {
            "rawText": "Primary domicile jurisdiction",
            "label": "Home jurisdiction",
            "sectionHeading": "Personal details",
            "inputType": "select",
            "role": "combobox",
            "semanticType": "UNKNOWN",
        }
    )
    assert result["state"] == "RESOLVED"
    assert result["concept_id"] == "contact.region"
    assert result["classification_lane"] == "ONTOLOGY_EMBEDDING"
    assert result["candidate_values_exposed"] is False


def test_embedding_lane_cannot_downgrade_protected_preclassification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUNSHI_UNIVERSAL_EMBEDDING_ENABLED", "true")
    monkeypatch.setattr(
        questions,
        "_preclassification",
        lambda _obs: {
            "family": "work_authorization",
            "sensitivity": "PROTECTED",
            "catalog_match": {"matched": False},
            "catalog_concept_id": None,
        },
    )
    monkeypatch.setattr(
        semantic_embedding,
        "retrieve_fail_closed",
        lambda observation, top_k=8: [
            {
                "concept_id": "contact.region",
                "score": 0.99,
                "sensitivity": "NORMAL",
                "answer_mode": "DIRECT_FACT",
                "category": "CONTACT",
                "description": "Candidate state.",
            }
        ],
    )
    result = questions.resolve_question(
        {"rawText": "Unfamiliar protected wording", "semanticType": "UNKNOWN"}
    )
    assert result["classification_lane"] != "ONTOLOGY_EMBEDDING"
    assert result["sensitivity"] == "PROTECTED"


def test_embedding_observation_projection_ignores_candidate_values() -> None:
    text = semantic_embedding._observation_text(
        {
            "raw_question": "Home base",
            "label": "Home base",
            "section_heading": "Contact",
            "execution_value": "New Jersey",
            "canonical_answer": "New Jersey",
            "password": "never-send-this",
        }
    )
    assert "Home base" in text
    assert "New Jersey" not in text
    assert "never-send-this" not in text


def test_embedding_status_declares_local_value_free_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUNSHI_UNIVERSAL_EMBEDDING_ENABLED", "true")
    monkeypatch.setenv("MUNSHI_UNIVERSAL_EMBEDDING_MODEL", "nomic-embed-text")
    status = semantic_embedding.status()
    assert status["enabled"] is True
    assert status["provider"] == "ollama-local"
    assert status["external_provider"] is False
    assert status["candidate_values_exposed"] is False
