from __future__ import annotations

import copy
import importlib
import sqlite3
from types import SimpleNamespace

import pytest

from app import application_submit_command_v1 as commands
from app import application_review_v2 as reviews
from app import database


def _plan() -> dict:
    return {"plan_id":"plan-1","tenant_id":"default","user_id":"owner","application_id":"app-1","provider":"GREENHOUSE","plan_digest":"a"*64,"status":"READY_TO_APPLY","executable":True,"submission_authority":False,"snapshot":{"application_id":"app-1","job":{"id":7,"apply_url":"https://fixture.invalid/app"},"resume":{"artifact_sha256":"c"*64,"filename":"r.pdf","artifact_id":"r","version_id":"v","source_bindings":{"job":{}}},"answers":[],"provider_policy":{},"expected_state":"READY_TO_APPLY","executable":True,"submission_authority":False}}


@pytest.fixture
def authority(hunter_db, monkeypatch):
    plan = _plan(); owner = SimpleNamespace(tenant_id="default", user_id="owner")
    monkeypatch.setenv(reviews.REVIEW_ENV, "true"); monkeypatch.setenv(commands.COMMAND_ENV, "true"); monkeypatch.setenv(commands.COMMAND_SECRET_ENV, "x" * 32)
    for module in (reviews, commands): monkeypatch.setattr(module, "current_owner", lambda _c: owner)
    monkeypatch.setattr(reviews.application_plan_v2, "get_application_plan", lambda _id: copy.deepcopy(plan)); monkeypatch.setattr(reviews.application_plan_v2, "application_plan_freshness", lambda _id: {"fresh": True}); monkeypatch.setattr(reviews.application_plan_v2, "runtime_plan_is_superseded", lambda _id: False)
    monkeypatch.setattr(commands.application_plan_v2, "application_plan_freshness", lambda _id: {"fresh": True}); monkeypatch.setattr(commands.application_plan_v2, "runtime_plan_is_superseded", lambda _id: False)

    def review_schema(connection=None):
        owns = connection is None; connection = connection or database.get_connection()
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS application_reviews_v2 (review_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,user_id TEXT NOT NULL,application_id TEXT NOT NULL,plan_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,review_version TEXT NOT NULL,plan_digest TEXT NOT NULL,prepared_package_digest TEXT NOT NULL,browser_form_digest TEXT NOT NULL,resume_artifact_sha256 TEXT NOT NULL,review_digest TEXT NOT NULL,review_json TEXT NOT NULL,invalidated_at TEXT,invalidation_reason TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(tenant_id,user_id,idempotency_key),UNIQUE(tenant_id,user_id,review_digest))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS application_review_approvals_v2 (approval_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,user_id TEXT NOT NULL,review_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,approval_version TEXT NOT NULL,review_digest TEXT NOT NULL,plan_digest TEXT NOT NULL,prepared_package_digest TEXT NOT NULL,browser_form_digest TEXT NOT NULL,approval_digest TEXT NOT NULL,approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,consumed_at TEXT,consumption_key TEXT,UNIQUE(tenant_id,user_id,review_id),UNIQUE(tenant_id,user_id,idempotency_key),UNIQUE(tenant_id,user_id,approval_digest))""")
            if owns: connection.commit()
        finally:
            if owns: connection.close()

    monkeypatch.setattr(reviews, "ensure_schema", review_schema)
    connection = sqlite3.connect(hunter_db)
    connection.execute("INSERT OR IGNORE INTO tenants(tenant_id,display_name) VALUES('default','Default')")
    connection.execute("INSERT OR IGNORE INTO app_users(user_id,display_name) VALUES('owner','Owner')")
    connection.execute("INSERT OR IGNORE INTO tenant_memberships(tenant_id,user_id,role) VALUES('default','owner','owner')")
    connection.execute("CREATE TABLE IF NOT EXISTS staging_synthetic_jobs(job_id INTEGER,environment TEXT,synthetic INTEGER,is_test_data INTEGER,source TEXT,external_actions_disabled INTEGER)")
    connection.execute("DELETE FROM staging_synthetic_jobs WHERE job_id=7")
    connection.execute("INSERT OR REPLACE INTO jobs(id,job_fingerprint,source,company_name,title,apply_url) VALUES(7,'fixture-job-7','staging_fixture','Fixture','Fixture role','https://fixture.invalid/app')")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(staging_synthetic_jobs)")}
    if "fixture_key" in columns:
        connection.execute("INSERT INTO staging_synthetic_jobs(job_id,fixture_key,fixture_version,environment,synthetic,is_test_data,source,external_actions_disabled,scenario_json) VALUES(7,'submit-command','test','staging',1,1,'staging_fixture',1,'{}')")
    else:
        connection.execute("INSERT INTO staging_synthetic_jobs VALUES(7,'staging',1,1,'staging_fixture',1)")
    connection.commit(); connection.close()
    return plan


def _review_and_approval():
    review = reviews.create_review(plan_id="plan-1", idempotency_key="review", prepared_package={"application_id":"app-1","provider":"GREENHOUSE","destination_url":"https://fixture.invalid/app","browser_form_digest":"b"*64,"resume_sha256":"c"*64,"plan_digest":"a"*64,"required_fields":0,"completed_required_fields":0,"unresolved_count":0,"validation_errors":[],"checkpoint_id":"checkpoint"})
    approval = reviews.approve_review(review_id=review["review_id"], idempotency_key="approval", expected_review_digest=review["review_digest"], expected_prepared_package_digest=review["prepared_package_digest"], expected_browser_form_digest=review["browser_form_digest"])
    return review, approval


def test_issues_exact_replay_only_for_owned_synthetic_fixture(authority):
    review, approval = _review_and_approval()
    kwargs = dict(review_id=review["review_id"], approval_id=approval["approval_id"], idempotency_key="command", expected_review_digest=review["review_digest"], expected_approval_digest=approval["approval_digest"], expected_plan_digest="a"*64, expected_prepared_package_digest=review["prepared_package_digest"], expected_browser_form_digest="b"*64, now=100)
    first = commands.issue_submit_command(**kwargs); second = commands.issue_submit_command(**kwargs)
    assert first["envelope"]["submission_authority"] is True
    assert first["envelope"]["target_url"].endswith(".invalid/app")
    assert second["replayed"] is True and second["command_id"] == first["command_id"]
    assert len(first["signature"]) == 64 and second["signature"] == first["signature"]
    assert "answers" not in first["envelope"]
    with pytest.raises(ValueError, match="different bindings"):
        commands.issue_submit_command(**{**kwargs, "expected_plan_digest": "d" * 64})


def test_tampered_stored_command_fails_closed(authority, hunter_db):
    review, approval = _review_and_approval()
    kwargs = dict(review_id=review["review_id"], approval_id=approval["approval_id"], idempotency_key="command", expected_review_digest=review["review_digest"], expected_approval_digest=approval["approval_digest"], expected_plan_digest="a"*64, expected_prepared_package_digest=review["prepared_package_digest"], expected_browser_form_digest="b"*64)
    commands.issue_submit_command(**kwargs)
    connection = sqlite3.connect(hunter_db)
    connection.execute("UPDATE application_submit_commands_v1 SET envelope_json='{}' WHERE tenant_id='default' AND user_id='owner'")
    connection.commit(); connection.close()
    with pytest.raises(RuntimeError, match="body digest"):
        commands.issue_submit_command(**kwargs)


def test_default_off_and_changed_command_binding_fail(authority, monkeypatch):
    review, approval = _review_and_approval(); monkeypatch.delenv(commands.COMMAND_ENV)
    kwargs = dict(review_id=review["review_id"], approval_id=approval["approval_id"], idempotency_key="command", expected_review_digest=review["review_digest"], expected_approval_digest=approval["approval_digest"], expected_plan_digest="a"*64, expected_prepared_package_digest=review["prepared_package_digest"], expected_browser_form_digest="b"*64)
    with pytest.raises(RuntimeError): commands.issue_submit_command(**kwargs)
    monkeypatch.setenv(commands.COMMAND_ENV, "true")
    with pytest.raises(ValueError): commands.issue_submit_command(**{**kwargs, "expected_plan_digest":"d"*64})


def test_fixture_target_mismatch_fails(authority, hunter_db):
    review, approval = _review_and_approval()
    connection = sqlite3.connect(hunter_db)
    connection.execute("UPDATE jobs SET apply_url='https://other.invalid/app' WHERE id=7")
    connection.commit(); connection.close()
    with pytest.raises(PermissionError, match="synthetic fixture"):
        commands.issue_submit_command(review_id=review["review_id"], approval_id=approval["approval_id"], idempotency_key="command", expected_review_digest=review["review_digest"], expected_approval_digest=approval["approval_digest"], expected_plan_digest="a"*64, expected_prepared_package_digest=review["prepared_package_digest"], expected_browser_form_digest="b"*64)


def test_migration_is_additive(hunter_db):
    connection = sqlite3.connect(hunter_db)
    try:
        importlib.import_module("migrations.047_synthetic_submit_commands_v1").apply(connection)
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='application_submit_commands_v1'").fetchone()
    finally:
        connection.close()
