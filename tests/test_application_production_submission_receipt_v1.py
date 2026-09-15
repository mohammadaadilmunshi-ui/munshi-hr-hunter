"""Production receipt ingestion: verification-gated, binding-checked, exactly-once.

`SUBMITTED` in Hunter is derived solely from a row in this ledger, so these tests are
what prevent the dashboard from claiming a submission that was never verified.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3

import pytest

from app import application_production_submission_receipt_v1 as receipts
from app import application_submit_authorization_v1 as auth
from app import database

AUTH_SECRET = "test-only-submit-authority-secret-not-a-real-key-0123456789"
RECEIPT_SECRET = "test-only-production-receipt-secret-not-a-real-key-0123456789"


@pytest.fixture(autouse=True)
def receipt_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test-only key material; never sourced from a real environment file."""
    monkeypatch.setenv("MUNSHI_PRODUCTION_SUBMIT_AUTH_HMAC_SECRET", AUTH_SECRET)
    monkeypatch.setenv("MUNSHI_PRODUCTION_RECEIPT_HMAC_SECRET", RECEIPT_SECRET)


@pytest.fixture
def receipt_db(hunter_db, monkeypatch: pytest.MonkeyPatch):
    def ensure(connection: sqlite3.Connection | None = None) -> None:
        owns = connection is None
        connection = connection or database.get_connection()
        try:
            # Minimal FK parents first: the production ledgers are created lazily in
            # the running app, so the migration-owned parents may not exist yet.
            connection.execute(
                """CREATE TABLE IF NOT EXISTS application_plans_v2 (
                       plan_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                       user_id TEXT NOT NULL, application_id TEXT NOT NULL,
                       job_id INTEGER NOT NULL,
                       provider TEXT NOT NULL, status TEXT NOT NULL,
                       executable INTEGER NOT NULL, plan_digest TEXT NOT NULL,
                       snapshot_json TEXT NOT NULL)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS application_plan_revisions_v2 (
                       prior_plan_id TEXT NOT NULL, replacement_plan_id TEXT)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS application_reviews_v2 (
                       review_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                       user_id TEXT NOT NULL, application_id TEXT NOT NULL,
                       plan_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                       review_version TEXT NOT NULL, plan_digest TEXT NOT NULL,
                       prepared_package_digest TEXT NOT NULL,
                       browser_form_digest TEXT NOT NULL,
                       resume_artifact_sha256 TEXT NOT NULL,
                       review_digest TEXT NOT NULL, review_json TEXT NOT NULL,
                       invalidated_at TEXT, invalidation_reason TEXT,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS application_review_approvals_v2 (
                       approval_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                       user_id TEXT NOT NULL, review_id TEXT NOT NULL,
                       idempotency_key TEXT NOT NULL, approval_version TEXT NOT NULL,
                       review_digest TEXT NOT NULL, plan_digest TEXT NOT NULL,
                       prepared_package_digest TEXT NOT NULL,
                       browser_form_digest TEXT NOT NULL, approval_digest TEXT NOT NULL,
                       approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       consumed_at TEXT, consumption_key TEXT)"""
            )
            for statement in auth.SCHEMA_STATEMENTS + receipts.SCHEMA_STATEMENTS:
                connection.execute(statement)
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    # The authority + receipt ledgers are created lazily in production; create them
    # explicitly here so the FKs to plan/review/approval rows resolve.
    monkeypatch.setattr(auth, "ensure_schema", ensure)
    monkeypatch.setattr(receipts, "ensure_schema", ensure)
    return ensure


def _receipt(**overrides) -> dict:
    requested_receipt_id = overrides.pop("receipt_id", None)
    body = {
        "version": receipts.RECEIPT_VERSION,
        "tenant_id": "default",
        "user_id": "local-owner",
        "application_id": "application-1",
        "authorization_id": "submit-auth-0000000000000000000000000000000000000001",
        "authority_digest": "a" * 64,
        "claim_digest": "b" * 64,
        "plan_id": "application-plan-1",
        "session_id": "apply-session-1",
        "review_id": "review-v2-" + "b" * 32,
        "approval_id": "review-approval-1",
        "provider": "GREENHOUSE",
        "provider_application_id": "gh-555123",
        "target_url": "https://boards.greenhouse.io/example/jobs/42",
        "verification_method": "CONFIRMATION_ROUTE_AND_PROVIDER_ID",
        "verification_evidence_digest": "c" * 64,
        "verification_status": "VERIFIED",
        "synthetic": False,
        "verified_at": "2026-09-14T00:10:00+00:00",
    }
    body.update(overrides)
    material = dict(body)
    receipt_digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    body["receipt_id"] = (
        str(requested_receipt_id)
        if requested_receipt_id is not None
        else "production-receipt-" + receipt_digest[:32]
    )
    body["receipt_digest"] = receipt_digest
    body["signature"] = (
        "sha256="
        + hmac.new(
            RECEIPT_SECRET.encode(), receipt_digest.encode(), hashlib.sha256
        ).hexdigest()
    )
    return body


def test_only_verified_non_synthetic_receipts_verify() -> None:
    assert receipts.verify_receipt(_receipt())["receipt_digest"]
    for field, value in (
        ("verification_status", "SUBMISSION_UNVERIFIED"),
        ("synthetic", True),
        ("version", "munshi-synthetic-submission-receipt-v1"),
    ):
        forged = _receipt()
        forged[field] = value
        with pytest.raises(PermissionError):
            receipts.verify_receipt(forged)


def test_receipt_digest_and_signature_are_tamper_evident() -> None:
    receipt = _receipt()
    tampered = dict(receipt)
    tampered["provider_application_id"] = "gh-999999"
    with pytest.raises(PermissionError):
        receipts.verify_receipt(tampered)

    unsigned = dict(receipt)
    unsigned["signature"] = "sha256=" + "0" * 64
    with pytest.raises(PermissionError):
        receipts.verify_receipt(unsigned)


def test_receipt_requires_a_currently_claimed_authorization(receipt_db) -> None:
    """A receipt can never be the thing that authorises a submission."""
    with pytest.raises(PermissionError, match="CLAIMED submit authorization"):
        receipts.ingest_receipt(_receipt())


def test_verified_receipt_consumes_the_claim_exactly_once(receipt_db, monkeypatch) -> None:
    """Drive the real authority ledger to CLAIMED, then ingest its receipt."""
    issued = {
        "version": auth.AUTH_VERSION,
        "authorization_id": "submit-auth-0000000000000000000000000000000000000001",
        "tenant_id": "default",
        "user_id": "local-owner",
        "application_id": "application-1",
        "plan_id": "application-plan-1",
        "session_id": "apply-session-1",
        "review_id": "review-v2-" + "b" * 32,
        "approval_id": "review-approval-1",
        "provider": "GREENHOUSE",
        "target_url": "https://boards.greenhouse.io/example/jobs/42",
        "checkpoint_id": "checkpoint-1",
        "generation": 1,
        "plan_digest": "1" * 64,
        "review_digest": "2" * 64,
        "approval_digest": "3" * 64,
        "prepared_package_digest": "4" * 64,
        "browser_form_digest": "5" * 64,
        "resume_sha256": "6" * 64,
        "cover_letter_sha256": None,
        "issued_at": "2026-09-14T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "synthetic": False,
        "submission_authority": True,
    }
    digest = hashlib.sha256(
        json.dumps(
            dict(issued), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()
    envelope = dict(issued)
    envelope["authority_digest"] = digest
    envelope["signature"] = (
        "sha256="
        + hmac.new(AUTH_SECRET.encode(), digest.encode(), hashlib.sha256).hexdigest()
    )

    connection = database.get_connection()
    try:
        receipt_db(connection)
        connection.execute("DELETE FROM application_submit_authorizations_v1")
        # The authority ledger declares FKs to plan/review/approval, so the referenced
        # rows must exist for a real insert to succeed.
        connection.execute(
            """INSERT OR IGNORE INTO jobs(
                   id,job_fingerprint,source,company_name,title,apply_url
               ) VALUES (
                   42,'production-receipt-job-42','test','Example','Analyst',
                   'https://boards.greenhouse.io/example/jobs/42'
               )"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO application_plans_v2
                   (plan_id,tenant_id,user_id,application_id,job_id,provider,status,
                    executable,plan_digest,snapshot_json)
               VALUES ('application-plan-1','default','local-owner','application-1',
                       42,'GREENHOUSE','READY_TO_APPLY',1,:plan_digest,'{}')""",
            {"plan_digest": "1" * 64},
        )
        connection.execute(
            """INSERT OR IGNORE INTO application_reviews_v2
                   (review_id,tenant_id,user_id,application_id,plan_id,idempotency_key,
                    review_version,plan_digest,prepared_package_digest,
                    browser_form_digest,resume_artifact_sha256,review_digest,review_json)
               VALUES (:review_id,'default','local-owner','application-1',
                       'application-plan-1','review-key','munshi-application-review-v2',
                       :plan_digest,:prepared,:form,:resume,:review_digest,'{}')""",
            {
                "review_id": envelope["review_id"],
                "plan_digest": "1" * 64,
                "prepared": "4" * 64,
                "form": "5" * 64,
                "resume": "6" * 64,
                "review_digest": "2" * 64,
            },
        )
        connection.execute(
            """INSERT OR IGNORE INTO application_review_approvals_v2
                   (approval_id,tenant_id,user_id,review_id,idempotency_key,
                    approval_version,review_digest,plan_digest,
                    prepared_package_digest,browser_form_digest,approval_digest)
               VALUES ('review-approval-1','default','local-owner',:review_id,
                       'approval-key','munshi-application-review-approval-v2',
                       :review_digest,:plan_digest,:prepared,:form,:approval_digest)""",
            {
                "review_id": envelope["review_id"],
                "review_digest": "2" * 64,
                "plan_digest": "1" * 64,
                "prepared": "4" * 64,
                "form": "5" * 64,
                "approval_digest": "3" * 64,
            },
        )
        connection.execute(
            """INSERT INTO application_submit_authorizations_v1(
                 authorization_id,tenant_id,user_id,application_id,plan_id,session_id,
                 review_id,approval_id,provider,target_url,checkpoint_id,generation,
                 plan_digest,review_digest,approval_digest,prepared_package_digest,
                 browser_form_digest,resume_sha256,cover_letter_sha256,authority_digest,
                 authority_json,status,issued_at,expires_at,claimed_at,claimed_by,
                 claim_digest)
               VALUES (:authorization_id,:tenant_id,:user_id,:application_id,:plan_id,
                       :session_id,:review_id,:approval_id,:provider,:target_url,
                       :checkpoint_id,:generation,:plan_digest,:review_digest,
                       :approval_digest,:prepared_package_digest,:browser_form_digest,
                       :resume_sha256,:cover_letter_sha256,:authority_digest,
                       :authority_json,'CLAIMED',:issued_at,:expires_at,:claimed_at,
                       :claimed_by,:claim_digest)""",
            {
                "authorization_id": envelope["authorization_id"],
                "tenant_id": "default",
                "user_id": "local-owner",
                "application_id": "application-1",
                "plan_id": "application-plan-1",
                "session_id": "apply-session-1",
                "review_id": envelope["review_id"],
                "approval_id": "review-approval-1",
                "provider": "GREENHOUSE",
                "target_url": "https://boards.greenhouse.io/example/jobs/42",
                "checkpoint_id": "checkpoint-1",
                "generation": 1,
                "plan_digest": "1" * 64,
                "review_digest": "2" * 64,
                "approval_digest": "3" * 64,
                "prepared_package_digest": "4" * 64,
                "browser_form_digest": "5" * 64,
                "resume_sha256": "6" * 64,
                "cover_letter_sha256": None,
                "authority_digest": digest,
                "authority_json": json.dumps(
                    envelope, sort_keys=True, separators=(",", ":")
                ),
                "issued_at": "2026-09-14T00:00:00+00:00",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "claimed_at": "2026-09-14T00:01:00+00:00",
                "claimed_by": "apply-worker-1",
                "claim_digest": "b" * 64,
            },
        )
        connection.commit()
    finally:
        connection.close()

    receipt = _receipt(authority_digest=digest)
    result = receipts.ingest_receipt(receipt)
    assert result["status"] == "INGESTED" and result["replayed"] is False
    assert result["application_state"] == "SUBMITTED"

    # Replay of the identical receipt is acknowledged, never re-counted.
    replay = receipts.ingest_receipt(receipt)
    assert replay["replayed"] is True

    connection = database.get_connection()
    try:
        rows = connection.execute(
            "SELECT COUNT(*) AS n FROM production_submission_receipts_v1 WHERE authorization_id=?",
            (envelope["authorization_id"],),
        ).fetchone()["n"]
        status = connection.execute(
            "SELECT status FROM application_submit_authorizations_v1 WHERE authorization_id=?",
            (envelope["authorization_id"],),
        ).fetchone()["status"]
        lifecycle_state = connection.execute(
            "SELECT state FROM application_lifecycle_records WHERE application_id=?",
            ("application-1",),
        ).fetchone()["state"]
        already_applied = connection.execute(
            "SELECT already_applied FROM jobs WHERE id=42"
        ).fetchone()["already_applied"]
    finally:
        connection.close()
    assert rows == 1
    assert lifecycle_state == "SUBMITTED"
    assert already_applied == 1
    # A second, differently-shaped receipt can no longer consume the claim.
    assert status == "CONSUMED"
    with pytest.raises(PermissionError, match="CLAIMED submit authorization"):
        receipts.ingest_receipt(
            _receipt(verification_evidence_digest="d" * 64)
        )


def test_receipt_binding_mismatch_is_refused(receipt_db) -> None:
    with pytest.raises(PermissionError):
        receipts.ingest_receipt(_receipt(user_id="someone-else"))


def test_required_fields_are_enforced(receipt_db) -> None:
    for key in ("provider_application_id", "claim_digest", "verified_at"):
        missing = _receipt()
        missing[key] = ""
        with pytest.raises((PermissionError, ValueError)):
            receipts.ingest_receipt(missing)
