from __future__ import annotations

import hashlib
from types import SimpleNamespace

from app import application_view_model_v1 as view_model
from app import prepare_orchestrator_v1 as prepare
from app import product_application_flow_v2 as flow
from app.job_destination_v1 import describe_job_destination


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_generic_two_job_browse_prepare_application_center_chain(monkeypatch) -> None:
    """Synthetic acceptance stops at review and never grants submit authority."""
    operations: dict[int, dict[str, object]] = {}
    creation_calls: list[int] = []

    class Connection:
        def close(self) -> None:
            return None

    def latest(job_id: int):
        return operations.get(int(job_id))

    def create_operation(**kwargs):
        job_id = int(kwargs["job_id"])
        creation_calls.append(job_id)
        operation = {
            "operation_id": f"prepare-operation-{job_id}",
            "application_id": kwargs["application_id"],
            "job_id": job_id,
            "status": "RUNNING",
            "tasks": [],
            "updated_at": "2026-09-22T12:00:00+00:00",
        }
        operations[job_id] = operation
        return {**operation, "replayed": False}

    def projection(job_id: int, *, summary: bool = False):
        operation = operations.get(int(job_id))
        return {
            "job_id": int(job_id),
            "application_id": operation["application_id"] if operation else None,
            "operation_id": operation["operation_id"] if operation else None,
            "state": operation["status"] if operation else "",
            "display_status": "Needs review" if operation and operation["status"] == "READY_FOR_REVIEW" else "Preparing",
            "authority": "single_customer_approval_required" if operation and operation["status"] == "READY_FOR_REVIEW" else "prepare",
            "prepare": operation,
            "lifecycle": None,
            "tasks": list(operation.get("tasks") or []) if operation else [],
            "runtime_questions": [],
            "artifacts": {},
            "package_review": None,
            "execution_mandate": None,
            "internal_integrity_review": None,
            "production_receipt": None,
            "cta": "workspace",
            "cta_label": "Review resume" if operation and operation["status"] == "READY_FOR_REVIEW" else "Preparing",
            "submission_authority": False,
        }

    monkeypatch.setattr(flow, "_latest_prepare", latest)
    monkeypatch.setattr(flow, "_latest_lifecycle", lambda _job_id: None)
    monkeypatch.setattr(
        flow,
        "safe_owned_job_snapshot",
        lambda job_id: {"job_snapshot_sha256": _digest(f"job:{job_id}")},
    )
    monkeypatch.setattr(
        flow,
        "current_application_truth_projection",
        lambda **_kwargs: {
            "candidate_profile_binding": {"profile_digest": _digest("candidate-truth")}
        },
    )
    monkeypatch.setattr(flow, "get_connection", Connection)
    monkeypatch.setattr(
        flow,
        "current_owner",
        lambda _connection: SimpleNamespace(tenant_id="tenant", user_id="candidate"),
    )
    monkeypatch.setattr(prepare, "start_prepare", create_operation)
    monkeypatch.setattr(flow, "flow_for_job", projection)

    first = flow.start_prepare_for_job(101)
    replay = flow.start_prepare_for_job(101)
    second = flow.start_prepare_for_job(202)

    assert first["application_id"] == replay["application_id"]
    assert first["operation_id"] == replay["operation_id"]
    assert replay["replayed"] is True
    assert second["application_id"] != first["application_id"]
    assert creation_calls == [101, 202]

    # Worker completion keeps the exact application and resume binding and
    # stops at the one customer review boundary.
    operations[101]["status"] = "READY_FOR_REVIEW"
    operations[101]["tasks"] = [
        {
            "task_kind": "RESUME_PREPARE",
            "status": "SUCCEEDED",
            "output": {
                "resume_version_id": "resume-exact-job-101",
                "reused_generated_version": True,
            },
        }
    ]
    model = view_model.view_for_job(
        101,
        seed={
            "id": 101,
            "company_name": "Northwind People",
            "title": "People Operations Analyst",
            "location_raw": "Remote",
            "remote_type": "Remote",
            "hunter_score": 92,
            "source": "Indeed via JobSpy",
            "job_url": "https://www.indeed.com/viewjob?jk=101",
            "apply_url": "https://northwind.wd1.myworkdayjobs.com/Careers/job/R-101",
        },
        flow_seed=projection(101, summary=True),
        heartbeat={"state": "healthy", "label": "Worker healthy"},
        visibility_seed={},
    )

    assert model.application_id == first["application_id"]
    assert model.resume_review_available is True
    assert model.next_action == "Review resume"
    assert model.state_group == "needs_you"
    assert model.apply_via == "Workday"
    assert model.source_platform == "Indeed"
    assert projection(101)["submission_authority"] is False

    other_destination = describe_job_destination(
        {
            "source": "Company feed",
            "apply_url": "https://jobs.lever.co/contoso/role-202",
        }
    )
    assert other_destination.apply_via == "Lever"
