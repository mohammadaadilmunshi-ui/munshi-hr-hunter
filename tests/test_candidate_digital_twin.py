from __future__ import annotations

import importlib

import pytest

from app import database
from app.candidate_digital_twin import (
    PAYLOAD_VERSION,
    add_evidence,
    internal_profile_payload,
    save_onboarding,
    upsert_fact,
    upsert_preference,
)
from app.product_state import candidate_facts, save_candidate_fact


def test_digital_twin_is_disabled_by_default_and_legacy_facts_stay_unmodified(hunter_db) -> None:
    save_candidate_fact("headline", "Existing candidate-entered value")
    with pytest.raises(RuntimeError, match="disabled"):
        upsert_fact(fact_key="headline", value="New value", provenance="candidate", confidence=1, user_confirmed=True)
    assert candidate_facts()[0]["fact_key"] == "headline"


def test_internal_payload_preserves_ids_confirmation_provenance_and_evidence(hunter_db, monkeypatch) -> None:
    monkeypatch.setenv("MUNSHI_CANDIDATE_DIGITAL_TWIN_ENABLED", "true")
    fact_id = upsert_fact(fact_key="work_authorization", value="US authorized", provenance="candidate-form", confidence=1, user_confirmed=True)
    assert upsert_fact(fact_key="work_authorization", value="US authorized", provenance="candidate-form", confidence=1, user_confirmed=True) == fact_id
    evidence_id = add_evidence(fact_id=fact_id, provenance="candidate-upload", source_reference="candidate://upload/1", excerpt="Authorized to work")
    preference_id = upsert_preference(preference_key="workplace", value=["remote"], provenance="candidate-form", confidence=0.95, user_confirmed=True)
    onboarding_id = save_onboarding(state="in_progress", completed_steps=["profile"], user_confirmed=True)

    payload = internal_profile_payload()
    assert payload["version"] == PAYLOAD_VERSION
    assert payload["facts"][0]["fact_id"] == fact_id
    assert payload["facts"][0]["user_confirmed"] == 1
    assert payload["facts"][0]["evidence"][0]["evidence_id"] == evidence_id
    assert payload["preferences"][0]["preference_id"] == preference_id
    assert payload["onboarding"]["onboarding_id"] == onboarding_id
    assert "apply" not in payload


def test_tenant_context_cannot_read_or_attach_another_candidates_fact(hunter_db, monkeypatch) -> None:
    monkeypatch.setenv("MUNSHI_CANDIDATE_DIGITAL_TWIN_ENABLED", "1")
    monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")
    fact_id = upsert_fact(fact_key="location", value="New York", provenance="candidate", confidence=1, user_confirmed=True)
    connection = database.get_connection()
    try:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES ('team-a','Team A')")
        connection.execute("INSERT INTO app_users(user_id,display_name) VALUES ('member-a','Member A')")
        connection.execute("INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES ('team-a','member-a','member')")
        connection.commit()
    finally:
        connection.close()
    from app.tenant_foundation import owner_context
    with owner_context(tenant_id="team-a", user_id="member-a"):
        assert internal_profile_payload()["facts"] == []
        with pytest.raises(LookupError):
            add_evidence(fact_id=fact_id, provenance="candidate", source_reference="candidate://other")


def test_migration_is_additive(tmp_path) -> None:
    connection = __import__("sqlite3").connect(tmp_path / "upgraded.sqlite")
    try:
        importlib.import_module("migrations.016_candidate_digital_twin").apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert {"candidate_digital_twin_facts", "candidate_digital_twin_evidence", "candidate_digital_twin_preferences", "candidate_digital_twin_onboarding"} <= tables
