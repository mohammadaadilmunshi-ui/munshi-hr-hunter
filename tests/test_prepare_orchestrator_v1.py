from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import database
from app import prepare_orchestrator_v1 as prepare


OWNER = SimpleNamespace(tenant_id="default", user_id="owner")
JOB_DIGEST = "a" * 64
PROJECTION_DIGEST = "b" * 64
PROFILE_DIGEST = "c" * 64


@pytest.fixture()
def prepare_db(hunter_db, monkeypatch):
    monkeypatch.setattr(prepare, "current_owner", lambda _connection: OWNER)
    monkeypatch.setattr(
        prepare,
        "safe_owned_job_snapshot",
        lambda job_id: {
            "job": {"id": int(job_id)},
            "job_snapshot_sha256": JOB_DIGEST,
        },
    )
    monkeypatch.setattr(
        prepare,
        "current_application_truth_projection",
        lambda job_id: {
            "projection_digest": PROJECTION_DIGEST,
            "candidate_profile_binding": {
                "profile_digest": PROFILE_DIGEST,
            },
            "job_context": {
                "job_snapshot_sha256": JOB_DIGEST,
            },
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
                   91,'prepare-job-91','fixture','Acme','People Analyst',
                   'https://fixture.invalid/apply'
               )"""
        )
        connection.commit()
    finally:
        connection.close()

    prepare.ensure_schema()
    return hunter_db


def _start(engine="NATIVE", key="prepare-1"):
    return prepare.start_prepare(
        application_id="application-91",
        job_id=91,
        engine=engine,
        idempotency_key=key,
    )


def _by_kind(operation):
    return {row["task_kind"]: row for row in operation["tasks"]}


def _claim_one(operation_id, kind, worker="worker-1"):
    rows = prepare.claim_ready_tasks(
        operation_id=operation_id,
        worker_id=worker,
        limit=1,
        task_kinds=[kind],
    )
    assert len(rows) == 1
    return rows[0]


def test_start_builds_bound_durable_graph_and_replays_exactly(prepare_db):
    first = _start()
    assert first["status"] == "RUNNING"
    assert first["engine"] == "NATIVE"
    assert first["job_snapshot_sha256"] == JOB_DIGEST
    assert first["application_truth_projection_digest"] == PROJECTION_DIGEST
    assert first["candidate_profile_digest"] == PROFILE_DIGEST
    assert first["submission_authority"] is False
    assert first["external_actions_executed"] is False
    assert {row["task_kind"] for row in first["tasks"]} == set(prepare.TASK_KINDS)

    tasks = _by_kind(first)
    assert tasks[prepare.TASK_RECRUITER_DISCOVERY]["required"] == 0
    assert tasks[prepare.TASK_APPLICATION_PREFLIGHT]["depends_on"] == [
        prepare.TASK_RESUME,
        prepare.TASK_ANSWER_RESOLUTION,
    ]

    replay = _start()
    assert replay["operation_id"] == first["operation_id"]
    assert replay["replayed"] is True


def test_same_idempotency_key_with_other_engine_fails_closed(prepare_db):
    _start(engine="NATIVE")
    with pytest.raises(ValueError, match="idempotency key"):
        _start(engine="N8N")


def test_initial_parallel_claims_only_dependency_free_tasks(prepare_db):
    operation = _start()
    claimed = prepare.claim_ready_tasks(
        operation_id=operation["operation_id"],
        worker_id="parallel-worker",
        limit=10,
    )
    kinds = {row["task_kind"] for row in claimed}
    assert kinds == {
        prepare.TASK_RESUME,
        prepare.TASK_COVER_LETTER_DECISION,
        prepare.TASK_RECRUITER_DISCOVERY,
        prepare.TASK_ANSWER_RESOLUTION,
    }
    assert prepare.TASK_APPLICATION_PREFLIGHT not in kinds
    assert prepare.TASK_ATS_FORM_PREPARATION not in kinds


def test_dependencies_unlock_preflight_then_ats(prepare_db):
    operation = _start()
    op_id = operation["operation_id"]

    resume = _claim_one(op_id, prepare.TASK_RESUME, "resume-worker")
    answers = _claim_one(op_id, prepare.TASK_ANSWER_RESOLUTION, "answer-worker")
    cover = _claim_one(
        op_id,
        prepare.TASK_COVER_LETTER_DECISION,
        "cover-worker",
    )

    prepare.complete_task(
        task_id=resume["task_id"],
        worker_id="resume-worker",
        output={"resume_version_id": "resume-v1", "pdf_sha256": "d" * 64},
    )
    prepare.complete_task(
        task_id=answers["task_id"],
        worker_id="answer-worker",
        output={"answers_complete": True},
    )

    preflight = _claim_one(
        op_id,
        prepare.TASK_APPLICATION_PREFLIGHT,
        "preflight-worker",
    )
    prepare.complete_task(
        task_id=preflight["task_id"],
        worker_id="preflight-worker",
        output={"preflight_package_id": "preflight-1"},
    )

    assert prepare.claim_ready_tasks(
        operation_id=op_id,
        worker_id="ats-too-early",
        limit=1,
        task_kinds=[prepare.TASK_ATS_FORM_PREPARATION],
    ) == []

    prepare.complete_task(
        task_id=cover["task_id"],
        worker_id="cover-worker",
        output={"cover_letter_required": False},
    )

    ats = _claim_one(
        op_id,
        prepare.TASK_ATS_FORM_PREPARATION,
        "ats-worker",
    )
    assert ats["task_kind"] == prepare.TASK_ATS_FORM_PREPARATION


def test_needs_input_pauses_operation_and_resolution_requeues_task(prepare_db):
    operation = _start()
    answer = _claim_one(
        operation["operation_id"],
        prepare.TASK_ANSWER_RESOLUTION,
        "answer-worker",
    )
    result = prepare.task_needs_input(
        task_id=answer["task_id"],
        worker_id="answer-worker",
        unresolved_keys=["WORK_AUTH_US", "SALARY"],
        output={"known_answers": 7},
    )
    assert result["operation_status"] == "NEEDS_INPUT"

    read = prepare.prepare_operation(operation["operation_id"])
    assert read["status"] == "NEEDS_INPUT"
    assert read["unresolved"] == ["SALARY", "WORK_AUTH_US"]

    resolved = prepare.resolve_task_input(
        task_id=answer["task_id"],
        idempotency_key="resolve-answer-1",
    )
    assert resolved["task_status"] == "PENDING"
    assert resolved["operation_status"] == "RUNNING"


def test_required_success_converges_to_ready_for_review_without_optional_recruiter(prepare_db):
    operation = _start()
    op_id = operation["operation_id"]

    for kind, worker, output in (
        (
            prepare.TASK_RESUME,
            "resume-worker",
            {"resume_version_id": "v1", "pdf_sha256": "d" * 64},
        ),
        (
            prepare.TASK_COVER_LETTER_DECISION,
            "cover-worker",
            {"cover_letter_required": False},
        ),
        (
            prepare.TASK_ANSWER_RESOLUTION,
            "answer-worker",
            {"answers_complete": True},
        ),
    ):
        task = _claim_one(op_id, kind, worker)
        prepare.complete_task(
            task_id=task["task_id"],
            worker_id=worker,
            output=output,
        )

    preflight = _claim_one(
        op_id,
        prepare.TASK_APPLICATION_PREFLIGHT,
        "preflight-worker",
    )
    prepare.complete_task(
        task_id=preflight["task_id"],
        worker_id="preflight-worker",
        output={"preflight_package_id": "preflight-1"},
    )

    ats = _claim_one(
        op_id,
        prepare.TASK_ATS_FORM_PREPARATION,
        "ats-worker",
    )
    final = prepare.complete_task(
        task_id=ats["task_id"],
        worker_id="ats-worker",
        output={"checkpoint_id": "checkpoint-1", "form_digest": "e" * 64},
    )
    assert final["operation_status"] == "READY_FOR_REVIEW"

    read = prepare.prepare_operation(op_id)
    recruiter = _by_kind(read)[prepare.TASK_RECRUITER_DISCOVERY]
    assert recruiter["status"] == "PENDING"
    assert read["status"] == "READY_FOR_REVIEW"


def test_required_task_exhaustion_fails_safely(prepare_db):
    operation = _start()
    task = _claim_one(
        operation["operation_id"],
        prepare.TASK_RESUME,
        "resume-worker",
    )
    first = prepare.fail_task(
        task_id=task["task_id"],
        worker_id="resume-worker",
        error_code="WRITER_TEMP",
        error_message="Temporary writer failure.",
        retryable=True,
    )
    assert first["task_status"] == "PENDING"
    assert first["operation_status"] == "RUNNING"

    second_claim = _claim_one(
        operation["operation_id"],
        prepare.TASK_RESUME,
        "resume-worker",
    )
    second = prepare.fail_task(
        task_id=second_claim["task_id"],
        worker_id="resume-worker",
        error_code="WRITER_FAILED",
        error_message="Writer failed again.",
        retryable=True,
    )
    assert second["task_status"] == "FAILED"
    assert second["operation_status"] == "FAILED_SAFELY"


def test_optional_recruiter_failure_does_not_fail_operation(prepare_db):
    operation = _start()
    recruiter = _claim_one(
        operation["operation_id"],
        prepare.TASK_RECRUITER_DISCOVERY,
        "recruiter-worker",
    )
    first = prepare.fail_task(
        task_id=recruiter["task_id"],
        worker_id="recruiter-worker",
        error_code="SEARCH_TEMP",
        error_message="Search provider unavailable.",
        retryable=True,
    )
    assert first["task_status"] == "PENDING"
    recruiter = _claim_one(
        operation["operation_id"],
        prepare.TASK_RECRUITER_DISCOVERY,
        "recruiter-worker",
    )
    second = prepare.fail_task(
        task_id=recruiter["task_id"],
        worker_id="recruiter-worker",
        error_code="SEARCH_UNAVAILABLE",
        error_message="Search provider still unavailable.",
        retryable=True,
    )
    assert second["task_status"] == "FAILED"
    assert second["operation_status"] == "RUNNING"


def test_wrong_worker_cannot_complete_claimed_task(prepare_db):
    operation = _start()
    task = _claim_one(
        operation["operation_id"],
        prepare.TASK_RESUME,
        "owner-worker",
    )
    with pytest.raises(PermissionError, match="another worker"):
        prepare.complete_task(
            task_id=task["task_id"],
            worker_id="intruder-worker",
            output={"resume_version_id": "v1"},
        )


def test_owner_isolation(prepare_db, monkeypatch):
    operation = _start()
    monkeypatch.setattr(
        prepare,
        "current_owner",
        lambda _connection: SimpleNamespace(
            tenant_id="default",
            user_id="other",
        ),
    )
    with pytest.raises(LookupError):
        prepare.prepare_operation(operation["operation_id"])
