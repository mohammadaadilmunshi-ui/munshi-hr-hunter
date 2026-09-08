from __future__ import annotations

from app import api
from app import application_plan_resolution_v2 as resolution


def test_runtime_resolution_api_wires_exact_authority_request(monkeypatch) -> None:
    captured = {}

    def resolve_runtime_needs_input(**kwargs):
        captured.update(kwargs)
        return {
            "success": True,
            "prior_plan_id": kwargs["prior_plan_id"],
            "replacement_plan_id": "replacement-plan-1",
            "replacement_plan_digest": "a" * 64,
            "handoff_id": "handoff-1",
            "state": "PLAN_ACCEPTED",
            "replayed": False,
            "submission_authority": False,
        }

    monkeypatch.setattr(
        resolution,
        "resolve_runtime_needs_input",
        resolve_runtime_needs_input,
    )
    payload = api.RuntimeNeedsInputResolutionRequest(
        expected_prior_plan_digest="b" * 64,
        revision_idempotency_key="revision-key-1",
        runtime_context={
            "session_id": "session-1",
            "checkpoint_id": "checkpoint-1",
            "browser_form_digest": "c" * 64,
            "unresolved_questions": [
                {
                    "question_key": "first_name",
                    "control_id": "first_name",
                    "question": "First name",
                    "semantic_type": "text",
                    "sensitivity_class": "NORMAL",
                }
            ],
        },
        answer_resolutions=[
            {"question_key": "first_name", "canonical_answer": "Aadil"}
        ],
    )
    result = api.resolve_runtime_application_plan_needs_input(
        "prior-plan-1",
        payload,
    )
    assert result["state"] == "PLAN_ACCEPTED"
    assert result["submission_authority"] is False
    assert captured["prior_plan_id"] == "prior-plan-1"
    assert captured["expected_prior_plan_digest"] == "b" * 64
    assert captured["runtime_context"]["session_id"] == "session-1"
    assert captured["answer_resolutions"] == [
        {"question_key": "first_name", "canonical_answer": "Aadil"}
    ]
