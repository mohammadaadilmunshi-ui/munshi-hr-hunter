from __future__ import annotations

import ast
import importlib
import sqlite3
from pathlib import Path

import pytest

from app import database
from app.relationship_intelligence import add_evidence, contact_evidence, contact_information_state, contacts_for_job, link_contact_to_job, save_contact
from app.tenant_foundation import associate_owned_record, owner_context


def _enable(monkeypatch: pytest.MonkeyPatch, *, tenants: bool = False) -> None:
    monkeypatch.setenv("MUNSHI_RELATIONSHIP_INTELLIGENCE_ENABLED", "1")
    if tenants:
        monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")


def _member(tenant_id: str = "team-a", user_id: str = "member-a") -> None:
    connection = database.get_connection()
    try:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES (?,?)", (tenant_id, tenant_id))
        connection.execute("INSERT INTO app_users(user_id,display_name) VALUES (?,?)", (user_id, user_id))
        connection.execute("INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES (?,?, 'member')", (tenant_id, user_id))
        connection.commit()
    finally:
        connection.close()


def _contact(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {"display_name": "Casey Recruiter", "company_name": "Example Co", "title": "Recruiter", "contact_type": "recruiter", "relevance": "Named on company careers page", "confidence": .8, "source": "public_company", "recommended_action": "review"}
    result.update(overrides)
    return result


def _job_and_owner(*, owner=None) -> int:
    connection = database.get_connection()
    try:
        sequence = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        cursor = connection.execute("INSERT INTO jobs(job_fingerprint,source,company_name,title) VALUES (?,?,?,?)", (f"relationship-job-{sequence}", "manual", "Example Co", "Analyst"))
        job_id = int(cursor.lastrowid)
        associate_owned_record(connection, record_domain="job", record_key=job_id, owner=owner)
        connection.commit()
        return job_id
    finally:
        connection.close()


def test_disabled_by_default_has_no_contact_write(hunter_db) -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        save_contact(_contact())
    connection = database.get_connection()
    try:
        assert connection.execute("SELECT COUNT(*) FROM relationship_contacts").fetchone()[0] == 0
    finally:
        connection.close()


def test_contact_email_provenance_and_evidence_validation(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    explicit = save_contact(_contact(email="casey@example.test", email_provenance="explicit_contact_email"))
    inferred = save_contact(_contact(display_name="Pattern Only", email_provenance="inferred_pattern", inferred_email_pattern="first.last@example.test"))
    assert explicit != inferred
    with pytest.raises(ValueError, match="explicitly supplied"):
        save_contact(_contact(email="guessed@example.test"))
    with pytest.raises(ValueError, match="explicitly supplied"):
        save_contact(_contact(email="guessed@example.test", email_provenance="inferred_pattern", inferred_email_pattern="first.last@example.test"))
    for malformed in ("not-a-pattern", "casey@example.test", "{first}@not a domain.test", "first.last@example"):
        with pytest.raises(ValueError, match="Inferred email pattern"):
            save_contact(_contact(email_provenance="inferred_pattern", inferred_email_pattern=malformed))
    with pytest.raises(ValueError, match="URL"):
        add_evidence(contact_id=explicit, source="public_company", evidence_summary="Company profile", confidence=.7, evidence_url="ftp://example.test/a")
    evidence_id = add_evidence(contact_id=explicit, source="public_company", evidence_summary="Company team page", confidence=.7, evidence_url="https://example.test/team")
    evidence = contact_evidence(explicit)
    assert evidence[0]["evidence_id"] == evidence_id
    assert evidence[0]["source"] == "public_company"


def test_contact_schema_rejects_unsupported_fields_and_exposes_explicit_information_states(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    with pytest.raises(ValueError, match="Unsupported contact fields"):
        save_contact(_contact(unverified_email="do-not-store@example.test"))
    supplied = save_contact(_contact(display_name="Supplied", source="user_supplied", email="supplied@example.test", email_provenance="explicit_contact_email"))
    observed = save_contact(_contact(display_name="Observed", source="public_profile"))
    inferred = save_contact(_contact(display_name="Pattern", source="existing_contact_finder", email_provenance="inferred_pattern", inferred_email_pattern="{first}.{last}@example.test"))
    assert contact_information_state(supplied) == {"contact": "known_contact", "relationship": "supplied_evidence", "email": "known_contact_email"}
    assert contact_information_state(observed) == {"contact": "known_contact", "relationship": "observed_relationship", "email": "unknown_unverified"}
    assert contact_information_state(inferred)["email"] == "inferred_pattern"


def test_tenant_isolation_and_defensive_shared_job_link(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch, tenants=True)
    default_contact = save_contact(_contact())
    default_evidence = add_evidence(contact_id=default_contact, source="public_company", evidence_summary="Public staffing page", confidence=.7)
    job_id = _job_and_owner()
    link_contact_to_job(contact_id=default_contact, job_id=job_id)
    _member()
    with owner_context(tenant_id="team-a", user_id="member-a"):
        assert contacts_for_job(job_id=job_id) == []
        assert contact_evidence(default_contact) == []
        with pytest.raises(LookupError, match="not owned"):
            contact_information_state(default_contact)
        with pytest.raises(LookupError, match="not owned"):
            add_evidence(contact_id=default_contact, source="public_company", evidence_summary="Foreign evidence attempt", confidence=.7)
        foreign_contact = save_contact(_contact(display_name="Tenant Contact"))
        with pytest.raises(LookupError, match="defensively associated"):
            link_contact_to_job(contact_id=foreign_contact, job_id=job_id)
        own_job_id = _job_and_owner()
        link_contact_to_job(contact_id=foreign_contact, job_id=own_job_id)
        assert [row["contact_id"] for row in contacts_for_job(job_id=own_job_id)] == [foreign_contact]
    assert [row["contact_id"] for row in contacts_for_job(job_id=job_id)] == [default_contact]
    assert [row["evidence_id"] for row in contact_evidence(default_contact)] == [default_evidence]


def test_source_contract_no_authority_integrations_and_additive_migration(tmp_path) -> None:
    source = (Path(__file__).resolve().parent.parent / "app" / "relationship_intelligence.py").read_text(encoding="utf-8")
    imports = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) and node.module}
    assert not {"app.n8n_dispatch", "app.gmail_integration", "app.answer_brain", "app.native_resume_shadow", "app.scoring"} & imports
    connection = sqlite3.connect(tmp_path / "upgraded.sqlite")
    try:
        importlib.import_module("migrations.021_relationship_intelligence").apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert {"relationship_contacts", "relationship_contact_evidence", "relationship_contact_job_links"} <= tables
