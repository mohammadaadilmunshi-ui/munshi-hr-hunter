from __future__ import annotations

from copy import deepcopy

import pytest

from app import application_checkpoint_continuation_v1 as continuation

APP = "application-case-1"
PLAN = "application-plan-1"
SESSION = "apply-session-1"
JOB = "prepare-job-1"
CHECKPOINT = "checkpoint-1"
DIGEST = "a" * 64


@pytest.fixture
def scene(monkeypatch):
    state = {
        "flow": {
            "application_id": APP,
            "execution_mandate": {"mandate_id": "approved-mandate"},
        },
        "prepare": {"status": "FAILED_SAFELY", "operation_id": "original-failed"},
        "plan": {
            "application_id": APP,
            "job_id": 4465,
            "plan_id": PLAN,
            "plan_digest": DIGEST,
            "tenant_id": "t",
            "user_id": "u",
            "executable": True,
            "submission_authority": False,
        },
        "job": {
            "job_id": JOB,
            "application_id": APP,
            "session_id": SESSION,
            "plan_id": PLAN,
            "state": "WAITING_INPUT",
            "attempt_count": 2,
            "max_attempts": 3,
            "lease_owner": None,
            "cancel_requested_at": None,
        },
        "payload": {
            "checkpoint_id": CHECKPOINT,
            "browser_form_digest": "b" * 64,
            "resolution_tasks": [],
        },
        "tasks": [
            {
                "task_id": "task-1",
                "checkpoint_id": CHECKPOINT,
                "semantic_type": "REFERRAL",
                "sensitivity_class": "NORMAL",
                "requires_user": False,
            },
            {
                "task_id": "task-2",
                "checkpoint_id": CHECKPOINT,
                "semantic_type": "PHONE",
                "sensitivity_class": "NORMAL",
                "requires_user": False,
            },
        ],
        "resolved_calls": [],
        "prepare_job_calls": [],
    }
    monkeypatch.setattr(
        continuation.recovery, "_pre_submit",
        lambda job_id: (deepcopy(state["flow"]), deepcopy(state["prepare"])),
    )
    monkeypatch.setattr(continuation, "_latest_accepted_plan", lambda app: PLAN)
    monkeypatch.setattr(
        continuation.application_plan_v2, "get_application_plan",
        lambda pid: deepcopy(state["plan"]),
    )
    monkeypatch.setattr(
        continuation.application_plan_v2, "runtime_plan_is_superseded",
        lambda pid: False,
    )
    monkeypatch.setattr(
        continuation.application_plan_v2, "application_plan_freshness",
        lambda pid: {"fresh": True},
    )

    def read_job(**kwargs):
        state["prepare_job_calls"].append(kwargs)
        return deepcopy(state["job"])

    monkeypatch.setattr(
        continuation.workers, "_read_apply_prepare_job", read_job,
    )
    monkeypatch.setattr(
        continuation.workers, "_read_apply_resolution_tasks",
        lambda **kwargs: deepcopy(state["payload"]),
    )
    monkeypatch.setattr(
        continuation.workers, "_validate_runtime_resolution_sync",
        lambda payload, **kwargs: (deepcopy(state["tasks"]), ["runtime-q1", "runtime-q2"]),
    )

    def create(**kwargs):
        state["resolved_calls"].append(kwargs)
        return {
            "replacement": {
                "success": True,
                "state": "PLAN_ACCEPTED",
                "prior_plan_id": PLAN,
                "replacement_plan_id": "application-plan-2",
                "submission_authority": False,
            },
            "resolved_count": 2,
            "value_exposed": False,
        }

    monkeypatch.setattr(
        continuation.workers, "_universal_runtime_auto_resolution", create,
    )
    return state


def recover(**kwargs):
    return continuation.continue_existing_checkpoint(
        job_id=4465, plan_id=PLAN, session_id=SESSION,
        prepare_job_id=JOB, checkpoint_id=CHECKPOINT,
        **kwargs,
    )


def test_same_session_checkpoint_recovery_is_exact_and_value_free(scene):
    output = recover()
    assert output["replacement_plan_id"] == "application-plan-2"
    assert output["new_application_created"] is False
    assert output["resume_regenerated"] is False
    assert output["submission_authority"] is False
    assert scene["prepare_job_calls"] == [
        {"job_id": JOB, "tenant_id": "t", "user_id": "u"}
    ]
    assert len(scene["resolved_calls"]) == 1
    attempt = scene["resolved_calls"][0]
    assert attempt["plan"] == scene["plan"]
    assert attempt["session_id"] == SESSION
    assert attempt["runtime_tasks"] == scene["tasks"]
    assert "candidate_answer" not in str(output)


@pytest.mark.parametrize(
    "key,replace,reason",
    [
        ("job", {"state": "RUNNING"}, "WAITING_INPUT"),
        ("job", {"plan_id": "application-plan-other"}, "WAITING_INPUT"),
        ("job", {"session_id": "apply-session-other"}, "WAITING_INPUT"),
        ("job", {"attempt_count": 3}, "attempt budget"),
        ("job", {"lease_owner": "other-worker"}, "WAITING_INPUT"),
        ("payload", {"checkpoint_id": "checkpoint-new"}, "checkpoint changed"),
        ("plan", {"executable": False}, "no longer executable"),
        ("plan", {"application_id": "application-other"}, "no longer executable"),
        ("flow", {"execution_mandate": None}, "execution mandate"),
        ("prepare", {"status": "RUNNING"}, "not paused"),
    ],
)
def test_runtime_recovery_fails_closed_on_mismatch(scene, key, replace, reason):
    scene[key].update(replace)
    with pytest.raises(ValueError, match=reason):
        recover()
    assert scene["resolved_calls"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"sensitivity_class": "PROTECTED"},
        {"requires_user": True},
        {"checkpoint_id": "checkpoint-stale"},
    ],
)
def test_runtime_recovery_never_fills_protected_or_stale_tasks(scene, change):
    scene["tasks"][0].update(change)
    with pytest.raises(ValueError, match="protected, human-only or mismatched"):
        recover()
    assert scene["resolved_calls"] == []


def test_runtime_recovery_missing_approval_and_bad_ack_fail_closed(scene, monkeypatch):
    monkeypatch.setattr(
        continuation, "_latest_accepted_plan", lambda app: "application-plan-new"
    )
    with pytest.raises(ValueError, match="Accepted Application Plan has changed"):
        recover()
    assert scene["resolved_calls"] == []
    monkeypatch.setattr(continuation, "_latest_accepted_plan", lambda app: PLAN)
    monkeypatch.setattr(
        continuation.workers, "_universal_runtime_auto_resolution",
        lambda **kwargs: None,
    )
    with pytest.raises(ValueError, match="cannot be fully resolved"):
        recover()


def test_exact_input_identities_are_required(scene):
    with pytest.raises(ValueError, match="exact apply-session-"):
        continuation.continue_existing_checkpoint(
            job_id=4465, plan_id=PLAN, session_id="other",
            prepare_job_id=JOB, checkpoint_id=CHECKPOINT,
        )
    assert scene["resolved_calls"] == []
