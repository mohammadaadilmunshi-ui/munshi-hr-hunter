from __future__ import annotations

import ast
import importlib
import sqlite3
from pathlib import Path

import pytest

from app import database
from app.answer_brain import save_answer
from app.candidate_artifacts import designate_master_resume, normalize_n8n_artifacts
from app.native_application_preparation import prepare_application
from app.tenant_foundation import associate_owned_record, owner_context


def _enable(monkeypatch: pytest.MonkeyPatch, *, tenants: bool = False) -> None:
    monkeypatch.setenv("MUNSHI_NATIVE_APPLICATION_PREPARATION_ENABLED", "1")
    monkeypatch.setenv("MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED", "1")
    monkeypatch.setenv("MUNSHI_CAREER_POLICY_ENABLED", "1")
    if tenants:
        monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")


def _job_and_resume() -> int:
    connection = database.get_connection()
    try:
        job_id = int(connection.execute("INSERT INTO jobs(job_fingerprint,source,company_name,title) VALUES ('phase8-job','fixture','Example Co','HR Analyst')").lastrowid)
        associate_owned_record(connection, record_domain="job", record_key=job_id)
        result_id = int(connection.execute("INSERT INTO n8n_results(job_id,job_fingerprint,send_mode,n8n_status,resume_pdf_url) VALUES (?, 'phase8-result', 'manual', 'completed', 'https://docs.example.test/master.pdf')", (job_id,)).lastrowid)
        normalize_n8n_artifacts(connection)
        connection.commit()
    finally:
        connection.close()
    assert result_id
    designate_master_resume(job_id=job_id, reference="https://docs.example.test/master.pdf", label="Master", source_label="Candidate selection")
    return job_id


def _opportunity() -> dict[str, object]:
    return {"role_family": "HR", "company_name": "Example Co", "location": "New York", "workplace": "remote", "salary_max": 100_000, "skill_fit": .8, "evidence_backed_experience_fit": .8, "career_direction_fit": .8, "eligibility_fit": 1}


def _ready_call(job_id: int, key: str = "phase8-key") -> dict[str, object]:
    return prepare_application(job_id=job_id, application_id="authoritative-app-1", idempotency_key=key, opportunity=_opportunity(), answer_requests=[{"question_family": "candidate_fact"}], eligibility="PASS", account_state="CREATABLE", permissions="PASS", duplicate_application="NO", job_open="YES")


def test_disabled_by_default_rejects_before_ledger_write(hunter_db) -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        prepare_application(job_id=1, application_id="app", idempotency_key="key", opportunity={})
    connection = database.get_connection()
    try:
        assert connection.execute("SELECT COUNT(*) FROM native_application_preparations").fetchone()[0] == 0
    finally:
        connection.close()


def test_deterministic_readiness_idempotency_and_no_candidate_answer_copy(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    job_id = _job_and_resume()
    save_answer(question_family="candidate_fact", canonical_answer="Candidate private answer", source="user", user_confirmed=True, confidence=1, autofill_allowed=True)
    first = _ready_call(job_id)
    again = _ready_call(job_id)
    assert first == again
    assert first["status"] == "READY_TO_APPLY"
    assert first["readiness"]["checks"] == {"eligibility": "PASS", "opportunity_policy": "PASS", "resume": "READY", "answers": "READY", "required_files": "READY", "account": "CREATABLE", "permissions": "PASS", "duplicate_application": "NO", "job_open": "YES"}
    assert "Candidate private answer" not in str(first["snapshot"])
    with pytest.raises(ValueError, match="different job or application"):
        prepare_application(job_id=job_id, application_id="other-app", idempotency_key="phase8-key", opportunity=_opportunity())


def test_unresolved_or_external_prerequisites_never_report_ready(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch)
    job_id = _job_and_resume()
    incomplete = prepare_application(job_id=job_id, application_id="app-input", idempotency_key="input", opportunity=_opportunity(), answer_requests=[{"question_family": "candidate_fact"}], eligibility="NEEDS_INPUT", account_state="NEEDS_INPUT", permissions="NEEDS_INPUT", duplicate_application="NEEDS_INPUT", job_open="NEEDS_INPUT")
    assert incomplete["status"] == "NEEDS_INPUT"
    external = prepare_application(job_id=job_id, application_id="app-external", idempotency_key="external", opportunity=_opportunity(), eligibility="PASS", account_state="BLOCKED_EXTERNAL", permissions="PASS", duplicate_application="NO", job_open="YES")
    assert external["status"] == "BLOCKED_EXTERNAL"
    with pytest.raises(ValueError, match="Sensitive or credential"):
        prepare_application(job_id=job_id, application_id="app-sensitive", idempotency_key="sensitive", opportunity=_opportunity(), answer_requests=[{"question_family": "voluntary_self_identification"}])


@pytest.mark.parametrize("answer_requests", [None, []])
def test_missing_or_empty_answer_requests_remain_explicit_needs_input(hunter_db, monkeypatch, answer_requests) -> None:
    _enable(monkeypatch)
    job_id = _job_and_resume()
    result = prepare_application(
        job_id=job_id, application_id=f"app-empty-{answer_requests is None}",
        idempotency_key=f"empty-{answer_requests is None}", opportunity=_opportunity(),
        answer_requests=answer_requests, eligibility="PASS", account_state="CREATABLE",
        permissions="PASS", duplicate_application="NO", job_open="YES",
    )
    assert result["status"] == "NEEDS_INPUT"
    assert result["readiness"]["checks"]["answers"] == "NEEDS_INPUT"


def test_tenant_job_association_blocks_cross_owner_reads(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch, tenants=True)
    job_id = _job_and_resume()
    connection = database.get_connection()
    try:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES ('team-a','Team A')")
        connection.execute("INSERT INTO app_users(user_id,display_name) VALUES ('member-a','Member A')")
        connection.execute("INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES ('team-a','member-a','member')")
        connection.commit()
    finally:
        connection.close()
    with owner_context(tenant_id="team-a", user_id="member-a"):
        with pytest.raises(LookupError, match="defensively associated"):
            prepare_application(job_id=job_id, application_id="foreign", idempotency_key="foreign", opportunity=_opportunity())


def test_local_contract_has_no_authority_imports_and_migration_is_additive(tmp_path) -> None:
    source = (Path(__file__).resolve().parent.parent / "app" / "native_application_preparation.py").read_text(encoding="utf-8")
    imports = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) and node.module}
    assert not {"app.n8n_dispatch", "app.gmail_integration", "app.api", "app.product_state"} & imports
    connection = sqlite3.connect(tmp_path / "upgraded.sqlite")
    try:
        importlib.import_module("migrations.022_native_application_preparation").apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert "native_application_preparations" in tables
