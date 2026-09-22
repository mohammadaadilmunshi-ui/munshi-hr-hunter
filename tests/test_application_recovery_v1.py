from __future__ import annotations

import pytest

from app import application_recovery_v1 as recovery
from app import database


@pytest.mark.parametrize(
    "state",
    ["SUBMITTED", "VERIFIED", "AWAITING_RESPONSE", "SUBMISSION_UNVERIFIED"],
)
def test_submission_evidence_blocks_all_durable_recovery(monkeypatch, state):
    monkeypatch.setattr(
        recovery.application_flow,
        "flow_for_job",
        lambda _job_id: {
            "state": state,
            "prepare": {"operation_id": "prepare-1"},
            "production_receipt": None,
            "execution_mandate": None,
            "internal_integrity_review": None,
        },
    )
    with pytest.raises(ValueError, match="submission authority or receipt evidence"):
        recovery._pre_submit(1)


def test_consumed_approval_blocks_recovery_even_before_receipt(monkeypatch):
    monkeypatch.setattr(
        recovery.application_flow,
        "flow_for_job",
        lambda _job_id: {
            "state": "SUBMITTING",
            "prepare": {"operation_id": "prepare-1"},
            "production_receipt": None,
            "execution_mandate": {"mandate_id": "mandate-1"},
            "internal_integrity_review": {"approval_consumed": True},
        },
    )
    with pytest.raises(ValueError, match="submission authority or receipt evidence"):
        recovery._pre_submit(1)


def test_every_durable_recovery_request_can_leave_an_audit_event(hunter_db):
    connection = database.get_connection()
    try:
        connection.execute(
            """INSERT INTO jobs(id,job_fingerprint,source,company_name,title,apply_url)
               VALUES(501,'recovery-audit-501','fixture','Acme','HR Analyst','https://fixture.invalid/apply')"""
        )
        connection.commit()
    finally:
        connection.close()

    recovery._audit(
        job_id=501,
        action="recover_expired_lease",
        status="requested",
        detail={"operation_id": "prepare-501"},
    )

    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT event_type,event_status,payload_json FROM events WHERE job_id=501"
        ).fetchone()
    finally:
        connection.close()
    assert row["event_type"] == recovery.EVENT_TYPE
    assert row["event_status"] == "requested"
    assert "submission_authority" in row["payload_json"]
