from __future__ import annotations

import importlib
import sqlite3

import pytest

from app import database
from app.candidate_artifacts import normalize_n8n_artifacts
from app.candidate_digital_twin import add_evidence, upsert_fact
from app.native_resume_shadow import (
    native_resume_authority_enabled,
    plan_shadow_resume,
)


def _baseline(connection: sqlite3.Connection, *, fingerprint: str = "shadow-job") -> tuple[int, str]:
    job_id = connection.execute(
        """INSERT INTO jobs(job_fingerprint,source,company_name,title,location_raw,description_raw)
           VALUES (?, 'fixture', 'Shadow Co', 'HR Analyst', 'NY', 'HR operations and Workday analytics')""",
        (fingerprint,),
    ).lastrowid
    result_id = connection.execute(
        """INSERT INTO n8n_results(job_id,job_fingerprint,send_mode,n8n_status,final_ats_score,resume_pdf_url)
           VALUES (?, ?, 'manual', 'completed', 96, 'https://docs.example.test/shadow.pdf')""",
        (job_id, f"{fingerprint}-result"),
    ).lastrowid
    normalize_n8n_artifacts(connection)
    artifact_id = connection.execute(
        "SELECT artifact_id FROM candidate_artifacts WHERE source_result_id=?",
        (result_id,),
    ).fetchone()[0]
    connection.commit()
    return int(job_id), str(artifact_id)


def _confirmed_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUNSHI_CANDIDATE_DIGITAL_TWIN_ENABLED", "1")
    fact_id = upsert_fact(
        fact_key="hr_operations_experience", value="HRIS reporting", provenance="candidate",
        confidence=1, user_confirmed=True,
    )
    add_evidence(
        fact_id=fact_id, provenance="candidate-upload", source_reference="candidate://resume/1",
        excerpt="Built Workday HR operations reports.",
    )


def test_disabled_mode_rejects_without_writing_or_mutating_n8n(hunter_db) -> None:
    connection = database.get_connection()
    try:
        job_id, artifact_id = _baseline(connection)
        before_n8n = tuple(connection.execute("SELECT * FROM n8n_results").fetchone())
        before_runs = connection.execute("SELECT COUNT(*) FROM native_resume_shadow_runs").fetchone()[0]
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="disabled"):
        plan_shadow_resume(job_id=job_id, artifact_id=artifact_id, job_description="Workday HR operations", idempotency_key="disabled")
    connection = database.get_connection()
    try:
        assert tuple(connection.execute("SELECT * FROM n8n_results").fetchone()) == before_n8n
        assert connection.execute("SELECT COUNT(*) FROM native_resume_shadow_runs").fetchone()[0] == before_runs
    finally:
        connection.close()


def test_default_owner_records_only_evidence_plan_and_known_n8n_baseline(hunter_db, monkeypatch) -> None:
    connection = database.get_connection()
    try:
        job_id, artifact_id = _baseline(connection)
        baseline_before = tuple(connection.execute("SELECT * FROM n8n_results").fetchone())
        artifact_count = connection.execute("SELECT COUNT(*) FROM candidate_artifacts").fetchone()[0]
    finally:
        connection.close()
    _confirmed_evidence(monkeypatch)
    monkeypatch.setenv("MUNSHI_NATIVE_RESUME_SHADOW_ENABLED", "true")
    run = plan_shadow_resume(
        job_id=job_id, artifact_id=artifact_id,
        job_description="Lead Workday HR operations analytics and stakeholder reporting.",
        idempotency_key="default-plan",
    )
    assert run["status"] == "BLOCKED_EXTERNAL"
    assert run["comparison"]["baseline_ats_score"] == 96
    assert run["comparison"]["native_artifact"] is None
    assert run["comparison"]["page_count"] is None
    assert run["prompt_plan"]["model_status"] == "BLOCKED_EXTERNAL"
    assert run["prompt_plan"]["renderer_status"] == "BLOCKED_EXTERNAL"
    assert run["exact_terms"] == sorted(run["exact_terms"], key=str.casefold)
    connection = database.get_connection()
    try:
        assert tuple(connection.execute("SELECT * FROM n8n_results").fetchone()) == baseline_before
        assert connection.execute("SELECT COUNT(*) FROM candidate_artifacts").fetchone()[0] == artifact_count
    finally:
        connection.close()


def test_cross_tenant_and_forged_artifact_rows_are_denied(hunter_db, monkeypatch) -> None:
    connection = database.get_connection()
    try:
        job_id, artifact_id = _baseline(connection)
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES ('team-a','Team A')")
        connection.execute("INSERT INTO app_users(user_id,display_name) VALUES ('member-a','Member A')")
        connection.execute("INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES ('team-a','member-a','member')")
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setenv("MUNSHI_NATIVE_RESUME_SHADOW_ENABLED", "true")
    monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "true")
    from app.tenant_foundation import owner_context
    with owner_context(tenant_id="team-a", user_id="member-a"):
        with pytest.raises(LookupError, match="does not belong"):
            plan_shadow_resume(job_id=job_id, artifact_id=artifact_id, job_description="Workday HR operations", idempotency_key="cross-owner")
    # Simulate a corrupted/forged row written with FK checks disabled; the
    # composite owner predicate must still keep it unreadable.
    connection = database.get_connection()
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """INSERT INTO candidate_artifacts(artifact_id,tenant_id,user_id,artifact_kind,display_label,
                   object_reference,source_system,source_result_id,source_field)
               VALUES ('forged','team-a','member-a','resume_pdf','Forged','https://bad.example/resume.pdf',
                       'n8n_results',99999,'resume_pdf_url')"""
        )
        connection.commit()
    finally:
        connection.close()
    with owner_context(tenant_id="team-a", user_id="member-a"):
        with pytest.raises(LookupError, match="known n8n baseline"):
            plan_shadow_resume(job_id=job_id, artifact_id="forged", job_description="Workday HR operations", idempotency_key="forged")


def test_idempotency_and_sensitive_self_id_exclusion_fail_closed(hunter_db, monkeypatch) -> None:
    connection = database.get_connection()
    try:
        job_id, artifact_id = _baseline(connection)
    finally:
        connection.close()
    _confirmed_evidence(monkeypatch)
    sensitive_id = upsert_fact(fact_key="race", value="private", provenance="candidate", confidence=1, user_confirmed=True)
    add_evidence(fact_id=sensitive_id, provenance="candidate", source_reference="candidate://private", excerpt="private")
    monkeypatch.setenv("MUNSHI_NATIVE_RESUME_SHADOW_ENABLED", "true")
    first = plan_shadow_resume(job_id=job_id, artifact_id=artifact_id, job_description="Workday HR operations", idempotency_key="retry")
    again = plan_shadow_resume(job_id=job_id, artifact_id=artifact_id, job_description="Changed description", idempotency_key="retry")
    assert again == first
    serialized = str(first["evidence"]) + str(first["prompt_plan"])
    assert "race" not in serialized.casefold()
    assert "private" not in serialized.casefold()
    assert native_resume_authority_enabled() is False


def test_missing_evidence_is_needs_input_and_migration_is_additive(hunter_db, monkeypatch, tmp_path) -> None:
    connection = database.get_connection()
    try:
        job_id, artifact_id = _baseline(connection)
    finally:
        connection.close()
    monkeypatch.setenv("MUNSHI_NATIVE_RESUME_SHADOW_ENABLED", "true")
    run = plan_shadow_resume(job_id=job_id, artifact_id=artifact_id, job_description="Workday HR operations", idempotency_key="needs-input")
    assert run["status"] == "NEEDS_INPUT"
    assert run["missing_fields"] == ["confirmed_non_sensitive_evidence"]
    connection = sqlite3.connect(tmp_path / "upgraded.sqlite")
    try:
        importlib.import_module("migrations.018_native_resume_shadow").apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert "native_resume_shadow_runs" in tables
