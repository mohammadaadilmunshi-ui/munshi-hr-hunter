from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import application_view_model_v1 as view_model


def _seed(job_id: int, **overrides):
    row = {
        "id": job_id,
        "job_id": job_id,
        "company_name": f"Company {job_id}",
        "title": f"Role {job_id}",
        "location_raw": "Philadelphia, PA",
        "remote_type": "Hybrid",
        "hunter_score": 88,
        "updated_at": "2026-09-22 10:00:00",
    }
    row.update(overrides)
    return row


def _flow(state: str, **overrides):
    payload = {
        "state": state,
        "authority": "prepare",
        "prepare": {
            "operation_id": "prepare-1",
            "application_id": "application-1",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "lifecycle": None,
        "tasks": [],
        "artifacts": {},
        "runtime_questions": [],
        "execution_mandate": None,
        "internal_integrity_review": None,
        "production_receipt": None,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("state", "human", "group", "action"),
    [
        ("RUNNING", "Preparing", "preparing", "No action needed"),
        ("READY_FOR_APPROVAL", "Needs review", "needs_you", "Review resume"),
        ("SUBMITTING", "Applying now", "applying", "No action needed"),
        ("FAILED_SAFELY", "Needs attention", "needs_attention", "Review the issue"),
        ("VERIFIED", "Submitted", "submitted", "View receipt"),
    ],
)
def test_customer_state_projection_is_consistent(monkeypatch, state, human, group, action):
    monkeypatch.setattr(view_model.application_flow, "flow_for_job", lambda _job_id: _flow(state))
    model = view_model.view_for_job(1, seed=_seed(1), heartbeat={"state": "healthy"})
    assert model.human_state == human
    assert model.state_group == group
    assert model.next_action == action


def test_protected_checkpoint_stays_on_same_application(monkeypatch):
    monkeypatch.setattr(
        view_model.application_flow,
        "flow_for_job",
        lambda _job_id: _flow("NEEDS_INPUT", authority="protected_human_checkpoint"),
    )
    model = view_model.view_for_job(7, seed=_seed(7), heartbeat={"state": "healthy"})
    assert model.application_id == "application-1"
    assert model.protected_checkpoint is True
    assert model.state_group == "needs_you"
    assert model.next_action == "Complete protected checkpoint"


def test_submission_unverified_never_exposes_mutating_recovery(monkeypatch):
    monkeypatch.setattr(
        view_model.application_flow,
        "flow_for_job",
        lambda _job_id: _flow("SUBMISSION_UNVERIFIED"),
    )
    model = view_model.view_for_job(8, seed=_seed(8), heartbeat={"state": "healthy"})
    assert model.submission_attempted is True
    assert model.next_action == "Awaiting independent employer acknowledgment · do not resubmit"
    assert model.allowed_recovery_actions == ("refresh_state", "clear_local_selection")


def test_customer_mandate_preserves_safe_prepare_retry(monkeypatch):
    monkeypatch.setattr(
        view_model.application_flow,
        "flow_for_job",
        lambda _job_id: _flow(
            "FAILED_SAFELY",
            execution_mandate={"mandate_id": "mandate-1"},
        ),
    )

    model = view_model.view_for_job(13, seed=_seed(13), heartbeat={"state": "healthy"})

    assert "retry_preparation" in model.allowed_recovery_actions


def test_consumed_submit_approval_hides_mutating_recovery(monkeypatch):
    monkeypatch.setattr(
        view_model.application_flow,
        "flow_for_job",
        lambda _job_id: _flow(
            "FAILED_SAFELY",
            execution_mandate={"mandate_id": "mandate-1"},
            internal_integrity_review={"approval_consumed": True},
        ),
    )

    model = view_model.view_for_job(14, seed=_seed(14), heartbeat={"state": "healthy"})

    assert model.allowed_recovery_actions == ("refresh_state", "clear_local_selection")


def test_expired_pre_submit_lease_is_recoverable(monkeypatch):
    expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(
        view_model.application_flow,
        "flow_for_job",
        lambda _job_id: _flow(
            "RUNNING",
            tasks=[
                {
                    "task_kind": "RESUME_PREPARE",
                    "label": "Tailored resume",
                    "status": "RUNNING",
                    "lease_expires_at": expired,
                }
            ],
        ),
    )
    model = view_model.view_for_job(9, seed=_seed(9), heartbeat={"state": "healthy"})
    assert "recover_expired_lease" in model.allowed_recovery_actions
    assert "cancel_preparation" in model.allowed_recovery_actions


def test_one_malformed_application_does_not_hide_other_views(monkeypatch):
    rows = [
        _seed(1),
        _seed(2, projection_error="JSONDecodeError"),
        _seed(3),
        _seed(4),
    ]
    states = {1: "RUNNING", 3: "READY_FOR_APPROVAL", 4: "VERIFIED"}
    monkeypatch.setattr(view_model.application_flow, "pipeline_rows", lambda limit: rows)
    monkeypatch.setattr(
        view_model.application_flow,
        "flow_for_job",
        lambda job_id: _flow(states[int(job_id)]),
    )
    monkeypatch.setattr(
        view_model,
        "worker_health_snapshot",
        lambda **_kwargs: {"state": "healthy", "label": "Worker healthy"},
    )

    views = view_model.application_views(limit=250)

    assert {item.job_id for item in views} == {1, 2, 3, 4}
    malformed = next(item for item in views if item.job_id == 2)
    assert malformed.degraded is True
    assert malformed.state_group == "needs_attention"
    assert "Other applications are still available" in malformed.issue_summary


def test_application_views_reuses_pipeline_flow_projection(monkeypatch):
    row = _seed(11, _flow_projection=_flow("RUNNING"))
    monkeypatch.setattr(
        view_model.application_flow,
        "pipeline_rows",
        lambda limit: [row],
    )
    monkeypatch.setattr(
        view_model.application_flow,
        "flow_for_job",
        lambda _job_id: (_ for _ in ()).throw(
            AssertionError("pipeline flow must not be projected twice")
        ),
    )
    monkeypatch.setattr(
        view_model,
        "worker_health_snapshot",
        lambda **_kwargs: {"state": "healthy", "label": "Worker healthy"},
    )
    monkeypatch.setattr(
        view_model.application_archive,
        "visibility_for",
        lambda _application_ids: {},
    )

    views = view_model.application_views(limit=250)

    assert len(views) == 1
    assert views[0].job_id == 11
    assert views[0].state_group == "preparing"


def test_archive_projection_failure_does_not_hide_application_views(monkeypatch):
    row = _seed(12, _flow_projection=_flow("RUNNING"))
    monkeypatch.setattr(view_model.application_flow, "pipeline_rows", lambda limit: [row])
    monkeypatch.setattr(
        view_model,
        "worker_health_snapshot",
        lambda **_kwargs: {"state": "healthy", "label": "Worker healthy"},
    )
    monkeypatch.setattr(
        view_model.application_archive,
        "visibility_for",
        lambda _ids: (_ for _ in ()).throw(RuntimeError("archive read unavailable")),
    )

    views = view_model.application_views(limit=250)

    assert len(views) == 1
    assert views[0].job_id == 12
    assert views[0].archived is False
    assert views[0].degraded is False


def test_archived_application_is_removed_from_auto_prepare_counts(monkeypatch):
    active = view_model.ApplicationViewModel(
        job_id=1, application_id="application-1", company="Alpha", title="Analyst",
        location="Remote", work_arrangement="Remote", match=90,
        canonical_state="RUNNING", human_state="Preparing", state_group="preparing",
        last_update=datetime.now(timezone.utc).isoformat(), next_action="No action needed",
        safe_progress_stage="Resume tailoring", stage_index=2, stage_count=6,
        resume_review_available=False, protected_checkpoint=False, issue_summary=None,
        stopped_at=None, submission_attempted=False, receipt_summary=None,
        worker_heartbeat={"state": "healthy"}, stale=False,
    )
    archived = view_model.ApplicationViewModel(
        **{**active.to_dict(), "job_id": 2, "application_id": "application-2", "archived": True}
    )
    monkeypatch.setattr(view_model, "application_views", lambda limit=250: [active, archived])
    monkeypatch.setattr(
        view_model,
        "worker_health_snapshot",
        lambda **_kwargs: {"state": "healthy", "label": "Worker healthy"},
    )

    snapshot = view_model.control_center_snapshot(limit=250)

    assert [item.application_id for item in snapshot["applications"]] == ["application-1"]
    assert snapshot["counts"]["preparing"] == 1
