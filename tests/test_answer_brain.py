from __future__ import annotations

import ast
import base64
import importlib
import sqlite3
from pathlib import Path

import pytest

from app import database
from app.answer_brain import (
    AnswerBrainError,
    classify_question,
    normal_answer_projection,
    planning_input,
    read_sensitive_self_identification,
    resolve_answer,
    resolve_sensitive_self_identification,
    save_answer,
    store_sensitive_self_identification,
)
from app.candidate_digital_twin import add_evidence, upsert_fact
from app.tenant_foundation import owner_context


VAULT_KEY = base64.urlsafe_b64encode(b"v" * 32).decode("ascii")


def _enable(monkeypatch: pytest.MonkeyPatch, *, twin: bool = False) -> None:
    monkeypatch.setenv("MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED", "1")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", VAULT_KEY)
    if twin:
        monkeypatch.setenv("MUNSHI_CANDIDATE_DIGITAL_TWIN_ENABLED", "1")


def _add_member() -> None:
    connection = database.get_connection()
    try:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES ('team-a','Team A')")
        connection.execute("INSERT INTO app_users(user_id,display_name) VALUES ('member-a','Member A')")
        connection.execute("INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES ('team-a','member-a','member')")
        connection.commit()
    finally:
        connection.close()


def test_answer_brain_is_disabled_by_default(hunter_db) -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        save_answer(
            question_family="location", canonical_answer="New York", source="user",
            user_confirmed=True, confidence=1, autofill_allowed=True,
        )


@pytest.mark.parametrize(("question", "family"), [
    ("May we ask about your veteran status?", "voluntary_self_identification"),
    ("What is your password for this site?", "credential_requirement"),
    ("Are you authorized to work in the United States?", "work_authorization"),
    ("What salary range are you seeking?", "salary"),
    ("Where are you located?", "location"),
    ("When can you start?", "availability"),
    ("How many years of experience with SQL do you have?", "experience_skill"),
    ("Why are you interested in this company?", "open_ended_job_specific"),
    ("What is your favorite color?", "unknown"),
])
def test_question_classifier_is_conservative_and_bounded(question, family) -> None:
    assert classify_question(question) == family
    with pytest.raises(ValueError):
        classify_question("x" * 2_001)


def test_verified_answer_precedence_conditions_and_metadata(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    with pytest.raises(ValueError, match="Autofill requires"):
        save_answer(question_family="location", canonical_answer="New York", source="profile_evidence", user_confirmed=False, confidence=.9, autofill_allowed=True)
    with pytest.raises(ValueError, match="User-sourced"):
        save_answer(question_family="location", canonical_answer="New York", source="user", user_confirmed=False, confidence=.9, autofill_allowed=False)
    save_answer(question_family="location", canonical_answer="New York", source="user", user_confirmed=True, confidence=.9, autofill_allowed=True, conditions={"country": "US"})
    resolution = resolve_answer(question_family="location", conditions={"country": "US"})
    assert resolution["status"] == "ANSWERED"
    assert resolution["resolution"] == "stored_verified"
    assert resolution["answer"]["source"] == "user"
    assert resolution["answer"]["confidence"] == .9
    assert resolution["answer"]["user_confirmed"] is True
    assert resolution["answer"]["autofill_allowed"] is True
    assert resolve_answer(question_family="location", conditions={}) == {"status": "NEEDS_INPUT", "reason": "no_safe_answer"}


def test_profile_evidence_is_only_second_to_stored_verified_answer(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch, twin=True)
    fact_id = upsert_fact(fact_key="work_authorization", value="Authorized", provenance="candidate-upload", confidence=.8, user_confirmed=True)
    add_evidence(fact_id=fact_id, provenance="resume", source_reference="artifact://1", excerpt="Authorized")
    profile = resolve_answer(question_family="work_authorization", profile_fact_key="work_authorization")
    assert profile["resolution"] == "deterministic_profile_evidence"
    assert profile["answer"] == {
        "canonical_answer": "Authorized", "source": "profile_evidence", "evidence_provenance": "candidate-upload",
        "confidence": .8, "user_confirmed": True, "autofill_allowed": False, "fact_key": "work_authorization",
    }
    save_answer(question_family="work_authorization", canonical_answer="Yes", source="user", user_confirmed=True, confidence=1, autofill_allowed=True)
    assert resolve_answer(question_family="work_authorization", profile_fact_key="work_authorization")["resolution"] == "stored_verified"


def test_unsupported_and_non_plaintext_families_never_guess_or_enter_plaintext_vault(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    for family in ("voluntary_self_identification", "post_offer_sensitive", "credential_requirement"):
        with pytest.raises(ValueError, match="not permitted"):
            save_answer(question_family=family, canonical_answer="secret", source="user", user_confirmed=True, confidence=1, autofill_allowed=True)
        assert resolve_answer(question_family=family)["status"] == "NEEDS_INPUT"
    connection = database.get_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="non-plaintext"):
            connection.execute("INSERT INTO application_answer_vault(answer_id,tenant_id,user_id,question_family,canonical_answer,source,user_confirmed,confidence,autofill_allowed,conditions_json) VALUES ('raw','default','local-owner','credential_requirement','secret','user',1,1,1,'{}')")
    finally:
        connection.close()


def test_sensitive_ciphertext_policies_and_tamper_fail_closed(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    secret = "Veteran status: confidential"
    record_id = store_sensitive_self_identification(category="veteran_status", response=secret, autofill_policy="use_saved_response")
    assert read_sensitive_self_identification(category="veteran_status") == secret
    assert resolve_sensitive_self_identification(category="veteran_status") == {"status": "ANSWERED", "resolution": "stored_sensitive_response", "answer": secret}
    connection = database.get_connection()
    try:
        row = connection.execute("SELECT ciphertext,nonce FROM sensitive_self_identification_vault WHERE self_id_id=?", (record_id,)).fetchone()
        assert secret.encode() not in bytes(row["ciphertext"])
        connection.execute("UPDATE sensitive_self_identification_vault SET ciphertext=? WHERE self_id_id=?", (b"bad", record_id))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(AnswerBrainError, match="could not be decrypted"):
        read_sensitive_self_identification(category="veteran_status")


def test_sensitive_missing_wrong_key_and_policy_retention_are_safe(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    assert resolve_sensitive_self_identification(category="disability_status") == {"status": "NEEDS_INPUT", "reason": "no_sensitive_policy"}
    with pytest.raises(AnswerBrainError, match="not configured"):
        monkeypatch.delenv("MUNSHI_VAULT_KEY")
        store_sensitive_self_identification(category="disability_status", response="No", autofill_policy="use_saved_response")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", VAULT_KEY)
    with pytest.raises(ValueError, match="Only use_saved_response"):
        store_sensitive_self_identification(category="disability_status", response="No", autofill_policy="ask_each_time")
    store_sensitive_self_identification(category="disability_status", autofill_policy="ask_each_time")
    assert read_sensitive_self_identification(category="disability_status") is None
    assert resolve_sensitive_self_identification(category="disability_status") == {"status": "NEEDS_INPUT", "reason": "ask_each_time"}
    store_sensitive_self_identification(category="race_ethnicity", autofill_policy="prefer_non_disclosure")
    assert read_sensitive_self_identification(category="race_ethnicity") is None
    assert resolve_sensitive_self_identification(category="race_ethnicity") == {"status": "ANSWERED", "resolution": "prefer_non_disclosure", "answer": "Prefer not to disclose"}
    store_sensitive_self_identification(category="gender_self_identification", response="X", autofill_policy="use_saved_response")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", base64.urlsafe_b64encode(b"w" * 32).decode())
    with pytest.raises(AnswerBrainError, match="could not be decrypted"):
        read_sensitive_self_identification(category="gender_self_identification")


def test_tenant_isolation_and_normal_projections_exclude_sensitive_values(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")
    save_answer(question_family="location", canonical_answer="New York", source="user", user_confirmed=True, confidence=1, autofill_allowed=True)
    store_sensitive_self_identification(category="veteran_status", response="No", autofill_policy="use_saved_response")
    _add_member()
    with owner_context(tenant_id="team-a", user_id="member-a"):
        assert normal_answer_projection() == []
        assert resolve_sensitive_self_identification(category="veteran_status")["status"] == "NEEDS_INPUT"
        save_answer(question_family="location", canonical_answer="Boston", source="user", user_confirmed=True, confidence=1, autofill_allowed=True)
    projection = normal_answer_projection()
    assert [row["canonical_answer"] for row in projection] == ["New York"]
    assert "veteran_status" not in str(projection)
    assert "veteran_status" not in str(planning_input())


def test_no_external_authority_surface_or_sensitive_ranking_resume_import(hunter_db) -> None:
    root = Path(__file__).resolve().parent.parent
    answer_source = (root / "app" / "answer_brain.py").read_text(encoding="utf-8")
    imports = {node.module for node in ast.walk(ast.parse(answer_source)) if isinstance(node, ast.ImportFrom) and node.module}
    assert not {"app.n8n_dispatch", "app.gmail_integration", "app.secure_vault", "app.native_resume_shadow"} & imports
    for path in (root / "app" / "scoring.py", root / "app" / "native_resume_shadow.py"):
        assert "sensitive_self_identification" not in path.read_text(encoding="utf-8")


def test_migration_is_additive_and_installs_plaintext_defense(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "upgraded.sqlite")
    try:
        importlib.import_module("migrations.019_application_answer_brain").apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    finally:
        connection.close()
    assert {"application_answer_vault", "sensitive_self_identification_vault"} <= tables
    assert {"answer_vault_reject_non_plaintext_insert", "answer_vault_reject_non_plaintext_update"} <= triggers
