from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import application_lifecycle_authority_v1 as lifecycle
from app import database


OWNER = SimpleNamespace(tenant_id="default", user_id="owner")


@pytest.fixture()
def lifecycle_db(hunter_db, monkeypatch):
    monkeypatch.setattr(lifecycle, "current_owner", lambda _connection: OWNER)

    connection = database.get_connection()
    try:
        connection.execute(
            "INSERT OR IGNORE INTO tenants(tenant_id,display_name) VALUES('default','Default')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO app_users(user_id,display_name) VALUES('owner','Owner')"
        )
        connection.execute(
            """INSERT OR IGNORE INTO tenant_memberships(tenant_id,user_id,role)
               VALUES('default','owner','owner')"""
        )
        connection.execute(
            """INSERT OR REPLACE INTO jobs(
                   id,job_fingerprint,source,company_name,title,location_raw,apply_url
               ) VALUES(
                   41,'lifecycle-job-41','fixture','Acme','People Analyst',
                   'New York, NY','https://fixture.invalid/acme'
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS verified_submission_receipts_v1(
                receipt_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                receipt_digest TEXT NOT NULL,
                plan_id TEXT,
                review_id TEXT,
                approval_id TEXT,
                resume_sha256 TEXT,
                cover_letter_sha256 TEXT,
                verification_status TEXT
            )"""
        )
        connection.commit()
    finally:
        connection.close()

    lifecycle.ensure_schema()
    return hunter_db


def _initialize(application_id="app-life-1", key="init-1"):
    return lifecycle.initialize_application(
        application_id=application_id,
        job_id=41,
        provider="GREENHOUSE",
        source="test",
        idempotency_key=key,
        provenance={"fixture": True},
        occurred_at="2026-09-09T15:00:00+00:00",
    )


def test_initialize_transition_and_exact_replay(lifecycle_db):
    initial = _initialize()
    assert initial["state"] == "DISCOVERED"
    assert initial["state_version"] == 1

    scored = lifecycle.transition_application_state(
        application_id="app-life-1",
        to_state="SCORED",
        expected_state_version=1,
        source="scoring_engine",
        idempotency_key="score-1",
        reason="Job scored.",
        provenance={"score": 96},
        occurred_at="2026-09-09T15:01:00+00:00",
    )
    assert scored["state"] == "SCORED"
    assert scored["state_version"] == 2
    assert scored["replayed"] is False

    replay = lifecycle.transition_application_state(
        application_id="app-life-1",
        to_state="SCORED",
        expected_state_version=1,
        source="scoring_engine",
        idempotency_key="score-1",
        reason="Job scored.",
        provenance={"score": 96},
        occurred_at="2026-09-09T15:01:00+00:00",
    )
    assert replay["state"] == "SCORED"
    assert replay["state_version"] == 2
    assert replay["replayed"] is True


def test_conflicting_idempotency_replay_fails_closed(lifecycle_db):
    _initialize()
    lifecycle.transition_application_state(
        application_id="app-life-1",
        to_state="SCORED",
        expected_state_version=1,
        source="scoring_engine",
        idempotency_key="same-key",
        provenance={"score": 90},
    )
    with pytest.raises(ValueError, match="conflicting"):
        lifecycle.transition_application_state(
            application_id="app-life-1",
            to_state="PURSUIT_APPROVED",
            expected_state_version=2,
            source="scoring_engine",
            idempotency_key="same-key",
            provenance={"score": 90},
        )


def test_illegal_skip_and_version_conflict_are_rejected(lifecycle_db):
    _initialize()
    with pytest.raises(ValueError, match="Illegal lifecycle transition"):
        lifecycle.transition_application_state(
            application_id="app-life-1",
            to_state="INTERVIEW",
            expected_state_version=1,
            source="test",
            idempotency_key="bad-skip",
        )

    with pytest.raises(RuntimeError, match="version conflict"):
        lifecycle.transition_application_state(
            application_id="app-life-1",
            to_state="SCORED",
            expected_state_version=999,
            source="test",
            idempotency_key="bad-version",
        )


def test_terminal_state_cannot_regress(lifecycle_db):
    _initialize()
    scored = lifecycle.transition_application_state(
        application_id="app-life-1",
        to_state="SCORED",
        expected_state_version=1,
        source="test",
        idempotency_key="s1",
    )
    approved = lifecycle.transition_application_state(
        application_id="app-life-1",
        to_state="PURSUIT_APPROVED",
        expected_state_version=scored["state_version"],
        source="test",
        idempotency_key="p1",
    )
    preparing = lifecycle.transition_application_state(
        application_id="app-life-1",
        to_state="PREPARING",
        expected_state_version=approved["state_version"],
        source="test",
        idempotency_key="prep1",
    )
    withdrawn = lifecycle.transition_application_state(
        application_id="app-life-1",
        to_state="WITHDRAWN",
        expected_state_version=preparing["state_version"],
        source="user",
        idempotency_key="withdraw-1",
        reason="User withdrew.",
    )
    assert withdrawn["state"] == "WITHDRAWN"

    with pytest.raises(ValueError, match="Terminal lifecycle state"):
        lifecycle.transition_application_state(
            application_id="app-life-1",
            to_state="PREPARING",
            expected_state_version=withdrawn["state_version"],
            source="test",
            idempotency_key="illegal-reopen",
        )


def _seed_receipt(receipt_id: str, digest_char: str = "a"):
    connection = database.get_connection()
    try:
        connection.execute(
            """INSERT INTO verified_submission_receipts_v1(
                   receipt_id,tenant_id,user_id,receipt_digest,plan_id,review_id,
                   approval_id,resume_sha256,cover_letter_sha256,verification_status
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                receipt_id,
                "default",
                "owner",
                digest_char * 64,
                "plan-41",
                "review-41",
                "approval-41",
                "b" * 64,
                None,
                "VERIFIED",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_verified_receipt_bootstrap_and_crm_read_model(lifecycle_db):
    _seed_receipt("receipt-41")
    connection = database.get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        state = lifecycle.record_verified_receipt(
            connection,
            owner=OWNER,
            application_id="app-receipt-41",
            job_id=41,
            provider="GREENHOUSE",
            provider_application_id="provider-41",
            receipt_id="receipt-41",
            submitted_at="2026-09-09T15:02:00+00:00",
            verified_at="2026-09-09T15:02:03+00:00",
            provenance={"verification_attempt_id": "verify-41"},
        )
        connection.commit()
        assert state == "AWAITING_RESPONSE"
    finally:
        connection.close()

    model = lifecycle.application_crm_read_model("app-receipt-41")
    assert model["state"] == "AWAITING_RESPONSE"
    assert model["state_version"] == 2
    assert model["company_name"] == "Acme"
    assert model["job_title"] == "People Analyst"
    assert model["provider_application_id"] == "provider-41"
    assert model["verified_receipt_id"] == "receipt-41"
    assert model["verification_status"] == "VERIFIED"
    assert model["resume_sha256"] == "b" * 64
    assert [event["to_state"] for event in model["timeline"]] == [
        "VERIFIED",
        "AWAITING_RESPONSE",
    ]
    assert model["submission_authority"] is False
    assert model["next_allowed_states"] == [
        "ASSESSMENT",
        "INTERVIEW",
        "OFFER",
        "REJECTED",
        "WITHDRAWN",
    ]


def test_post_submission_crm_progression_and_list(lifecycle_db):
    _seed_receipt("receipt-42", "c")
    connection = database.get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        lifecycle.record_verified_receipt(
            connection,
            owner=OWNER,
            application_id="app-receipt-42",
            job_id=41,
            provider="GREENHOUSE",
            provider_application_id="provider-42",
            receipt_id="receipt-42",
            submitted_at="2026-09-09T15:03:00+00:00",
            verified_at="2026-09-09T15:03:02+00:00",
        )
        connection.commit()
    finally:
        connection.close()

    assessment = lifecycle.transition_application_state(
        application_id="app-receipt-42",
        to_state="ASSESSMENT",
        expected_state_version=2,
        source="crm",
        idempotency_key="assessment-42",
        provenance={"signal": "assessment_invite"},
    )
    interview = lifecycle.transition_application_state(
        application_id="app-receipt-42",
        to_state="INTERVIEW",
        expected_state_version=assessment["state_version"],
        source="crm",
        idempotency_key="interview-42",
        provenance={"round": 1},
    )
    assert interview["state"] == "INTERVIEW"
    rows = lifecycle.list_application_crm(states=["INTERVIEW"])
    assert any(row["application_id"] == "app-receipt-42" for row in rows)


def test_owner_isolation(lifecycle_db, monkeypatch):
    _initialize()
    monkeypatch.setattr(
        lifecycle,
        "current_owner",
        lambda _connection: SimpleNamespace(tenant_id="default", user_id="other"),
    )
    with pytest.raises(LookupError):
        lifecycle.application_crm_read_model("app-life-1")


def test_history_is_append_only_and_versioned(lifecycle_db):
    _initialize()
    lifecycle.transition_application_state(
        application_id="app-life-1",
        to_state="SCORED",
        expected_state_version=1,
        source="test",
        idempotency_key="history-score",
    )
    connection = database.get_connection()
    try:
        rows = connection.execute(
            """SELECT from_state,to_state,state_version,idempotency_key
               FROM application_lifecycle_history_v1
               WHERE application_id='app-life-1'
               ORDER BY state_version"""
        ).fetchall()
    finally:
        connection.close()
    assert [tuple(row) for row in rows] == [
        (None, "DISCOVERED", 1, "init-1"),
        ("DISCOVERED", "SCORED", 2, "history-score"),
    ]
