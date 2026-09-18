from __future__ import annotations

import ast
import hashlib
import hmac
import importlib
import sqlite3
from pathlib import Path

import pytest

from app import database
from app.answer_brain import save_answer
from app.apply_handoff import accept_handoff, create_handoff
from app.candidate_artifacts import designate_master_resume, normalize_n8n_artifacts
from app.native_application_preparation import prepare_application
from app.tenant_foundation import associate_owned_record, owner_context


def _enable(monkeypatch: pytest.MonkeyPatch, *, tenants: bool = False) -> None:
    for name in ("MUNSHI_NATIVE_APPLICATION_PREPARATION_ENABLED", "MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED", "MUNSHI_CAREER_POLICY_ENABLED", "MUNSHI_APPLY_HANDOFF_ENABLED"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET", "test-only-handoff-secret-not-production")
    if tenants:
        monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")


def _preparation() -> dict[str, object]:
    connection = database.get_connection()
    try:
        job_id = int(connection.execute("INSERT INTO jobs(job_fingerprint,source,company_name,title,apply_url) VALUES ('handoff-job','fixture','Example Co','HR Analyst','https://boards.greenhouse.io/example/jobs/1')").lastrowid)
        associate_owned_record(connection, record_domain="job", record_key=job_id)
        connection.execute("INSERT INTO n8n_results(job_id,job_fingerprint,send_mode,n8n_status,resume_pdf_url) VALUES (?, 'handoff-result','manual','completed','https://docs.example.test/master.pdf')", (job_id,))
        normalize_n8n_artifacts(connection)
        connection.commit()
    finally:
        connection.close()
    designate_master_resume(job_id=job_id, reference="https://docs.example.test/master.pdf", label="Master", source_label="test")
    save_answer(question_family="candidate_fact", canonical_answer="private answer", source="user", user_confirmed=True, confidence=1, autofill_allowed=True)
    return prepare_application(job_id=job_id, application_id="application-1", idempotency_key="preparation-key", opportunity={"role_family":"HR", "company_name":"Example Co", "location":"New York", "workplace":"remote", "salary_max":100000, "skill_fit":.8, "evidence_backed_experience_fit":.8, "career_direction_fit":.8, "eligibility_fit":1}, answer_requests=[{"question_family":"candidate_fact"}], eligibility="PASS", account_state="CREATABLE", permissions="PASS", duplicate_application="NO", job_open="YES")


def test_disabled_default_and_handoff_is_inert(hunter_db, monkeypatch) -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        create_handoff(preparation_id="x", idempotency_key="x")
    _enable(monkeypatch)
    monkeypatch.delenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET")
    with pytest.raises(RuntimeError, match="secret"):
        create_handoff(preparation_id="x", idempotency_key="x")
    connection = database.get_connection()
    try:
        assert connection.execute("SELECT COUNT(*) FROM apply_preparation_handoffs").fetchone()[0] == 0
    finally:
        connection.close()
    monkeypatch.setenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET", "test-only-handoff-secret-not-production")
    preparation = _preparation()
    handoff = create_handoff(preparation_id=preparation["preparation_id"], idempotency_key="handoff-key")
    assert handoff["state"] == "READY_TO_APPLY"
    assert handoff["provider"] == "GREENHOUSE"
    assert "private answer" not in str(handoff["payload"])
    accepted = accept_handoff(payload=handoff["payload"], signature=handoff["signature"])
    assert accepted["state"] == "HANDOFF_ACCEPTED"
    assert "SUBMITTED" not in str(accepted)
    from app.apply_handoff import sign_transport
    signed = sign_transport(handoff["payload"], timestamp=1_700_000_000)
    assert signed.headers["X-Munshi-Event-Id"] == handoff["payload"]["handoff_id"]
    assert signed.headers["X-Munshi-Content-SHA256"] == hashlib.sha256(signed.body).hexdigest()
    expected = hmac.new(b"test-only-handoff-secret-not-production", f"{handoff['payload']['handoff_id']}.1700000000.{signed.headers['X-Munshi-Content-SHA256']}".encode(), hashlib.sha256).hexdigest()
    assert signed.headers["X-Munshi-Signature"] == f"sha256={expected}"


def test_signature_malformed_replay_and_tenant_rejection(hunter_db, monkeypatch) -> None:
    _enable(monkeypatch, tenants=True)
    preparation = _preparation()
    handoff = create_handoff(preparation_id=preparation["preparation_id"], idempotency_key="handoff-key")
    with pytest.raises(PermissionError, match="signature"):
        accept_handoff(payload=handoff["payload"], signature="bad")
    malformed = dict(handoff["payload"])
    malformed["unexpected"] = True
    from app.apply_handoff import sign_payload
    with pytest.raises(ValueError, match="malformed"):
        accept_handoff(payload=malformed, signature=sign_payload(malformed))
    assert accept_handoff(payload=handoff["payload"], signature=handoff["signature"])["state"] == "HANDOFF_ACCEPTED"
    assert accept_handoff(payload=handoff["payload"], signature=handoff["signature"])["state"] == "HANDOFF_ACCEPTED"
    connection = database.get_connection()
    try:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES ('team-b','B')")
        connection.execute("INSERT INTO app_users(user_id,display_name) VALUES ('member-b','B')")
        connection.execute("INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES ('team-b','member-b','member')")
        connection.commit()
    finally:
        connection.close()
    with owner_context(tenant_id="team-b", user_id="member-b"):
        with pytest.raises(PermissionError, match="owner"):
            accept_handoff(payload=handoff["payload"], signature=handoff["signature"])


def test_local_contract_has_no_transport_or_authority_imports_and_migration_is_additive(tmp_path) -> None:
    source = (Path(__file__).resolve().parent.parent / "app" / "apply_handoff.py").read_text(encoding="utf-8")
    imports = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) and node.module}
    assert not {"app.n8n_dispatch", "app.gmail_integration", "app.api", "app.product_state", "requests"} & imports
    connection = sqlite3.connect(tmp_path / "upgraded.sqlite")
    try:
        importlib.import_module("migrations.024_apply_preparation_handoff").apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert "apply_preparation_handoffs" in tables
