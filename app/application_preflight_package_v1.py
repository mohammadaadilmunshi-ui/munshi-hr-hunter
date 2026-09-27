"""Phase 9 immutable application preflight package.

This is the final Hunter-side preparation object before a future MUNSHI Apply
handoff.  It binds the exact owned job, current Candidate Truth projection,
strengthened resume, safe Answer Brain inventory, Phase 6 opportunity evaluation,
optional Phase 7 relationship strategy, and Phase 4-7 integrity result into one
content-addressed snapshot.

The package is inert: it has no browser, ATS, HTTP, credential, n8n, Gmail,
outreach, or submission authority.  ``READY_TO_APPLY`` means the package has
sufficient preparation evidence; it never means submitted.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from app import answer_brain_v2
from app import native_resume_service_v4 as resume_v4
from app import opportunity_intelligence_v3 as opportunity_v3
from app import relationship_intelligence_v3 as relationship_v3
from app.database import get_connection
from app.phase47_integrity import application_preparation_readiness
from app.phase67_common import safe_owned_job_snapshot, sha256_json
from app.phase8_application_truth import (
    current_application_truth_projection,
    projection_digest_payload,
)
from app.tenant_foundation import (
    current_owner,
    current_owner_readonly,
    ensure_schema as ensure_tenant_schema,
)

PREFLIGHT_PACKAGE_VERSION = "munshi-application-preflight-package-v1"
PREFLIGHT_STATUSES = frozenset({"PREPARED", "NEEDS_INPUT", "READY_TO_APPLY"})

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS application_preflight_packages (
        package_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        application_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        package_version TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('PREPARED','NEEDS_INPUT','READY_TO_APPLY')),
        application_truth_projection_digest TEXT NOT NULL,
        job_snapshot_sha256 TEXT NOT NULL,
        resume_version_id TEXT NOT NULL,
        resume_sha256 TEXT NOT NULL,
        opportunity_evaluation_id TEXT NOT NULL,
        opportunity_result_sha256 TEXT NOT NULL,
        relationship_strategy_id TEXT,
        relationship_result_sha256 TEXT,
        snapshot_json TEXT NOT NULL,
        package_digest TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        UNIQUE(tenant_id,user_id,idempotency_key),
        UNIQUE(tenant_id,user_id,package_digest)
    );""",
    """CREATE INDEX IF NOT EXISTS idx_application_preflight_packages_owner_job
       ON application_preflight_packages(tenant_id,user_id,job_id,created_at DESC);""",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, label: str, maximum: int = 240) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return result


def _sha(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise ValueError(f"{label} must be a lowercase SHA-256.")
    return result


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _answer_inventory(planning: Mapping[str, Any]) -> dict[str, Any]:
    answers: list[dict[str, Any]] = []
    for row in planning.get("answers") or []:
        if not isinstance(row, Mapping):
            continue
        # Deliberately omit canonical_answer and every free-form answer body.
        answers.append(
            {
                key: row.get(key)
                for key in (
                    "answer_id",
                    "question_key",
                    "question_family",
                    "planning_use",
                    "source",
                    "confidence",
                )
                if row.get(key) is not None
            }
        )
    excluded: list[dict[str, Any]] = []
    for row in planning.get("excluded_answers") or []:
        if not isinstance(row, Mapping):
            continue
        excluded.append(
            {
                key: row.get(key)
                for key in ("answer_id", "question_key", "question_family", "reason")
                if row.get(key) is not None
            }
        )
    return {
        "candidate_truth_binding": planning.get("candidate_truth_binding"),
        "answers": answers,
        "excluded_answers": excluded,
    }


def _resume_reference(version_id: str) -> dict[str, Any]:
    resume = resume_v4.get_version(version_id)
    digest = _sha(resume.get("html_sha256"), "Resume rendered artifact digest")
    return {
        "version_id": str(resume["version_id"]),
        "job_id": int(resume["job_id"]),
        "version_number": int(resume["version_number"]),
        "rendered_resume_sha256": digest,
        "candidate_truth_binding": resume.get("candidate_truth_binding"),
        "job_snapshot_binding": resume.get("job_snapshot_binding"),
        "status": resume.get("status"),
    }


def _opportunity_reference(evaluation_id: str) -> dict[str, Any]:
    evaluation = opportunity_v3.get_evaluation(evaluation_id)
    freshness = opportunity_v3.evaluation_freshness(evaluation_id)
    return {
        "evaluation_id": str(evaluation_id),
        "job_id": int(evaluation["job_id"]),
        "result_sha256": _sha(evaluation.get("result_sha256"), "Opportunity result digest"),
        "job_snapshot_sha256": _sha(
            evaluation.get("job_snapshot_sha256"), "Opportunity job snapshot digest"
        ),
        "candidate_truth_binding": evaluation.get("candidate_truth_binding"),
        "status": evaluation.get("status"),
        "fresh": freshness.get("fresh") is True,
    }


def _relationship_reference(strategy_id: str | None) -> dict[str, Any] | None:
    if strategy_id is None:
        return None
    strategy = relationship_v3.get_strategy(strategy_id)
    freshness = relationship_v3.strategy_freshness(strategy_id)
    return {
        "strategy_id": str(strategy_id),
        "job_id": int(strategy["job_id"]),
        "result_sha256": _sha(strategy.get("result_sha256"), "Relationship result digest"),
        "job_snapshot_sha256": _sha(
            strategy.get("job_snapshot_sha256"), "Relationship job snapshot digest"
        ),
        "opportunity_context": strategy.get("opportunity_context"),
        "fresh": freshness.get("fresh") is True,
    }


def _normalized_unresolved(values: Sequence[str] | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("Unresolved application question keys must be a list.")
    result = sorted({str(value).strip() for value in values if str(value).strip()})
    if any(len(value) > 256 for value in result):
        raise ValueError("An unresolved application question key is too long.")
    return result


def _capture(
    *,
    job_id: int,
    application_id: str,
    resume_version_id: str,
    opportunity_evaluation_id: str,
    relationship_strategy_id: str | None,
    application_questions_complete: bool,
    unresolved_question_keys: Sequence[str] | None,
    legal_attestation_execution: bool = False,
) -> dict[str, Any]:
    job_snapshot = safe_owned_job_snapshot(job_id)
    job = dict(job_snapshot["job"])
    projection = current_application_truth_projection(job_id=job_id)
    resume = _resume_reference(resume_version_id)
    opportunity = _opportunity_reference(opportunity_evaluation_id)
    relationship = _relationship_reference(relationship_strategy_id)
    readiness = application_preparation_readiness(
        job_id=job_id,
        resume_version_id=resume_version_id,
        opportunity_evaluation_id=opportunity_evaluation_id,
        relationship_strategy_id=relationship_strategy_id,
    )
    planning = _answer_inventory(answer_brain_v2.planning_input())
    unresolved = _normalized_unresolved(unresolved_question_keys)

    if int(resume["job_id"]) != int(job_id):
        raise ValueError("Selected resume is attached to another job.")
    if int(opportunity["job_id"]) != int(job_id):
        raise ValueError("Selected opportunity evaluation is attached to another job.")
    if relationship is not None and int(relationship["job_id"]) != int(job_id):
        raise ValueError("Selected relationship strategy is attached to another job.")

    job_digest = _sha(job_snapshot["job_snapshot_sha256"], "Current job snapshot digest")
    if str(projection.get("job_context", {}).get("job_snapshot_sha256")) != job_digest:
        raise RuntimeError("Phase 8 application truth is not bound to the current job snapshot.")
    resume_job = resume.get("job_snapshot_binding") or {}
    if str(resume_job.get("job_snapshot_sha256") or "") != job_digest:
        raise RuntimeError("Selected resume is not bound to the current job snapshot.")
    if opportunity["job_snapshot_sha256"] != job_digest:
        raise RuntimeError("Selected opportunity evaluation is not bound to the current job snapshot.")
    if relationship is not None and relationship["job_snapshot_sha256"] != job_digest:
        raise RuntimeError("Selected relationship strategy is not bound to the current job snapshot.")

    binding = projection["candidate_profile_binding"]
    for label, candidate in (
        ("resume", resume.get("candidate_truth_binding")),
        ("Answer Brain", planning.get("candidate_truth_binding")),
        ("opportunity", opportunity.get("candidate_truth_binding")),
    ):
        if not isinstance(candidate, Mapping) or any(
            str(candidate.get(field)) != str(binding.get(field))
            for field in ("source_extraction_id", "profile_revision", "profile_digest")
        ):
            raise RuntimeError(f"{label} Candidate Truth binding does not match Phase 8.")

    blockers = list(readiness.get("blockers") or [])
    if blockers or unresolved:
        status = "NEEDS_INPUT"
    elif application_questions_complete:
        status = "READY_TO_APPLY"
    else:
        status = "PREPARED"

    return {
        "version": PREFLIGHT_PACKAGE_VERSION,
        "status": status,
        "application_id": application_id,
        "job": {
            key: job.get(key)
            for key in ("id", "company_name", "title", "job_url", "apply_url", "location_raw")
        },
        "job_snapshot_sha256": job_digest,
        "application_truth": projection,
        "resume": resume,
        "answer_inventory": planning,
        "application_question_state": {
            "complete": bool(application_questions_complete),
            "unresolved_question_keys": unresolved,
        },
        "opportunity": opportunity,
        "relationship": relationship,
        "readiness": readiness,
        **(
            {
                "execution_capabilities": {
                    "legal_attestation_execution": True,
                }
            }
            if legal_attestation_execution
            else {}
        ),
        "submission_authority": False,
        "automatic_actions_executed": False,
    }


def package_digest_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable preflight package inputs for optimistic revalidation.

    Phase 8 truth projections include generated_at as observational metadata.
    That timestamp is deliberately excluded from the Phase 8 projection digest,
    so preflight must not re-introduce it into its own stability comparison.
    Real Candidate Truth/job/resume/answer/opportunity/relationship drift remains
    fully represented and still fails closed.
    """
    payload = dict(snapshot)
    application_truth = payload.get("application_truth")
    if isinstance(application_truth, Mapping):
        payload["application_truth"] = projection_digest_payload(application_truth)
    return payload


def prepare_preflight_package(
    *,
    job_id: int,
    application_id: str,
    idempotency_key: str,
    resume_version_id: str,
    opportunity_evaluation_id: str,
    relationship_strategy_id: str | None = None,
    application_questions_complete: bool = False,
    unresolved_question_keys: Sequence[str] | None = None,
    legal_attestation_execution: bool = True,
) -> dict[str, Any]:
    """Persist one immutable, revalidated application preflight snapshot."""
    try:
        resolved_job_id = int(job_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Job id must be an integer.") from error
    if resolved_job_id <= 0:
        raise ValueError("Job id must be positive.")
    application_id = _text(application_id, "Application id", 240)
    idempotency_key = _text(idempotency_key, "Idempotency key", 240)
    resume_version_id = _text(resume_version_id, "Resume version id", 160)
    opportunity_evaluation_id = _text(
        opportunity_evaluation_id, "Opportunity evaluation id", 160
    )
    if relationship_strategy_id is not None:
        relationship_strategy_id = _text(
            relationship_strategy_id, "Relationship strategy id", 160
        )

    # A worker retry must resume the immutable package it already committed.
    # Re-evaluating opportunity intelligence first can mint a new evaluation id
    # for equivalent evidence and then make the stable operation key appear to
    # conflict with itself.  Reuse only when the stored authority is still fresh
    # and its durable job/application/resume bindings are exact.
    replay_connection = get_connection()
    try:
        owner = current_owner_readonly(replay_connection)
        table_ready = replay_connection.execute(
            """SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='application_preflight_packages'"""
        ).fetchone()
        prior_row = (
            replay_connection.execute(
                """SELECT package_id,job_id,application_id,resume_version_id
                     FROM application_preflight_packages
                    WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
                (owner.tenant_id, owner.user_id, idempotency_key),
            ).fetchone()
            if table_ready is not None
            else None
        )
    finally:
        replay_connection.close()
    if prior_row is not None:
        if (
            int(prior_row["job_id"]) != resolved_job_id
            or str(prior_row["application_id"]) != application_id
            or str(prior_row["resume_version_id"]) != resume_version_id
        ):
            raise ValueError(
                "Idempotency key belongs to a different preflight package."
            )
        prior_id = str(prior_row["package_id"])
        freshness = preflight_package_freshness(prior_id)
        if freshness.get("fresh") is not True:
            raise RuntimeError(
                "Existing application preflight package became stale."
            )
        return get_preflight_package(prior_id)

    first = _capture(
        job_id=resolved_job_id,
        application_id=application_id,
        resume_version_id=resume_version_id,
        opportunity_evaluation_id=opportunity_evaluation_id,
        relationship_strategy_id=relationship_strategy_id,
        application_questions_complete=bool(application_questions_complete),
        unresolved_question_keys=unresolved_question_keys,
        legal_attestation_execution=bool(legal_attestation_execution),
    )
    first_digest = sha256_json(package_digest_payload(first))

    # Optimistic revalidation immediately before persistence. Any source change
    # across job/truth/resume/answers/opportunity/relationship/readiness fails closed.
    second = _capture(
        job_id=resolved_job_id,
        application_id=application_id,
        resume_version_id=resume_version_id,
        opportunity_evaluation_id=opportunity_evaluation_id,
        relationship_strategy_id=relationship_strategy_id,
        application_questions_complete=bool(application_questions_complete),
        unresolved_question_keys=unresolved_question_keys,
        legal_attestation_execution=bool(legal_attestation_execution),
    )
    second_digest = sha256_json(package_digest_payload(second))
    if first_digest != second_digest:
        raise RuntimeError("Application preparation inputs changed during package creation.")

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        prior = connection.execute(
            """SELECT * FROM application_preflight_packages
                 WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, idempotency_key),
        ).fetchone()
        if prior is not None:
            if str(prior["package_digest"]) != first_digest:
                raise ValueError("Idempotency key belongs to a different preflight package.")
            return get_preflight_package(str(prior["package_id"]), connection=connection)

        package_id = f"preflight-package-{uuid4()}"
        relationship = second.get("relationship") or {}
        connection.execute(
            """INSERT INTO application_preflight_packages(
                   package_id,tenant_id,user_id,job_id,application_id,idempotency_key,
                   package_version,status,application_truth_projection_digest,
                   job_snapshot_sha256,resume_version_id,resume_sha256,
                   opportunity_evaluation_id,opportunity_result_sha256,
                   relationship_strategy_id,relationship_result_sha256,
                   snapshot_json,package_digest
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                package_id,
                owner.tenant_id,
                owner.user_id,
                resolved_job_id,
                application_id,
                idempotency_key,
                PREFLIGHT_PACKAGE_VERSION,
                second["status"],
                second["application_truth"]["projection_digest"],
                second["job_snapshot_sha256"],
                resume_version_id,
                second["resume"]["rendered_resume_sha256"],
                opportunity_evaluation_id,
                second["opportunity"]["result_sha256"],
                relationship_strategy_id,
                relationship.get("result_sha256"),
                json.dumps(second, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                first_digest,
            ),
        )
        connection.commit()
        return get_preflight_package(package_id, connection=connection)
    finally:
        connection.close()


def get_preflight_package(
    package_id: str, *, connection: sqlite3.Connection | None = None
) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        owner = current_owner_readonly(connection)
        row = connection.execute(
            """SELECT * FROM application_preflight_packages
                 WHERE package_id=? AND tenant_id=? AND user_id=?""",
            (str(package_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Preflight package is not owned by the current tenant user.")
        result = dict(row)
        result["snapshot"] = json.loads(result.pop("snapshot_json"))
        result["submission_authority"] = False
        result["automatic_actions_executed"] = False
        return result
    finally:
        if owns:
            connection.close()


_CONTINUATION_JOB_IDENTITY_FIELDS = (
    "id",
    "company_name",
    "title",
    "job_url",
    "apply_url",
)
_CANDIDATE_BINDING_FIELDS = (
    "source_extraction_id",
    "profile_revision",
    "profile_digest",
)


def _semantic_truth_extension(
    stored_truth: Mapping[str, Any],
    current_truth: Mapping[str, Any],
) -> bool:
    """Return whether current truth only re-sources/adds facts.

    A confirmed master-resume replacement can produce a new extraction/profile
    digest even when every previously approved fact is unchanged.  An in-flight
    package may remain pinned to its original truth snapshot only when the new
    projection preserves every stored fact verbatim (apart from provenance),
    keeps the same protected/unresolved sets, and does not roll the profile
    revision backward or forward.
    """
    if any(
        str(stored_truth.get(field) or "") != str(current_truth.get(field) or "")
        for field in ("tenant_id", "user_id")
    ):
        return False
    stored_binding = dict(stored_truth.get("candidate_profile_binding") or {})
    current_binding = dict(current_truth.get("candidate_profile_binding") or {})
    if int(stored_binding.get("profile_revision") or 0) != int(
        current_binding.get("profile_revision") or 0
    ):
        return False
    if list(stored_truth.get("protected_fact_keys") or []) != list(
        current_truth.get("protected_fact_keys") or []
    ) or list(stored_truth.get("unresolved_fact_keys") or []) != list(
        current_truth.get("unresolved_fact_keys") or []
    ):
        return False

    stored_facts = {
        str(fact.get("key") or ""): dict(fact)
        for fact in (stored_truth.get("facts") or [])
        if isinstance(fact, Mapping) and str(fact.get("key") or "")
    }
    current_facts = {
        str(fact.get("key") or ""): dict(fact)
        for fact in (current_truth.get("facts") or [])
        if isinstance(fact, Mapping) and str(fact.get("key") or "")
    }
    if len(stored_facts) != len(stored_truth.get("facts") or []):
        return False
    for key, stored_fact in stored_facts.items():
        current_fact = current_facts.get(key)
        if current_fact is None:
            return False
        if {k: v for k, v in stored_fact.items() if k != "source"} != {
            k: v for k, v in current_fact.items() if k != "source"
        }:
            return False
    return True


def _immutable_inflight_continuation(package: Mapping[str, Any]) -> dict[str, Any]:
    """Allow a prepared package to survive additive source enrichment safely.

    Discovery may enrich a live job after preparation (description, parsed
    location, score, timestamps).  Requiring the resume to be regenerated for
    those additions defeats immutable in-flight execution.  Continuation is
    allowed only while the exact employer/job/destination, current eligibility,
    Candidate Truth, validated resume version, and rendered resume digest remain
    unchanged.  A changed target or candidate fact still fails closed.
    """
    snapshot = dict(package.get("snapshot") or {})
    stored_job = dict(snapshot.get("job") or {})
    current_job_snapshot = safe_owned_job_snapshot(int(package["job_id"]))
    current_job = dict(current_job_snapshot.get("job") or {})
    changed_identity = [
        field
        for field in _CONTINUATION_JOB_IDENTITY_FIELDS
        if str(stored_job.get(field) or "") != str(current_job.get(field) or "")
    ]
    if changed_identity:
        return {"allowed": False, "reason": "job_identity_changed"}

    connection = get_connection()
    try:
        owner = current_owner_readonly(connection)
        row = connection.execute(
            """SELECT primary_decision,status,hard_rejection_reason,
                      cpt_trapdoor,already_applied
                 FROM jobs
                WHERE id=?""",
            (int(package["job_id"]),),
        ).fetchone()
        if row is None:
            return {"allowed": False, "reason": "job_missing"}
        if (
            str(row["primary_decision"] or "") != "ELIGIBLE"
            or str(row["status"] or "") != "found"
            or row["hard_rejection_reason"] is not None
            or bool(int(row["cpt_trapdoor"] or 0))
            or bool(int(row["already_applied"] or 0))
        ):
            return {"allowed": False, "reason": "job_no_longer_eligible"}
        if (
            str(snapshot.get("tenant_id") or snapshot.get("application_truth", {}).get("tenant_id") or "")
            != owner.tenant_id
            or str(snapshot.get("user_id") or snapshot.get("application_truth", {}).get("user_id") or "")
            != owner.user_id
        ):
            return {"allowed": False, "reason": "owner_changed"}
    finally:
        connection.close()

    current_truth = current_application_truth_projection(job_id=int(package["job_id"]))
    stored_truth = dict(snapshot.get("application_truth") or {})
    current_binding = dict(current_truth.get("candidate_profile_binding") or {})
    stored_binding = dict(stored_truth.get("candidate_profile_binding") or {})
    binding_changed = any(
        str(current_binding.get(field) or "") != str(stored_binding.get(field) or "")
        for field in _CANDIDATE_BINDING_FIELDS
    )
    if binding_changed and not _semantic_truth_extension(stored_truth, current_truth):
        return {"allowed": False, "reason": "candidate_truth_changed"}

    resume = _resume_reference(str(package["resume_version_id"]))
    resume_binding = dict(resume.get("candidate_truth_binding") or {})
    if (
        str(resume.get("status") or "") != "VALIDATED"
        or str(resume.get("version_id") or "") != str(package["resume_version_id"])
        or str(resume.get("rendered_resume_sha256") or "")
        != str(package["resume_sha256"])
        or any(
            str(resume_binding.get(field) or "")
            != str(stored_binding.get(field) or "")
            for field in _CANDIDATE_BINDING_FIELDS
        )
    ):
        return {"allowed": False, "reason": "resume_changed"}

    return {
        "allowed": True,
        "reason": "additive_job_enrichment",
        "current_job_snapshot_sha256": str(
            current_job_snapshot["job_snapshot_sha256"]
        ),
    }


def preflight_package_freshness(package_id: str) -> dict[str, Any]:
    package = get_preflight_package(package_id)
    snapshot = package["snapshot"]
    try:
        current = _capture(
            job_id=int(package["job_id"]),
            application_id=str(package["application_id"]),
            resume_version_id=str(package["resume_version_id"]),
            opportunity_evaluation_id=str(package["opportunity_evaluation_id"]),
            relationship_strategy_id=(
                str(package["relationship_strategy_id"])
                if package.get("relationship_strategy_id")
                else None
            ),
            application_questions_complete=bool(
                (snapshot.get("application_question_state") or {}).get("complete")
            ),
            unresolved_question_keys=list(
                (snapshot.get("application_question_state") or {}).get(
                    "unresolved_question_keys"
                )
                or []
            ),
            legal_attestation_execution=bool(
                (snapshot.get("execution_capabilities") or {}).get(
                    "legal_attestation_execution"
                )
            ),
        )
    except (LookupError, RuntimeError, ValueError):
        continuation = _immutable_inflight_continuation(package)
        if continuation.get("allowed") is True:
            return {
                "package_id": str(package_id),
                "fresh": True,
                "reasons": [],
                "stored_package_digest": str(package["package_digest"]),
                "current_package_digest": str(package["package_digest"]),
                "immutable_inflight_continuation": True,
                "continuation_reason": str(continuation["reason"]),
                "current_job_snapshot_sha256": str(
                    continuation["current_job_snapshot_sha256"]
                ),
                "checked_at": _now(),
                "submission_authority": False,
            }
        raise
    current_digest = sha256_json(package_digest_payload(current))
    reasons: list[str] = []
    if current_digest != str(package["package_digest"]):
        reasons.append("package_inputs_changed")
    return {
        "package_id": str(package_id),
        "fresh": not reasons,
        "reasons": reasons,
        "stored_package_digest": str(package["package_digest"]),
        "current_package_digest": current_digest,
        "checked_at": _now(),
        "submission_authority": False,
    }
