from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import database
from app import prepare_orchestrator_v1 as orchestrator
from app import prepare_worker_adapters_v1a as workers


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
                   id,job_fingerprint,source,company_name,title,apply_url
               ) VALUES(
                   92,'prepare-worker-job-92','fixture','Acme','People Analyst',
                   'https://boards.greenhouse.io/acme/jobs/92'
               )"""
        )
        connection.commit()
    finally:
        connection.close()

    orchestrator.ensure_schema()
    return hunter_db


def _start(engine="NATIVE", key="workers-1"):
    return orchestrator.start_prepare(
        application_id="application-92",
        job_id=92,
        engine=engine,
        idempotency_key=key,
    )


def _task(operation_id, kind):
    operation = orchestrator.prepare_operation(operation_id)
    return next(
        row for row in operation["tasks"] if row["task_kind"] == kind
    )


def _bundle(*, job_digest=JOB_DIGEST, profile_digest=PROFILE_DIGEST):
    return {
        "job_id": 92,
        "candidate_truth_binding": {"profile_digest": profile_digest},
        "job_snapshot_binding": {"job_snapshot_sha256": job_digest},
        "stage_b_binding": {"plan_id": "plan-stage-b"},
        "page_verification": {
            "status": "PASS",
            "pdf_pages": 1,
            "occupied_height_ratio": 0.82,
        },
        "docx": {
            "artifact_id": "docx-1",
            "artifact_kind": "DOCX",
            "object_reference": "hunter-native-resume://resume-v1/docx",
            "sha256": "d" * 64,
            "filename": "resume.docx",
            "mime_type": (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            "byte_count": 12000,
            "page_count": None,
        },
        "pdf": {
            "artifact_id": "pdf-1",
            "artifact_kind": "PDF",
            "object_reference": "hunter-native-resume://resume-v1/pdf",
            "sha256": "e" * 64,
            "filename": "resume.pdf",
            "mime_type": "application/pdf",
            "byte_count": 24000,
            "page_count": 1,
        },
        "submission_authority": False,
    }


def test_native_resume_worker_completes_exact_pdf_docx(worker_db, monkeypatch):
    operation = _start()

    monkeypatch.setattr(
        workers.native_resume_service_v5,
        "generate_resume",
        lambda **kwargs: {
            "version_id": "resume-v1",
            "version_number": 1,
            "stage_b_bound": True,
        },
    )
    monkeypatch.setattr(
        workers.native_resume_artifact_v5,
        "materialize",
        lambda version_id: _bundle(),
    )

    result = workers.run_native_resume_once(
        operation_id=operation["operation_id"],
        worker_id="native-worker",
    )
    assert result["claimed"] is True
    assert result["task_status"] == "SUCCEEDED"
    assert result["output"]["pdf"]["artifact_sha256"] == "e" * 64
    assert result["output"]["docx"]["artifact_sha256"] == "d" * 64
    assert result["output"]["pdf"]["page_count"] == 1
    assert result["output"]["submission_authority"] is False

    task = _task(operation["operation_id"], orchestrator.TASK_RESUME)
    assert task["status"] == "SUCCEEDED"
    assert task["output"]["pdf"]["artifact_reference"].endswith("/pdf")


def test_native_worker_never_claims_n8n_resume(worker_db):
    operation = _start(engine="N8N", key="workers-n8n")
    result = workers.run_native_resume_once(
        operation_id=operation["operation_id"],
        worker_id="native-worker",
    )
    assert result["claimed"] is False
    assert result["reason"] == "engine_not_native"
    assert _task(
        operation["operation_id"], orchestrator.TASK_RESUME
    )["status"] == "PENDING"


def test_native_binding_mismatch_fails_closed_and_requeues(worker_db, monkeypatch):
    operation = _start(key="workers-native-mismatch")
    monkeypatch.setattr(
        workers.native_resume_service_v5,
        "generate_resume",
        lambda **kwargs: {
            "version_id": "resume-v1",
            "version_number": 1,
            "stage_b_bound": True,
        },
    )
    monkeypatch.setattr(
        workers.native_resume_artifact_v5,
        "materialize",
        lambda version_id: _bundle(job_digest="f" * 64),
    )

    with pytest.raises(RuntimeError, match="job snapshot binding"):
        workers.run_native_resume_once(
            operation_id=operation["operation_id"],
            worker_id="native-worker",
        )
    task = _task(operation["operation_id"], orchestrator.TASK_RESUME)
    assert task["status"] == "PENDING"
    assert task["attempts"] == 1


def test_recruiter_worker_persists_advisory_ranked_contacts(worker_db, monkeypatch):
    operation = _start(key="workers-recruiter")
    monkeypatch.setattr(
        workers.relationship_intelligence_v3,
        "strategy_for_job",
        lambda job_id, persist=True: {
            "strategy_id": "relationship-1",
            "result_sha256": "f" * 64,
            "job_id": 92,
            "job_snapshot_sha256": JOB_DIGEST,
            "ranked_contacts": [
                {
                    "contact_id": "contact-1",
                    "display_name": "Recruiter One",
                    "title": "Talent Acquisition",
                    "email": "must-not-copy@example.invalid",
                    "relationship_score": 88,
                    "score_confidence": 0.95,
                    "recommended_action": "connect",
                    "source": "stored_relationship_evidence",
                    "unknowns": [],
                }
            ],
            "strategy": {
                "base_pursuit_state": "APPLY",
                "combined_pursuit_state": "NETWORK + APPLY",
                "networking_action": "connect",
                "reason": "Strong relationship evidence.",
            },
            "advisory_only": True,
            "automatic_outreach_executed": False,
        },
    )

    result = workers.run_recruiter_evidence_once(
        operation_id=operation["operation_id"],
        worker_id="relationship-worker",
    )
    assert result["task_status"] == "SUCCEEDED"
    assert result["output"]["contacts_found"] == 1
    assert result["output"]["automatic_outreach_executed"] is False
    assert result["output"]["external_people_search_executed"] is False
    assert "email" not in result["output"]["ranked_contacts"][0]
    assert result["output"]["submission_authority"] is False


def test_relationship_disabled_is_valid_optional_result(worker_db, monkeypatch):
    operation = _start(key="workers-recruiter-disabled")

    def disabled(*args, **kwargs):
        raise RuntimeError("Relationship intelligence is disabled.")

    monkeypatch.setattr(
        workers.relationship_intelligence_v3,
        "strategy_for_job",
        disabled,
    )
    result = workers.run_recruiter_evidence_once(
        operation_id=operation["operation_id"],
        worker_id="relationship-worker",
    )
    assert result["task_status"] == "SUCCEEDED"
    assert result["output"]["available"] is False
    assert result["output"]["contacts_found"] == 0
    assert result["operation_status"] == "RUNNING"


def test_relationship_stale_job_binding_fails_closed(worker_db, monkeypatch):
    operation = _start(key="workers-recruiter-stale")
    monkeypatch.setattr(
        workers.relationship_intelligence_v3,
        "strategy_for_job",
        lambda job_id, persist=True: {
            "strategy_id": "relationship-stale",
            "result_sha256": "f" * 64,
            "job_id": 92,
            "job_snapshot_sha256": "0" * 64,
            "ranked_contacts": [],
            "strategy": {},
            "advisory_only": True,
            "automatic_outreach_executed": False,
        },
    )

    with pytest.raises(RuntimeError, match="job snapshot"):
        workers.run_recruiter_evidence_once(
            operation_id=operation["operation_id"],
            worker_id="relationship-worker",
        )
    task = _task(
        operation["operation_id"],
        orchestrator.TASK_RECRUITER_DISCOVERY,
    )
    assert task["status"] == "PENDING"
    assert task["attempts"] == 1


def test_workers_disabled_before_any_claim(worker_db, monkeypatch):
    operation = _start(key="workers-disabled")
    monkeypatch.delenv(workers.WORKERS_ENV, raising=False)

    with pytest.raises(RuntimeError, match="disabled"):
        workers.run_native_resume_once(
            operation_id=operation["operation_id"],
            worker_id="native-worker",
        )
    assert _task(
        operation["operation_id"], orchestrator.TASK_RESUME
    )["status"] == "PENDING"


def test_adapter_constants_never_grant_submission(worker_db):
    assert workers.SUBMISSION_AUTHORITY is False
