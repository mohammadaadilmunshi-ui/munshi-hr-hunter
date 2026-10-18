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
    monkeypatch.setattr(orchestrator, "current_owner_readonly", lambda _connection: OWNER)
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


def test_preflight_retry_reuses_exact_fresh_package_without_writer_call(
    worker_db, monkeypatch
):
    prior = _start("v1c-preflight-reuse-prior")
    _ready_dependencies(prior["operation_id"], cover="OPTIONAL")
    prior_output = {
        "version": workers.WORKER_VERSION,
        "preflight_package_id": "preflight-reused-94",
        "preflight_package_digest": "e" * 64,
        "preflight_status": "READY_TO_APPLY",
        "opportunity_evaluation_id": "opportunity-94",
        "opportunity_result_sha256": "f" * 64,
        "relationship_strategy_id": None,
        "resume_version_id": "resume-v5-94",
        "resume_pdf_sha256": "d" * 64,
        "cover_letter_decision": "OPTIONAL",
        "cover_letter_id": "cover-letter-94",
        "cover_letter_artifact_sha256": "c" * 64,
        "cover_letter_page_count": 1,
        "single_approval_package_ready": True,
        "answer_inventory_snapshot_complete": True,
        "known_requirement_set_complete": True,
        "live_form_discovery_pending": True,
        "employer_form_claimed_complete": False,
        "submission_authority": False,
        "automatic_submission_executed": False,
    }
    _complete_root(
        prior["operation_id"],
        orchestrator.TASK_APPLICATION_PREFLIGHT,
        "prior-preflight-worker",
        prior_output,
    )

    retry = _start("v1c-preflight-reuse-retry")
    _ready_dependencies(retry["operation_id"], cover="OPTIONAL")
    monkeypatch.setattr(
        workers.preflight_v1,
        "get_preflight_package",
        lambda package_id: {
            "package_id": package_id,
            "package_digest": "e" * 64,
            "application_id": "application-94",
            "job_id": 94,
            "job_snapshot_sha256": JOB_DIGEST,
            "resume_version_id": "resume-v5-94",
            "status": "READY_TO_APPLY",
            "snapshot": {
                "application_truth": {
                    "candidate_profile_binding": {
                        "profile_digest": PROFILE_DIGEST,
                    },
                },
            },
        },
    )
    monkeypatch.setattr(
        workers.preflight_v1,
        "preflight_package_freshness",
        lambda package_id: {"fresh": True},
    )
    monkeypatch.setattr(
        workers.preflight_v1,
        "prepare_preflight_package",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("retry must not create a new preflight package")
        ),
    )
    monkeypatch.setattr(
        workers.native_cover_letter_writer_v1,
        "generate",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("retry must not regenerate a cover letter")
        ),
    )

    result = workers.run_application_preflight_once(
        operation_id=retry["operation_id"],
        worker_id="retry-preflight-worker",
    )

    assert result["task_status"] == "SUCCEEDED"
    assert result["reused_preflight_package"] is True
    assert result["output"] == prior_output


def test_preflight_retry_prefers_equally_fresh_package_with_existing_mandate(
    worker_db, monkeypatch
):
    def complete_prior(key: str, package_id: str, package_digest: str) -> dict:
        operation = _start(key)
        _ready_dependencies(operation["operation_id"], cover="OPTIONAL")
        output = {
            "version": workers.WORKER_VERSION,
            "preflight_package_id": package_id,
            "preflight_package_digest": package_digest,
            "preflight_status": "READY_TO_APPLY",
            "opportunity_evaluation_id": "opportunity-94",
            "opportunity_result_sha256": "f" * 64,
            "relationship_strategy_id": None,
            "resume_version_id": "resume-v5-94",
            "resume_pdf_sha256": "d" * 64,
            "cover_letter_decision": "OPTIONAL",
            "cover_letter_id": "cover-letter-94",
            "cover_letter_artifact_sha256": "c" * 64,
            "cover_letter_page_count": 1,
            "single_approval_package_ready": True,
            "answer_inventory_snapshot_complete": True,
            "known_requirement_set_complete": True,
            "live_form_discovery_pending": True,
            "employer_form_claimed_complete": False,
            "submission_authority": False,
            "automatic_submission_executed": False,
        }
        _complete_root(
            operation["operation_id"],
            orchestrator.TASK_APPLICATION_PREFLIGHT,
            "prior-preflight-worker",
            output,
        )
        return output

    approved_output = complete_prior(
        "v1c-preflight-approved-prior", "preflight-approved-94", "e" * 64
    )
    complete_prior(
        "v1c-preflight-unapproved-newer", "preflight-unapproved-94", "a" * 64
    )
    retry = _start("v1c-preflight-prefer-approved-retry")
    _ready_dependencies(retry["operation_id"], cover="OPTIONAL")

    monkeypatch.setattr(
        workers.preflight_v1,
        "get_preflight_package",
        lambda package_id: {
            "package_id": package_id,
            "package_digest": (
                "e" * 64 if package_id == "preflight-approved-94" else "a" * 64
            ),
            "application_id": "application-94",
            "job_id": 94,
            "job_snapshot_sha256": JOB_DIGEST,
            "resume_version_id": "resume-v5-94",
            "status": "READY_TO_APPLY",
            "snapshot": {
                "application_truth": {
                    "candidate_profile_binding": {
                        "profile_digest": PROFILE_DIGEST,
                    },
                },
            },
        },
    )
    monkeypatch.setattr(
        workers.preflight_v1,
        "preflight_package_freshness",
        lambda package_id: {"fresh": True},
    )
    monkeypatch.setattr(
        workers.application_execution_mandate_v1,
        "current_for_preflight",
        lambda package_id: (
            {"mandate_id": "execution-mandate-94"}
            if package_id == "preflight-approved-94"
            else None
        ),
    )
    monkeypatch.setattr(
        workers.preflight_v1,
        "prepare_preflight_package",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("retry must reuse the approved preflight package")
        ),
    )
    monkeypatch.setattr(
        workers.native_cover_letter_writer_v1,
        "generate",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("retry must not regenerate a cover letter")
        ),
    )

    result = workers.run_application_preflight_once(
        operation_id=retry["operation_id"],
        worker_id="retry-preflight-worker",
    )

    assert result["reused_preflight_package"] is True
    assert result["output"] == approved_output


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
    assert result["output"]["customer_review_approved"] is True
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
    assert task["error_code"] == "APPLICATION_PREFLIGHT_COVER_LETTER_FAILED"
    assert "writer unavailable" not in str(task["error_message"])


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
    assert result["output"]["customer_review_approved"] is True
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

    terminal = _session("FAILED_SAFELY")
    terminal["preparation_job"]["last_error"] = "browser adapter failed closed"
    result = workers.run_ats_form_preparation_once(
        operation_id=operation["operation_id"],
        worker_id="ats-worker",
        poll_interval_seconds=0,
        plan_delivery=lambda **kwargs: _delivery(),
        session_starter=lambda plan: terminal,
        status_reader=lambda **kwargs: {},
    )
    assert result["task_status"] == "FAILED"
    assert result["operation_status"] == "FAILED_SAFELY"
    assert result["apply_last_error"] == "browser adapter failed closed"
    task = _task(
        operation["operation_id"], orchestrator.TASK_ATS_FORM_PREPARATION
    )
    assert "browser adapter failed closed" in str(task["error_message"])


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


def test_apply_bridge_failure_preserves_safe_machine_code(worker_db, monkeypatch):
    operation = _ready_ats(worker_db, monkeypatch, "v1c-bridge-code")
    monkeypatch.setattr(
        workers.application_plan_v2,
        "prepare_application_plan",
        lambda **kwargs: _fake_plan(),
    )

    def fail_delivery(**_kwargs):
        raise workers.ApplyRuntimeRequestError(
            "STALE_HOSTED_PLAN",
            "private provider details must not persist",
            retryable=True,
        )

    with pytest.raises(workers.ApplyRuntimeRequestError):
        workers.run_ats_form_preparation_once(
            operation_id=operation["operation_id"],
            worker_id="ats-worker",
            poll_interval_seconds=0,
            plan_delivery=fail_delivery,
        )

    task = _task(
        operation["operation_id"], orchestrator.TASK_ATS_FORM_PREPARATION
    )
    assert task["status"] == "PENDING"
    assert task["error_code"] == "STALE_HOSTED_PLAN"
    assert "private provider details" not in str(task)


def test_apply_terminal_diagnostic_redacts_pii_and_credentials():
    assert workers._safe_apply_diagnostic(
        "Upload failed for person@example.com at https://ats.example/path"
    ) == "Upload failed for <email> at <url>"
    assert "secret-value" not in workers._safe_apply_diagnostic(
        "OAuth session_token secret-value"
    )


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
        "question": "Portfolio URL", "semantic_type": "URL",
        "question_observation": {
            "raw_question": "Portfolio URL", "label": "Portfolio URL",
            "section_heading": "Links", "control_id": "portfolio",
            "apply_semantic_type": "UNKNOWN", "options": [],
        },
        "category": "MISSING_FACT",
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
        "browser_form_digest": "f" * 64, "checkpoint_id": "checkpoint-runtime",
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


def test_content_addressed_plan_follows_existing_runtime_replacement(
    monkeypatch,
) -> None:
    prior = {"plan_id": "plan-prior"}
    replacement = {
        "plan_id": "plan-replacement",
        "idempotency_key": "runtime-replacement-key",
    }
    seen = {}
    monkeypatch.setattr(
        workers.application_plan_v2,
        "runtime_plan_is_superseded",
        lambda plan_id: plan_id == "plan-prior",
    )

    def runtime_replacement(**kwargs):
        seen.update(kwargs)
        return replacement, "plan-prior"

    monkeypatch.setattr(workers, "_runtime_replacement_plan", runtime_replacement)
    resolved, prior_id = workers._follow_content_addressed_runtime_replacement(
        plan=prior,
        operation={"operation_id": "operation-94"},
        preflight={"preflight_package_id": "preflight-94"},
    )

    assert resolved == replacement
    assert prior_id == "plan-prior"
    assert seen["pending_task"] == {"output": {"plan_id": "plan-prior"}}


def test_content_addressed_plan_follows_chained_runtime_replacements(
    monkeypatch,
) -> None:
    plans = {
        "plan-prior": {"plan_id": "plan-prior"},
        "plan-middle": {
            "plan_id": "plan-middle",
            "idempotency_key": "runtime-middle-key",
        },
        "plan-current": {
            "plan_id": "plan-current",
            "idempotency_key": "runtime-current-key",
        },
    }
    replacements = {
        "plan-prior": "plan-middle",
        "plan-middle": "plan-current",
    }
    seen = []

    monkeypatch.setattr(
        workers.application_plan_v2,
        "runtime_plan_is_superseded",
        lambda plan_id: plan_id in replacements,
    )

    def runtime_replacement(**kwargs):
        prior_id = kwargs["pending_task"]["output"]["plan_id"]
        seen.append(prior_id)
        replacement_id = replacements[prior_id]
        return plans[replacement_id], prior_id

    monkeypatch.setattr(workers, "_runtime_replacement_plan", runtime_replacement)

    resolved, prior_id = workers._follow_content_addressed_runtime_replacement(
        plan=plans["plan-prior"],
        operation={"operation_id": "operation-94-chain"},
        preflight={"preflight_package_id": "preflight-94-chain"},
    )

    assert resolved == plans["plan-current"]
    assert prior_id == "plan-middle"
    assert seen == ["plan-prior", "plan-middle"]


def test_content_addressed_plan_rejects_runtime_replacement_cycle(
    monkeypatch,
) -> None:
    plans = {
        "plan-a": {"plan_id": "plan-a"},
        "plan-b": {"plan_id": "plan-b"},
    }
    replacements = {
        "plan-a": "plan-b",
        "plan-b": "plan-a",
    }

    monkeypatch.setattr(
        workers.application_plan_v2,
        "runtime_plan_is_superseded",
        lambda _plan_id: True,
    )

    def runtime_replacement(**kwargs):
        prior_id = kwargs["pending_task"]["output"]["plan_id"]
        return plans[replacements[prior_id]], prior_id

    monkeypatch.setattr(workers, "_runtime_replacement_plan", runtime_replacement)

    with pytest.raises(RuntimeError, match="contains a cycle"):
        workers._follow_content_addressed_runtime_replacement(
            plan=plans["plan-a"],
            operation={"operation_id": "operation-94-cycle"},
            preflight={"preflight_package_id": "preflight-94-cycle"},
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


def test_universal_runtime_auto_resolution_creates_one_immutable_replacement(monkeypatch):
    payload = _runtime_resolution_payload()
    task = dict(payload["resolution_tasks"][0])
    task["group_key"] = "unknown.portfolio"
    task["question_observation"] = {
        "raw_question": "State New Jersey",
        "label": "State",
        "section_heading": "Contact Information",
        "control_id": "portfolio",
        "apply_semantic_type": "UNKNOWN",
        "options": ["New Jersey", "New York", "Pennsylvania"],
    }
    seen = {}
    monkeypatch.setattr(workers.universal_graph, "universal_normal_autofill_enabled", lambda: True)
    monkeypatch.setattr(
        workers.application_plan_v2,
        "runtime_application_plan_resolution_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        workers.universal_answers,
        "resolve_observation",
        lambda observation, **_kwargs: {
            "answer_status": "ANSWERED",
            "answer_mode": "DIRECT_FACT",
            "concept_id": "contact.region",
            "value": "New Jersey",
            "value_ref": "hunter-value://universal-brain/region",
            "confidence": 0.99,
            "autofill_allowed": True,
            "knowledge_revision": 27,
            "question_resolution": {
                "classification_lane": "ONTOLOGY_CONTEXT",
                "confidence": 0.99,
            },
            "evidence_ids": [],
        },
    )
    monkeypatch.setattr(
        workers.universal_answers,
        "public_trace",
        lambda result: {key: value for key, value in result.items() if key != "value"},
    )

    def resolve_runtime_needs_input(**kwargs):
        seen.update(kwargs)
        return {
            "success": True,
            "prior_plan_id": "plan-94",
            "replacement_plan_id": "plan-95",
            "replacement_plan_digest": "9" * 64,
            "handoff_id": "handoff-95",
            "state": "PLAN_ACCEPTED",
            "replayed": False,
            "submission_authority": False,
        }

    monkeypatch.setattr(
        workers.application_plan_resolution_v2,
        "resolve_runtime_needs_input",
        resolve_runtime_needs_input,
    )
    result = workers._universal_runtime_auto_resolution(
        operation_id="operation-94",
        plan={"plan_id": "plan-94", "plan_digest": "8" * 64},
        session_id="session-94",
        resolution_payload=payload,
        runtime_tasks=[task],
    )
    assert result is not None
    assert result["replacement"]["replacement_plan_id"] == "plan-95"
    assert result["resolved_count"] == 1
    assert result["value_exposed"] is False
    assert seen["runtime_context"]["unresolved_questions"][0]["question_observation"]["label"] == "State"
    assert seen["answer_resolutions"] == [
        {
            "question_key": "unknown.portfolio",
            "canonical_answer": "New Jersey",
            "provenance": "universal_brain_existing_knowledge",
        }
    ]
    assert "New Jersey" not in repr(result["trace"])


def test_universal_runtime_referral_derives_current_hunter_job_source(monkeypatch):
    payload = _runtime_resolution_payload()
    task = dict(payload["resolution_tasks"][0])
    task.update(
        {
            "group_key": "q-referral",
            "semantic_type": "REFERRAL",
            "question": "How Did You Hear About Us?",
            "question_observation": {
                "raw_question": "How Did You Hear About Us?",
                "label": "How Did You Hear About Us?",
                "control_id": "referral",
                "apply_semantic_type": "REFERRAL",
                "apply_semantic_confidence": 1.0,
                "options": ["Indeed", "LinkedIn", "Other"],
            },
        }
    )
    seen = []
    job_source = {"value": "JobSpy/indeed"}
    monkeypatch.setattr(workers.universal_graph, "universal_normal_autofill_enabled", lambda: True)
    monkeypatch.setattr(workers.universal_graph, "universal_derivation_enabled", lambda: True)
    monkeypatch.setattr(
        workers.application_plan_v2,
        "runtime_application_plan_resolution_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        workers.phase67_common,
        "safe_owned_job_snapshot",
        lambda job_id, **_kwargs: {
            "job_snapshot_sha256": "d" * 64,
            "job": {"id": int(job_id), "source": job_source["value"]},
        },
    )
    monkeypatch.setattr(
        workers.universal_answers.ingestion,
        "read_only_knowledge_view",
        lambda: {"profile_revision": 6, "facts": [], "answer_memory": [], "claims": [], "profile": {}},
    )

    def resolve_runtime_needs_input(**kwargs):
        seen.append(kwargs)
        return {
            "success": True,
            "prior_plan_id": "plan-94",
            "replacement_plan_id": "plan-95",
            "replacement_plan_digest": "9" * 64,
            "handoff_id": "handoff-95",
            "state": "PLAN_ACCEPTED",
            "replayed": False,
            "submission_authority": False,
        }

    monkeypatch.setattr(
        workers.application_plan_resolution_v2,
        "resolve_runtime_needs_input",
        resolve_runtime_needs_input,
    )
    result = workers._universal_runtime_auto_resolution(
        operation_id="operation-94",
        plan={
            "plan_id": "plan-94",
            "plan_digest": "8" * 64,
            "job_id": 4465,
            "snapshot": {"job": {"id": 4465, "job_snapshot_digest": "d" * 64}},
        },
        session_id="session-94",
        resolution_payload=payload,
        runtime_tasks=[task],
    )

    assert result is not None
    assert seen[0]["answer_resolutions"] == [
        {
            "question_key": "q-referral",
            "canonical_answer": "Indeed",
            "provenance": "universal_brain_derived_evidence",
        }
    ]

    first_revision_key = result["revision_idempotency_key"]
    job_source["value"] = "JobSpy/linkedin"
    second = workers._universal_runtime_auto_resolution(
        operation_id="operation-94",
        plan={
            "plan_id": "plan-94",
            "plan_digest": "8" * 64,
            "job_id": 4465,
            "snapshot": {"job": {"id": 4465, "job_snapshot_digest": "d" * 64}},
        },
        session_id="session-94",
        resolution_payload=payload,
        runtime_tasks=[task],
    )
    assert second is not None
    assert seen[1]["answer_resolutions"][0]["canonical_answer"] == "LinkedIn"
    assert second["revision_idempotency_key"] != first_revision_key


def test_universal_runtime_auto_resolution_falls_through_when_any_fact_is_missing(monkeypatch):
    payload = _runtime_resolution_payload()
    task = dict(payload["resolution_tasks"][0])
    task["group_key"] = "unknown.portfolio"
    monkeypatch.setattr(workers.universal_graph, "universal_normal_autofill_enabled", lambda: True)
    monkeypatch.setattr(
        workers.application_plan_v2,
        "runtime_application_plan_resolution_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        workers.universal_answers,
        "resolve_observation",
        lambda observation, **_kwargs: {
            "answer_status": "MISSING_FACT",
            "concept_id": "contact.portfolio",
            "autofill_allowed": False,
            "confidence": 0.9,
            "knowledge_revision": 27,
            "question_resolution": {"classification_lane": "ONTOLOGY_RETRIEVAL"},
        },
    )
    monkeypatch.setattr(workers.universal_answers, "public_trace", lambda result: dict(result))
    assert workers._universal_runtime_auto_resolution(
        operation_id="operation-94",
        plan={"plan_id": "plan-94", "plan_digest": "8" * 64},
        session_id="session-94",
        resolution_payload=payload,
        runtime_tasks=[task],
    ) is None


def test_apply_semantic_interpretation_bridge_is_value_free(monkeypatch):
    seen = {}

    def fake_request(**kwargs):
        seen.update(kwargs)
        return {
            "concept_id": "contact.region",
            "confidence": 0.97,
            "reason_code": "STATE_SELECTION_CONTEXT",
            "candidate_values_sent": False,
            "secret_material_sent": False,
            "protected_values_sent": False,
        }

    monkeypatch.setattr(workers, "_apply_base_url", lambda: "http://apply.internal")
    monkeypatch.setattr(
        workers,
        "_apply_headers",
        lambda **kwargs: {"X-Munshi-Tenant-Id": kwargs["tenant_id"], "X-Munshi-User-Id": kwargs["user_id"]},
    )
    monkeypatch.setattr(workers, "_json_request", fake_request)
    payload = {
        "resolver_version": "universal-question-resolver-v1",
        "observation": {"raw_question": "Home base", "label": "Home base"},
        "ontology_candidates": [
            {
                "concept_id": "contact.region",
                "score": 0.72,
                "sensitivity": "NORMAL",
                "answer_mode": "DIRECT_FACT",
                "category": "CONTACT",
            }
        ],
        "candidate_values": None,
    }
    result = workers._interpret_apply_universal_semantics(
        payload, tenant_id="tenant-a", user_id="user-a"
    )
    assert result == {
        "concept_id": "contact.region",
        "confidence": 0.97,
        "reason_code": "STATE_SELECTION_CONTEXT",
    }
    assert seen["url"].endswith("/v1/universal-brain/semantic-interpretation")
    sent = seen["body"]["payload"]
    assert sent["candidate_values"] is None
    assert "canonical_answer" not in repr(sent)
    assert "execution_value" not in repr(sent)


def test_start_apply_session_accepts_correlated_needs_input_replay(monkeypatch):
    monkeypatch.setattr(workers, "_apply_base_url", lambda: "http://apply.internal")
    monkeypatch.setattr(workers, "_apply_headers", lambda **kwargs: {})
    monkeypatch.setattr(
        workers,
        "_json_request",
        lambda **kwargs: {
            "session_id": "session-94",
            "application_id": "application-94",
            "plan_id": "plan-94",
            "state": "NEEDS_INPUT",
            "preparation_job": {
                "job_id": "prepare-job-94",
                "session_id": "session-94",
                "application_id": "application-94",
                "plan_id": "plan-94",
                "state": "WAITING_INPUT",
            },
        },
    )

    result = workers._start_apply_session(
        {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "application_id": "application-94",
            "plan_id": "plan-94",
        }
    )

    assert result["session_id"] == "session-94"
    assert result["state"] == "NEEDS_INPUT"
    assert result["preparation_job"]["state"] == "WAITING_INPUT"


def test_start_apply_session_rejects_post_review_replay(monkeypatch):
    monkeypatch.setattr(workers, "_apply_base_url", lambda: "http://apply.internal")
    monkeypatch.setattr(workers, "_apply_headers", lambda **kwargs: {})
    monkeypatch.setattr(
        workers,
        "_json_request",
        lambda **kwargs: {
            "session_id": "session-94",
            "application_id": "application-94",
            "plan_id": "plan-94",
            "state": "READY_TO_SUBMIT",
            "preparation_job": {
                "job_id": "prepare-job-94",
                "session_id": "session-94",
                "application_id": "application-94",
                "plan_id": "plan-94",
                "state": "READY_FOR_REVIEW",
            },
        },
    )

    with pytest.raises(RuntimeError, match="session acknowledgement"):
        workers._start_apply_session(
            {
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "application_id": "application-94",
                "plan_id": "plan-94",
            }
        )


def test_apply_semantic_bridge_rejects_provider_contract_leak(monkeypatch):
    monkeypatch.setattr(workers, "_apply_base_url", lambda: "http://apply.internal")
    monkeypatch.setattr(workers, "_apply_headers", lambda **kwargs: {})
    monkeypatch.setattr(
        workers,
        "_json_request",
        lambda **kwargs: {
            "concept_id": "contact.region",
            "confidence": 0.99,
            "candidate_values_sent": True,
            "secret_material_sent": False,
            "protected_values_sent": False,
        },
    )
    with pytest.raises(RuntimeError, match="value-free"):
        workers._interpret_apply_universal_semantics(
            {
                "resolver_version": "v1",
                "observation": {"label": "Home base"},
                "ontology_candidates": [],
                "candidate_values": None,
            },
            tenant_id="tenant-a",
            user_id="user-a",
        )

def test_ats_prepare_poll_timeout_policy_is_generic_and_runtime_configurable(
    monkeypatch,
):
    monkeypatch.delenv(workers.ATS_PREPARE_POLL_TIMEOUT_ENV, raising=False)
    assert workers._ats_prepare_poll_timeout_seconds() == 300.0

    monkeypatch.setenv(workers.ATS_PREPARE_POLL_TIMEOUT_ENV, "420")
    assert workers._ats_prepare_poll_timeout_seconds() == 420.0

    assert workers._ats_prepare_poll_timeout_seconds(0.25) == 0.25

    monkeypatch.setenv(workers.ATS_PREPARE_POLL_TIMEOUT_ENV, "0")
    with pytest.raises(ValueError, match="between 0 and 3600"):
        workers._ats_prepare_poll_timeout_seconds()

    monkeypatch.setenv(workers.ATS_PREPARE_POLL_TIMEOUT_ENV, "not-a-number")
    with pytest.raises(ValueError, match="numeric value"):
        workers._ats_prepare_poll_timeout_seconds()
