from __future__ import annotations

from app import product_application_flow_v2 as flow


def test_state_labels_cover_complete_loop_states():
    assert flow.STATE_LABELS["NEEDS_INPUT"] == "Needs input"
    assert flow.STATE_LABELS["READY_FOR_APPROVAL"] == "Ready for approval"
    assert flow.STATE_LABELS["READY_FOR_REVIEW"] == "Applying"
    assert (
        flow.STATE_LABELS["SUBMISSION_UNVERIFIED"]
        == "Submission needs verification"
    )
    assert flow.STATE_LABELS["VERIFIED"] == "Submission verified"
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
