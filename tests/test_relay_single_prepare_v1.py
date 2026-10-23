"""Offline one-click Relay consent tests. Never access real provider or production."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import relay_single_prepare_v1 as one_click

ORIGINAL_PREFLIGHT = one_click.preflight_job


@pytest.fixture()
def safe_ledger(tmp_path: Path, monkeypatch):
    db = tmp_path / "intent.db"

    def connect():
        c = sqlite3.connect(db, timeout=5)
        c.row_factory = sqlite3.Row
        return c

    con = connect()
    con.execute(
        "CREATE TABLE candidate_digital_twin_preferences ("
        "preference_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, "
        "user_id TEXT NOT NULL, preference_key TEXT NOT NULL, "
        "value_json TEXT NOT NULL, provenance TEXT NOT NULL, "
        "confidence INTEGER NOT NULL, user_confirmed INTEGER NOT NULL, "
        "updated_at TEXT DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE(tenant_id,user_id,preference_key))"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(one_click, "get_connection", connect)
    monkeypatch.setattr(
        one_click, "current_owner_readonly",
        lambda _con: SimpleNamespace(tenant_id="tenant-test", user_id="owner-test"),
    )
    monkeypatch.setattr(one_click, "preflight_job", lambda _job: None)
    monkeypatch.setattr(one_click, "_bindings", lambda _job: ("a" * 64, "b" * 64))
    from app import prepare_orchestrator_v1 as orchestrator
    from app import product_application_flow_v2 as flow
    from app import tsenta_backend_readiness_v1 as readiness

    operation = {
        "job_id": 7, "engine": "NATIVE", "status": "RUNNING",
        "operation_id": "prepare-op-7",
        "created_at": datetime.now(UTC).isoformat(),
    }
    monkeypatch.setattr(orchestrator, "prepare_operation", lambda _id: operation)
    monkeypatch.setattr(readiness, "connection_status", lambda: {"execution_ready": True})
    review = {
        "state": "READY_FOR_APPROVAL",
        "prepare": {"operation_id": "prepare-op-7"},
        "package_review": {"review_digest": "c" * 64},
        "artifacts": {"preflight": {
            "preflight_package_id": "immutable-package-7",
            "preflight_status": "READY_TO_APPLY",
        }},
    }
    monkeypatch.setattr(flow, "flow_for_job", lambda _job: review)
    return monkeypatch, operation, review, flow


def test_one_click_claim_dispatches_only_once(safe_ledger):
    monkeypatch, _operation, _review, flow = safe_ledger
    requests = []

    def dispatch(job_id, *, execution_provider, approval_source, consent_slot):
        requests.append((job_id, execution_provider, approval_source, consent_slot))
        assert one_click.validate_claimed_intent(
            job_id=job_id,
            operation_id=consent_slot,
            review_digest="c" * 64,
            preflight_package_id="immutable-package-7",
        )
        return {"status": "PROVIDER_SUBMITTED"}

    monkeypatch.setattr(flow, "approve_and_submit_for_job", dispatch)
    assert one_click.reserve_before_prepare(7)["status"] == "PENDING_PREPARE"
    assert one_click.authorize_new_operation(7, "prepare-op-7")["status"] == "REQUESTED"
    first = one_click.maybe_dispatch("prepare-op-7")
    second = one_click.maybe_dispatch("prepare-op-7")
    assert first == {"authorized": True, "status": "PROVIDER_SUBMITTED",
                     "retry_allowed": False}
    assert second["authorized"] is False
    assert second["reason"] == "intent_already_claimed_or_expired"
    assert requests == [
        (7, "TSENTA", "ONE_CLICK_PREPARE_RELAY", "prepare-op-7")
    ]


def test_changed_candidate_truth_blocks_claim(safe_ledger):
    monkeypatch, _operation, _review, flow = safe_ledger
    monkeypatch.setattr(
        flow, "approve_and_submit_for_job",
        lambda *args, **kwargs: pytest.fail("Must not submit changed candidate truth"),
    )
    one_click.reserve_before_prepare(7)
    one_click.authorize_new_operation(7, "prepare-op-7")
    monkeypatch.setattr(one_click, "_bindings", lambda _job: ("a" * 64, "d" * 64))
    result = one_click.maybe_dispatch("prepare-op-7")
    assert result == {"authorized": False, "reason": "job_or_profile_changed"}
    assert one_click.status(7)["status"] == "REQUESTED"


def test_uncertain_relay_outcome_never_retries(safe_ledger):
    monkeypatch, _operation, _review, flow = safe_ledger
    attempts = []

    def uncertain(*_args, **_kwargs):
        attempts.append(True)
        raise TimeoutError("Provider response lost")

    monkeypatch.setattr(flow, "approve_and_submit_for_job", uncertain)
    one_click.reserve_before_prepare(7)
    one_click.authorize_new_operation(7, "prepare-op-7")
    first = one_click.maybe_dispatch("prepare-op-7")
    second = one_click.maybe_dispatch("prepare-op-7")
    assert first["status"] == "OUTCOME_NEEDS_RECONCILIATION"
    assert first["retry_allowed"] is False
    assert second["authorized"] is False
    assert len(attempts) == 1


def test_existing_job_cannot_get_second_one_click_intent(safe_ledger):
    one_click.reserve_before_prepare(7)
    one_click.authorize_new_operation(7, "prepare-op-7")
    with pytest.raises(PermissionError, match="already exists"):
        one_click.reserve_before_prepare(7)


def test_mismatched_package_is_not_claimed(safe_ledger):
    _, _operation, review, _flow = safe_ledger
    one_click.reserve_before_prepare(7)
    one_click.authorize_new_operation(7, "prepare-op-7")
    review["package_review"]["review_digest"] = "z" * 64
    result = one_click.maybe_dispatch("prepare-op-7")
    assert result["reason"] == "package_not_exactly_ready"
    assert one_click.status(7)["status"] == "REQUESTED"


def test_resume_db_lock_message_is_safe():
    from app.resume_studio_page_v4 import _studio_failure_message

    text = _studio_failure_message(sqlite3.OperationalError("database is locked"))
    assert "check the Resume Library" in text
    assert "No application was submitted" in text
    assert "database is locked" not in text


def test_resume_studio_no_cross_session_monkeypatch():
    root = Path(__file__).resolve().parents[1]
    v5 = (root / "app/resume_studio_page_v5.py").read_text()
    v6 = (root / "app/resume_studio_page_v6.py").read_text()
    assert "v4._generate_checked =" not in v5
    assert "resume_v5.version_html =" not in v6
    assert "generate_checked=_generate_checked" in v5
    assert "version_html=styled_version_html" in v6



def test_worker_never_falls_through_to_native_during_intent_binding(safe_ledger):
    monkeypatch, _op, _review, _flow = safe_ledger
    from app import autonomous_prepare_v1 as worker
    from app import native_standing_consent_v1 as standing
    from app import prepare_worker_adapters_v1a as v1a
    from app import prepare_worker_adapters_v1b as v1b
    from app import prepare_worker_adapters_v1c as v1c

    for module, name in (
        (v1a, "run_native_resume_once"),
        (v1a, "run_recruiter_evidence_once"),
        (v1b, "run_answer_resolution_once"),
        (v1b, "run_cover_letter_decision_once"),
        (v1c, "run_application_preflight_once"),
    ):
        monkeypatch.setattr(module, name, lambda **kwargs: {"claimed": False})
    monkeypatch.setattr(
        v1c, "run_ats_form_preparation_once",
        lambda **kwargs: pytest.fail("Native ATS must not run for Relay intent"),
    )
    monkeypatch.setattr(
        standing, "status",
        lambda: pytest.fail("Native standing consent must never apply to Relay"),
    )
    one_click.reserve_before_prepare(7)
    waiting = worker.drive_operation_once("prepare-op-7")
    assert waiting["relay_submission_state"] == "awaiting_exact_operation_binding"
    assert one_click.status(7)["status"] == "PENDING_PREPARE"

    one_click.authorize_new_operation(7, "prepare-op-7")
    monkeypatch.setattr(
        _flow, "approve_and_submit_for_job",
        lambda *args, **kwargs: {"status": "PROVIDER_SUBMITTED"},
    )
    applied = worker.drive_operation_once("prepare-op-7")
    assert applied["relay_submission_state"] == "PROVIDER_SUBMITTED"
    assert one_click.status(7)["status"] == "CLAIMED"
    no_retry = worker.drive_operation_once("prepare-op-7")
    assert no_retry["relay_submission_state"] == (
        "one_click_reserved_for_other_or_expired_operation"
    )


def test_pending_one_click_binding_may_only_be_consumed_once(safe_ledger):
    assert one_click.reserve_before_prepare(7)["status"] == "PENDING_PREPARE"
    with pytest.raises(PermissionError, match="already exists"):
        one_click.reserve_before_prepare(7)
    assert one_click.authorize_new_operation(7, "prepare-op-7")["status"] == "REQUESTED"
    with pytest.raises(PermissionError, match="No fresh"):
        one_click.authorize_new_operation(7, "prepare-op-7")


def test_preflight_refuses_existing_application_record(safe_ledger):
    monkeypatch, _, _, flow = safe_ledger
    from app import tsenta_backend_readiness_v1 as readiness

    monkeypatch.setattr(
        flow, "flow_for_job",
        lambda _job, summary=True: {
            "state": "SUBMISSION_UNVERIFIED",
            "provider_route": {"provider": "TSENTA"},
        },
    )
    monkeypatch.setattr(
        readiness, "connection_status", lambda: {"execution_ready": True},
    )
    # Restore the actual preflight implementation instead of the fixture's
    # shortcut. The duplicate guard precedes any job table access.
    with monkeypatch.context() as patch:
        patch.setattr(one_click, "preflight_job", ORIGINAL_PREFLIGHT)
        with pytest.raises(PermissionError, match="already has an application"):
            one_click.preflight_job(7)
