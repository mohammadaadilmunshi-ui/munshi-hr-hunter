"""Answer Resolution + Cover Letter Decision workers for Prepare Orchestrator V1.

These workers remain preparation-only.

ANSWER_RESOLUTION:
- captures the current, planner-safe Answer Brain inventory;
- binds it to exact Candidate Truth + exact job state;
- never copies canonical/free-form answer bodies into the orchestrator ledger;
- never pretends the employer application form has already been discovered.

COVER_LETTER_DECISION:
- reads only the exact Hunter-owned job snapshot;
- classifies explicit cover-letter language deterministically;
- does not generate or upload a cover letter;
- leaves final employer-form confirmation to ATS_FORM_PREPARATION.

No HTTP, browser, n8n, provider, recruiter search, outreach, email, credential,
or submission authority exists in this module.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from app import answer_brain_v2
from app import application_answer_autofill_v1
from app import prepare_orchestrator_v1 as orchestrator
from app.phase67_common import safe_owned_job_snapshot
from app.phase8_application_truth import current_application_truth_projection


WORKERS_ENV = "MUNSHI_PREPARE_WORKER_ADAPTERS_V1B_ENABLED"
WORKER_VERSION = "munshi-prepare-worker-adapters-v1b"
SUBMISSION_AUTHORITY = False

COVER_REQUIRED = "REQUIRED"
COVER_OPTIONAL = "OPTIONAL"
COVER_NOT_REQUIRED = "NOT_REQUIRED"
COVER_UNDETERMINED = "UNDETERMINED"

_COVER_NOT_REQUIRED_PATTERNS = (
    ("cover_letter_not_required", re.compile(r"\bcover\s+letter(?:s)?\s+(?:is|are)?\s*not\s+required\b", re.I)),
    ("no_cover_letter_required", re.compile(r"\bno\s+cover\s+letter(?:s)?\s+(?:is|are)?\s*required\b", re.I)),
    ("do_not_submit_cover_letter", re.compile(r"\bdo\s+not\s+(?:submit|upload|send)\s+(?:a\s+)?cover\s+letter\b", re.I)),
    ("cover_letters_not_accepted", re.compile(r"\bcover\s+letter(?:s)?\s+(?:is|are)\s+not\s+accepted\b", re.I)),
)

_COVER_OPTIONAL_PATTERNS = (
    ("cover_letter_optional", re.compile(r"\b(?:an?\s+)?cover\s+letter\s+(?:is\s+)?optional\b", re.I)),
    ("optional_cover_letter", re.compile(r"\boptional\s+cover\s+letter\b", re.I)),
    ("may_submit_cover_letter", re.compile(r"\b(?:you\s+)?may\s+(?:also\s+)?(?:submit|upload|include|provide)\s+(?:a\s+)?cover\s+letter\b", re.I)),
    ("cover_letter_encouraged", re.compile(r"\bcover\s+letter(?:s)?\s+(?:is|are)?\s*(?:encouraged|welcome|welcomed)\b", re.I)),
)

_COVER_REQUIRED_PATTERNS = (
    ("cover_letter_required", re.compile(r"\b(?:a\s+)?cover\s+letter\s+(?:is\s+)?required\b", re.I)),
    ("must_submit_cover_letter", re.compile(r"\bmust\s+(?:submit|upload|include|provide)\s+(?:a\s+)?cover\s+letter\b", re.I)),
    ("please_submit_cover_letter", re.compile(r"\bplease\s+(?:submit|upload|include|provide)\s+(?:a\s+)?cover\s+letter\b", re.I)),
    ("submit_resume_and_cover_letter", re.compile(r"\bsubmit\s+(?:your\s+|a\s+)?resume\s+(?:and|with)\s+(?:a\s+|your\s+)?cover\s+letter\b", re.I)),
    ("include_cover_letter_application", re.compile(r"\binclude\s+(?:a\s+|your\s+)?cover\s+letter\s+(?:with|in)\s+(?:your\s+|the\s+)?application\b", re.I)),
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
            "Prepare Worker Adapters V1B are disabled for this environment."
        )


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


def _current_truth(operation: Mapping[str, Any]) -> dict[str, Any]:
    projection = current_application_truth_projection(job_id=int(operation["job_id"]))
    if str(projection.get("projection_digest") or "") != str(
        operation["application_truth_projection_digest"]
    ):
        raise RuntimeError("Application Truth projection changed after Prepare started.")

    candidate = dict(projection.get("candidate_profile_binding") or {})
    if str(candidate.get("profile_digest") or "") != str(
        operation["candidate_profile_digest"]
    ):
        raise RuntimeError("Candidate Truth changed after Prepare started.")

    job_context = dict(projection.get("job_context") or {})
    if str(job_context.get("job_snapshot_sha256") or "") != str(
        operation["job_snapshot_sha256"]
    ):
        raise RuntimeError("Application Truth job binding changed after Prepare started.")

    if projection.get("submission_authority") is not False:
        raise RuntimeError("Application Truth unexpectedly carries submission authority.")
    return projection


def _safe_answer_inventory(planning: Mapping[str, Any]) -> dict[str, Any]:
    """Strip free-form values before persisting Answer Brain state in orchestration."""
    answers: list[dict[str, Any]] = []
    for raw in planning.get("answers") or []:
        if not isinstance(raw, Mapping):
            continue
        answers.append(
            {
                key: raw.get(key)
                for key in (
                    "answer_id",
                    "question_key",
                    "question_family",
                    "source",
                    "confidence",
                    "user_confirmed",
                    "autofill_allowed",
                    "planning_use",
                    "truth_binding_current",
                )
                if raw.get(key) is not None
            }
        )

    excluded: list[dict[str, Any]] = []
    for raw in planning.get("excluded_answers") or []:
        if not isinstance(raw, Mapping):
            continue
        excluded.append(
            {
                key: raw.get(key)
                for key in (
                    "answer_id",
                    "question_key",
                    "question_family",
                    "reason",
                )
                if raw.get(key) is not None
            }
        )

    return {
        "answers": answers,
        "excluded_answers": excluded,
        "autofill_ready_count": sum(
            1 for row in answers if row.get("planning_use") == "autofill_ready"
        ),
        "context_only_count": sum(
            1 for row in answers if row.get("planning_use") == "context_only"
        ),
        "excluded_count": len(excluded),
    }


def run_answer_resolution_once(
    *,
    operation_id: str,
    worker_id: str,
) -> dict[str, Any]:
    """Snapshot planner-safe answer readiness without claiming form completeness."""
    _enabled()
    operation = _operation(operation_id)

    if _pending_task(operation, orchestrator.TASK_ANSWER_RESOLUTION) is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "answer_resolution_task_not_pending",
            "operation_status": str(operation["status"]),
            "submission_authority": False,
        }

    task = _claim_one(
        operation_id=str(operation_id),
        worker_id=str(worker_id),
        task_kind=orchestrator.TASK_ANSWER_RESOLUTION,
    )
    if task is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "answer_resolution_task_not_ready",
            "submission_authority": False,
        }

    try:
        projection = _current_truth(operation)
        # Populate reusable NORMAL answers from confirmed evidence before reading
        # readiness. Missing facts remain unresolved; protected values keep their
        # existing secure resolver path.
        refreshed = application_answer_autofill_v1.prepare_from_candidate_truth()
        if str(refreshed.get("profile_digest") or "") != str(
            operation["candidate_profile_digest"]
        ):
            raise RuntimeError("Candidate Truth changed during answer preparation.")
        # Detect concurrent edits before accepting the refreshed inventory.
        projection = _current_truth(operation)
        planning = answer_brain_v2.planning_input()
        binding = dict(planning.get("candidate_truth_binding") or {})
        if str(binding.get("profile_digest") or "") != str(
            operation["candidate_profile_digest"]
        ):
            raise RuntimeError("Answer Brain Candidate Truth binding is stale.")

        inventory = _safe_answer_inventory(planning)
        output = {
            "version": WORKER_VERSION,
            "candidate_profile_digest": str(operation["candidate_profile_digest"]),
            "application_truth_projection_digest": str(
                operation["application_truth_projection_digest"]
            ),
            "job_snapshot_sha256": str(operation["job_snapshot_sha256"]),
            "answer_inventory": inventory,
            "baseline_unresolved_fact_keys": sorted(
                str(value)
                for value in (projection.get("unresolved_fact_keys") or [])
                if str(value).strip()
            ),
            # We have not discovered the employer's actual application form yet.
            "application_questions_complete": False,
            "requires_ats_form_confirmation": True,
            "canonical_answer_bodies_persisted": False,
            "protected_values_persisted": False,
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
            error_code="ANSWER_RESOLUTION_PREPARE_FAILED",
            error_message="Answer resolution preparation failed closed.",
            retryable=True,
        )
        raise


def _cover_letter_text(job: Mapping[str, Any]) -> str:
    values = [
        job.get("description_raw"),
        job.get("responsibilities"),
        job.get("qualifications"),
        job.get("preferred_qualifications"),
    ]
    return "\n".join(str(value) for value in values if str(value or "").strip())


def _cover_letter_decision(text: str) -> dict[str, Any]:
    # Negative evidence has precedence because "not required" contains "required".
    for decision, patterns in (
        (COVER_NOT_REQUIRED, _COVER_NOT_REQUIRED_PATTERNS),
        (COVER_OPTIONAL, _COVER_OPTIONAL_PATTERNS),
        (COVER_REQUIRED, _COVER_REQUIRED_PATTERNS),
    ):
        for rule_id, pattern in patterns:
            if pattern.search(text):
                return {
                    "decision": decision,
                    "evidence_rule": rule_id,
                    "explicit_evidence_found": True,
                }
    return {
        "decision": COVER_UNDETERMINED,
        "evidence_rule": None,
        "explicit_evidence_found": False,
    }


def run_cover_letter_decision_once(
    *,
    operation_id: str,
    worker_id: str,
) -> dict[str, Any]:
    """Classify explicit JD cover-letter evidence without creating an artifact."""
    _enabled()
    operation = _operation(operation_id)

    if _pending_task(operation, orchestrator.TASK_COVER_LETTER_DECISION) is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "cover_letter_decision_task_not_pending",
            "operation_status": str(operation["status"]),
            "submission_authority": False,
        }

    task = _claim_one(
        operation_id=str(operation_id),
        worker_id=str(worker_id),
        task_kind=orchestrator.TASK_COVER_LETTER_DECISION,
    )
    if task is None:
        return {
            "operation_id": str(operation_id),
            "claimed": False,
            "reason": "cover_letter_decision_task_not_ready",
            "submission_authority": False,
        }

    try:
        snapshot = safe_owned_job_snapshot(int(operation["job_id"]))
        if str(snapshot.get("job_snapshot_sha256") or "") != str(
            operation["job_snapshot_sha256"]
        ):
            raise RuntimeError("Job snapshot changed after Prepare started.")

        job = dict(snapshot.get("job") or {})
        decision = _cover_letter_decision(_cover_letter_text(job))
        output = {
            "version": WORKER_VERSION,
            **decision,
            "job_snapshot_sha256": str(operation["job_snapshot_sha256"]),
            "artifact_generated": False,
            "artifact_id": None,
            # Even an explicit JD statement must be reconciled with the actual
            # application form once ATS preparation has discovered it.
            "requires_ats_form_confirmation": True,
            "live_form_overrides_jd_decision": True,
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
            error_code="COVER_LETTER_DECISION_FAILED",
            error_message="Cover-letter decision failed closed.",
            retryable=True,
        )
        raise
