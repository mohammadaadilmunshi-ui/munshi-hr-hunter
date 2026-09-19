"""First real worker adapters for the durable Prepare Orchestrator.

V1A connects exactly two root tasks:

1. RESUME_PREPARE for the MUNSHI Native Resume V5 engine.
   The worker generates one truth/JD-bound resume and materializes the exact
   immutable DOCX + one-page PDF. The orchestrator stores only immutable
   identifiers/hashes/references, never raw file bytes.

2. RECRUITER_DISCOVERY using Relationship Intelligence V3.
   This ranks recruiter/contact evidence already owned by Hunter. It performs
   no external people search and no outreach. A later discovery provider may
   feed additional relationship evidence without changing this authority.

The existing production n8n dispatcher is deliberately NOT invoked here.
A separate guarded n8n worker adapter is required before N8N operations may
claim RESUME_PREPARE.

No adapter in this module grants submission authority.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from app import native_resume_artifact_v5
from app import native_resume_service_v5
from app import prepare_orchestrator_v1 as orchestrator
from app import relationship_intelligence_v3


WORKERS_ENV = "MUNSHI_PREPARE_WORKER_ADAPTERS_V1A_ENABLED"
PINNED_RESUME_ENV = "MUNSHI_PREPARE_PIN_RESUME_VERSION_ID"
WORKER_VERSION = "munshi-prepare-worker-adapters-v1a"
SUBMISSION_AUTHORITY = False

_DEFAULT_NATIVE_INSTRUCTION = (
    "Create the strongest truthful one-page ATS resume for this job. "
    "Prioritize directly relevant evidence, keep the writing natural and concise, "
    "retain enough supported evidence to create a visually complete page, and "
    "omit unsupported requirements rather than inventing them."
)


def workers_enabled() -> bool:
    return str(os.getenv(WORKERS_ENV) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def _enabled() -> None:
    if not workers_enabled():
        raise RuntimeError(
            "Prepare Worker Adapters V1A are disabled for this environment."
        )


def _sha(value: Any, label: str) -> str:
    result = str(value or "").strip().casefold()
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise ValueError(f"{label} must be a lowercase SHA-256.")
    return result


def _operation(operation_id: str) -> dict[str, Any]:
    operation = orchestrator.prepare_operation(str(operation_id))
    if operation.get("submission_authority") is not False:
        raise RuntimeError("Prepare operation unexpectedly carries submission authority.")
    return operation


def _pending_task(
    operation: Mapping[str, Any],
    task_kind: str,
) -> dict[str, Any] | None:
    for raw in operation.get("tasks") or []:
        if (
            isinstance(raw, Mapping)
            and str(raw.get("task_kind")) == task_kind
            and str(raw.get("status")) == "PENDING"
        ):
            return dict(raw)
    return None


def _claim_one(
    *,
    operation_id: str,
    worker_id: str,
    task_kind: str,
) -> dict[str, Any] | None:
    claimed = orchestrator.claim_ready_tasks(
        operation_id=str(operation_id),
        worker_id=str(worker_id),
        limit=1,
        task_kinds=[task_kind],
    )
    if not claimed:
        return None
    if len(claimed) != 1 or str(claimed[0].get("task_kind")) != task_kind:
        raise RuntimeError("Prepare worker claimed an unexpected task.")
    return dict(claimed[0])


def _native_output(
    *,
    operation: Mapping[str, Any],
    record: Mapping[str, Any],
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    if record.get("stage_b_bound") is not True:
        raise RuntimeError("Native Resume V5 returned an unbound resume version.")

    if int(bundle.get("job_id") or 0) != int(operation["job_id"]):
        raise RuntimeError("Native Resume artifact job binding changed.")

    candidate = dict(bundle.get("candidate_truth_binding") or {})
    if str(candidate.get("profile_digest") or "") != str(
        operation["candidate_profile_digest"]
    ):
        raise RuntimeError("Native Resume Candidate Truth binding changed.")

    job_binding = dict(bundle.get("job_snapshot_binding") or {})
    if str(job_binding.get("job_snapshot_sha256") or "") != str(
        operation["job_snapshot_sha256"]
    ):
        raise RuntimeError("Native Resume job snapshot binding changed.")

    page = dict(bundle.get("page_verification") or {})
    if str(page.get("status") or "") != "PASS":
        raise RuntimeError("Native Resume PDF did not pass page-fit verification.")

    docx = dict(bundle.get("docx") or {})
    pdf = dict(bundle.get("pdf") or {})
    if str(docx.get("artifact_kind") or "") != "DOCX":
        raise RuntimeError("Native Resume DOCX artifact is missing.")
    if str(pdf.get("artifact_kind") or "") != "PDF":
        raise RuntimeError("Native Resume PDF artifact is missing.")
    if int(pdf.get("page_count") or 0) != 1:
        raise RuntimeError("Native Resume PDF must be exactly one page.")

    docx_sha = _sha(docx.get("sha256"), "DOCX artifact SHA-256")
    pdf_sha = _sha(pdf.get("sha256"), "PDF artifact SHA-256")

    return {
        "version": WORKER_VERSION,
        "engine": "NATIVE_V5",
        "resume_version_id": str(record["version_id"]),
        "resume_version_number": int(record.get("version_number") or 0),
        "stage_b_bound": True,
        "candidate_profile_digest": str(operation["candidate_profile_digest"]),
        "job_snapshot_sha256": str(operation["job_snapshot_sha256"]),
        "docx": {
            "artifact_id": str(docx["artifact_id"]),
            "artifact_reference": str(docx["object_reference"]),
            "artifact_sha256": docx_sha,
            "filename": str(docx["filename"]),
            "mime_type": str(docx["mime_type"]),
            "byte_count": int(docx["byte_count"]),
        },
        "pdf": {
            "artifact_id": str(pdf["artifact_id"]),
            "artifact_reference": str(pdf["object_reference"]),
            "artifact_sha256": pdf_sha,
            "filename": str(pdf["filename"]),
            "mime_type": str(pdf["mime_type"]),
            "byte_count": int(pdf["byte_count"]),
            "page_count": 1,
        },
        "page_verification": page,
        "submission_authority": False,
        "automatic_submission_executed": False,
    }


def _reusable_generated_resume(
    *,
    operation: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Reuse a validated post-generation resume after a retryable artifact failure.

    The first attempt may already have paid for and persisted a fully validated,
    truth/job/Stage-B-bound resume before local PDF/DOCX materialization fails.
    A retry must not spend another model call for identical durable inputs.
    """
    if int(task.get("attempts") or 0) < 2:
        return None
    if str(task.get("error_code") or "") != "NATIVE_RESUME_PREPARE_FAILED":
        return None

    operation_created = str(operation.get("created_at") or "")
    for summary in native_resume_service_v5.list_versions(
        job_id=int(operation["job_id"]),
        limit=12,
    ):
        if operation_created and str(summary.get("created_at") or "") < operation_created:
            continue
        try:
            record = native_resume_service_v5.get_version(str(summary["version_id"]))
        except (LookupError, RuntimeError, ValueError):
            continue
        if record.get("stage_b_bound") is not True:
            continue
        truth = dict(record.get("candidate_truth_binding") or {})
        job = dict(record.get("job_snapshot_binding") or {})
        if str(truth.get("profile_digest") or "") != str(
            operation.get("candidate_profile_digest") or ""
        ):
            continue
        if str(job.get("job_snapshot_sha256") or "") != str(
            operation.get("job_snapshot_sha256") or ""
        ):
            continue
        return record
    return None


def _pinned_existing_resume(
    *,
    operation: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Resolve one explicitly pinned immutable resume for this exact operation.

    This is an opt-in terminal/deployment contract. It never selects a resume
    by filename or recency. The pinned version must still be Stage-B bound to
    the operation's Candidate Truth digest and immutable job snapshot.
    """
    version_id = str(os.getenv(PINNED_RESUME_ENV) or "").strip()
    if not version_id:
        return None

    record = native_resume_service_v5.get_version(version_id)
    if record.get("stage_b_bound") is not True:
        raise RuntimeError("Pinned existing resume is not Stage-B bound.")

    truth = dict(record.get("candidate_truth_binding") or {})
    if str(truth.get("profile_digest") or "") != str(
        operation.get("candidate_profile_digest") or ""
    ):
        raise RuntimeError(
            "Pinned existing resume Candidate Truth binding changed."
        )

    job = dict(record.get("job_snapshot_binding") or {})
    if str(job.get("job_snapshot_sha256") or "") != str(
        operation.get("job_snapshot_sha256") or ""
    ):
        raise RuntimeError("Pinned existing resume job snapshot binding changed.")

    return record


def run_native_resume_once(
    *,
    operation_id: str,
    worker_id: str,
    instruction: str = _DEFAULT_NATIVE_INSTRUCTION,
    rewrite_mode: str = "medium",
) -> dict[str, Any]:
    """Claim and execute one Native RESUME_PREPARE task.

    N8N operations are deliberately left unclaimed. This prevents this source
    tranche from accidentally reaching the existing production n8n dispatcher.
    """
    _enabled()
    operation = _operation(operation_id)

    if str(operation.get("engine") or "").upper() != "NATIVE":
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "engine_not_native",
            "engine": str(operation.get("engine") or ""),
            "submission_authority": False,
        }

    if _pending_task(operation, orchestrator.TASK_RESUME) is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "resume_task_not_pending",
            "operation_status": str(operation["status"]),
            "submission_authority": False,
        }

    task = _claim_one(
        operation_id=str(operation_id),
        worker_id=str(worker_id),
        task_kind=orchestrator.TASK_RESUME,
    )
    if task is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "resume_task_not_ready",
            "submission_authority": False,
        }

    try:
        record = _pinned_existing_resume(operation=operation)
        pinned_existing_version = record is not None
        if record is None:
            record = _reusable_generated_resume(operation=operation, task=task)
        reused_generated_version = record is not None
        if record is None:
            record = native_resume_service_v5.generate_resume(
                job_id=int(operation["job_id"]),
                instruction=str(instruction or "").strip(),
                rewrite_mode=str(rewrite_mode or "medium"),
            )
        bundle = native_resume_artifact_v5.materialize(str(record["version_id"]))
        output = _native_output(
            operation=operation,
            record=record,
            bundle=bundle,
        )
        result = orchestrator.complete_task(
            task_id=str(task["task_id"]),
            worker_id=str(worker_id),
            output=output,
        )
        return {
            **result,
            "claimed": True,
            "output": output,
            "reused_generated_version": reused_generated_version,
            "pinned_existing_version": pinned_existing_version,
            "submission_authority": False,
        }
    except Exception:
        # A retry may safely create a new immutable resume version. The
        # orchestrator caps attempts. Never reinterpret a failed preparation as
        # submission success.
        orchestrator.fail_task(
            task_id=str(task["task_id"]),
            worker_id=str(worker_id),
            error_code="NATIVE_RESUME_PREPARE_FAILED",
            error_message="Native resume preparation failed closed.",
            retryable=True,
        )
        raise


def _public_ranked_contacts(
    values: Any,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in values or []:
        if not isinstance(raw, Mapping):
            continue
        # Keep recruiter output useful but deliberately omit raw contact email,
        # phone, guessed address patterns, and other unneeded personal values.
        item = {
            key: raw.get(key)
            for key in (
                "contact_id",
                "display_name",
                "title",
                "company_name",
                "relationship_score",
                "score_confidence",
                "recommended_action",
                "source",
                "unknowns",
            )
            if raw.get(key) is not None
        }
        result.append(item)
    return result


def _recruiter_output(
    *,
    operation: Mapping[str, Any],
    strategy: Mapping[str, Any],
) -> dict[str, Any]:
    if int(strategy.get("job_id") or 0) != int(operation["job_id"]):
        raise RuntimeError("Relationship strategy belongs to another job.")
    if str(strategy.get("job_snapshot_sha256") or "") != str(
        operation["job_snapshot_sha256"]
    ):
        raise RuntimeError("Relationship strategy job snapshot is stale.")
    if strategy.get("advisory_only") is not True:
        raise RuntimeError("Relationship strategy unexpectedly carries action authority.")
    if strategy.get("automatic_outreach_executed") is not False:
        raise RuntimeError("Relationship worker must never execute outreach.")

    contacts = _public_ranked_contacts(strategy.get("ranked_contacts"))
    strategy_summary = dict(strategy.get("strategy") or {})
    return {
        "version": WORKER_VERSION,
        "relationship_strategy_id": strategy.get("strategy_id"),
        "relationship_result_sha256": strategy.get("result_sha256"),
        "job_snapshot_sha256": str(operation["job_snapshot_sha256"]),
        "contacts_found": len(contacts),
        "ranked_contacts": contacts,
        "strategy": {
            key: strategy_summary.get(key)
            for key in (
                "base_pursuit_state",
                "combined_pursuit_state",
                "networking_action",
                "reason",
            )
            if strategy_summary.get(key) is not None
        },
        "advisory_only": True,
        "external_people_search_executed": False,
        "automatic_outreach_executed": False,
        "submission_authority": False,
    }


def run_recruiter_evidence_once(
    *,
    operation_id: str,
    worker_id: str,
) -> dict[str, Any]:
    """Claim recruiter task and rank existing Hunter relationship evidence.

    This is intentionally not an external people-search connector. Zero stored
    contacts is a valid optional result and never blocks READY_FOR_REVIEW.
    """
    _enabled()
    operation = _operation(operation_id)

    if _pending_task(operation, orchestrator.TASK_RECRUITER_DISCOVERY) is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "recruiter_task_not_pending",
            "operation_status": str(operation["status"]),
            "submission_authority": False,
        }

    task = _claim_one(
        operation_id=str(operation_id),
        worker_id=str(worker_id),
        task_kind=orchestrator.TASK_RECRUITER_DISCOVERY,
    )
    if task is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "recruiter_task_not_ready",
            "submission_authority": False,
        }

    try:
        try:
            strategy = relationship_intelligence_v3.strategy_for_job(
                int(operation["job_id"]),
                persist=True,
            )
        except RuntimeError as error:
            if "disabled" not in str(error).casefold():
                raise
            output = {
                "version": WORKER_VERSION,
                "available": False,
                "reason": "relationship_intelligence_disabled",
                "contacts_found": 0,
                "ranked_contacts": [],
                "advisory_only": True,
                "external_people_search_executed": False,
                "automatic_outreach_executed": False,
                "submission_authority": False,
            }
        else:
            output = _recruiter_output(
                operation=operation,
                strategy=strategy,
            )
            output["available"] = True

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
            error_code="RECRUITER_EVIDENCE_PREPARE_FAILED",
            error_message="Recruiter relationship evidence preparation failed closed.",
            retryable=True,
        )
        raise
