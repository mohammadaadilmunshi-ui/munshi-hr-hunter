from __future__ import annotations

from app import application_plan_v2 as plans
from app import prepare_worker_adapters_v1c as workers


def _task(control_id: str, semantic: str, question: str, key: str) -> dict:
    return {
        "task_id": "task-" + control_id,
        "control_id": control_id,
        "group_key": key,
        "semantic_type": semantic,
        "sensitivity_class": "NORMAL",
        "question": question,
        "question_observation": {
            "control_id": control_id,
            "apply_semantic_type": semantic,
            "raw_question": question,
            "label": question,
        },
    }


def _plan() -> dict:
    return {
        "plan_id": "application-plan-crescent-D",
        "plan_digest": "a" * 64,
        "snapshot": {
            "answers": [
                {
                    "question_key": "q-ctl-h9otse",
                    "normalized_question": "How Did You Hear About Us?*",
                    "sensitivity_class": "NORMAL",
                    "autofill_allowed": True,
                    "execution_value": "Indeed",
                },
                {
                    "question_key": "phonetype",
                    "normalized_question": "Phone Device Type Select One Required",
                    "sensitivity_class": "NORMAL",
                    "autofill_allowed": True,
                    "execution_value": "Mobile",
                },
            ]
        },
    }


def test_exact_approved_referral_can_be_reused_and_never_cross_fills_other_fields():
    plan = _plan()
    task = _task("ctl-h9otse", "REFERRAL", "How Did You Hear About Us?*", "q-ctl-h9otse")
    assert workers._previously_approved_referral_answer(plan=plan, task=task) == "Indeed"
    phone = _task("ctl-l2giat", "PHONE", "Phone Number*", "phoneNumber")
    assert workers._previously_approved_referral_answer(plan=plan, task=phone) is None

    changed = {**task, "question": "How did you hear about another company?"}
    assert workers._previously_approved_referral_answer(plan=plan, task=changed) is None
    changed_id = {**task, "control_id": "ctl-other"}
    assert workers._previously_approved_referral_answer(plan=plan, task=changed_id) is None
    changed_semantic = {**task, "semantic_type": "PROTECTED"}
    assert workers._previously_approved_referral_answer(plan=plan, task=changed_semantic) is None
    changed_sensitivity = {**task, "sensitivity_class": "PROTECTED"}
    assert workers._previously_approved_referral_answer(plan=plan, task=changed_sensitivity) is None

    duplicated = _plan()
    duplicated["snapshot"]["answers"].append(
        dict(duplicated["snapshot"]["answers"][0])
    )
    assert workers._previously_approved_referral_answer(plan=duplicated, task=task) is None

    disallowed = _plan()
    disallowed["snapshot"]["answers"][0]["autofill_allowed"] = False
    assert workers._previously_approved_referral_answer(plan=disallowed, task=task) is None


def test_phone_from_confirmed_truth_and_prior_referral_create_one_scoped_plan(monkeypatch):
    referral = _task(
        "ctl-h9otse", "REFERRAL", "How Did You Hear About Us?*", "q-ctl-h9otse"
    )
    phone = _task("ctl-l2giat", "PHONE", "Phone Number*", "phoneNumber")
    captured = []

    monkeypatch.setattr(
        workers.universal_graph, "universal_normal_autofill_enabled", lambda: True
    )
    monkeypatch.setattr(
        workers.application_plan_v2,
        "runtime_application_plan_resolution_enabled",
        lambda: True,
    )
    monkeypatch.setattr(workers, "_universal_runtime_job_context", lambda _p: {})

    def resolved(*, plan, session_id, checkpoint_id, observation, job_context):
        _ = (plan, session_id, checkpoint_id, job_context)
        if observation["apply_semantic_type"] == "PHONE":
            return (
                {
                    "answer_status": "ANSWERED",
                    "autofill_allowed": True,
                    "value": "2015550199",  # Synthetic test only, not candidate data.
                    "question_resolution": {"classification_lane": "ONTOLOGY"},
                },
                None,
            )
        return (
            {
                "answer_status": "MISSING_FACT",
                "autofill_allowed": False,
                "value": None,
                "question_resolution": {"classification_lane": "ONTOLOGY"},
            },
            None,
        )

    monkeypatch.setattr(workers, "_resolve_universal_runtime_observation", resolved)
    monkeypatch.setattr(
        workers.universal_answers, "public_trace",
        lambda row: {k: v for k, v in row.items() if k not in ("value", "canonical_answer")},
    )

    def create_replacement(**kwargs):
        captured.append(kwargs)
        return {
            "replacement_plan_id": "application-plan-crescent-E",
            "replacement_plan_digest": "b" * 64,
            "submission_authority": False,
        }

    monkeypatch.setattr(
        workers.application_plan_resolution_v2,
        "resolve_runtime_needs_input",
        create_replacement,
    )
    runtime_context = {
        "checkpoint_id": "checkpoint-crescent-current",
        "browser_form_digest": "f" * 64,
    }
    result = workers._universal_runtime_auto_resolution(
        operation_id="operation-crescent",
        plan=_plan(),
        session_id="session-crescent-original",
        resolution_payload=runtime_context,
        runtime_tasks=[referral, phone],
    )
    assert result is not None
    assert len(captured) == 1
    assert captured[0]["prior_plan_id"] == "application-plan-crescent-D"
    assert captured[0]["runtime_context"]["unresolved_questions"] == [
        {
            "question_key": "q-ctl-h9otse",
            "control_id": "ctl-h9otse",
            "question": "How Did You Hear About Us?*",
            "semantic_type": "REFERRAL",
            "sensitivity_class": "NORMAL",
            "question_observation": referral["question_observation"],
        },
        {
            "question_key": "phoneNumber",
            "control_id": "ctl-l2giat",
            "question": "Phone Number*",
            "semantic_type": "PHONE",
            "sensitivity_class": "NORMAL",
            "question_observation": phone["question_observation"],
        },
    ]
    assert captured[0]["answer_resolutions"] == [
        {
            "question_key": "q-ctl-h9otse",
            "canonical_answer": "Indeed",
            "provenance": "prior_approved_normal_referral_answer",
        },
        {
            "question_key": "phoneNumber",
            "canonical_answer": "2015550199",
            "provenance": "universal_brain_existing_knowledge",
        },
    ]
    assert result["resolved_count"] == 2
    assert result["value_exposed"] is False
    assert "Indeed" not in str(result["trace"])
    assert "2015550199" not in str(result["trace"])


def test_missing_prior_referral_fails_closed_without_runtime_revision(monkeypatch):
    monkeypatch.setattr(
        workers.universal_graph, "universal_normal_autofill_enabled", lambda: True
    )
    monkeypatch.setattr(
        workers.application_plan_v2,
        "runtime_application_plan_resolution_enabled",
        lambda: True,
    )
    monkeypatch.setattr(workers, "_universal_runtime_job_context", lambda _p: {})
    monkeypatch.setattr(
        workers, "_resolve_universal_runtime_observation",
        lambda **_kwargs: (
            {"answer_status": "MISSING_FACT", "autofill_allowed": False, "value": None}, None
        ),
    )
    monkeypatch.setattr(workers.universal_answers, "public_trace", lambda r: dict(r))
    monkeypatch.setattr(
        workers.application_plan_resolution_v2,
        "resolve_runtime_needs_input",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("unexpected write")),
    )
    plan = _plan()
    plan["snapshot"]["answers"][0]["normalized_question"] = "Unrelated question"
    assert workers._universal_runtime_auto_resolution(
        operation_id="op", plan=plan, session_id="session",
        resolution_payload={"checkpoint_id": "checkpoint", "browser_form_digest": "f" * 64},
        runtime_tasks=[
            _task("ctl-h9otse", "REFERRAL", "How Did You Hear About Us?*", "q-ctl-h9otse")
        ],
    ) is None


def test_approved_referral_provenance_is_supported_for_runtime_context():
    ctx = {
        "unresolved_questions": [
            {"question_key": "q-ctl-h9otse"},
        ]
    }
    rows = plans._runtime_resolution_inputs(
        ctx,
        [
            {
                "question_key": "q-ctl-h9otse",
                "canonical_answer": "Indeed",
                "provenance": "prior_approved_normal_referral_answer",
            }
        ],
    )
    assert rows[0]["provenance"] == "prior_approved_normal_referral_answer"
