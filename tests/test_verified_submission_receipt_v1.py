from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app import application_submission_receipt_v1 as receipts
from app import application_submit_command_v1 as commands
from app import database


OWNER = SimpleNamespace(tenant_id="default", user_id="owner")


def _command_schema(connection=None):
    owns = connection is None
    connection = connection or database.get_connection()
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS application_submit_commands_v1 (
                command_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                approval_id TEXT NOT NULL,
                review_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                application_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                checkpoint_id TEXT NOT NULL,
                fixture_job_id INTEGER NOT NULL,
                target_url TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                review_digest TEXT NOT NULL,
                approval_digest TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                prepared_package_digest TEXT NOT NULL,
                browser_form_digest TEXT NOT NULL,
                resume_sha256 TEXT NOT NULL,
                cover_letter_sha256 TEXT,
                expires_at INTEGER NOT NULL,
                envelope_json TEXT NOT NULL,
                body_sha256 TEXT NOT NULL,
                signature TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tenant_id,user_id,idempotency_key),
                UNIQUE(tenant_id,user_id,approval_id)
            )"""
        )
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _canonical_command(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


@pytest.fixture()
def authority(hunter_db, monkeypatch):
    monkeypatch.setattr(receipts, "current_owner", lambda _connection: OWNER)
    monkeypatch.setattr(commands, "ensure_schema", _command_schema)
    monkeypatch.setenv(commands.COMMAND_SECRET_ENV, "x" * 32)

    connection = database.get_connection()
    try:
        # Test setup only: create the monkeypatched submit-command table
        # before seeding the synthetic command fixture.
        _command_schema(connection)
        connection.execute(
            """INSERT OR IGNORE INTO tenants(tenant_id,display_name)
               VALUES('default','Default')"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO app_users(user_id,display_name)
               VALUES('owner','Owner')"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO tenant_memberships(tenant_id,user_id,role)
               VALUES('default','owner','owner')"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS staging_synthetic_jobs(
                job_id INTEGER,
                environment TEXT,
                synthetic INTEGER,
                is_test_data INTEGER,
                source TEXT,
                external_actions_disabled INTEGER
            )"""
        )
        connection.execute("DELETE FROM staging_synthetic_jobs WHERE job_id=7")
        connection.execute(
            """INSERT OR REPLACE INTO jobs(
                id,job_fingerprint,source,company_name,title,apply_url,already_applied
            ) VALUES(
                7,'receipt-fixture-7','staging_fixture','Fixture','Fixture role',
                'https://fixture.invalid/app',0
            )"""
        )
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(staging_synthetic_jobs)"
            ).fetchall()
        }
        if "fixture_key" in columns:
            connection.execute(
                """INSERT INTO staging_synthetic_jobs(
                    job_id,fixture_key,fixture_version,environment,synthetic,
                    is_test_data,source,external_actions_disabled,scenario_json
                ) VALUES(
                    7,'verified-receipt','test','staging',1,1,
                    'staging_fixture',1,'{}'
                )"""
            )
        else:
            connection.execute(
                """INSERT INTO staging_synthetic_jobs
                   VALUES(7,'staging',1,1,'staging_fixture',1)"""
            )

        envelope = {
            "version": commands.COMMAND_VERSION,
            "purpose": commands.COMMAND_PURPOSE,
            "command_id": "synthetic-submit-command-test",
            "tenant_id": "default",
            "user_id": "owner",
            "application_id": "app-1",
            "plan_id": "plan-1",
            "session_id": "session-1",
            "provider": "GREENHOUSE",
            "review_id": "review-1",
            "approval_id": "approval-1",
            "synthetic": True,
            "submission_authority": True,
            "fixture_job_id": 7,
            "target_url": "https://fixture.invalid/app",
            "checkpoint_id": "checkpoint-1",
            "review_digest": "b" * 64,
            "approval_digest": "c" * 64,
            "plan_digest": "a" * 64,
            "prepared_package_digest": "d" * 64,
            "browser_form_digest": "e" * 64,
            "resume_sha256": "f" * 64,
            "cover_letter_sha256": None,
            "issued_at": 100,
            "expires_at": 400,
        }
        body = _canonical_command(envelope)
        body_sha = hashlib.sha256(body).hexdigest()
        signature = hmac.new(b"x" * 32, body, hashlib.sha256).hexdigest()
        connection.execute(
            """INSERT INTO application_submit_commands_v1(
                command_id,tenant_id,user_id,approval_id,review_id,plan_id,
                application_id,provider,checkpoint_id,fixture_job_id,target_url,
                idempotency_key,review_digest,approval_digest,plan_digest,
                prepared_package_digest,browser_form_digest,resume_sha256,
                cover_letter_sha256,expires_at,envelope_json,body_sha256,signature
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                envelope["command_id"],
                "default",
                "owner",
                "approval-1",
                "review-1",
                "plan-1",
                "app-1",
                "GREENHOUSE",
                "checkpoint-1",
                7,
                "https://fixture.invalid/app",
                "command-key",
                "b" * 64,
                "c" * 64,
                "a" * 64,
                "d" * 64,
                "e" * 64,
                "f" * 64,
                None,
                400,
                body.decode(),
                body_sha,
                signature,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    receipts.ensure_schema()
    return hunter_db


def _receipt(**changes):
    value = {
        "version": receipts.RECEIPT_VERSION,
        "command_id": "synthetic-submit-command-test",
        "application_id": "app-1",
        "plan_id": "plan-1",
        "session_id": "session-1",
        "review_id": "review-1",
        "approval_id": "approval-1",
        "provider": "GREENHOUSE",
        "provider_application_id": "provider-app-123",
        "submitted_at": "2026-09-09T12:00:00+00:00",
        "verified_at": "2026-09-09T12:00:03+00:00",
        "verification_method": receipts.VERIFICATION_METHOD,
        "verification_attempt_id": "attempt-1",
        "verification_evidence_digest": "1" * 64,
        "plan_digest": "a" * 64,
        "review_digest": "b" * 64,
        "approval_digest": "c" * 64,
        "prepared_package_digest": "d" * 64,
        "browser_form_digest": "e" * 64,
        "resume_sha256": "f" * 64,
        "cover_letter_sha256": None,
        "answers_digest": "2" * 64,
        "execution_result_digest": "3" * 64,
        "execution_chain_digest": "4" * 64,
        "verification_status": "VERIFIED",
    }
    value.update(changes)
    return value


def _ingest(value):
    digest = receipts.receipt_digest(value)
    return receipts.ingest_verified_receipt(
        receipt_id="synthetic-submission-receipt-" + digest[:32],
        receipt_digest_value=digest,
        receipt=value,
    )


def test_verified_receipt_is_exact_once_crm_authority(authority):
    value = _receipt()
    first = _ingest(value)
    second = _ingest(value)

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["state"] == second["state"] == "AWAITING_RESPONSE"
    assert first["submission_authority"] is False
    assert first["crm_authority"] is True

    connection = database.get_connection()
    try:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM verified_submission_receipts_v1"
            ).fetchone()[0]
            == 1
        )
        states = [
            row[0]
            for row in connection.execute(
                """SELECT state FROM application_lifecycle_events_v1
                   WHERE application_id='app-1' ORDER BY rowid"""
            ).fetchall()
        ]
        assert states == ["VERIFIED", "AWAITING_RESPONSE"]
        lifecycle = connection.execute(
            """SELECT state,verified_receipt_id,state_version
               FROM application_lifecycle_records WHERE application_id='app-1'"""
        ).fetchone()
        assert lifecycle[0] == "AWAITING_RESPONSE"
        assert lifecycle[2] == 2
        assert (
            connection.execute(
                "SELECT already_applied FROM jobs WHERE id=7"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_unverified_and_digest_tampering_fail_closed(authority):
    with pytest.raises(ValueError, match="VERIFIED"):
        _ingest(_receipt(verification_status="SUBMITTED"))

    original = _receipt()
    digest = receipts.receipt_digest(original)
    tampered = dict(original)
    tampered["answers_digest"] = "9" * 64
    with pytest.raises(ValueError, match="digest mismatch"):
        receipts.ingest_verified_receipt(
            receipt_id="synthetic-submission-receipt-" + digest[:32],
            receipt_digest_value=digest,
            receipt=tampered,
        )


def test_command_binding_and_owner_mismatch_fail_closed(authority, monkeypatch):
    with pytest.raises(ValueError, match="plan_digest binding"):
        _ingest(_receipt(plan_digest="9" * 64))

    monkeypatch.setattr(
        receipts,
        "current_owner",
        lambda _connection: SimpleNamespace(tenant_id="default", user_id="other"),
    )
    with pytest.raises(PermissionError, match="no owned Hunter submit-command"):
        _ingest(_receipt())


def test_conflicting_replay_for_same_command_is_rejected(authority):
    _ingest(_receipt())
    conflicting = _receipt(
        verification_attempt_id="attempt-2",
        verification_evidence_digest="8" * 64,
    )
    with pytest.raises(ValueError, match="Conflicting verified receipt replay"):
        _ingest(conflicting)


def test_provider_application_identity_cannot_be_rebound(authority):
    first = _receipt()
    _ingest(first)

    connection = database.get_connection()
    try:
        # Seed a second independent Hunter command for another application but
        # deliberately reuse the provider application identity.
        row = connection.execute(
            """SELECT * FROM application_submit_commands_v1
               WHERE command_id='synthetic-submit-command-test'"""
        ).fetchone()
        copied = dict(row)
        envelope = json.loads(copied["envelope_json"])
        envelope.update(
            {
                "command_id": "synthetic-submit-command-two",
                "application_id": "app-2",
                "session_id": "session-2",
                "review_id": "review-2",
                "approval_id": "approval-2",
            }
        )
        body = _canonical_command(envelope)
        copied["body_sha256"] = hashlib.sha256(body).hexdigest()
        copied["signature"] = hmac.new(b"x" * 32, body, hashlib.sha256).hexdigest()
        copied["envelope_json"] = body.decode()
        connection.execute(
            """INSERT INTO application_submit_commands_v1(
                command_id,tenant_id,user_id,approval_id,review_id,plan_id,
                application_id,provider,checkpoint_id,fixture_job_id,target_url,
                idempotency_key,review_digest,approval_digest,plan_digest,
                prepared_package_digest,browser_form_digest,resume_sha256,
                cover_letter_sha256,expires_at,envelope_json,body_sha256,signature
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "synthetic-submit-command-two",
                "default",
                "owner",
                "approval-2",
                "review-2",
                "plan-1",
                "app-2",
                "GREENHOUSE",
                "checkpoint-1",
                7,
                "https://fixture.invalid/app",
                "command-key-two",
                "b" * 64,
                "c" * 64,
                "a" * 64,
                "d" * 64,
                "e" * 64,
                "f" * 64,
                None,
                400,
                copied["envelope_json"],
                copied["body_sha256"],
                copied["signature"],
            ),
        )
        connection.commit()
    finally:
        connection.close()

    second = _receipt(
        command_id="synthetic-submit-command-two",
        application_id="app-2",
        session_id="session-2",
        review_id="review-2",
        approval_id="approval-2",
    )
    with pytest.raises(ValueError, match="Provider application identity"):
        _ingest(second)


def test_migration_048_is_additive(authority):
    connection = sqlite3.connect(authority)
    try:
        importlib.import_module(
            "migrations.048_verified_submission_receipts_v1"
        ).apply(connection)
        for table in (
            "verified_submission_receipts_v1",
            "application_lifecycle_records",
            "application_lifecycle_events_v1",
        ):
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
    finally:
        connection.close()
