from __future__ import annotations

from types import SimpleNamespace

from app import application_plan_resolution_v2 as resolution


def test_runtime_resolution_orchestrates_exact_replacement_and_delivery(monkeypatch) -> None:
    replacement = {
        "plan_id": "replacement-plan-1",
        "plan_digest": "a" * 64,
        "inputs": {
            "answer_requirements": [
                {
                    "question_key": "contact.postal_code",
                    "question_family": "candidate_fact",
                    "conditions": {"profile_projection": "identity"},
                }
            ]
        },
    }
    calls: list[tuple[str, dict]] = []

    def reprepare(**kwargs):
        calls.append(("reprepare", kwargs))
        return replacement

    def deliver(**kwargs):
        calls.append(("deliver", kwargs))
        return SimpleNamespace(
            handoff_id="handoff-1",
            plan_id="replacement-plan-1",
            plan_digest="a" * 64,
            state="PLAN_ACCEPTED",
            replayed=False,
            submission_authority=False,
        )

    monkeypatch.setattr(
        resolution.application_plan_v2, "reprepare_runtime_needs_input_plan", reprepare
    )
    monkeypatch.setattr(
        resolution.application_plan_delivery_v2, "deliver_application_plan", deliver
    )
    promoted = []
    monkeypatch.setattr(
        resolution.answer_brain_v2,
        "save_answer",
        lambda **kwargs: promoted.append(kwargs) or "answer-1",
    )
    result = resolution.resolve_runtime_needs_input(
        prior_plan_id="prior-plan-1",
        expected_prior_plan_digest="b" * 64,
        revision_idempotency_key="revision-key-1",
        runtime_context={
            "session_id": "session-1",
            "checkpoint_id": "checkpoint-1",
            "browser_form_digest": "c" * 64,
            "unresolved_questions": [],
        },
        answer_resolutions=[
            {"question_key": "contact.postal_code", "canonical_answer": "08065"}
        ],
    )
    assert result["replacement_plan_id"] == "replacement-plan-1"
    assert result["state"] == "PLAN_ACCEPTED"
    assert result["submission_authority"] is False
    assert promoted == []
    assert calls[0][0] == "reprepare"
    assert calls[1][0] == "deliver"
    assert calls[1][1]["idempotency_key"].startswith(
        "runtime-resolution-handoff-"
    )
    assert "canonical_answer" not in str(result)
