from __future__ import annotations

import inspect
from types import SimpleNamespace

from app import product_application_flow_v2 as flow
from app import prepare_orchestrator_v1 as prepare


def test_state_labels_cover_complete_loop_states():
    assert flow.STATE_LABELS["NEEDS_INPUT"] == "Action required"
    assert flow.STATE_LABELS["READY_FOR_APPROVAL"] == "Needs review"
    assert flow.STATE_LABELS["READY_FOR_REVIEW"] == "Needs review"
    assert (
        flow.STATE_LABELS["SUBMISSION_UNVERIFIED"]
        == "Submission needs verification"
    )
    assert flow.STATE_LABELS["VERIFIED"] == "Submitted"
    assert flow.STATE_LABELS["AWAITING_RESPONSE"] == "Awaiting response"


def test_prepare_progress_counts_only_required_tasks():
    tasks = [
        {"required": True, "status": "SUCCEEDED"},
        {"required": True, "status": "RUNNING"},
        {"required": True, "status": "PENDING"},
        {"required": False, "status": "SUCCEEDED"},
    ]
    assert flow._required_progress(tasks) == 50


def test_runtime_question_projection_never_contains_resolution_value():
    tasks = [
        {
            "output": {
                "runtime_resolution_tasks": [
                    {
                        "task_id": "task-1",
                        "question_id": "q-1",
                        "question": "Are you willing to relocate?",
                        "semantic_type": "YES_NO",
                        "category": "RELOCATION",
                        "sensitivity_class": "NORMAL",
                        "risk_level": "LOW",
                        "requires_user": True,
                        "reason": "No confirmed answer",
                        "resolution": {"value": "secret"},
                    }
                ]
            }
        }
    ]
    projected = flow._runtime_questions(tasks)
    assert projected[0]["question"] == "Are you willing to relocate?"
    assert "resolution" not in projected[0]
    assert "value" not in projected[0]


def test_post_prepare_lifecycle_wins_over_old_prepare_state():
    prepare = {"status": "READY_FOR_REVIEW"}
    lifecycle = {"state": "AWAITING_RESPONSE"}
    assert flow._effective_state(prepare, lifecycle) == (
        "AWAITING_RESPONSE",
        "lifecycle",
    )


def test_prepare_state_wins_before_submission_authority():
    prepare = {"status": "NEEDS_INPUT"}
    lifecycle = {"state": "PREPARING"}
    assert flow._effective_state(prepare, lifecycle) == (
        "NEEDS_INPUT",
        "prepare",
    )


def test_artifact_summary_reads_bound_outputs_without_submit_authority():
    tasks = [
        {
            "task_kind": "RESUME_PREPARE",
            "output": {
                "resume_version_id": "resume-v5",
                "engine": "NATIVE_V5",
                "pdf": {
                    "artifact_id": "resume-pdf",
                    "filename": "resume.pdf",
                },
            },
        },
        {
            "task_kind": "ATS_FORM_PREPARATION",
            "output": {
                "cover_letter_decision": "REQUIRED",
                "cover_letter_id": "cover-1",
                "cover_letter_artifact_sha256": "a" * 64,
                "cover_letter_page_count": 1,
                "plan_id": "plan-1",
                "provider": "GREENHOUSE",
                "apply_prepare_state": "READY_FOR_REVIEW",
                "browser_preparation_completed": True,
                "final_review_approved": False,
                "final_submit_executed": False,
                "submission_authority": False,
            },
        },
    ]
    result = flow._artifact_summary(tasks)
    assert result["resume"]["pdf"]["artifact_id"] == "resume-pdf"
    assert result["cover_letter"]["cover_letter_id"] == "cover-1"
    assert result["plan"]["browser_preparation_completed"] is True
    assert "submission_authority" not in result["plan"]


def test_application_center_projection_uses_readonly_owner_resolution():
    for projection in (
        flow._latest_prepare,
        flow._latest_lifecycle,
        flow._production_receipt,
        flow._pipeline_candidate_jobs,
    ):
        source = inspect.getsource(projection)
        assert "current_owner_readonly(connection)" in source
        assert "owner = current_owner(connection)" not in source

    # Starting a durable operation remains an intentional mutation boundary.
    assert "owner = current_owner(connection)" in inspect.getsource(
        flow.start_prepare_for_job
    )


def test_summary_projection_skips_full_review_reconstruction(monkeypatch):
    monkeypatch.setattr(
        flow,
        "_latest_prepare",
        lambda _job_id: {
            "status": "READY_FOR_REVIEW",
            "application_id": "application-1",
            "tasks": [
                {
                    "task_kind": "APPLICATION_PREFLIGHT",
                    "status": "SUCCEEDED",
                    "required": True,
                    "output": {
                        "preflight_package_id": "preflight-1",
                        "single_approval_package_ready": True,
                    },
                },
                {
                    "task_kind": "ATS_FORM_PREPARATION",
                    "status": "PENDING",
                    "required": True,
                    "output": {},
                },
            ],
        },
    )
    monkeypatch.setattr(flow, "_latest_lifecycle", lambda _job_id: None)
    monkeypatch.setattr(
        flow,
        "_package_review",
        lambda _preflight: (_ for _ in ()).throw(
            AssertionError("summary projection must not rebuild the review")
        ),
    )
    monkeypatch.setattr(
        flow,
        "_execution_mandate",
        lambda _preflight, **_kwargs: None,
    )
    monkeypatch.setattr(flow, "_production_receipt", lambda _application_id: None)

    result = flow.flow_for_job(1, summary=True)

    assert result["state"] == "READY_FOR_APPROVAL"
    assert result["package_review"] == {"summary_projection": True}


def test_committed_prepare_identity_survives_secondary_projection_failure(monkeypatch):
    class Connection:
        def close(self):
            return None

    monkeypatch.setattr(flow, "_latest_prepare", lambda _job_id: None)
    monkeypatch.setattr(
        flow,
        "safe_owned_job_snapshot",
        lambda _job_id: {"job_snapshot_sha256": "a" * 64},
    )
    monkeypatch.setattr(
        flow,
        "current_application_truth_projection",
        lambda **_kwargs: {
            "candidate_profile_binding": {"profile_digest": "b" * 64}
        },
    )
    monkeypatch.setattr(flow, "get_connection", lambda: Connection())
    monkeypatch.setattr(
        flow,
        "current_owner",
        lambda _connection: SimpleNamespace(tenant_id="default", user_id="owner"),
    )
    monkeypatch.setattr(flow, "_latest_lifecycle", lambda _job_id: None)
    monkeypatch.setattr(
        prepare,
        "start_prepare",
        lambda **kwargs: {
            "operation_id": "operation-4134",
            "application_id": kwargs["application_id"],
            "status": "RUNNING",
            "replayed": False,
        },
    )
    monkeypatch.setattr(
        flow,
        "flow_for_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("secondary projection temporarily unavailable")
        ),
    )

    result = flow.start_prepare_for_job(4134)

    assert result["operation_id"] == "operation-4134"
    assert result["application_id"].startswith("application-")
    assert result["display_status"] == "Preparing"
    assert result["replayed"] is False
