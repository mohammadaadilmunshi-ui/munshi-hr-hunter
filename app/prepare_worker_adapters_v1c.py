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

from app import application_execution_mandate_v1
from app import application_plan_delivery_v2
from app import application_review_v2
from app import application_plan_v2
from app import application_plan_resolution_v2
from app import application_semantic_binding_v1 as universal_binding
from app import candidate_knowledge_graph_v1 as universal_graph
from app import candidate_ontology_v1 as universal_ontology
from app import universal_answer_resolver_v1 as universal_answers
from app import universal_question_resolver_v1 as universal_questions
from app import application_submit_authorization_v1
from app import application_preflight_package_v1 as preflight_v1
from app import opportunity_intelligence_v3
from app import relationship_intelligence_v3
from app import native_cover_letter_writer_v1
from app import phase67_common
from app import universal_open_ended_generator_v1 as universal_open_ended
from app import prepare_orchestrator_v1 as orchestrator
from app.database import get_connection


WORKERS_ENV = "MUNSHI_PREPARE_WORKER_ADAPTERS_V1C_ENABLED"
ATS_RUNTIME_ENV = "MUNSHI_PREPARE_ATS_RUNTIME_START_ENABLED"
AUTHORITY_DELIVERY_ENV = "MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED"
APPLY_BASE_URL_ENV = "MUNSHI_APPLY_BASE_URL"
APPLY_COMMAND_SECRET_ENV = "MUNSHI_APPLY_COMMAND_SECRET"
APPLY_INTERNAL_HTTP_ENABLED_ENV = "MUNSHI_APPLY_INTERNAL_HTTP_ENABLED"
APPLY_INTERNAL_HTTP_BASE_URL_ENV = "MUNSHI_APPLY_INTERNAL_HTTP_BASE_URL"

WORKER_VERSION = "munshi-prepare-worker-adapters-v1c"
SUBMISSION_AUTHORITY = False

_APPLY_SESSION_PATH = "/v1/complete-loop/sessions"
_APPLY_PREPARE_JOB_PATH = "/v1/complete-loop/preparation-jobs/{job_id}"
_APPLY_RESOLUTION_TASK_PATH = "/v1/complete-loop/preparation-jobs/{job_id}/resolution-tasks"
_APPLY_REVIEW_PATH = "/v1/complete-loop/sessions/{session_id}/review"
_APPLY_UNIVERSAL_SEMANTIC_PATH = "/v1/universal-brain/semantic-interpretation"
_APPLY_SUBMIT_AUTHORITY_PATH = (
    "/v1/complete-loop/sessions/{session_id}/submit-authority"
)
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


def submit_authority_delivery_enabled() -> bool:
    """Canonical-authority delivery is separately gated and default-off."""
    return _truthy(AUTHORITY_DELIVERY_ENV)


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


def _optional_relationship_id(
    operation: Mapping[str, Any],
    *,
    opportunity: Mapping[str, Any],
) -> str | None:
    """Return only advisory relationship context bound to this exact opportunity.

    Recruiter discovery runs before Phase 6 opportunity evaluation, so an older
    advisory strategy may legitimately lack the exact evaluation binding needed
    by the immutable preflight package. Because people context is optional, that
    stale/mismatched advisory must be omitted rather than blocking the required
    application path.
    """
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
    if not value:
        return None
    try:
        strategy = relationship_intelligence_v3.get_strategy(value)
        freshness = relationship_intelligence_v3.strategy_freshness(value)
    except (LookupError, RuntimeError, ValueError):
        return None
    if freshness.get("fresh") is not True:
        return None
    if int(strategy.get("job_id") or 0) != int(operation["job_id"]):
        return None
    if str(strategy.get("job_snapshot_sha256") or "") != str(
        operation["job_snapshot_sha256"]
    ):
        return None
    context = dict(strategy.get("opportunity_context") or {})
    if str(context.get("evaluation_id") or "") != str(
        opportunity.get("evaluation_id") or ""
    ):
        return None
    if str(context.get("result_sha256") or "") != str(
        opportunity.get("result_sha256") or ""
    ):
        return None
    return value


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


def _reusable_preflight_output(
    *,
    operation: Mapping[str, Any],
    resume: Mapping[str, Any],
    cover: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Reuse an exact fresh preflight package across technical retries.

    The customer mandate is bound to the immutable preflight package. Creating
    a new package for an otherwise identical retry would discard that binding
    and can needlessly regenerate a cover letter. Only exact application, job,
    truth, snapshot, resume, and cover-policy matches are eligible for reuse.
    """
    connection = get_connection()
    try:
        rows = connection.execute(
            """SELECT t.output_json
                 FROM prepare_tasks_v1 AS t
                 JOIN prepare_operations_v1 AS o
                   ON o.operation_id=t.operation_id
                WHERE o.tenant_id=? AND o.user_id=?
                  AND o.application_id=? AND o.job_id=?
                  AND o.job_snapshot_sha256=?
                  AND o.application_truth_projection_digest=?
                  AND o.candidate_profile_digest=?
                  AND o.operation_id<>?
                  AND t.task_kind='APPLICATION_PREFLIGHT'
                  AND t.status='SUCCEEDED'
                ORDER BY datetime(t.updated_at) DESC,t.rowid DESC
                LIMIT 12""",
            (
                str(operation["tenant_id"]),
                str(operation["user_id"]),
                str(operation["application_id"]),
                int(operation["job_id"]),
                str(operation["job_snapshot_sha256"]),
                str(operation["application_truth_projection_digest"]),
                str(operation["candidate_profile_digest"]),
                str(operation["operation_id"]),
            ),
        ).fetchall()
    finally:
        connection.close()

    resume_pdf = dict(resume.get("pdf") or {})
    for row in rows:
        try:
            output = json.loads(str(row["output_json"] or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(output, dict):
            continue
        package_id = str(output.get("preflight_package_id") or "").strip()
        if (
            not package_id
            or output.get("submission_authority") is not False
            or str(output.get("preflight_status") or "") != "READY_TO_APPLY"
            or str(output.get("resume_version_id") or "")
            != str(resume.get("resume_version_id") or "")
            or str(output.get("resume_pdf_sha256") or "")
            != str(resume_pdf.get("artifact_sha256") or "")
            or str(output.get("cover_letter_decision") or "")
            != str(cover.get("decision") or "")
        ):
            continue
        try:
            package = preflight_v1.get_preflight_package(package_id)
            freshness = preflight_v1.preflight_package_freshness(package_id)
        except (LookupError, RuntimeError, ValueError):
            continue
        if (
            freshness.get("fresh") is not True
            or str(package.get("application_id") or "")
            != str(operation["application_id"])
            or int(package.get("job_id") or 0) != int(operation["job_id"])
            or str(package.get("job_snapshot_sha256") or "")
            != str(operation["job_snapshot_sha256"])
            or str(package.get("candidate_profile_digest") or "")
            != str(operation["candidate_profile_digest"])
            or str(package.get("resume_version_id") or "")
            != str(resume.get("resume_version_id") or "")
            or str(package.get("package_digest") or "")
            != str(output.get("preflight_package_digest") or "")
            or str(package.get("status") or "") != "READY_TO_APPLY"
        ):
            continue
        return output
    return None


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
        cover = _cover_decision(operation)
        reused_output = _reusable_preflight_output(
            operation=operation,
            resume=resume,
            cover=cover,
        )
        if reused_output is not None:
            result = orchestrator.complete_task(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
                output=reused_output,
            )
            return {
                **result,
                "claimed": True,
                "output": reused_output,
                "reused_preflight_package": True,
                "submission_authority": False,
            }
        opportunity = opportunity_intelligence_v3.evaluate_job(
            int(operation["job_id"]),
            persist=True,
        )
        relationship_strategy_id = _optional_relationship_id(
            operation,
            opportunity=opportunity,
        )

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

        reviewed_cover_letter: dict[str, Any] | None = None
        if str(cover.get("decision") or "") != "NOT_REQUIRED":
            reviewed_cover_letter = native_cover_letter_writer_v1.generate(
                preflight_package_id=str(package["package_id"]),
            )
            if (
                int(reviewed_cover_letter.get("page_count") or 0) != 1
                or reviewed_cover_letter.get("submission_authority") is not False
                or str(reviewed_cover_letter.get("job_snapshot_sha256") or "")
                != str(operation["job_snapshot_sha256"])
                or str(reviewed_cover_letter.get("candidate_profile_digest") or "")
                != str(operation["candidate_profile_digest"])
                or not str(reviewed_cover_letter.get("cover_letter_id") or "")
            ):
                raise RuntimeError(
                    "Reviewable cover-letter artifact is not exactly bound to the current application."
                )

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
            "cover_letter_decision": str(cover.get("decision") or ""),
            "cover_letter_id": (
                str(reviewed_cover_letter.get("cover_letter_id") or "")
                if reviewed_cover_letter is not None
                else None
            ),
            "cover_letter_artifact_sha256": (
                str(reviewed_cover_letter.get("artifact_sha256") or "")
                if reviewed_cover_letter is not None
                else None
            ),
            "cover_letter_page_count": (
                int(reviewed_cover_letter.get("page_count") or 0)
                if reviewed_cover_letter is not None
                else None
            ),
            "single_approval_package_ready": True,
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
    except Exception as error:
        if orchestrator.sqlite_writer_busy(error):
            orchestrator.defer_task_for_contention(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
            )
            raise
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
    internal_http = (
        parsed.scheme == "http"
        and _truthy(APPLY_INTERNAL_HTTP_ENABLED_ENV)
        and raw == str(os.getenv(APPLY_INTERNAL_HTTP_BASE_URL_ENV) or "").strip().rstrip("/")
        and bool(host)
        and "." not in host
        and parsed.path in {"", "/"}
    )
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not host
        or not (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and loopback)
            or internal_http
        )
    ):
        raise ValueError(
            "Apply base URL must use HTTPS, loopback HTTP, or the exact explicitly "
            "allowlisted private Docker bridge URL."
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
    require_ats_runtime: bool = True,
) -> dict[str, Any]:
    if require_ats_runtime and not ats_runtime_enabled():
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


def _interpret_apply_universal_semantics(
    payload: Mapping[str, Any],
    *,
    tenant_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Ask Apply/Sonnet to choose among Hunter ontology candidates, value-free."""
    if payload.get("candidate_values") is not None:
        raise ValueError("Universal semantic interpretation must never send candidate values.")
    base = _apply_base_url()
    result = _json_request(
        method="POST",
        url=base + _APPLY_UNIVERSAL_SEMANTIC_PATH,
        headers=_apply_headers(tenant_id=tenant_id, user_id=user_id),
        body={"payload": dict(payload)},
        timeout_seconds=30.0,
    )
    if (
        result.get("candidate_values_sent") is not False
        or result.get("secret_material_sent") is not False
        or result.get("protected_values_sent") is not False
    ):
        raise RuntimeError("Apply semantic interpretation violated the value-free contract.")
    return {
        "concept_id": str(result.get("concept_id") or "").strip().casefold(),
        "confidence": float(result.get("confidence") or 0.0),
        "reason_code": str(result.get("reason_code") or "MODEL_SEMANTIC_INTERPRETATION")[:120],
    }


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
    "semantic_type", "question_observation", "category", "sensitivity_class", "status", "risk_level", "auto_resolvable",
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
    browser_form_digest = str(payload.get("browser_form_digest") or "").strip().lower()
    checkpoint_id = str(payload.get("checkpoint_id") or "").strip()
    raw_tasks = payload.get("resolution_tasks")
    if not isinstance(raw_tasks, list):
        raise RuntimeError("Apply runtime resolution task list is invalid.")
    if raw_tasks:
        if len(browser_form_digest) != 64 or any(ch not in "0123456789abcdef" for ch in browser_form_digest):
            raise RuntimeError("Apply runtime resolution browser form digest is invalid.")
        if not checkpoint_id:
            raise RuntimeError("Apply runtime resolution checkpoint binding is missing.")
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


def _universal_runtime_execution_text(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, Mapping):
        return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def _runtime_binding_source(classification_lane: str) -> str:
    lane = str(classification_lane or "").upper()
    if lane == "BOUNDED_MODEL_INTERPRETATION":
        return "MODEL"
    if lane.startswith("DETERMINISTIC"):
        return "DETERMINISTIC"
    return "ONTOLOGY"


def _universal_runtime_job_context(plan: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = dict(plan.get("snapshot") or {})
    planned_job = dict(snapshot.get("job") or plan.get("job") or {})
    job_id = planned_job.get("id") or plan.get("job_id")
    if job_id in (None, ""):
        return {}
    tenant_id = str(plan.get("tenant_id") or "").strip()
    user_id = str(plan.get("user_id") or "").strip()
    if tenant_id and user_id:
        owned = phase67_common.safe_owned_job_snapshot(
            int(job_id), tenant_id=tenant_id, user_id=user_id
        )
    else:
        owned = phase67_common.safe_owned_job_snapshot(int(job_id))
    expected = str(planned_job.get("job_snapshot_digest") or "").strip().lower()
    actual = str(owned.get("job_snapshot_sha256") or "").strip().lower()
    if expected and expected != actual:
        raise RuntimeError("Universal Brain job context no longer matches immutable Application Plan.")
    return dict(owned.get("job") or {})


def _resolve_universal_runtime_observation(
    *,
    plan: Mapping[str, Any],
    session_id: str,
    checkpoint_id: str,
    observation: Mapping[str, Any],
    job_context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resolve one value-free browser observation and maintain durable semantics."""
    application_id = str(plan.get("application_id") or "").strip()
    plan_id = str(plan.get("plan_id") or "").strip()
    binding_row: dict[str, Any] | None = None

    if universal_graph.universal_browser_binding_enabled() and application_id and session_id:
        try:
            binding_row = universal_binding.lookup(
                application_id=application_id,
                session_id=session_id,
                observation=dict(observation),
                allow_application_fallback=True,
            )
        except (LookupError, RuntimeError, ValueError):
            binding_row = None

    if binding_row is not None:
        concept = universal_ontology.require_concept(str(binding_row.get("concept_id") or ""))
        if concept.sensitivity != "NORMAL":
            raise ValueError("Durable runtime semantic binding points to a protected concept.")
        if binding_row.get("cross_session") is True:
            binding_row = universal_binding.bind(
                application_id=application_id,
                session_id=session_id,
                observation=dict(observation),
                concept_id=concept.concept_id,
                classification_source=str(binding_row.get("classification_source") or "ONTOLOGY"),
                confidence=float(binding_row.get("confidence") or 0.0),
                plan_id=plan_id or None,
                checkpoint_id=checkpoint_id or None,
            )
        question = {
            "state": "RESOLVED",
            "concept_id": concept.concept_id,
            "confidence": float(binding_row.get("confidence") or 0.0),
            "classification_lane": "DURABLE_BROWSER_BINDING",
            "sensitivity": "NORMAL",
            "reason_code": "REUSED_LOGICAL_FIELD_BINDING",
            "conditions": {},
            "candidate_values_exposed": False,
        }
        return (
            universal_answers.resolve_concept(
                question,
                open_ended_generator=universal_open_ended.generate,
                job_context=dict(job_context or {}),
            ),
            universal_binding.public_binding(binding_row),
        )

    model_interpreter = None
    if universal_questions.model_semantics_enabled():
        tenant_id = str(plan.get("tenant_id") or "").strip()
        user_id = str(plan.get("user_id") or "").strip()
        if tenant_id and user_id:
            def model_interpreter(payload: dict[str, Any]) -> Mapping[str, Any]:
                try:
                    return _interpret_apply_universal_semantics(
                        payload, tenant_id=tenant_id, user_id=user_id
                    )
                except (RuntimeError, ValueError, TypeError):
                    return {}

    if model_interpreter is None:
        resolved = universal_answers.resolve_observation(
            dict(observation),
            open_ended_generator=universal_open_ended.generate,
            job_context=dict(job_context or {}),
        )
    else:
        resolved = universal_answers.resolve_observation(
            dict(observation), model_interpreter=model_interpreter,
            open_ended_generator=universal_open_ended.generate,
            job_context=dict(job_context or {}),
        )

    question = dict(resolved.get("question_resolution") or {})
    if (
        universal_graph.universal_browser_binding_enabled()
        and application_id
        and session_id
        and question.get("state") == "RESOLVED"
        and question.get("concept_id")
        and str(question.get("sensitivity") or "NORMAL").upper() == "NORMAL"
    ):
        try:
            bound = universal_binding.bind(
                application_id=application_id,
                session_id=session_id,
                observation=dict(observation),
                concept_id=str(question["concept_id"]),
                classification_source=_runtime_binding_source(
                    str(question.get("classification_lane") or "")
                ),
                confidence=float(question.get("confidence") or 0.0),
                plan_id=plan_id or None,
                checkpoint_id=checkpoint_id or None,
            )
            binding_row = universal_binding.public_binding(bound)
        except (LookupError, RuntimeError, ValueError):
            binding_row = None
    return resolved, binding_row


def _universal_runtime_auto_resolution(
    *,
    operation_id: str,
    plan: Mapping[str, Any],
    session_id: str,
    resolution_payload: Mapping[str, Any],
    runtime_tasks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve every NORMAL runtime field from Universal Brain or return None.

    Partial automatic resolution is intentionally not committed. Either every
    current normal runtime field is safely grounded and a single immutable
    replacement plan is created, or the existing user-facing NEEDS_INPUT path
    remains authoritative for the unresolved set.
    """
    if (
        not universal_graph.universal_normal_autofill_enabled()
        or not application_plan_v2.runtime_application_plan_resolution_enabled()
        or not runtime_tasks
    ):
        return None

    answers: list[dict[str, str]] = []
    questions: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    job_context = _universal_runtime_job_context(plan)
    for task in runtime_tasks:
        if str(task.get("sensitivity_class") or "").upper() != "NORMAL":
            return None
        observation = task.get("question_observation")
        if not isinstance(observation, Mapping):
            return None
        resolved, binding = _resolve_universal_runtime_observation(
            plan=plan,
            session_id=str(session_id),
            checkpoint_id=str(resolution_payload.get("checkpoint_id") or ""),
            observation=dict(observation),
            job_context=job_context,
        )
        trace = universal_answers.public_trace(resolved)
        traces.append(
            {
                "task_id": str(task.get("task_id") or ""),
                "concept_id": trace.get("concept_id"),
                "answer_status": trace.get("answer_status"),
                "answer_mode": trace.get("answer_mode"),
                "classification_lane": dict(trace.get("question_resolution") or {}).get("classification_lane"),
                "confidence": trace.get("confidence"),
                "knowledge_revision": trace.get("knowledge_revision"),
                "autofill_allowed": bool(trace.get("autofill_allowed")),
                "browser_binding_id": (binding or {}).get("binding_id"),
                "browser_binding_revision": (binding or {}).get("binding_revision"),
                "value_exposed": False,
            }
        )
        status = str(resolved.get("answer_status") or "")
        if (
            status not in {"ANSWERED", "DERIVED", "POLICY_RESOLVED", "GENERATED"}
            or resolved.get("autofill_allowed") is not True
            or resolved.get("value") is None
        ):
            return None
        question_key = str(task.get("group_key") or task.get("question_id") or "").strip()
        if not question_key:
            return None
        provenance = {
            "ANSWERED": "universal_brain_existing_knowledge",
            "DERIVED": "universal_brain_derived_evidence",
            "POLICY_RESOLVED": "universal_brain_policy_resolution",
            "GENERATED": "universal_brain_generated_evidence",
        }[status]
        answers.append(
            {
                "question_key": question_key,
                "canonical_answer": _universal_runtime_execution_text(resolved.get("value")),
                "provenance": provenance,
            }
        )
        questions.append(
            {
                "question_key": question_key,
                "control_id": str(task.get("control_id") or ""),
                "question": str(task.get("question") or question_key),
                "semantic_type": str(task.get("semantic_type") or "unknown"),
                "sensitivity_class": "NORMAL",
                "question_observation": dict(observation),
            }
        )

    browser_form_digest = str(resolution_payload.get("browser_form_digest") or "")
    checkpoint_id = str(resolution_payload.get("checkpoint_id") or "")
    stable_material = json.dumps(
        {
            "plan_id": str(plan["plan_id"]),
            "form_digest": browser_form_digest,
            "questions": sorted((q["question_key"], q["control_id"]) for q in questions),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    import hashlib
    token = hashlib.sha256(stable_material.encode("utf-8")).hexdigest()[:32]
    revision_key = f"prepare:{operation_id}:universal-runtime:{token}"
    replacement = application_plan_resolution_v2.resolve_runtime_needs_input(
        prior_plan_id=str(plan["plan_id"]),
        expected_prior_plan_digest=str(plan["plan_digest"]),
        revision_idempotency_key=revision_key,
        runtime_context={
            "session_id": str(session_id),
            "checkpoint_id": checkpoint_id,
            "browser_form_digest": browser_form_digest,
            "unresolved_questions": questions,
        },
        answer_resolutions=answers,
    )
    return {
        "replacement": replacement,
        "trace": traces,
        "resolved_count": len(answers),
        "revision_idempotency_key": revision_key,
        "value_exposed": False,
    }


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


def _application_plan_idempotency_key(
    *,
    operation: Mapping[str, Any],
    pending_task: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> tuple[str, str | None]:
    """Select the immutable plan key, preserving provider-capability recovery.

    A prior V1C plan may have stopped solely because its ATS family was not yet
    supported.  Once that exact provider is added, reusing the original
    idempotency key would correctly detect a different digest.  In that one
    narrowly-scoped case we create a new immutable plan revision key.  Any
    answer blocker, stale binding, mixed blocker set, or authority anomaly keeps
    the original key/fails closed.
    """
    base = f"prepare:{operation['operation_id']}:application-plan:v1c"
    raw_output = pending_task.get("output")
    if not isinstance(raw_output, Mapping):
        return base, None
    prior_plan_id = str(raw_output.get("plan_id") or "").strip()
    if not prior_plan_id:
        return base, None

    prior = application_plan_v2.get_application_plan(prior_plan_id)
    snapshot = dict(prior.get("snapshot") or {})
    blockers = list(snapshot.get("global_blockers") or [])
    unresolved = list(snapshot.get("unresolved") or [])
    policy = dict(snapshot.get("provider_policy") or {})
    resume = dict(snapshot.get("resume") or {})

    provider_only = (
        str(prior.get("status") or "") == "NEEDS_INPUT"
        and prior.get("executable") is False
        and prior.get("submission_authority") is False
        and str(snapshot.get("expected_state") or "") == "NEEDS_INPUT"
        and blockers == ["provider_not_permitted_or_supported"]
        and unresolved == []
        and str(prior.get("provider") or "") == "UNSUPPORTED"
        and str(policy.get("provider") or "") == "UNSUPPORTED"
        and policy.get("permitted") is False
    )
    if not provider_only:
        return base, None

    exact_binding = (
        str(prior.get("application_id") or "") == str(operation["application_id"])
        and int(prior.get("job_id") or 0) == int(operation["job_id"])
        and str(prior.get("preflight_package_id") or "")
        == str(preflight["preflight_package_id"])
        and str(resume.get("version_id") or "")
        == str(preflight["resume_version_id"])
        and str(resume.get("artifact_sha256") or "")
        == str(preflight["resume_pdf_sha256"])
    )
    if not exact_binding:
        raise RuntimeError(
            "Provider-capability retry refused a mismatched prior Application Plan."
        )
    # Re-capture the exact immutable inputs and permit only the semantic
    # transition caused by newly-supported provider capability.  Any Candidate
    # Truth, job, preflight, resume, answer, permission, or cover-letter drift
    # remains a hard stop.
    inputs = dict(prior.get("inputs") or {})
    current = application_plan_v2._capture(
        preflight_package_id=str(inputs["preflight_package_id"]),
        requirements=list(inputs["answer_requirements"]),
        permissions=dict(inputs["permissions"]),
        provider_policy=dict(inputs["provider_policy"]),
        secure_refs=dict(inputs["secure_resolver_refs"]),
        resume_format=str(inputs["resume_format"]),
        cover_letter_id=(
            str(inputs.get("cover_letter_id") or "").strip() or None
        ),
        answer_overrides=dict(inputs.get("answer_overrides") or {}),
    )
    old_payload = application_plan_v2.plan_digest_payload(snapshot)
    new_payload = application_plan_v2.plan_digest_payload(current)
    changed = {
        key
        for key in set(old_payload) | set(new_payload)
        if old_payload.get(key) != new_payload.get(key)
    }
    expected_changed = {
        "provider_policy",
        "global_blockers",
        "expected_state",
        "executable",
    }
    current_policy = dict(current.get("provider_policy") or {})
    capability_only = (
        changed == expected_changed
        and current.get("executable") is True
        and str(current.get("expected_state") or "") == "READY_TO_APPLY"
        and list(current.get("global_blockers") or []) == []
        and list(current.get("unresolved") or []) == []
        and str(current_policy.get("provider") or "") in application_plan_v2.SUPPORTED_PROVIDERS
        and str(current_policy.get("provider") or "") != "UNSUPPORTED"
        and current_policy.get("permitted") is True
    )
    if not capability_only:
        raise RuntimeError(
            "Provider-capability retry detected non-provider Application Plan drift."
        )
    return f"{base}:provider-capability-v2", prior_plan_id


def _runtime_replacement_plan(
    *,
    operation: Mapping[str, Any],
    pending_task: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Return the exact immutable runtime replacement plan when one already exists.

    Runtime NEEDS_INPUT is resolved by creating a distinct immutable Application
    Plan revision.  Re-running the ATS task must never recreate or re-deliver the
    superseded plan under its original idempotency key.  This is provider-neutral:
    the only authority is the stored plan-supersession lineage.
    """
    raw_output = pending_task.get("output")
    if not isinstance(raw_output, Mapping):
        return None, None
    prior_plan_id = str(raw_output.get("plan_id") or "").strip()
    if not prior_plan_id:
        return None, None

    supersession = application_plan_v2.runtime_plan_replacement(prior_plan_id)
    if not supersession:
        return None, None
    replacement_id = str(supersession.get("replacement_plan_id") or "").strip()
    if not replacement_id:
        raise RuntimeError("Runtime plan supersession has no replacement plan.")

    replacement = application_plan_v2.get_application_plan(replacement_id)
    snapshot = dict(replacement.get("snapshot") or {})
    resume = dict(snapshot.get("resume") or {})
    exact_binding = (
        str(replacement.get("application_id") or "") == str(operation["application_id"])
        and int(replacement.get("job_id") or 0) == int(operation["job_id"])
        and str(replacement.get("preflight_package_id") or "")
        == str(preflight["preflight_package_id"])
        and str(resume.get("version_id") or "") == str(preflight["resume_version_id"])
        and str(resume.get("artifact_sha256") or "")
        == str(preflight["resume_pdf_sha256"])
        and str(replacement.get("status") or "") == "READY_TO_APPLY"
        and replacement.get("executable") is True
        and replacement.get("submission_authority") is False
        and application_plan_v2.application_plan_freshness(replacement_id).get("fresh")
        is True
    )
    if not exact_binding:
        raise RuntimeError(
            "Runtime replacement Application Plan is stale or mismatched."
        )
    return replacement, prior_plan_id


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

    pending_ats_task = _pending_task(
        operation, orchestrator.TASK_ATS_FORM_PREPARATION
    )
    if pending_ats_task is None:
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
    mandate = application_execution_mandate_v1.current_for_preflight(
        str(preflight["preflight_package_id"])
    )
    if mandate is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "single_customer_approval_required",
            "preflight_package_id": str(preflight["preflight_package_id"]),
            "operation_status": str(operation["status"]),
            "submission_authority": False,
        }

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
    cover_decision = str(cover.get("decision") or "")
    if cover_decision != "NOT_REQUIRED":
        cover_letter_id_value = str(preflight.get("cover_letter_id") or "").strip()
        cover_letter_sha = str(
            preflight.get("cover_letter_artifact_sha256") or ""
        ).strip()
        if (
            not cover_letter_id_value
            or len(cover_letter_sha) != 64
            or int(preflight.get("cover_letter_page_count") or 0) != 1
        ):
            orchestrator.fail_task(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
                error_code="REVIEWED_COVER_LETTER_MISSING",
                error_message=(
                    "The cover-letter policy allowed a document, but no reviewed "
                    "artifact was present before browser execution."
                ),
                retryable=False,
            )
            raise RuntimeError(
                "Potential cover-letter artifact must be reviewed before unattended ATS execution."
            )
        cover_letter = {
            "cover_letter_id": cover_letter_id_value,
            "artifact_sha256": cover_letter_sha,
            "page_count": 1,
            "submission_authority": False,
        }

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
        runtime_plan, runtime_supersession_from_plan_id = _runtime_replacement_plan(
            operation=operation,
            pending_task=pending_ats_task,
            preflight=preflight,
        )
        provider_capability_retry_from_plan_id = None
        if runtime_plan is not None:
            plan = runtime_plan
            plan_key = str(plan["idempotency_key"])
        else:
            plan_key, provider_capability_retry_from_plan_id = (
                _application_plan_idempotency_key(
                    operation=operation,
                    pending_task=pending_ats_task,
                    preflight=preflight,
                )
            )
            plan = application_plan_v2.prepare_application_plan(
                preflight_package_id=str(preflight["preflight_package_id"]),
                idempotency_key=plan_key,
                # Initial plan intentionally contains only currently-known
                # requirements. Runtime-discovered NORMAL questions use the
                # immutable replacement-plan flow above on continuation.
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
                    "provider_capability_retry_from_plan_id": (
                        provider_capability_retry_from_plan_id
                    ),
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

        handoff_key = (
            f"prepare:{operation_id}:handoff:runtime:{plan['plan_id']}"
            if runtime_supersession_from_plan_id
            else f"prepare:{operation_id}:handoff:v1c"
        )
        delivery = deliver(
            plan_id=str(plan["plan_id"]),
            idempotency_key=handoff_key,
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
            universal_runtime = _universal_runtime_auto_resolution(
                operation_id=str(operation_id),
                plan=plan,
                session_id=str(session["session_id"]),
                resolution_payload=resolution_payload,
                runtime_tasks=runtime_tasks,
            )
            if universal_runtime is not None:
                replacement = dict(universal_runtime["replacement"])
                result = orchestrator.requeue_task_after_internal_resolution(
                    task_id=str(task["task_id"]),
                    worker_id=str(worker_id),
                    resolution_kind="UNIVERSAL_BRAIN_NORMAL_FIELDS",
                    output={
                        "version": WORKER_VERSION,
                        "plan_id": str(plan["plan_id"]),
                        "plan_digest": str(plan["plan_digest"]),
                        "apply_session_id": str(session["session_id"]),
                        "apply_prepare_job_id": prepare_job_id,
                        "apply_prepare_state": state,
                        "runtime_resolution_tasks": runtime_tasks,
                        "universal_brain_auto_resolved": True,
                        "universal_brain_resolution_trace": universal_runtime["trace"],
                        "universal_brain_resolved_count": int(universal_runtime["resolved_count"]),
                        "replacement_plan_id": str(replacement["replacement_plan_id"]),
                        "replacement_plan_digest": str(replacement["replacement_plan_digest"]),
                        "replacement_plan_authority": "application_plan_resolution_v2",
                        "runtime_resolution_values_persisted": False,
                        "submission_authority": False,
                    },
                )
                return {
                    **result,
                    "claimed": True,
                    "universal_brain_auto_resolved": True,
                    "universal_brain_resolved_count": int(universal_runtime["resolved_count"]),
                    "replacement_plan_id": str(replacement["replacement_plan_id"]),
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
                    idempotency_key=f"prepare:{operation_id}:internal-integrity-review:v2",
                )
            )
            technical_approval = application_review_v2.approve_review_from_execution_mandate(
                review_id=str(hunter_review["review_id"]),
                execution_mandate_id=str(mandate["mandate_id"]),
                idempotency_key=(
                    f"prepare:{operation_id}:mandate-derived-internal-approval:v1"
                ),
            )
            internal_snapshot = dict(hunter_review.get("review") or {})
            internal_bindings = dict(internal_snapshot.get("bindings") or {})
            authority = application_submit_authorization_v1.issue_authorization(
                {
                    "idempotency_key": (
                        f"prepare:{operation_id}:autonomous-submit-authority:v1"
                    ),
                    "tenant_id": str(plan["tenant_id"]),
                    "user_id": str(plan["user_id"]),
                    "application_id": str(hunter_review["application_id"]),
                    "plan_id": str(plan["plan_id"]),
                    "session_id": str(session["session_id"]),
                    "review_id": str(hunter_review["review_id"]),
                    "approval_id": str(technical_approval["approval_id"]),
                    "provider": str(internal_snapshot["provider"]),
                    "target_url": str(internal_snapshot["destination_url"]),
                    "checkpoint_id": str(internal_bindings["checkpoint_id"]),
                    "plan_digest": str(hunter_review["plan_digest"]),
                    "review_digest": str(hunter_review["review_digest"]),
                    "approval_digest": str(technical_approval["approval_digest"]),
                    "prepared_package_digest": str(
                        hunter_review["prepared_package_digest"]
                    ),
                    "browser_form_digest": str(hunter_review["browser_form_digest"]),
                    "resume_sha256": str(hunter_review["resume_artifact_sha256"]),
                    "cover_letter_sha256": internal_bindings.get(
                        "cover_letter_artifact_sha256"
                    ),
                }
            )
            authority_delivery = deliver_submit_authorization(authority)
            output = {
                "version": WORKER_VERSION,
                "plan_id": str(plan["plan_id"]),
                "plan_digest": str(plan["plan_digest"]),
                "provider": str(plan["provider"]),
                "provider_capability_retry_from_plan_id": (
                    provider_capability_retry_from_plan_id
                ),
                "runtime_supersession_from_plan_id": (
                    runtime_supersession_from_plan_id
                ),
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
                "internal_integrity_review_id": str(hunter_review["review_id"]),
                "internal_integrity_review_digest": str(hunter_review["review_digest"]),
                # Compatibility aliases remain read-only during migration. Product
                # UI must never render them as a second customer review.
                "customer_review_id": str(hunter_review["review_id"]),
                "customer_review_digest": str(hunter_review["review_digest"]),
                "execution_mandate_id": str(mandate["mandate_id"]),
                "single_customer_approval_complete": True,
                "technical_approval_id": str(technical_approval["approval_id"]),
                "submit_authorization_id": str(authority["authorization_id"]),
                "authority_delivery": authority_delivery,
                "prepared_package_digest": str(
                    hunter_review["prepared_package_digest"]
                ),
                "browser_form_digest": str(
                    hunter_review["browser_form_digest"]
                ),
                "customer_review_approved": True,
                "additional_customer_approval_required": False,
                "post_approval_user_review_required": False,
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
            # Preserve Apply's bounded terminal diagnostic instead of collapsing
            # every browser/runtime failure into the same opaque Hunter error.
            # This is observability only: it grants no retry or submission authority.
            apply_last_error = " ".join(
                str(latest.get("last_error") or "").split()
            )[:1000]
            terminal_message = (
                f"Apply background preparation ended in {state}."
                + (
                    f" Apply diagnostic: {apply_last_error}"
                    if apply_last_error
                    else ""
                )
            )
            orchestrator.fail_task(
                task_id=str(task["task_id"]),
                worker_id=str(worker_id),
                error_code=f"APPLY_PREPARE_{state}",
                error_message=terminal_message,
                retryable=False,
            )
            return {
                "operation_id": str(operation_id),
                "claimed": True,
                "task_status": "FAILED",
                "operation_status": "FAILED_SAFELY",
                "apply_prepare_job_id": prepare_job_id,
                "apply_prepare_state": state,
                "apply_last_error": apply_last_error or None,
                "submission_authority": False,
            }

        raise RuntimeError(f"Unexpected Apply preparation state: {state or 'EMPTY'}")
    except Exception as error:
        # Infrastructure SQLite contention is not a customer/model attempt. If
        # this worker still owns the task, refund the claim before deferring.
        if orchestrator.sqlite_writer_busy(error):
            try:
                orchestrator.defer_task_for_contention(
                    task_id=str(task["task_id"]),
                    worker_id=str(worker_id),
                )
            except (LookupError, PermissionError, RuntimeError, ValueError):
                pass
            raise
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


def deliver_submit_authorization(
    envelope: Mapping[str, Any], *, timeout_seconds: float = 5.0
) -> dict[str, Any]:
    """Hand the canonical one-use authority to the Apply session that earned it.

    Best-effort by design. The customer's approval and the minted authority are
    already durable, and the authority stays ISSUED, so Apply can still pull the
    identical envelope over the authenticated bridge
    (``SUBMIT_AUTHORIZATION_READ``) if this call fails or its response is lost.
    A caller must therefore never roll back the approval because delivery failed.
    """
    if not submit_authority_delivery_enabled():
        return {"attempted": False, "delivered": False, "error": None}

    session_id = str(envelope.get("session_id") or "").strip()
    if not session_id:
        return {
            "attempted": False,
            "delivered": False,
            "error": "Submit authority has no Apply session binding.",
        }

    try:
        result = _json_request(
            method="POST",
            url=_apply_base_url()
            + _APPLY_SUBMIT_AUTHORITY_PATH.format(session_id=session_id),
            headers=_apply_headers(
                tenant_id=str(envelope.get("tenant_id") or ""),
                user_id=str(envelope.get("user_id") or ""),
            ),
            body=dict(envelope),
            timeout_seconds=timeout_seconds,
            require_ats_runtime=False,
        )
    except (RuntimeError, ValueError) as error:
        return {"attempted": True, "delivered": False, "error": str(error)}

    if (
        str(result.get("authorization_id") or "")
        != str(envelope.get("authorization_id") or "")
        or result.get("accepted") is not True
        or str(result.get("session_id") or "") != session_id
    ):
        return {
            "attempted": True,
            "delivered": False,
            "error": "Apply submit-authority acknowledgement is not correlated.",
        }
    return {"attempted": True, "delivered": True, "error": None}
