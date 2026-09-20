from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import database
from app import prepare_orchestrator_v1 as orchestrator
from app import prepare_worker_adapters_v1c as workers


OWNER = SimpleNamespace(tenant_id="default", user_id="owner")
JOB_DIGEST = "a" * 64
PROJECTION_DIGEST = "b" * 64
PROFILE_DIGEST = "c" * 64


@pytest.fixture()
def worker_db(hunter_db, monkeypatch):
    monkeypatch.setenv(workers.WORKERS_ENV, "1")
    monkeypatch.setenv(workers.ATS_RUNTIME_ENV, "1")
    monkeypatch.setattr(
        workers.application_execution_mandate_v1,
        "current_for_preflight",
        lambda package_id: {
            "mandate_id": "execution-mandate-94",
            "preflight_package_id": package_id,
        },
    )
    monkeypatch.setattr(
        workers.application_review_v2,
        "approve_review_from_execution_mandate",
        lambda **kwargs: {
            "approval_id": "technical-approval-94",
            "approval_digest": "6" * 64,
            "approval_source": "EXECUTION_MANDATE",
            "execution_mandate_id": kwargs["execution_mandate_id"],
            "approved": True,
            "submission_authority": False,
        },
    )
    monkeypatch.setattr(
        workers.application_submit_authorization_v1,
        "issue_authorization",
        lambda payload: {
            **dict(payload),
            "authorization_id": "submit-auth-94",
            "authority_digest": "7" * 64,
            "submission_authority": True,
        },
    )
    monkeypatch.setattr(
        workers,
        "deliver_submit_authorization",
        lambda envelope, timeout_seconds=5.0: {
            "delivered": True,
            "authorization_id": envelope["authorization_id"],
            "submission_authority": False,
        },
    )
    monkeypatch.setattr(orchestrator, "current_owner", lambda _connection: OWNER)
    monkeypatch.setattr(
        orchestrator,
        "safe_owned_job_snapshot",
        lambda job_id: {
            "job": {"id": int(job_id)},
            "job_snapshot_sha256": JOB_DIGEST,
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "current_application_truth_projection",
        lambda job_id: {
            "projection_digest": PROJECTION_DIGEST,
            "candidate_profile_binding": {"profile_digest": PROFILE_DIGEST},
            "job_context": {"job_snapshot_sha256": JOB_DIGEST},
        },
    )

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
                   id,job_fingerprint,source,company_name,title,apply_url
               ) VALUES(
                   94,'prepare-worker-v1c-job-94','fixture','Acme','People Analyst',
                   'https://boards.greenhouse.io/acme/jobs/94'
               )"""
        )
        connection.commit()
    finally:
        connection.close()

    orchestrator.ensure_schema()
    return hunter_db


def _start(key):
    return orchestrator.start_prepare(
        application_id="application-94",
        job_id=94,
        engine="NATIVE",
        idempotency_key=key,
    )


def _task(operation_id, kind):
    operation = orchestrator.prepare_operation(operation_id)
    return next(row for row in operation["tasks"] if row["task_kind"] == kind)


def _complete_root(operation_id, kind, worker_id, output):
    claimed = orchestrator.claim_ready_tasks(
        operation_id=operation_id,
        worker_id=worker_id,
        limit=1,
        task_kinds=[kind],
    )
    assert len(claimed) == 1
    return orchestrator.complete_task(
        task_id=claimed[0]["task_id"],
        worker_id=worker_id,
        output=output,
    )


def _resume_output():
    return {
        "resume_version_id": "resume-v5-94",
        "candidate_profile_digest": PROFILE_DIGEST,
        "job_snapshot_sha256": JOB_DIGEST,
        "pdf": {
            "artifact_sha256": "d" * 64,
            "page_count": 1,
        },
        "submission_authority": False,
    }


def _answer_output():
    return {
        "candidate_profile_digest": PROFILE_DIGEST,
        "application_truth_projection_digest": PROJECTION_DIGEST,
        "job_snapshot_sha256": JOB_DIGEST,
        "requires_ats_form_confirmation": True,
        "submission_authority": False,
    }


def _cover_output(decision="NOT_REQUIRED"):
    return {
        "decision": decision,
        "requires_ats_form_confirmation": True,
        "submission_authority": False,
    }


def _ready_dependencies(operation_id, *, cover="NOT_REQUIRED"):
    _complete_root(
        operation_id,
        orchestrator.TASK_RESUME,
        "resume-worker",
        _resume_output(),
    )
    _complete_root(
        operation_id,
        orchestrator.TASK_ANSWER_RESOLUTION,
        "answer-worker",
        _answer_output(),
    )
    _complete_root(
        operation_id,
        orchestrator.TASK_COVER_LETTER_DECISION,
        "cover-worker",
        _cover_output(cover),
    )


def _fake_ready_package():
    return {
        "package_id": "preflight-94",
        "package_digest": "e" * 64,
        "status": "READY_TO_APPLY",
        "job_id": 94,
        "job_snapshot_sha256": JOB_DIGEST,
        "resume_version_id": "resume-v5-94",
        "snapshot": {
            "application_question_state": {
                "complete": True,
                "unresolved_question_keys": [],
            },
            "readiness": {"blockers": []},
        },
    }


def _run_preflight_ready(operation, monkeypatch):
    monkeypatch.setattr(
        workers.opportunity_intelligence_v3,
        "evaluate_job",
        lambda job_id, persist=True: {
            "evaluation_id": "opportunity-94",
            "result_sha256": "f" * 64,
        },
    )
    monkeypatch.setattr(
        workers.preflight_v1,
        "prepare_preflight_package",
        lambda **kwargs: _fake_ready_package(),
    )
    return workers.run_application_preflight_once(
        operation_id=operation["operation_id"],
        worker_id="preflight-worker",
    )


def test_preflight_worker_converges_known_requirement_package(worker_db, monkeypatch):
    operation = _start("v1c-preflight-ready")
    _ready_dependencies(operation["operation_id"])

    result = _run_preflight_ready(operation, monkeypatch)
    assert result["task_status"] == "SUCCEEDED"
    output = result["output"]
    assert output["preflight_status"] == "READY_TO_APPLY"
    assert output["known_requirement_set_complete"] is True
    assert output["live_form_discovery_pending"] is True
    assert output["employer_form_claimed_complete"] is False
    assert output["submission_authority"] is False


def test_preflight_worker_passes_v5_resume_and_known_scope(worker_db, monkeypatch):
    operation = _start("v1c-preflight-args")
    _ready_dependencies(operation["operation_id"])
    seen = {}

    monkeypatch.setattr(
        workers.opportunity_intelligence_v3,
        "evaluate_job",
        lambda job_id, persist=True: {
            "evaluation_id": "opportunity-94",
            "result_sha256": "f" * 64,
        },
    )

    def capture(**kwargs):
        seen.update(kwargs)
        return _fake_ready_package()

    monkeypatch.setattr(workers.preflight_v1, "prepare_preflight_package", capture)
    workers.run_application_preflight_once(
        operation_id=operation["operation_id"],
        worker_id="preflight-worker",
    )

    assert seen["resume_version_id"] == "resume-v5-94"
    assert seen["application_questions_complete"] is True
    assert seen["unresolved_question_keys"] == []



def test_preflight_omits_optional_relationship_not_bound_to_current_opportunity(
    worker_db, monkeypatch
):
    operation = _start("v1c-preflight-optional-relationship")
    _ready_dependencies(operation["operation_id"])
    _complete_root(
        operation["operation_id"],
        orchestrator.TASK_RECRUITER_DISCOVERY,
        "recruiter-worker",
        {
            "available": True,
            "relationship_strategy_id": "relationship-old",
            "submission_authority": False,
        },
    )

    opportunity = {
        "evaluation_id": "opportunity-94",
        "result_sha256": "f" * 64,
    }
    monkeypatch.setattr(
        workers.opportunity_intelligence_v3,
        "evaluate_job",
        lambda job_id, persist=True: dict(opportunity),
    )
    monkeypatch.setattr(
        workers.relationship_intelligence_v3,
        "get_strategy",
        lambda strategy_id: {
            "strategy_id": strategy_id,
            "job_id": 94,
            "job_snapshot_sha256": JOB_DIGEST,
            "opportunity_context": None,
        },
    )
    monkeypatch.setattr(
        workers.relationship_intelligence_v3,
        "strategy_freshness",
        lambda strategy_id: {"fresh": True},
    )

    seen = {}
    def capture(**kwargs):
        seen.update(kwargs)
        return _fake_ready_package()

    monkeypatch.setattr(workers.preflight_v1, "prepare_preflight_package", capture)

    result = workers.run_application_preflight_once(
        operation_id=operation["operation_id"],
        worker_id="preflight-worker",
    )

    assert result["task_status"] == "SUCCEEDED"
    assert seen["relationship_strategy_id"] is None

def test_preflight_not_ready_becomes_needs_input(worker_db, monkeypatch):
    operation = _start("v1c-preflight-needs-input")
    _ready_dependencies(operation["operation_id"])

    monkeypatch.setattr(
        workers.opportunity_intelligence_v3,
        "evaluate_job",
        lambda job_id, persist=True: {
            "evaluation_id": "opportunity-94",
            "result_sha256": "f" * 64,
        },
    )
    package = _fake_ready_package()
    package["status"] = "NEEDS_INPUT"
    package["snapshot"]["readiness"]["blockers"] = ["truth_binding_stale"]
    monkeypatch.setattr(
        workers.preflight_v1,
        "prepare_preflight_package",
        lambda **kwargs: package,
    )

    result = workers.run_application_preflight_once(
        operation_id=operation["operation_id"],
        worker_id="preflight-worker",
    )
    assert result["operation_status"] == "NEEDS_INPUT"
    assert "preflight.blocker:truth_binding_stale" in orchestrator.prepare_operation(
        operation["operation_id"]
    )["unresolved"]


def test_task_heartbeat_requires_exact_worker(worker_db):
    operation = _start("v1c-heartbeat")
    _ready_dependencies(operation["operation_id"])
    # APPLICATION_PREFLIGHT is now ready.
    claimed = orchestrator.claim_ready_tasks(
        operation_id=operation["operation_id"],
        worker_id="preflight-owner",
        limit=1,
        task_kinds=[orchestrator.TASK_APPLICATION_PREFLIGHT],
    )
    assert len(claimed) == 1

    heartbeat = orchestrator.heartbeat_task(
        task_id=claimed[0]["task_id"],
        worker_id="preflight-owner",
        lease_seconds=300,
    )
    assert heartbeat["status"] == "RUNNING"
    assert heartbeat["submission_authority"] is False

    with pytest.raises(PermissionError, match="another worker"):
        orchestrator.heartbeat_task(
            task_id=claimed[0]["task_id"],
            worker_id="intruder",
            lease_seconds=300,
        )


def _fake_plan():
    return {
        "plan_id": "plan-94",
        "plan_digest": "1" * 64,
        "application_id": "application-94",
        "tenant_id": "default",
        "user_id": "owner",
        "provider": "GREENHOUSE",
        "status": "READY_TO_APPLY",
        "executable": True,
        "submission_authority": False,
        "snapshot": {
            "permissions": {
                "background_prepare": True,
                "resume_upload": True,
                "cover_letter_upload": False,
                "normal_answer_autofill": True,
                "protected_fact_execution": False,
                "self_id_execution": False,
                "final_review": False,
                "final_submit": False,
            },
            "unresolved": [],
            "resume": {"artifact_sha256": "8" * 64},
        },
    }


def _frozen_review(*, cover_sha256=None):
    review = {
        "application_id": "application-94",
        "plan_id": "plan-94",
        "session_id": "session-94",
        "provider": "GREENHOUSE",
        "destination_url": "https://boards.example.test/submit",
        "resume": {"sha256": "8" * 64},
        "application": {
            "required_fields": 2,
            "completed": 2,
            "unresolved": 0,
            "warnings": [],
        },
        "answers": [],
        "plan_digest": "1" * 64,
        "checkpoint_id": "checkpoint-94",
        "browser_verification": {"form_digest": "2" * 64},
        "submission_readiness": "READY_FOR_REVIEW",
    }
    if cover_sha256:
        review["cover_letter"] = {"sha256": cover_sha256}
    return {"review_id": "apply-review-94", "review_digest": "3" * 64, "review": review}


def _recorded_review(**kwargs):
    return {
        "review_id": "hunter-review-94",
        "application_id": "application-94",
        "plan_id": "plan-94",
        "plan_digest": "1" * 64,
        "review_digest": "4" * 64,
        "prepared_package_digest": "5" * 64,
        "browser_form_digest": "2" * 64,
        "resume_artifact_sha256": "8" * 64,
        "review": {
            "provider": "GREENHOUSE",
            "destination_url": "https://boards.example.test/submit",
            "bindings": {
                "checkpoint_id": "checkpoint-94",
                "cover_letter_artifact_sha256": (
                    "9" * 64 if kwargs.get("prepared_package", {}).get("cover_letter_sha256") else None
                ),
            },
        },
        "approved": False,
    }


def _delivery():
    return SimpleNamespace(
        state="PLAN_ACCEPTED",
        handoff_id="handoff-94",
        plan_id="plan-94",
        plan_digest="1" * 64,
        replayed=False,
        submission_authority=False,
    )


def _session(state="QUEUED"):
    return {
        "session_id": "session-94",
        "application_id": "application-94",
        "plan_id": "plan-94",
        "state": "SESSION_STARTING",
        "state_version": 2,
        "preparation_job": {
            "job_id": "prepare-job-94",
            "session_id": "session-94",
            "application_id": "application-94",
            "plan_id": "plan-94",
            "state": state,
        },
    }


def _ready_ats(worker_db, monkeypatch, key, cover="NOT_REQUIRED"):
    operation = _start(key)
    _ready_dependencies(operation["operation_id"], cover=cover)
    _run_preflight_ready(operation, monkeypatch)
    return operation


def test_ats_runtime_default_off_does_not_claim(worker_db, monkeypatch):
    operation = _ready_ats(worker_db, monkeypatch, "v1c-runtime-off")
    monkeypatch.delenv(workers.ATS_RUNTIME_ENV, raising=False)

    result = workers.run_ats_form_preparation_once(
        operation_id=operation["operation_id"],
        worker_id="ats-worker",
    )
    assert result["claimed"] is False
    assert result["reason"] == "ats_runtime_start_disabled"
    assert _task(
        operation["operation_id"], orchestrator.TASK_ATS_FORM_PREPARATION
    )["status"] == "PENDING"


def test_required_cover_letter_generates_exact_artifact_and_enters_plan(
    worker_db, monkeypatch
):
    seen = {}

    monkeypatch.setattr(
        workers.native_cover_letter_writer_v1,
        "generate",
        lambda **kwargs: {
            "cover_letter_id": "cover-letter-required-94",
            "artifact_id": "cover-letter-required-94",
            "artifact_reference": (
                "hunter-cover-letter://cover-letter-required-94/pdf"
            ),
            "artifact_sha256": "9" * 64,
            "page_count": 1,
            "job_snapshot_sha256": JOB_DIGEST,
            "candidate_profile_digest": PROFILE_DIGEST,
            "submission_authority": False,
        },
    )
    operation = _ready_ats(
        worker_db,
        monkeypatch,
        "v1c-cover-required",
        cover="REQUIRED",
    )

    def prepare_plan(**kwargs):
        seen["cover_letter_id"] = kwargs["cover_letter_id"]
        seen["permissions"] = dict(kwargs["permissions"])
        plan = _fake_plan()
        plan["snapshot"]["permissions"]["cover_letter_upload"] = True
        return plan

    monkeypatch.setattr(
        workers.application_plan_v2,
        "prepare_application_plan",
        prepare_plan,
    )

    result = workers.run_ats_form_preparation_once(
        operation_id=operation["operation_id"],
        worker_id="ats-worker",
        poll_interval_seconds=0,
        plan_delivery=lambda **kwargs: _delivery(),
        session_starter=lambda plan: _session("QUEUED"),
        status_reader=lambda **kwargs: {
            **_session("READY_FOR_REVIEW")["preparation_job"],
            "state": "READY_FOR_REVIEW",
        },
        review_builder=lambda **kwargs: _frozen_review(cover_sha256="9" * 64),
        review_recorder=_recorded_review,
    )

    assert result["task_status"] == "SUCCEEDED"
    assert result["operation_status"] == "READY_FOR_REVIEW"
    assert seen["cover_letter_id"] == "cover-letter-required-94"
    assert seen["permissions"]["cover_letter_upload"] is True
    assert seen["permissions"]["final_review"] is False
    assert seen["permissions"]["final_submit"] is False
    assert result["output"]["cover_letter_id"] == "cover-letter-required-94"
    assert result["output"]["cover_letter_artifact_sha256"] == "9" * 64
    assert result["output"]["cover_letter_page_count"] == 1
    assert result["output"]["customer_review_approved"] is False
    assert result["output"]["additional_customer_approval_required"] is False
    assert result["output"]["final_submit_executed"] is False
    assert result["output"]["submission_authority"] is False


def test_required_cover_letter_writer_failure_requeues_preflight_safely(
    worker_db, monkeypatch
):
    operation = _start("v1c-cover-required-failure")
    _ready_dependencies(operation["operation_id"], cover="REQUIRED")

    monkeypatch.setattr(
        workers.native_cover_letter_writer_v1,
        "generate",
        lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("writer unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="writer unavailable"):
        _run_preflight_ready(operation, monkeypatch)

    task = _task(
        operation["operation_id"],
        orchestrator.TASK_APPLICATION_PREFLIGHT,
    )
    assert task["status"] == "PENDING"
    assert task["attempts"] == 1


def test_ats_ready_for_review_completes_orchestrator(worker_db, monkeypatch):
    operation = _ready_ats(worker_db, monkeypatch, "v1c-ats-ready")
    seen = {}

    def prepare_plan(**kwargs):
        seen["permissions"] = dict(kwargs["permissions"])
        seen["requirements"] = list(kwargs["answer_requirements"])
        return _fake_plan()

    monkeypatch.setattr(
        workers.application_plan_v2,
        "prepare_application_plan",
        prepare_plan,
    )

    result = workers.run_ats_form_preparation_once(
        operation_id=operation["operation_id"],
        worker_id="ats-worker",
        poll_interval_seconds=0,
        plan_delivery=lambda **kwargs: _delivery(),
        session_starter=lambda plan: _session("QUEUED"),
        status_reader=lambda **kwargs: {
            **_session("READY_FOR_REVIEW")["preparation_job"],
            "state": "READY_FOR_REVIEW",
        },
        review_builder=lambda **kwargs: _frozen_review(),
        review_recorder=_recorded_review,
    )

    assert result["task_status"] == "SUCCEEDED"
    assert result["operation_status"] == "READY_FOR_REVIEW"
    assert result["output"]["apply_prepare_state"] == "READY_FOR_REVIEW"
    assert result["output"]["customer_review_approved"] is False
    assert result["output"]["additional_customer_approval_required"] is False
    assert result["output"]["final_submit_executed"] is False
    assert seen["requirements"] == []
    assert seen["permissions"]["final_review"] is False
    assert seen["permissions"]["final_submit"] is False


def test_apply_waiting_input_maps_to_hunter_needs_input(worker_db, monkeypatch):
    operation = _ready_ats(worker_db, monkeypatch, "v1c-ats-needs-input")
    monkeypatch.setattr(
        workers.application_plan_v2,
        "prepare_application_plan",
        lambda **kwargs: _fake_plan(),
    )

    result = workers.run_ats_form_preparation_once(
        operation_id=operation["operation_id"],
        worker_id="ats-worker",
        poll_interval_seconds=0,
        plan_delivery=lambda **kwargs: _delivery(),
        session_starter=lambda plan: _session("WAITING_INPUT"),
        status_reader=lambda **kwargs: {},
        resolution_task_reader=lambda **kwargs: _runtime_resolution_payload(),
    )
    assert result["operation_status"] == "NEEDS_INPUT"
    unresolved = orchestrator.prepare_operation(operation["operation_id"])["unresolved"]
    assert len(unresolved) == 1
    assert unresolved[0].startswith("apply.runtime_question:")
    assert "apply.runtime_questions" not in unresolved


def test_apply_terminal_failure_fails_safely(worker_db, monkeypatch):
    operation = _ready_ats(worker_db, monkeypatch, "v1c-ats-failed")
    monkeypatch.setattr(
        workers.application_plan_v2,
        "prepare_application_plan",
        lambda **kwargs: _fake_plan(),
    )

    result = workers.run_ats_form_preparation_once(
        operation_id=operation["operation_id"],
        worker_id="ats-worker",
        poll_interval_seconds=0,
        plan_delivery=lambda **kwargs: _delivery(),
        session_starter=lambda plan: _session("FAILED_SAFELY"),
        status_reader=lambda **kwargs: {},
    )
    assert result["task_status"] == "FAILED"
    assert result["operation_status"] == "FAILED_SAFELY"


def test_poll_timeout_requeues_without_submission(worker_db, monkeypatch):
    operation = _ready_ats(worker_db, monkeypatch, "v1c-ats-timeout")
    monkeypatch.setattr(
        workers.application_plan_v2,
        "prepare_application_plan",
        lambda **kwargs: _fake_plan(),
    )

    result = workers.run_ats_form_preparation_once(
        operation_id=operation["operation_id"],
        worker_id="ats-worker",
        poll_timeout_seconds=0.0001,
        poll_interval_seconds=0,
        plan_delivery=lambda **kwargs: _delivery(),
        session_starter=lambda plan: _session("QUEUED"),
        status_reader=lambda **kwargs: _session("RUNNING")["preparation_job"],
    )
    assert result["task_status"] == "PENDING"
    assert result["retry_scheduled"] is True
    assert result["submission_authority"] is False


def test_v1c_workers_disabled_before_any_claim(worker_db, monkeypatch):
    operation = _start("v1c-disabled")
    _ready_dependencies(operation["operation_id"])
    monkeypatch.delenv(workers.WORKERS_ENV, raising=False)

    with pytest.raises(RuntimeError, match="disabled"):
        workers.run_application_preflight_once(
            operation_id=operation["operation_id"],
            worker_id="preflight-worker",
        )
    assert _task(
        operation["operation_id"], orchestrator.TASK_APPLICATION_PREFLIGHT
    )["status"] == "PENDING"


def test_v1c_never_grants_submission_authority(worker_db):
    assert workers.SUBMISSION_AUTHORITY is False
    permissions = workers._plan_permissions()
    assert permissions["final_review"] is False
    assert permissions["final_submit"] is False


# V1D runtime NEEDS_INPUT synchronization

def _runtime_resolution_payload(*, include_resolution=False, session_id="session-94"):
    task = {
        "schema_version": "resolution-task-v1", "task_id": "runtime-task-portfolio",
        "application_id": "application-94", "session_id": session_id,
        "checkpoint_id": "checkpoint-runtime", "page_id": "page-runtime",
        "control_id": "portfolio", "question_id": "portfolio",
        "question": "Portfolio URL", "semantic_type": "URL", "category": "MISSING_FACT",
        "sensitivity_class": "NORMAL", "status": "WAITING_FOR_USER", "risk_level": "LOW",
        "auto_resolvable": False, "requires_user": True,
        "grouping_scope": "APPLICATION", "group_key": "application-94",
        "reason": "No confirmed answer",
        "created_at": "2026-09-09T12:00:00+00:00",
        "updated_at": "2026-09-09T12:00:01+00:00",
        "submission_authority": False,
    }
    if include_resolution:
        task["resolution"] = {"value": "SECRET-MUST-NOT-PERSIST"}
    return {
        "version": "munshi-runtime-resolution-read-v1",
        "job_id": "prepare-job-94", "session_id": "session-94",
        "application_id": "application-94", "plan_id": "plan-94",
        "provider": "GREENHOUSE", "prepare_state": "WAITING_INPUT",
        "resolution_tasks": [task], "open_task_count": 1,
        "resolution_values_exposed": False, "final_review_authority": False,
        "submission_authority": False,
    }


def test_apply_waiting_input_syncs_exact_structured_runtime_questions(worker_db, monkeypatch):
    operation = _ready_ats(worker_db, monkeypatch, "v1d-runtime-sync")
    monkeypatch.setattr(workers.application_plan_v2, "prepare_application_plan", lambda **kwargs: _fake_plan())
    result = workers.run_ats_form_preparation_once(
        operation_id=operation["operation_id"], worker_id="ats-worker",
        poll_interval_seconds=0, plan_delivery=lambda **kwargs: _delivery(),
        session_starter=lambda plan: _session("WAITING_INPUT"),
        status_reader=lambda **kwargs: {},
        resolution_task_reader=lambda **kwargs: _runtime_resolution_payload(),
    )
    assert result["operation_status"] == "NEEDS_INPUT"
    persisted = _task(operation["operation_id"], orchestrator.TASK_ATS_FORM_PREPARATION)
    output = persisted["output"]
    assert output["runtime_question_details_synced"] is True
    assert output["runtime_question_details_pending_sync"] is False
    assert output["runtime_resolution_values_persisted"] is False
    assert output["replacement_plan_authority"] == "application_plan_resolution_v2"
    assert output["runtime_resolution_tasks"][0]["question"] == "Portfolio URL"
    assert output["runtime_resolution_tasks"][0]["sensitivity_class"] == "NORMAL"
    assert "resolution" not in output["runtime_resolution_tasks"][0]
    assert "SECRET-MUST-NOT-PERSIST" not in repr(output)
    unresolved = orchestrator.prepare_operation(operation["operation_id"])["unresolved"]
    assert len(unresolved) == 1
    assert unresolved[0].startswith("apply.runtime_question:")
    assert "apply.runtime_questions" not in unresolved


def test_runtime_sync_rejects_any_resolution_value_and_requeues(worker_db, monkeypatch):
    operation = _ready_ats(worker_db, monkeypatch, "v1d-resolution-leak")
    monkeypatch.setattr(workers.application_plan_v2, "prepare_application_plan", lambda **kwargs: _fake_plan())
    with pytest.raises(RuntimeError, match="exposed a resolution value"):
        workers.run_ats_form_preparation_once(
            operation_id=operation["operation_id"], worker_id="ats-worker",
            poll_interval_seconds=0, plan_delivery=lambda **kwargs: _delivery(),
            session_starter=lambda plan: _session("WAITING_INPUT"),
            status_reader=lambda **kwargs: {},
            resolution_task_reader=lambda **kwargs: _runtime_resolution_payload(include_resolution=True),
        )
    task = _task(operation["operation_id"], orchestrator.TASK_ATS_FORM_PREPARATION)
    assert task["status"] == "PENDING"
    assert "SECRET-MUST-NOT-PERSIST" not in repr(task)


def test_runtime_sync_rejects_cross_session_task(worker_db, monkeypatch):
    operation = _ready_ats(worker_db, monkeypatch, "v1d-cross-session")
    monkeypatch.setattr(workers.application_plan_v2, "prepare_application_plan", lambda **kwargs: _fake_plan())
    payload = _runtime_resolution_payload(session_id="session-other")
    payload["session_id"] = "session-94"
    with pytest.raises(RuntimeError, match="task binding"):
        workers.run_ats_form_preparation_once(
            operation_id=operation["operation_id"], worker_id="ats-worker",
            poll_interval_seconds=0, plan_delivery=lambda **kwargs: _delivery(),
            session_starter=lambda plan: _session("WAITING_INPUT"), status_reader=lambda **kwargs: {},
            resolution_task_reader=lambda **kwargs: payload,
        )


def test_waiting_input_before_task_visibility_requeues_for_safe_sync(worker_db, monkeypatch):
    operation = _ready_ats(worker_db, monkeypatch, "v1d-sync-race")
    monkeypatch.setattr(workers.application_plan_v2, "prepare_application_plan", lambda **kwargs: _fake_plan())
    payload = _runtime_resolution_payload()
    payload["resolution_tasks"] = []
    payload["open_task_count"] = 0
    result = workers.run_ats_form_preparation_once(
        operation_id=operation["operation_id"], worker_id="ats-worker",
        poll_interval_seconds=0, plan_delivery=lambda **kwargs: _delivery(),
        session_starter=lambda plan: _session("WAITING_INPUT"), status_reader=lambda **kwargs: {},
        resolution_task_reader=lambda **kwargs: payload,
    )
    assert result["task_status"] == "PENDING"
    assert result["runtime_question_sync_pending"] is True
    assert result["submission_authority"] is False


def test_runtime_sync_rejects_authority_escalation():
    payload = _runtime_resolution_payload()
    payload["submission_authority"] = True
    with pytest.raises(RuntimeError, match="not exactly correlated"):
        workers._validate_runtime_resolution_sync(
            payload, prepare_job_id="prepare-job-94", session_id="session-94",
            application_id="application-94", plan_id="plan-94",
        )


def test_provider_capability_retry_uses_new_immutable_key_only_for_exact_blocker(
    monkeypatch,
) -> None:
    operation = {
        "operation_id": "prepare-op-provider-retry",
        "application_id": "application-94",
        "job_id": 94,
    }
    pending = {"output": {"plan_id": "prior-provider-plan"}}
    preflight = {
        "preflight_package_id": "preflight-94",
        "resume_version_id": "resume-v5-94",
        "resume_pdf_sha256": "d" * 64,
    }
    prior = {
        "plan_id": "prior-provider-plan",
        "status": "NEEDS_INPUT",
        "executable": False,
        "submission_authority": False,
        "application_id": "application-94",
        "job_id": 94,
        "preflight_package_id": "preflight-94",
        "provider": "UNSUPPORTED",
        "inputs": {
            "preflight_package_id": "preflight-94",
            "answer_requirements": [],
            "permissions": {},
            "provider_policy": {},
            "secure_resolver_refs": {},
            "resume_format": "PDF",
            "answer_overrides": {},
        },
        "snapshot": {
            "expected_state": "NEEDS_INPUT",
            "executable": False,
            "global_blockers": ["provider_not_permitted_or_supported"],
            "unresolved": [],
            "provider_policy": {
                "provider": "UNSUPPORTED",
                "permitted": False,
            },
            "resume": {
                "version_id": "resume-v5-94",
                "artifact_sha256": "d" * 64,
            },
        },
    }
    monkeypatch.setattr(
        workers.application_plan_v2,
        "get_application_plan",
        lambda _plan_id: prior,
    )
    monkeypatch.setattr(
        workers.application_plan_v2,
        "_capture",
        lambda **_kwargs: {
            **prior["snapshot"],
            "expected_state": "READY_TO_APPLY",
            "executable": True,
            "global_blockers": [],
            "provider_policy": {
                "provider": "AGILE_ATS",
                "permitted": True,
            },
        },
    )

    key, prior_id = workers._application_plan_idempotency_key(
        operation=operation,
        pending_task=pending,
        preflight=preflight,
    )

    assert key == (
        "prepare:prepare-op-provider-retry:application-plan:v1c:"
        "provider-capability-v2"
    )
    assert prior_id == "prior-provider-plan"


def test_provider_capability_retry_never_bypasses_mixed_or_answer_blockers(
    monkeypatch,
) -> None:
    operation = {
        "operation_id": "prepare-op-provider-retry",
        "application_id": "application-94",
        "job_id": 94,
    }
    pending = {"output": {"plan_id": "prior-provider-plan"}}
    preflight = {
        "preflight_package_id": "preflight-94",
        "resume_version_id": "resume-v5-94",
        "resume_pdf_sha256": "d" * 64,
    }
    prior = {
        "plan_id": "prior-provider-plan",
        "status": "NEEDS_INPUT",
        "executable": False,
        "submission_authority": False,
        "application_id": "application-94",
        "job_id": 94,
        "preflight_package_id": "preflight-94",
        "provider": "UNSUPPORTED",
        "snapshot": {
            "expected_state": "NEEDS_INPUT",
            "global_blockers": [
                "provider_not_permitted_or_supported",
                "resume_upload_permission_missing",
            ],
            "unresolved": [{"question_key": "job.custom", "reason": "needs_input"}],
            "provider_policy": {
                "provider": "UNSUPPORTED",
                "permitted": False,
            },
            "resume": {
                "version_id": "resume-v5-94",
                "artifact_sha256": "d" * 64,
            },
        },
    }
    monkeypatch.setattr(
        workers.application_plan_v2,
        "get_application_plan",
        lambda _plan_id: prior,
    )

    key, prior_id = workers._application_plan_idempotency_key(
        operation=operation,
        pending_task=pending,
        preflight=preflight,
    )

    assert key == "prepare:prepare-op-provider-retry:application-plan:v1c"
    assert prior_id is None


def test_provider_capability_retry_fails_closed_on_binding_drift(monkeypatch) -> None:
    operation = {
        "operation_id": "prepare-op-provider-retry",
        "application_id": "application-94",
        "job_id": 94,
    }
    pending = {"output": {"plan_id": "prior-provider-plan"}}
    preflight = {
        "preflight_package_id": "preflight-94",
        "resume_version_id": "resume-v5-94",
        "resume_pdf_sha256": "d" * 64,
    }
    prior = {
        "plan_id": "prior-provider-plan",
        "status": "NEEDS_INPUT",
        "executable": False,
        "submission_authority": False,
        "application_id": "application-94",
        "job_id": 94,
        "preflight_package_id": "preflight-94",
        "provider": "UNSUPPORTED",
        "snapshot": {
            "expected_state": "NEEDS_INPUT",
            "global_blockers": ["provider_not_permitted_or_supported"],
            "unresolved": [],
            "provider_policy": {
                "provider": "UNSUPPORTED",
                "permitted": False,
            },
            "resume": {
                "version_id": "resume-v5-94",
                "artifact_sha256": "e" * 64,
            },
        },
    }
    monkeypatch.setattr(
        workers.application_plan_v2,
        "get_application_plan",
        lambda _plan_id: prior,
    )

    with pytest.raises(RuntimeError, match="mismatched prior Application Plan"):
        workers._application_plan_idempotency_key(
            operation=operation,
            pending_task=pending,
            preflight=preflight,
        )


def test_provider_capability_retry_fails_closed_on_non_provider_semantic_drift(
    monkeypatch,
) -> None:
    operation = {
        "operation_id": "prepare-op-provider-retry-drift",
        "application_id": "application-94",
        "job_id": 94,
    }
    pending = {"output": {"plan_id": "prior-provider-plan"}}
    preflight = {
        "preflight_package_id": "preflight-94",
        "resume_version_id": "resume-v5-94",
        "resume_pdf_sha256": "d" * 64,
    }
    prior = {
        "plan_id": "prior-provider-plan",
        "status": "NEEDS_INPUT",
        "executable": False,
        "submission_authority": False,
        "application_id": "application-94",
        "job_id": 94,
        "preflight_package_id": "preflight-94",
        "provider": "UNSUPPORTED",
        "inputs": {
            "preflight_package_id": "preflight-94",
            "answer_requirements": [],
            "permissions": {},
            "provider_policy": {},
            "secure_resolver_refs": {},
            "resume_format": "PDF",
            "answer_overrides": {},
        },
        "snapshot": {
            "expected_state": "NEEDS_INPUT",
            "executable": False,
            "global_blockers": ["provider_not_permitted_or_supported"],
            "unresolved": [],
            "provider_policy": {
                "provider": "UNSUPPORTED",
                "permitted": False,
            },
            "resume": {
                "version_id": "resume-v5-94",
                "artifact_sha256": "d" * 64,
            },
            "candidate_truth_binding": {"profile_digest": "a" * 64},
        },
    }
    monkeypatch.setattr(
        workers.application_plan_v2,
        "get_application_plan",
        lambda _plan_id: prior,
    )
    monkeypatch.setattr(
        workers.application_plan_v2,
        "_capture",
        lambda **_kwargs: {
            **prior["snapshot"],
            "expected_state": "READY_TO_APPLY",
            "executable": True,
            "global_blockers": [],
            "provider_policy": {
                "provider": "AGILE_ATS",
                "permitted": True,
            },
            "candidate_truth_binding": {"profile_digest": "b" * 64},
        },
    )

    with pytest.raises(RuntimeError, match="non-provider Application Plan drift"):
        workers._application_plan_idempotency_key(
            operation=operation,
            pending_task=pending,
            preflight=preflight,
        )


def test_ats_browser_waits_for_single_prebrowser_mandate(
    worker_db, monkeypatch
):
    operation = _ready_ats(
        worker_db,
        monkeypatch,
        "v1c-single-mandate-gate",
    )
    monkeypatch.setattr(
        workers.application_execution_mandate_v1,
        "current_for_preflight",
        lambda package_id: None,
    )

    def browser_must_not_start(**kwargs):
        raise AssertionError("ATS plan/browser must not start before customer mandate")

    monkeypatch.setattr(
        workers.application_plan_v2,
        "prepare_application_plan",
        browser_must_not_start,
    )

    result = workers.run_ats_form_preparation_once(
        operation_id=operation["operation_id"],
        worker_id="ats-worker",
    )

    assert result["claimed"] is False
    assert result["reason"] == "single_customer_approval_required"
    assert _task(
        operation["operation_id"],
        orchestrator.TASK_ATS_FORM_PREPARATION,
    )["status"] == "PENDING"
    assert result["submission_authority"] is False
