from __future__ import annotations

from datetime import date
import importlib

import pytest

from app import application_semantic_binding_v1 as bindings
from app import application_question_catalog_v2 as catalog
from app import candidate_derivation_engine_v1 as derivation
from app import candidate_knowledge_graph_v1 as graph
from app import candidate_knowledge_ingestion_v1 as ingestion
from app import candidate_ontology_v1 as ontology
from app import universal_answer_resolver_v1 as answers
from app import universal_question_resolver_v1 as questions


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
