from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import database
from app import prepare_orchestrator_v1 as orchestrator
from app import prepare_worker_adapters_v1b as workers


OWNER = SimpleNamespace(tenant_id="default", user_id="owner")
JOB_DIGEST = "a" * 64
PROJECTION_DIGEST = "b" * 64
PROFILE_DIGEST = "c" * 64


@pytest.fixture()
def worker_db(hunter_db, monkeypatch):
    monkeypatch.setenv(workers.WORKERS_ENV, "1")
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
                   id,job_fingerprint,source,company_name,title,apply_url,description_raw
               ) VALUES(
                   93,'prepare-worker-v1b-job-93','fixture','Acme','People Analyst',
                   'https://boards.greenhouse.io/acme/jobs/93','Fixture description'
               )"""
        )
        connection.commit()
    finally:
        connection.close()

    orchestrator.ensure_schema()
    return hunter_db


def _start(key):
    return orchestrator.start_prepare(
        application_id="application-93",
        job_id=93,
        engine="NATIVE",
        idempotency_key=key,
    )


def _task(operation_id, kind):
    operation = orchestrator.prepare_operation(operation_id)
    return next(row for row in operation["tasks"] if row["task_kind"] == kind)


def _truth(*, projection_digest=PROJECTION_DIGEST, profile_digest=PROFILE_DIGEST):
    return {
        "projection_digest": projection_digest,
        "candidate_profile_binding": {"profile_digest": profile_digest},
        "job_context": {"job_snapshot_sha256": JOB_DIGEST},
        "unresolved_fact_keys": ["application_defaults.sponsorship_required"],
        "submission_authority": False,
    }


def _planning(*, profile_digest=PROFILE_DIGEST):
    return {
        "candidate_truth_binding": {"profile_digest": profile_digest},
        "answers": [
            {
                "answer_id": "answer-1",
                "question_key": "salary.expectation",
                "question_family": "salary",
                "canonical_answer": "SHOULD NEVER ENTER ORCHESTRATOR OUTPUT",
                "source": "candidate",
                "confidence": 1.0,
                "user_confirmed": True,
                "autofill_allowed": True,
                "planning_use": "autofill_ready",
            },
            {
                "answer_id": "answer-2",
                "question_key": "job.why_company",
                "question_family": "open_ended_job_specific",
                "canonical_answer": "ALSO MUST NOT ENTER OUTPUT",
                "source": "candidate",
                "confidence": 1.0,
                "user_confirmed": True,
                "autofill_allowed": False,
                "planning_use": "context_only",
            },
        ],
        "excluded_answers": [
            {
                "answer_id": "answer-stale",
                "question_key": "location.current",
                "question_family": "location",
                "reason": "stale_or_unbound_profile_answer",
            }
        ],
    }


def test_answer_worker_snapshots_safe_inventory_without_answer_bodies(worker_db, monkeypatch):
    operation = _start("v1b-answer-safe")
    monkeypatch.setattr(workers, "current_application_truth_projection", lambda job_id: _truth())
    monkeypatch.setattr(workers.answer_brain_v2, "planning_input", lambda: _planning())

    result = workers.run_answer_resolution_once(
        operation_id=operation["operation_id"],
        worker_id="answer-worker",
    )

    assert result["task_status"] == "SUCCEEDED"
    output = result["output"]
    assert output["answer_inventory"]["autofill_ready_count"] == 1
    assert output["answer_inventory"]["context_only_count"] == 1
    assert output["answer_inventory"]["excluded_count"] == 1
    assert output["application_questions_complete"] is False
    assert output["requires_ats_form_confirmation"] is True
    assert output["canonical_answer_bodies_persisted"] is False
    assert output["protected_values_persisted"] is False
    assert output["submission_authority"] is False

    serialized = repr(output)
    assert "SHOULD NEVER ENTER" not in serialized
    assert "ALSO MUST NOT ENTER" not in serialized


def test_answer_worker_stale_truth_fails_closed_and_requeues(worker_db, monkeypatch):
    operation = _start("v1b-answer-stale")
    monkeypatch.setattr(
        workers,
        "current_application_truth_projection",
        lambda job_id: _truth(projection_digest="f" * 64),
    )
    monkeypatch.setattr(workers.answer_brain_v2, "planning_input", lambda: _planning())

    with pytest.raises(RuntimeError, match="projection changed"):
        workers.run_answer_resolution_once(
            operation_id=operation["operation_id"],
            worker_id="answer-worker",
        )
    task = _task(operation["operation_id"], orchestrator.TASK_ANSWER_RESOLUTION)
    assert task["status"] == "PENDING"
    assert task["attempts"] == 1


def test_answer_worker_stale_answer_brain_binding_fails_closed(worker_db, monkeypatch):
    operation = _start("v1b-answer-brain-stale")
    monkeypatch.setattr(workers, "current_application_truth_projection", lambda job_id: _truth())
    monkeypatch.setattr(
        workers.answer_brain_v2,
        "planning_input",
        lambda: _planning(profile_digest="f" * 64),
    )

    with pytest.raises(RuntimeError, match="Answer Brain Candidate Truth"):
        workers.run_answer_resolution_once(
            operation_id=operation["operation_id"],
            worker_id="answer-worker",
        )


@pytest.mark.parametrize(
    ("description", "decision"),
    [
        ("Please submit a cover letter with your application.", workers.COVER_REQUIRED),
        ("A cover letter is optional.", workers.COVER_OPTIONAL),
        ("Cover letter is not required.", workers.COVER_NOT_REQUIRED),
        ("We value clear written communication.", workers.COVER_UNDETERMINED),
    ],
)
def test_cover_letter_decision_rules(worker_db, monkeypatch, description, decision):
    operation = _start("v1b-cover-" + decision.casefold())
    monkeypatch.setattr(
        workers,
        "safe_owned_job_snapshot",
        lambda job_id: {
            "job_snapshot_sha256": JOB_DIGEST,
            "job": {
                "id": 93,
                "description_raw": description,
                "responsibilities": None,
                "qualifications": None,
                "preferred_qualifications": None,
            },
        },
    )

    result = workers.run_cover_letter_decision_once(
        operation_id=operation["operation_id"],
        worker_id="cover-worker",
    )
    output = result["output"]
    assert result["task_status"] == "SUCCEEDED"
    assert output["decision"] == decision
    assert output["artifact_generated"] is False
    assert output["requires_ats_form_confirmation"] is True
    assert output["live_form_overrides_jd_decision"] is True
    assert output["submission_authority"] is False


def test_cover_letter_negative_rule_precedes_required_word(worker_db, monkeypatch):
    operation = _start("v1b-cover-negative-precedence")
    monkeypatch.setattr(
        workers,
        "safe_owned_job_snapshot",
        lambda job_id: {
            "job_snapshot_sha256": JOB_DIGEST,
            "job": {"id": 93, "description_raw": "No cover letter is required."},
        },
    )
    result = workers.run_cover_letter_decision_once(
        operation_id=operation["operation_id"],
        worker_id="cover-worker",
    )
    assert result["output"]["decision"] == workers.COVER_NOT_REQUIRED


def test_cover_letter_stale_job_fails_closed_and_requeues(worker_db, monkeypatch):
    operation = _start("v1b-cover-stale")
    monkeypatch.setattr(
        workers,
        "safe_owned_job_snapshot",
        lambda job_id: {
            "job_snapshot_sha256": "f" * 64,
            "job": {"id": 93, "description_raw": "Cover letter required."},
        },
    )

    with pytest.raises(RuntimeError, match="Job snapshot changed"):
        workers.run_cover_letter_decision_once(
            operation_id=operation["operation_id"],
            worker_id="cover-worker",
        )
    task = _task(operation["operation_id"], orchestrator.TASK_COVER_LETTER_DECISION)
    assert task["status"] == "PENDING"
    assert task["attempts"] == 1


def test_v1b_disabled_before_claim(worker_db, monkeypatch):
    operation = _start("v1b-disabled")
    monkeypatch.delenv(workers.WORKERS_ENV, raising=False)

    with pytest.raises(RuntimeError, match="disabled"):
        workers.run_answer_resolution_once(
            operation_id=operation["operation_id"],
            worker_id="answer-worker",
        )
    assert _task(
        operation["operation_id"], orchestrator.TASK_ANSWER_RESOLUTION
    )["status"] == "PENDING"


def test_v1b_constants_never_grant_submission(worker_db):
    assert workers.SUBMISSION_AUTHORITY is False
