"""Application Preflight + hosted ATS preparation workers for Prepare V1.

This is the convergence bridge between Hunter preparation authority and the
already-built MUNSHI Apply background browser plane.

APPLICATION_PREFLIGHT:
- consumes exact orchestrator resume + answer outputs;
- persists current Opportunity Intelligence V3;
- consumes Relationship V3 when already available;
- creates the initial immutable READY_TO_APPLY preflight package for the
  *currently known* requirement set;
- explicitly records that live employer-form discovery is still pending.

ATS_FORM_PREPARATION:
- creates immutable Application Plan V2 with final_review/final_submit false;
- delivers the signed plan to Apply;
- starts the durable Apply preparation session/queue;
- waits only for pre-submit outcomes:
    WAITING_INPUT -> Hunter NEEDS_INPUT
    READY_FOR_REVIEW -> Hunter task success / operation convergence
- never approves final review and never submits.

A JD-explicit REQUIRED cover letter fails closed to NEEDS_INPUT until the
separate native cover-letter writer/artifact tranche supplies the exact PDF.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit

from app import application_plan_delivery_v2
from app import application_review_v2
from app import application_plan_v2
from app import application_preflight_package_v1 as preflight_v1
from app import opportunity_intelligence_v3
from app import native_cover_letter_writer_v1
from app import prepare_orchestrator_v1 as orchestrator


WORKERS_ENV = "MUNSHI_PREPARE_WORKER_ADAPTERS_V1C_ENABLED"
ATS_RUNTIME_ENV = "MUNSHI_PREPARE_ATS_RUNTIME_START_ENABLED"
APPLY_BASE_URL_ENV = "MUNSHI_APPLY_BASE_URL"
APPLY_COMMAND_SECRET_ENV = "MUNSHI_APPLY_COMMAND_SECRET"

WORKER_VERSION = "munshi-prepare-worker-adapters-v1c"
SUBMISSION_AUTHORITY = False

_APPLY_SESSION_PATH = "/v1/complete-loop/sessions"
_APPLY_PREPARE_JOB_PATH = "/v1/complete-loop/preparation-jobs/{job_id}"
_APPLY_RESOLUTION_TASK_PATH = "/v1/complete-loop/preparation-jobs/{job_id}/resolution-tasks"
_APPLY_REVIEW_PATH = "/v1/complete-loop/sessions/{session_id}/review"
_RUNNING_APPLY_STATES = frozenset({"QUEUED", "RUNNING"})
_TERMINAL_APPLY_STATES = frozenset(
    {"WAITING_INPUT", "READY_FOR_REVIEW", "BLOCKED", "FAILED_SAFELY", "CANCELLED"}
)


def _truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def workers_enabled() -> bool:
    return _truthy(WORKERS_ENV)


def ats_runtime_enabled() -> bool:
    return _truthy(ATS_RUNTIME_ENV)


def _enabled() -> None:
    if not workers_enabled():
        raise RuntimeError(
            "Prepare Worker Adapters V1C are disabled for this environment."
        )


def _operation(operation_id: str) -> dict[str, Any]:
    operation = orchestrator.prepare_operation(str(operation_id))
    if operation.get("submission_authority") is not False:
        raise RuntimeError("Prepare operation unexpectedly carries submission authority.")
    return operation


def _task(
    operation: Mapping[str, Any],
    task_kind: str,
) -> dict[str, Any] | None:
    for raw in operation.get("tasks") or []:
        if isinstance(raw, Mapping) and str(raw.get("task_kind")) == task_kind:
            return dict(raw)
    return None


def _pending_task(
    operation: Mapping[str, Any],
    task_kind: str,
) -> dict[str, Any] | None:
    row = _task(operation, task_kind)
    return row if row is not None and str(row.get("status")) == "PENDING" else None


def _claim_one(
    *,
    operation_id: str,
    worker_id: str,
    task_kind: str,
    lease_seconds: int = 300,
) -> dict[str, Any] | None:
    claimed = orchestrator.claim_ready_tasks(
        operation_id=str(operation_id),
        worker_id=str(worker_id),
        limit=1,
        lease_seconds=int(lease_seconds),
        task_kinds=[task_kind],
    )
    if not claimed:
        return None
    if len(claimed) != 1 or str(claimed[0].get("task_kind")) != task_kind:
        raise RuntimeError("Prepare worker claimed an unexpected task.")
    return dict(claimed[0])


def _succeeded_output(
    operation: Mapping[str, Any],
    task_kind: str,
) -> dict[str, Any]:
    row = _task(operation, task_kind)
    if row is None or str(row.get("status")) != "SUCCEEDED":
        raise RuntimeError(f"{task_kind} has not completed successfully.")
    output = row.get("output")
    if not isinstance(output, Mapping):
        raise RuntimeError(f"{task_kind} has no durable output.")
    if output.get("submission_authority") is not False:
        raise RuntimeError(f"{task_kind} unexpectedly carries submission authority.")
    return dict(output)


def _optional_relationship_id(operation: Mapping[str, Any]) -> str | None:
    row = _task(operation, orchestrator.TASK_RECRUITER_DISCOVERY)
    if row is None or str(row.get("status")) != "SUCCEEDED":
        return None
    output = row.get("output")
    if not isinstance(output, Mapping):
        return None
    if output.get("submission_authority") is not False:
        raise RuntimeError("Recruiter evidence unexpectedly carries submission authority.")
    if output.get("available") is False:
        return None
    value = str(output.get("relationship_strategy_id") or "").strip()
    return value or None


def _validate_resume_and_answers(operation: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    resume = _succeeded_output(operation, orchestrator.TASK_RESUME)
    answers = _succeeded_output(operation, orchestrator.TASK_ANSWER_RESOLUTION)

    if (
        str(resume.get("candidate_profile_digest") or "")
        != str(operation["candidate_profile_digest"])
        or str(resume.get("job_snapshot_sha256") or "")
        != str(operation["job_snapshot_sha256"])
    ):
        raise RuntimeError("Resume preparation binding changed before preflight.")

    pdf = dict(resume.get("pdf") or {})
    if (
        int(pdf.get("page_count") or 0) != 1
        or len(str(pdf.get("artifact_sha256") or "")) != 64
        or not str(resume.get("resume_version_id") or "").strip()
    ):
        raise RuntimeError("Resume preparation output is not an exact one-page PDF artifact.")

    if (
        str(answers.get("candidate_profile_digest") or "")
        != str(operation["candidate_profile_digest"])
        or str(answers.get("application_truth_projection_digest") or "")
        != str(operation["application_truth_projection_digest"])
        or str(answers.get("job_snapshot_sha256") or "")
        != str(operation["job_snapshot_sha256"])
    ):
        raise RuntimeError("Answer preparation binding changed before preflight.")
    if answers.get("requires_ats_form_confirmation") is not True:
        raise RuntimeError("Answer preparation must preserve live-form confirmation.")

    return resume, answers


def _preflight_unresolved(package: Mapping[str, Any]) -> list[str]:
    snapshot = dict(package.get("snapshot") or {})
    question_state = dict(snapshot.get("application_question_state") or {})
    result = {
        str(value)
        for value in (question_state.get("unresolved_question_keys") or [])
        if str(value).strip()
    }
    readiness = dict(snapshot.get("readiness") or {})
    result.update(
        "preflight.blocker:" + str(value)
        for value in (readiness.get("blockers") or [])
        if str(value).strip()
    )
    return sorted(result)


def run_application_preflight_once(
    *,
    operation_id: str,
    worker_id: str,
) -> dict[str, Any]:
    """Create the initial known-requirements READY_TO_APPLY package.

    `application_questions_complete=True` here means the initial requirement set
    supplied to the preflight package is complete. It does NOT assert that the
    unseen employer form has no additional questions. Apply remains responsible
    for live form discovery and may return NEEDS_INPUT.
    """
    _enabled()
    operation = _operation(operation_id)

    if _pending_task(operation, orchestrator.TASK_APPLICATION_PREFLIGHT) is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "application_preflight_task_not_pending",
            "operation_status": str(operation["status"]),
            "submission_authority": False,
        }

    task = _claim_one(
        operation_id=str(operation_id),
        worker_id=str(worker_id),
        task_kind=orchestrator.TASK_APPLICATION_PREFLIGHT,
    )
    if task is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "application_preflight_task_not_ready",
            "submission_authority": False,
        }

    try:
        resume, answers = _validate_resume_and_answers(operation)
        opportunity = opportunity_intelligence_v3.evaluate_job(
            int(operation["job_id"]),
            persist=True,
        )
        relationship_strategy_id = _optional_relationship_id(operation)

        package = preflight_v1.prepare_preflight_package(
            job_id=int(operation["job_id"]),
            application_id=str(operation["application_id"]),
            idempotency_key=f"prepare:{operation_id}:preflight:v1c",
            resume_version_id=str(resume["resume_version_id"]),
            opportunity_evaluation_id=str(opportunity["evaluation_id"]),
            relationship_strategy_id=relationship_strategy_id,
            # This is the currently-known requirement inventory only.
            # The actual provider form is still undiscovered.
            application_questions_complete=True,
            unresolved_question_keys=[],
        )

        if str(package.get("job_snapshot_sha256") or "") != str(
            operation["job_snapshot_sha256"]
        ):
            raise RuntimeError("Preflight package job snapshot binding changed.")
        if str(package.get("resume_version_id") or "") != str(
            resume["resume_version_id"]
        ):
            raise RuntimeError("Preflight package resume version binding changed.")

        status = str(package.get("status") or "")
        unresolved = _preflight_unresolved(package)
        if status != "READY_TO_APPLY":
            unresolved = unresolved or ["preflight.not_ready"]
            result = orchestrator.task_needs_input(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
                unresolved_keys=unresolved,
                output={
                    "version": WORKER_VERSION,
                    "preflight_package_id": package.get("package_id"),
                    "preflight_package_digest": package.get("package_digest"),
                    "preflight_status": status,
                    "known_requirement_set_complete": True,
                    "live_form_discovery_pending": True,
                    "submission_authority": False,
                },
            )
            return {
                **result,
                "claimed": True,
                "preflight_status": status,
                "submission_authority": False,
            }

        output = {
            "version": WORKER_VERSION,
            "preflight_package_id": str(package["package_id"]),
            "preflight_package_digest": str(package["package_digest"]),
            "preflight_status": "READY_TO_APPLY",
            "opportunity_evaluation_id": str(opportunity["evaluation_id"]),
            "opportunity_result_sha256": str(opportunity["result_sha256"]),
            "relationship_strategy_id": relationship_strategy_id,
            "resume_version_id": str(resume["resume_version_id"]),
            "resume_pdf_sha256": str(dict(resume["pdf"])["artifact_sha256"]),
            "answer_inventory_snapshot_complete": True,
            "known_requirement_set_complete": True,
            "live_form_discovery_pending": True,
            "employer_form_claimed_complete": False,
            "submission_authority": False,
            "automatic_submission_executed": False,
        }
        result = orchestrator.complete_task(
            task_id=str(task["task_id"]),
            worker_id=str(worker_id),
            output=output,
        )
        return {
            **result,
            "claimed": True,
            "output": output,
            "submission_authority": False,
        }
    except Exception:
        orchestrator.fail_task(
            task_id=str(task["task_id"]),
            worker_id=str(worker_id),
            error_code="APPLICATION_PREFLIGHT_PREPARE_FAILED",
            error_message="Application preflight preparation failed closed.",
            retryable=True,
        )
        raise


def _cover_decision(operation: Mapping[str, Any]) -> dict[str, Any]:
    output = _succeeded_output(operation, orchestrator.TASK_COVER_LETTER_DECISION)
    decision = str(output.get("decision") or "").upper()
    if decision not in {"REQUIRED", "OPTIONAL", "NOT_REQUIRED", "UNDETERMINED"}:
        raise RuntimeError("Cover-letter decision is invalid.")
    if output.get("requires_ats_form_confirmation") is not True:
        raise RuntimeError("Cover-letter decision must preserve ATS form confirmation.")
    return output


def _apply_base_url() -> str:
    raw = str(os.getenv(APPLY_BASE_URL_ENV) or "").strip().rstrip("/")
    if not raw:
        raise RuntimeError("Apply base URL is not configured.")
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").casefold()
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not host
        or not (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and loopback)
        )
    ):
        raise ValueError(
            "Apply base URL must use HTTPS, except loopback HTTP is allowed."
        )
    return raw


def _apply_headers(*, tenant_id: str, user_id: str) -> dict[str, str]:
    secret = str(os.getenv(APPLY_COMMAND_SECRET_ENV) or "")
    if not secret:
        raise RuntimeError("Apply command secret is not configured.")
    return {
        "Content-Type": "application/json",
        "X-Munshi-Command-Secret": secret,
        "X-Munshi-Tenant-Id": str(tenant_id),
        "X-Munshi-User-Id": str(user_id),
    }


def _json_request(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any] | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    if not ats_runtime_enabled():
        raise RuntimeError("Prepare ATS runtime start is disabled.")
    data = (
        json.dumps(
            dict(body),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        if body is not None
        else None
    )
    outbound = request.Request(
        url,
        data=data,
        headers=dict(headers),
        method=str(method),
    )
    try:
        with request.urlopen(outbound, timeout=float(timeout_seconds)) as response:  # noqa: S310
            code = int(getattr(response, "status", 0) or 0)
            payload = response.read()
    except error.HTTPError as exc:
        raise RuntimeError(f"Apply runtime returned HTTP {exc.code}.") from exc
    except error.URLError as exc:
        raise RuntimeError("Apply runtime transport failed.") from exc
    if code not in {200, 201, 202}:
        raise RuntimeError(f"Apply runtime returned unexpected HTTP {code}.")
    try:
        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Apply runtime returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Apply runtime response must be an object.")
    return value


def _start_apply_session(plan: Mapping[str, Any]) -> dict[str, Any]:
    base = _apply_base_url()
    headers = _apply_headers(
        tenant_id=str(plan["tenant_id"]),
        user_id=str(plan["user_id"]),
    )
    result = _json_request(
        method="POST",
        url=base + _APPLY_SESSION_PATH,
        headers=headers,
        body={"plan_id": str(plan["plan_id"])},
    )
    if (
        str(result.get("plan_id") or "") != str(plan["plan_id"])
        or str(result.get("application_id") or "") != str(plan["application_id"])
        or str(result.get("state") or "") != "SESSION_STARTING"
        or not str(result.get("session_id") or "")
    ):
        raise RuntimeError("Apply preparation session acknowledgement is not correlated.")
    job = result.get("preparation_job")
    if not isinstance(job, Mapping):
        raise RuntimeError("Apply did not return a durable preparation job.")
    if (
        str(job.get("session_id") or "") != str(result["session_id"])
        or str(job.get("plan_id") or "") != str(plan["plan_id"])
        or str(job.get("application_id") or "") != str(plan["application_id"])
        or str(job.get("state") or "") not in (
            _RUNNING_APPLY_STATES | _TERMINAL_APPLY_STATES
        )
        or not str(job.get("job_id") or "")
    ):
        raise RuntimeError("Apply preparation job acknowledgement is not correlated.")
    return result


def _read_apply_prepare_job(
    *,
    job_id: str,
    tenant_id: str,
    user_id: str,
) -> dict[str, Any]:
    base = _apply_base_url()
    return _json_request(
        method="GET",
        url=base + _APPLY_PREPARE_JOB_PATH.format(job_id=str(job_id)),
        headers=_apply_headers(tenant_id=tenant_id, user_id=user_id),
    )


def _read_apply_resolution_tasks(
    *, job_id: str, tenant_id: str, user_id: str,
) -> dict[str, Any]:
    base = _apply_base_url()
    return _json_request(
        method="GET",
        url=base + _APPLY_RESOLUTION_TASK_PATH.format(job_id=str(job_id)),
        headers=_apply_headers(tenant_id=tenant_id, user_id=user_id),
    )


def _freeze_apply_review(
    *, session_id: str, tenant_id: str, user_id: str
) -> dict[str, Any]:
    base = _apply_base_url()
    return _json_request(
        method="POST",
        url=base + _APPLY_REVIEW_PATH.format(session_id=str(session_id)),
        headers=_apply_headers(tenant_id=tenant_id, user_id=user_id),
    )


_RUNTIME_TASK_STATUSES = frozenset({"PENDING", "RESOLVING", "WAITING_FOR_USER"})
_RUNTIME_TASK_SAFE_FIELDS = (
    "schema_version", "task_id", "application_id", "session_id",
    "checkpoint_id", "page_id", "control_id", "question_id", "question",
    "semantic_type", "category", "sensitivity_class", "status", "risk_level", "auto_resolvable",
    "requires_user", "grouping_scope", "group_key", "reason",
    "created_at", "updated_at",
)


def _runtime_unresolved_key(task_id: str) -> str:
    import hashlib
    token = hashlib.sha256(str(task_id).encode("utf-8")).hexdigest()[:24]
    return f"apply.runtime_question:{token}"


def _validate_runtime_resolution_sync(
    payload: Mapping[str, Any], *, prepare_job_id: str, session_id: str,
    application_id: str, plan_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if (
        str(payload.get("job_id") or "") != str(prepare_job_id)
        or str(payload.get("session_id") or "") != str(session_id)
        or str(payload.get("application_id") or "") != str(application_id)
        or str(payload.get("plan_id") or "") != str(plan_id)
        or str(payload.get("prepare_state") or "") != "WAITING_INPUT"
        or payload.get("resolution_values_exposed") is not False
        or payload.get("final_review_authority") is not False
        or payload.get("submission_authority") is not False
    ):
        raise RuntimeError("Apply runtime resolution read model is not exactly correlated.")
    raw_tasks = payload.get("resolution_tasks")
    if not isinstance(raw_tasks, list):
        raise RuntimeError("Apply runtime resolution task list is invalid.")
    safe_tasks = []
    unresolved = []
    seen = set()
    for raw in raw_tasks:
        if not isinstance(raw, Mapping):
            raise RuntimeError("Apply runtime resolution task entry is invalid.")
        if "resolution" in raw:
            raise RuntimeError("Apply runtime resolution read model exposed a resolution value.")
        task_id = str(raw.get("task_id") or "").strip()
        if not task_id or task_id in seen:
            raise RuntimeError("Apply runtime resolution task identity is invalid.")
        seen.add(task_id)
        if (
            str(raw.get("application_id") or "") != str(application_id)
            or str(raw.get("session_id") or "") != str(session_id)
            or str(raw.get("status") or "") not in _RUNTIME_TASK_STATUSES
            or raw.get("submission_authority") is not False
        ):
            raise RuntimeError("Apply runtime resolution task binding is invalid.")
        sanitized = {key: raw.get(key) for key in _RUNTIME_TASK_SAFE_FIELDS if raw.get(key) is not None}
        sanitized["submission_authority"] = False
        safe_tasks.append(sanitized)
        unresolved.append(_runtime_unresolved_key(task_id))
    if int(payload.get("open_task_count") or 0) != len(safe_tasks):
        raise RuntimeError("Apply runtime resolution task count is inconsistent.")
    safe_tasks.sort(key=lambda row: str(row["task_id"]))
    unresolved.sort()
    return safe_tasks, unresolved


def _plan_permissions(
    *,
    cover_letter_present: bool = False,
) -> dict[str, bool]:
    return {
        "background_prepare": True,
        "resume_upload": True,
        "cover_letter_upload": bool(cover_letter_present),
        "normal_answer_autofill": True,
        "protected_fact_execution": False,
        "self_id_execution": False,
        "final_review": False,
        "final_submit": False,
    }


def run_ats_form_preparation_once(
    *,
    operation_id: str,
    worker_id: str,
    poll_timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 1.0,
    lease_seconds: int = 300,
    plan_delivery: Callable[..., Any] | None = None,
    session_starter: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    status_reader: Callable[..., Mapping[str, Any]] | None = None,
    resolution_task_reader: Callable[..., Mapping[str, Any]] | None = None,
    review_builder: Callable[..., Mapping[str, Any]] | None = None,
    review_recorder: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Start/poll Apply's pre-submit browser preparation.

    The real runtime path remains default-off. Tests inject inert adapters.
    """
    _enabled()
    operation = _operation(operation_id)

    if not ats_runtime_enabled():
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "ats_runtime_start_disabled",
            "submission_authority": False,
        }

    if _pending_task(operation, orchestrator.TASK_ATS_FORM_PREPARATION) is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "ats_form_preparation_task_not_pending",
            "operation_status": str(operation["status"]),
            "submission_authority": False,
        }

    if float(poll_timeout_seconds) <= 0 or float(poll_timeout_seconds) > 3600:
        raise ValueError("ATS preparation poll timeout must be between 0 and 3600 seconds.")
    if float(poll_interval_seconds) < 0 or float(poll_interval_seconds) > 30:
        raise ValueError("ATS preparation poll interval must be between 0 and 30 seconds.")

    preflight = _succeeded_output(operation, orchestrator.TASK_APPLICATION_PREFLIGHT)
    cover = _cover_decision(operation)

    task = _claim_one(
        operation_id=str(operation_id),
        worker_id=str(worker_id),
        task_kind=orchestrator.TASK_ATS_FORM_PREPARATION,
        lease_seconds=int(lease_seconds),
    )
    if task is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "ats_form_preparation_task_not_ready",
            "submission_authority": False,
        }

    cover_letter: dict[str, Any] | None = None
    if str(cover.get("decision")) == "REQUIRED":
        try:
            cover_letter = native_cover_letter_writer_v1.generate(
                preflight_package_id=str(preflight["preflight_package_id"]),
            )
            if (
                int(cover_letter.get("page_count") or 0) != 1
                or cover_letter.get("submission_authority") is not False
                or str(cover_letter.get("job_snapshot_sha256") or "")
                != str(operation["job_snapshot_sha256"])
                or str(cover_letter.get("candidate_profile_digest") or "")
                != str(operation["candidate_profile_digest"])
                or not str(cover_letter.get("cover_letter_id") or "")
            ):
                raise RuntimeError(
                    "Native cover-letter worker returned an invalid authority binding."
                )
        except Exception:
            orchestrator.fail_task(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
                error_code="NATIVE_COVER_LETTER_PREPARE_FAILED",
                error_message="Required Native cover-letter generation failed closed.",
                retryable=True,
            )
            raise

    cover_letter_id = (
        str(cover_letter["cover_letter_id"])
        if cover_letter is not None
        else None
    )

    deliver = plan_delivery or application_plan_delivery_v2.deliver_application_plan
    start_session = session_starter or _start_apply_session
    read_status = status_reader or _read_apply_prepare_job
    read_resolutions = resolution_task_reader or _read_apply_resolution_tasks
    freeze_review = review_builder or _freeze_apply_review
    record_review = review_recorder or application_review_v2.create_review

    try:
        plan = application_plan_v2.prepare_application_plan(
            preflight_package_id=str(preflight["preflight_package_id"]),
            idempotency_key=f"prepare:{operation_id}:application-plan:v1c",
            # Initial plan intentionally contains only currently-known
            # requirements. Runtime-discovered NORMAL questions use the already
            # existing immutable replacement-plan flow.
            answer_requirements=[],
            permissions=_plan_permissions(
                cover_letter_present=cover_letter is not None
            ),
            provider_policy=None,
            secure_resolver_refs={},
            resume_format="PDF",
            cover_letter_id=cover_letter_id,
        )
        if (
            str(plan.get("status") or "") != "READY_TO_APPLY"
            or plan.get("executable") is not True
            or plan.get("submission_authority") is not False
        ):
            unresolved = [
                str(row.get("question_key") or row.get("reason") or "application_plan")
                for row in (dict(plan.get("snapshot") or {}).get("unresolved") or [])
                if isinstance(row, Mapping)
            ]
            unresolved = unresolved or ["application_plan.not_executable"]
            result = orchestrator.task_needs_input(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
                unresolved_keys=unresolved,
                output={
                    "version": WORKER_VERSION,
                    "plan_id": plan.get("plan_id"),
                    "plan_status": plan.get("status"),
                    "submission_authority": False,
                },
            )
            return {
                **result,
                "claimed": True,
                "submission_authority": False,
            }

        permissions = dict(dict(plan.get("snapshot") or {}).get("permissions") or {})
        if (
            permissions.get("final_review") is not False
            or permissions.get("final_submit") is not False
        ):
            raise RuntimeError("Background preparation plan gained review/submit authority.")

        delivery = deliver(
            plan_id=str(plan["plan_id"]),
            idempotency_key=f"prepare:{operation_id}:handoff:v1c",
        )
        if (
            str(getattr(delivery, "state", "")) != "PLAN_ACCEPTED"
            or getattr(delivery, "submission_authority", None) is not False
            or str(getattr(delivery, "plan_id", "")) != str(plan["plan_id"])
            or str(getattr(delivery, "plan_digest", "")) != str(plan["plan_digest"])
        ):
            raise RuntimeError("Apply plan handoff acknowledgement is not correlated.")

        session = dict(start_session(plan))
        prepare_job = dict(session["preparation_job"])
        prepare_job_id = str(prepare_job["job_id"])

        deadline = time.monotonic() + float(poll_timeout_seconds)
        latest = prepare_job

        while str(latest.get("state") or "") in _RUNNING_APPLY_STATES:
            if time.monotonic() >= deadline:
                orchestrator.fail_task(
                    task_id=str(task["task_id"]),
                    worker_id=str(worker_id),
                    error_code="APPLY_PREPARATION_STILL_RUNNING",
                    error_message="Apply background preparation is still running.",
                    retryable=True,
                )
                return {
                    "operation_id": str(operation_id),
                    "claimed": True,
                    "task_status": "PENDING",
                    "operation_status": "RUNNING",
                    "apply_prepare_job_id": prepare_job_id,
                    "apply_prepare_state": str(latest.get("state") or ""),
                    "retry_scheduled": True,
                    "submission_authority": False,
                }

            orchestrator.heartbeat_task(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
                lease_seconds=int(lease_seconds),
            )
            if poll_interval_seconds:
                time.sleep(float(poll_interval_seconds))
            latest = dict(
                read_status(
                    job_id=prepare_job_id,
                    tenant_id=str(plan["tenant_id"]),
                    user_id=str(plan["user_id"]),
                )
            )

        state = str(latest.get("state") or "")
        if state == "WAITING_INPUT":
            resolution_payload = dict(
                read_resolutions(
                    job_id=prepare_job_id,
                    tenant_id=str(plan["tenant_id"]),
                    user_id=str(plan["user_id"]),
                )
            )
            runtime_tasks, unresolved_keys = _validate_runtime_resolution_sync(
                resolution_payload,
                prepare_job_id=prepare_job_id,
                session_id=str(session["session_id"]),
                application_id=str(plan["application_id"]),
                plan_id=str(plan["plan_id"]),
            )
            if not runtime_tasks:
                orchestrator.fail_task(
                    task_id=str(task["task_id"]),
                    worker_id=str(worker_id),
                    error_code="APPLY_RUNTIME_QUESTION_SYNC_PENDING",
                    error_message="Apply reported WAITING_INPUT before runtime question metadata became available.",
                    retryable=True,
                )
                return {
                    "operation_id": str(operation_id),
                    "claimed": True,
                    "task_status": "PENDING",
                    "operation_status": "RUNNING",
                    "apply_prepare_job_id": prepare_job_id,
                    "apply_prepare_state": state,
                    "runtime_question_sync_pending": True,
                    "submission_authority": False,
                }
            result = orchestrator.task_needs_input(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
                unresolved_keys=unresolved_keys,
                output={
                    "version": WORKER_VERSION,
                    "plan_id": str(plan["plan_id"]),
                    "plan_digest": str(plan["plan_digest"]),
                    "handoff_id": str(getattr(delivery, "handoff_id", "")),
                    "apply_session_id": str(session["session_id"]),
                    "apply_prepare_job_id": prepare_job_id,
                    "apply_prepare_state": state,
                    "runtime_resolution_tasks": runtime_tasks,
                    "runtime_question_details_synced": True,
                    "runtime_question_details_pending_sync": False,
                    "runtime_resolution_values_persisted": False,
                    "replacement_plan_authority": "application_plan_resolution_v2",
                    "submission_authority": False,
                },
            )
            return {
                **result,
                "claimed": True,
                "runtime_resolution_tasks": runtime_tasks,
                "submission_authority": False,
            }

        if state == "READY_FOR_REVIEW":
            frozen = dict(
                freeze_review(
                    session_id=str(session["session_id"]),
                    tenant_id=str(plan["tenant_id"]),
                    user_id=str(plan["user_id"]),
                )
            )
            snapshot = dict(frozen.get("review") or {})
            browser = dict(snapshot.get("browser_verification") or {})
            application = dict(snapshot.get("application") or {})
            resume_snapshot = dict(snapshot.get("resume") or {})
            if (
                str(snapshot.get("application_id") or "") != str(plan["application_id"])
                or str(snapshot.get("plan_id") or "") != str(plan["plan_id"])
                or str(snapshot.get("session_id") or "") != str(session["session_id"])
                or str(snapshot.get("plan_digest") or "") != str(plan["plan_digest"])
                or str(snapshot.get("submission_readiness") or "") != "READY_FOR_REVIEW"
                or str(resume_snapshot.get("sha256") or "")
                != str(dict(plan["snapshot"]["resume"])["artifact_sha256"])
            ):
                raise RuntimeError("Apply frozen review acknowledgement is not correlated.")
            prepared_package = {
                "application_id": str(snapshot["application_id"]),
                "provider": str(snapshot["provider"]),
                "destination_url": str(snapshot["destination_url"]),
                "browser_form_digest": str(browser["form_digest"]),
                "resume_sha256": str(resume_snapshot["sha256"]),
                "plan_digest": str(snapshot["plan_digest"]),
                "required_fields": int(application.get("required_fields") or 0),
                "completed_required_fields": int(application.get("completed") or 0),
                "unresolved_count": int(application.get("unresolved") or 0),
                "validation_errors": list(application.get("warnings") or []),
                "checkpoint_id": str(snapshot["checkpoint_id"]),
                "review_fields": list(snapshot.get("answers") or []),
            }
            frozen_cover = snapshot.get("cover_letter")
            if isinstance(frozen_cover, Mapping):
                prepared_package["cover_letter_sha256"] = str(
                    frozen_cover.get("sha256") or ""
                )
            hunter_review = dict(
                record_review(
                    plan_id=str(plan["plan_id"]),
                    prepared_package=prepared_package,
                    idempotency_key=f"prepare:{operation_id}:customer-review:v1",
                )
            )
            output = {
                "version": WORKER_VERSION,
                "plan_id": str(plan["plan_id"]),
                "plan_digest": str(plan["plan_digest"]),
                "provider": str(plan["provider"]),
                "handoff_id": str(getattr(delivery, "handoff_id", "")),
                "handoff_state": "PLAN_ACCEPTED",
                "apply_session_id": str(session["session_id"]),
                "apply_session_state_at_start": str(session["state"]),
                "apply_prepare_job_id": prepare_job_id,
                "apply_prepare_state": "READY_FOR_REVIEW",
                "browser_preparation_completed": True,
                "cover_letter_decision": str(cover.get("decision") or ""),
                "cover_letter_id": cover_letter_id,
                "cover_letter_artifact_sha256": (
                    str(cover_letter.get("artifact_sha256") or "")
                    if cover_letter is not None
                    else None
                ),
                "cover_letter_page_count": (
                    int(cover_letter.get("page_count") or 0)
                    if cover_letter is not None
                    else None
                ),
                "customer_review_id": str(hunter_review["review_id"]),
                "customer_review_digest": str(hunter_review["review_digest"]),
                "prepared_package_digest": str(
                    hunter_review["prepared_package_digest"]
                ),
                "browser_form_digest": str(
                    hunter_review["browser_form_digest"]
                ),
                "customer_review_approved": bool(hunter_review.get("approved")),
                "additional_customer_approval_required": False,
                "final_submit_executed": False,
                "submission_authority": False,
            }
            result = orchestrator.complete_task(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
                output=output,
            )
            return {
                **result,
                "claimed": True,
                "output": output,
                "submission_authority": False,
            }

        if state in {"BLOCKED", "FAILED_SAFELY", "CANCELLED"}:
            orchestrator.fail_task(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
                error_code=f"APPLY_PREPARE_{state}",
                error_message=f"Apply background preparation ended in {state}.",
                retryable=False,
            )
            return {
                "operation_id": str(operation_id),
                "claimed": True,
                "task_status": "FAILED",
                "operation_status": "FAILED_SAFELY",
                "apply_prepare_job_id": prepare_job_id,
                "apply_prepare_state": state,
                "submission_authority": False,
            }

        raise RuntimeError(f"Unexpected Apply preparation state: {state or 'EMPTY'}")
    except Exception:
        # If the task is still RUNNING and owned by this worker, return it to the
        # orchestrator retry budget. If a branch above already completed/paused/
        # failed it, fail_task itself will reject and we preserve the original.
        try:
            orchestrator.fail_task(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
                error_code="ATS_FORM_PREPARATION_FAILED",
                error_message="ATS form preparation failed closed.",
                retryable=True,
            )
        except (LookupError, PermissionError, RuntimeError, ValueError):
            pass
        raise
