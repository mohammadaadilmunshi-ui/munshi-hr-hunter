"""Immutable execution-ready Application Plan V2 for the Complete Application Loop.

`ApplicationPreflightPackage` remains preparation/readiness intelligence. This
module creates the separate exact execution intent consumed by MUNSHI Apply. It
is source-only and performs no HTTP, browser, ATS, n8n, Gmail, credential, or
submission action.

Protected values are never serialized generically. A protected/self-ID answer
may be represented only by a narrowly scoped `hunter-secure://` resolver
reference and explicit execution permission. Credential/post-offer values remain
unresolved in V2.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from app import answer_brain_v2
from app import application_preflight_package_v1 as preflight_v1
from app import native_resume_artifact_v5
from app import native_resume_service_v5 as resume_v5
from app.database import get_connection
from app.phase45_truth_binding import canonical_question_key
from app.phase67_common import safe_owned_job_snapshot, sha256_json
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema

PLAN_VERSION = "munshi-application-plan-v2"
PLAN_ENV = "MUNSHI_APPLICATION_PLAN_V2_ENABLED"
SUPPORTED_PROVIDERS = frozenset(
    {"GREENHOUSE", "LEVER", "ASHBY", "SMARTRECRUITERS", "WORKDAY"}
)
SENSITIVITY_CLASSES = frozenset(
    {"NORMAL", "PROTECTED", "SELF_ID", "CREDENTIAL", "POST_OFFER"}
)
_RESOLVER_RE = re.compile(r"^hunter-secure://[A-Za-z0-9._~:/?#@!$&'()*+,;=%-]{1,1000}$")

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS application_plans_v2 (
        plan_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        preflight_package_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        plan_version TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('NEEDS_INPUT','READY_TO_APPLY')),
        executable INTEGER NOT NULL CHECK(executable IN (0,1)),
        provider TEXT NOT NULL,
        job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
        candidate_profile_digest TEXT NOT NULL CHECK(length(candidate_profile_digest)=64),
        resume_version_id TEXT NOT NULL,
        resume_artifact_id TEXT NOT NULL,
        resume_artifact_sha256 TEXT NOT NULL CHECK(length(resume_artifact_sha256)=64),
        snapshot_json TEXT NOT NULL,
        inputs_json TEXT NOT NULL,
        plan_digest TEXT NOT NULL CHECK(length(plan_digest)=64),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,idempotency_key),
        UNIQUE(tenant_id,user_id,plan_digest),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(preflight_package_id)
          REFERENCES application_preflight_packages(package_id) ON DELETE RESTRICT,
        FOREIGN KEY(resume_version_id)
          REFERENCES native_resume_versions(version_id) ON DELETE RESTRICT,
        FOREIGN KEY(resume_artifact_id)
          REFERENCES native_resume_v5_artifacts(artifact_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_application_plans_v2_owner_application
       ON application_plans_v2(tenant_id,user_id,application_id,created_at DESC);""",
)


def application_plan_v2_enabled() -> bool:
    return str(os.getenv(PLAN_ENV) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        preflight_v1.ensure_schema(connection)
        native_resume_artifact_v5.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _text(value: Any, label: str, maximum: int = 2000) -> str:
    result = " ".join(str(value or "").split())
    if not result or len(result) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return result


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean.")
    return value


def _conditions(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Application answer conditions must be an object.")
    result = copy.deepcopy(dict(value))
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded) > 4000:
        raise ValueError("Application answer conditions are too large.")
    return result


def _infer_sensitivity(family: str, key: str, explicit: Any) -> str:
    if explicit is not None:
        value = str(explicit).strip().upper()
        if value not in SENSITIVITY_CLASSES:
            raise ValueError("Unsupported application answer sensitivity class.")
        return value
    if family == "voluntary_self_identification":
        return "SELF_ID"
    if family == "credential_requirement":
        return "CREDENTIAL"
    if family == "post_offer_sensitive":
        return "POST_OFFER"
    if family == "work_authorization":
        return "PROTECTED"
    folded = key.casefold()
    if any(token in folded for token in ("ssn", "social_security", "passport", "date_of_birth")):
        return "POST_OFFER"
    return "NORMAL"


def _normalize_requirement(raw: Mapping[str, Any]) -> dict[str, Any]:
    key = canonical_question_key(_text(raw.get("question_key"), "Question key", 160))
    family = str(raw.get("question_family") or "").strip().casefold()
    if family not in answer_brain_v2.QUESTION_FAMILIES:
        raise ValueError(f"Unsupported question family for {key}.")
    normalized_question = _text(
        raw.get("normalized_question") or raw.get("question") or key,
        "Normalized application question",
        2000,
    )
    required = raw.get("required", True)
    if not isinstance(required, bool):
        raise ValueError(f"Required flag for {key} must be boolean.")
    profile_fact_key = str(raw.get("profile_fact_key") or "").strip().casefold() or None
    sensitivity = _infer_sensitivity(family, key, raw.get("sensitivity_class"))
    return {
        "question_key": key,
        "question_family": family,
        "normalized_question": normalized_question,
        "required": required,
        "profile_fact_key": profile_fact_key,
        "conditions": _conditions(raw.get("conditions")),
        "sensitivity_class": sensitivity,
    }


def _normalize_requirements(values: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("Application answer requirements must be a list.")
    result = [_normalize_requirement(value) for value in values]
    keys = [value["question_key"] for value in result]
    if len(keys) != len(set(keys)):
        raise ValueError("Application answer requirements must have unique question keys.")
    return sorted(result, key=lambda value: value["question_key"])


def _normalize_permissions(value: Mapping[str, Any] | None) -> dict[str, bool]:
    raw = dict(value or {})
    names = (
        "background_prepare",
        "resume_upload",
        "normal_answer_autofill",
        "protected_fact_execution",
        "self_id_execution",
        "final_review",
        "final_submit",
    )
    result: dict[str, bool] = {}
    for name in names:
        candidate = raw.get(name, False)
        if not isinstance(candidate, bool):
            raise ValueError(f"Permission {name} must be boolean.")
        result[name] = candidate
    unknown = sorted(set(raw) - set(names))
    if unknown:
        raise ValueError("Unsupported Application Plan permissions: " + ", ".join(unknown))
    return result


def _normalize_secure_refs(value: Mapping[str, Any] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Secure resolver references must be an object.")
    result: dict[str, str] = {}
    for raw_key, raw_reference in value.items():
        key = canonical_question_key(str(raw_key))
        reference = str(raw_reference or "").strip()
        if not _RESOLVER_RE.fullmatch(reference):
            raise ValueError(f"Secure resolver reference for {key} is invalid.")
        result[key] = reference
    return result


def _provider_from_job(job: Mapping[str, Any]) -> str:
    haystack = " ".join(
        str(job.get(key) or "") for key in ("apply_url", "job_url", "source")
    ).casefold()
    if "greenhouse" in haystack:
        return "GREENHOUSE"
    if "lever" in haystack:
        return "LEVER"
    if "ashby" in haystack:
        return "ASHBY"
    if "smartrecruiters" in haystack:
        return "SMARTRECRUITERS"
    if "workday" in haystack or "myworkdayjobs" in haystack:
        return "WORKDAY"
    return "UNSUPPORTED"


def _normalize_provider_policy(
    value: Mapping[str, Any] | None,
    *,
    provider: str,
) -> dict[str, Any]:
    raw = dict(value or {})
    permitted = raw.get("permitted", provider in SUPPORTED_PROVIDERS)
    if not isinstance(permitted, bool):
        raise ValueError("Provider policy permitted must be boolean.")
    authentication_mode = str(raw.get("authentication_mode") or "PAUSE_IF_REQUIRED").strip().upper()
    if authentication_mode not in {"NONE", "PAUSE_IF_REQUIRED"}:
        raise ValueError("Unsupported provider authentication mode.")
    captcha_policy = str(raw.get("captcha_policy") or "PAUSE").strip().upper()
    if captcha_policy != "PAUSE":
        raise ValueError("Application Plan V2 never permits automatic CAPTCHA handling.")
    return {
        "provider": provider,
        "permitted": permitted,
        "authentication_mode": authentication_mode,
        "captcha_policy": "PAUSE",
        "mfa_policy": "PAUSE",
        "credentials_authority": False,
    }


def _display_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text[:300]


def _resolve_requirement(
    requirement: Mapping[str, Any],
    *,
    permissions: Mapping[str, bool],
    secure_refs: Mapping[str, str],
) -> tuple[dict[str, Any], str | None]:
    key = str(requirement["question_key"])
    family = str(requirement["question_family"])
    sensitivity = str(requirement["sensitivity_class"])
    required = bool(requirement["required"])
    common = {
        "question_key": key,
        "question_family": family,
        "normalized_question": requirement["normalized_question"],
        "conditions": copy.deepcopy(requirement["conditions"]),
        "sensitivity_class": sensitivity,
        "required": required,
    }

    if sensitivity in {"CREDENTIAL", "POST_OFFER"}:
        return (
            {
                **common,
                "execution_value": None,
                "secure_resolver_ref": None,
                "display_value": "[requires explicit user/security flow]",
                "source": "blocked_sensitive_domain",
                "confidence": None,
                "provenance": None,
                "autofill_allowed": False,
                "requires_review": True,
                "current_truth_binding": None,
            },
            "credential_or_post_offer_value_requires_explicit_flow" if required else None,
        )

    if sensitivity in {"PROTECTED", "SELF_ID"}:
        reference = secure_refs.get(key)
        permission_name = (
            "self_id_execution" if sensitivity == "SELF_ID" else "protected_fact_execution"
        )
        permitted = bool(permissions.get(permission_name))
        if reference and permitted:
            return (
                {
                    **common,
                    "execution_value": None,
                    "secure_resolver_ref": reference,
                    "display_value": "[protected value resolved at execution]",
                    "source": "hunter_secure_resolver",
                    "confidence": 1.0,
                    "provenance": "candidate_truth_protected_domain",
                    "autofill_allowed": True,
                    "requires_review": True,
                    "current_truth_binding": None,
                },
                None,
            )
        return (
            {
                **common,
                "execution_value": None,
                "secure_resolver_ref": reference,
                "display_value": "[protected value unresolved]",
                "source": "hunter_secure_resolver" if reference else "unresolved",
                "confidence": None,
                "provenance": None,
                "autofill_allowed": False,
                "requires_review": True,
                "current_truth_binding": None,
            },
            (
                "protected_execution_permission_missing"
                if reference and not permitted
                else "protected_value_requires_secure_resolver"
            )
            if required
            else None,
        )

    resolved = answer_brain_v2.resolve_answer(
        question_family=family,
        conditions=dict(requirement["conditions"]),
        profile_fact_key=requirement.get("profile_fact_key"),
        question_key=key,
    )
    if resolved.get("status") != "ANSWERED":
        return (
            {
                **common,
                "execution_value": None,
                "secure_resolver_ref": None,
                "display_value": None,
                "source": "unresolved",
                "confidence": None,
                "provenance": resolved.get("reason"),
                "autofill_allowed": False,
                "requires_review": True,
                "current_truth_binding": None,
            },
            str(resolved.get("reason") or "no_safe_answer") if required else None,
        )

    answer = dict(resolved.get("answer") or {})
    answer_autofill = bool(answer.get("autofill_allowed"))
    user_confirmed = bool(answer.get("user_confirmed"))
    permitted = bool(permissions.get("normal_answer_autofill"))
    executable = answer_autofill and user_confirmed and permitted
    execution_value = answer.get("canonical_answer") if executable else None
    reason: str | None = None
    if required and not executable:
        if not user_confirmed:
            reason = "answer_not_user_confirmed"
        elif not answer_autofill:
            reason = "answer_not_autofill_approved"
        else:
            reason = "normal_answer_autofill_permission_missing"
    return (
        {
            **common,
            "execution_value": execution_value,
            "secure_resolver_ref": None,
            "display_value": _display_value(answer.get("canonical_answer")),
            "source": answer.get("source") or resolved.get("resolution"),
            "confidence": answer.get("confidence"),
            "provenance": answer.get("evidence_provenance"),
            "autofill_allowed": executable,
            "requires_review": not executable or family in {"salary", "open_ended_job_specific"},
            "current_truth_binding": answer.get("candidate_truth_binding"),
        },
        reason,
    )


def _submitted_duplicate(connection: sqlite3.Connection, application_id: str, job_id: int) -> bool:
    job = connection.execute("SELECT already_applied FROM jobs WHERE id=?", (int(job_id),)).fetchone()
    if job is not None and int(job[0] or 0) == 1:
        return True
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "application_lifecycle_records" in tables:
        row = connection.execute(
            "SELECT state FROM application_lifecycle_records WHERE application_id=?",
            (str(application_id),),
        ).fetchone()
        if row is not None and str(row[0]) in {
            "SUBMITTED",
            "VERIFIED",
            "AWAITING_RESPONSE",
            "ASSESSMENT",
            "INTERVIEW",
            "OFFER",
            "REJECTED",
            "WITHDRAWN",
        }:
            return True
    return False


def _capture(
    *,
    preflight_package_id: str,
    requirements: Sequence[Mapping[str, Any]],
    permissions: Mapping[str, bool],
    provider_policy: Mapping[str, Any] | None,
    secure_refs: Mapping[str, str],
    resume_format: str,
) -> dict[str, Any]:
    package = preflight_v1.get_preflight_package(preflight_package_id)
    freshness = preflight_v1.preflight_package_freshness(preflight_package_id)
    if freshness.get("fresh") is not True:
        raise RuntimeError("Application preflight package is stale.")
    if str(package.get("status")) != "READY_TO_APPLY":
        raise ValueError("Application Plan requires a READY_TO_APPLY preflight package.")

    package_snapshot = dict(package["snapshot"])
    job = dict(package_snapshot.get("job") or {})
    job_id = int(package["job_id"])
    owned_job = safe_owned_job_snapshot(job_id)
    job_digest = str(owned_job["job_snapshot_sha256"])
    if job_digest != str(package["job_snapshot_sha256"]):
        raise RuntimeError("Application Plan job snapshot is stale.")

    resume_version_id = str(package["resume_version_id"])
    resume = resume_v5.get_version(resume_version_id)
    if resume.get("stage_b_bound") is not True:
        raise ValueError("Execution-ready Application Plan requires a Stage B-bound Native Resume V5 version.")
    if str(resume.get("html_sha256") or "") != str(package["resume_sha256"]):
        raise RuntimeError("Preflight and Native V5 rendered resume digests differ.")

    materialized = native_resume_artifact_v5.materialize(resume_version_id)
    selected_kind = str(resume_format or "PDF").strip().upper()
    if selected_kind not in {"PDF", "DOCX"}:
        raise ValueError("Resume format must be PDF or DOCX.")
    selected = dict(materialized[selected_kind.casefold()])

    projection = dict(package_snapshot.get("application_truth") or {})
    candidate_binding = dict(projection.get("candidate_profile_binding") or {})
    resume_binding = dict(resume.get("candidate_truth_binding") or {})
    for field in ("source_extraction_id", "profile_revision", "profile_digest"):
        if str(candidate_binding.get(field) or "") != str(resume_binding.get(field) or ""):
            raise RuntimeError("Application Plan resume Candidate Truth binding is stale.")

    provider = _provider_from_job(job)
    policy = _normalize_provider_policy(provider_policy, provider=provider)
    answers: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for requirement in requirements:
        answer, reason = _resolve_requirement(
            requirement,
            permissions=permissions,
            secure_refs=secure_refs,
        )
        answers.append(answer)
        if reason:
            unresolved.append(
                {"question_key": str(requirement["question_key"]), "reason": reason}
            )

    global_blockers: list[str] = []
    if not permissions.get("background_prepare"):
        global_blockers.append("background_prepare_permission_missing")
    if not permissions.get("resume_upload"):
        global_blockers.append("resume_upload_permission_missing")
    if provider not in SUPPORTED_PROVIDERS or not policy["permitted"]:
        global_blockers.append("provider_not_permitted_or_supported")

    connection = get_connection()
    try:
        ensure_schema(connection)
        if _submitted_duplicate(connection, str(package["application_id"]), job_id):
            global_blockers.append("duplicate_submitted_application")
    finally:
        connection.close()

    executable = not unresolved and not global_blockers
    status = "READY_TO_APPLY" if executable else "NEEDS_INPUT"
    return {
        "version": PLAN_VERSION,
        "application_id": str(package["application_id"]),
        "preflight_package_id": str(preflight_package_id),
        "preflight_package_digest": str(package["package_digest"]),
        "job": {
            "id": job_id,
            "company": job.get("company_name"),
            "title": job.get("title"),
            "job_url": job.get("job_url"),
            "apply_url": job.get("apply_url"),
            "job_snapshot_digest": job_digest,
        },
        "candidate_truth_binding": candidate_binding,
        "resume": {
            "engine": "NATIVE_V5",
            "version_id": resume_version_id,
            "version_number": int(resume["version_number"]),
            "rendered_output_sha256": str(resume["html_sha256"]),
            "artifact_id": selected["artifact_id"],
            "artifact_reference": selected["object_reference"],
            "artifact_sha256": selected["sha256"],
            "filename": selected["filename"],
            "mime_type": selected["mime_type"],
            "byte_count": selected["byte_count"],
            "pdf_page_count": materialized["pdf"]["page_count"],
            "source_bindings": {
                "candidate_truth": resume["candidate_truth_binding"],
                "job": resume["job_snapshot_binding"],
                "stage_b": resume["stage_b_binding"],
            },
        },
        "answers": answers,
        "unresolved": unresolved,
        "permissions": dict(permissions),
        "provider_policy": policy,
        "expected_state": "READY_TO_APPLY" if executable else "NEEDS_INPUT",
        "executable": executable,
        "global_blockers": global_blockers,
        "submission_authority": False,
        "automatic_actions_executed": False,
    }


def plan_digest_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in snapshot.items()
        if key not in {"plan_id", "idempotency_key", "plan_digest", "created_at"}
    }


def prepare_application_plan(
    *,
    preflight_package_id: str,
    idempotency_key: str,
    answer_requirements: Sequence[Mapping[str, Any]] | None = None,
    permissions: Mapping[str, Any] | None = None,
    provider_policy: Mapping[str, Any] | None = None,
    secure_resolver_refs: Mapping[str, Any] | None = None,
    resume_format: str = "PDF",
) -> dict[str, Any]:
    """Create one immutable, idempotent, revalidated Application Plan V2."""
    if not application_plan_v2_enabled():
        raise RuntimeError("Application Plan V2 is disabled.")
    package_id = _text(preflight_package_id, "Preflight package id", 200)
    key = _text(idempotency_key, "Application Plan idempotency key", 240)
    requirements = _normalize_requirements(answer_requirements)
    normalized_permissions = _normalize_permissions(permissions)
    secure_refs = _normalize_secure_refs(secure_resolver_refs)
    resume_format = str(resume_format or "PDF").strip().upper()

    inputs = {
        "preflight_package_id": package_id,
        "answer_requirements": requirements,
        "permissions": normalized_permissions,
        "provider_policy": copy.deepcopy(dict(provider_policy or {})),
        "secure_resolver_refs": secure_refs,
        "resume_format": resume_format,
    }
    first = _capture(
        preflight_package_id=package_id,
        requirements=requirements,
        permissions=normalized_permissions,
        provider_policy=provider_policy,
        secure_refs=secure_refs,
        resume_format=resume_format,
    )
    digest = sha256_json(plan_digest_payload(first))

    # Repeat the whole capture immediately before persistence. This proves that
    # Candidate Truth, job, resume, preflight and Answer Brain did not drift.
    second = _capture(
        preflight_package_id=package_id,
        requirements=requirements,
        permissions=normalized_permissions,
        provider_policy=provider_policy,
        secure_refs=secure_refs,
        resume_format=resume_format,
    )
    second_digest = sha256_json(plan_digest_payload(second))
    if second_digest != digest:
        raise RuntimeError("Application Plan inputs changed during creation.")

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        prior = connection.execute(
            """SELECT * FROM application_plans_v2
               WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if prior is not None:
            if str(prior["plan_digest"]) != digest:
                raise ValueError("Idempotency key belongs to a different Application Plan.")
            return get_application_plan(str(prior["plan_id"]), connection=connection)

        same = connection.execute(
            """SELECT plan_id FROM application_plans_v2
               WHERE tenant_id=? AND user_id=? AND plan_digest=?""",
            (owner.tenant_id, owner.user_id, digest),
        ).fetchone()
        if same is not None:
            return get_application_plan(str(same["plan_id"]), connection=connection)

        plan_id = f"application-plan-{uuid4()}"
        snapshot = {**second, "plan_id": plan_id, "idempotency_key": key, "plan_digest": digest}
        connection.execute(
            """INSERT INTO application_plans_v2(
                   plan_id,tenant_id,user_id,application_id,job_id,preflight_package_id,
                   idempotency_key,plan_version,status,executable,provider,
                   job_snapshot_sha256,candidate_profile_digest,resume_version_id,
                   resume_artifact_id,resume_artifact_sha256,snapshot_json,inputs_json,
                   plan_digest
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                plan_id,
                owner.tenant_id,
                owner.user_id,
                second["application_id"],
                int(second["job"]["id"]),
                package_id,
                key,
                PLAN_VERSION,
                second["expected_state"],
                int(bool(second["executable"])),
                second["provider_policy"]["provider"],
                second["job"]["job_snapshot_digest"],
                second["candidate_truth_binding"]["profile_digest"],
                second["resume"]["version_id"],
                second["resume"]["artifact_id"],
                second["resume"]["artifact_sha256"],
                json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                digest,
            ),
        )
        connection.commit()
        return get_application_plan(plan_id, connection=connection)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_application_plan(
    plan_id: str,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM application_plans_v2
               WHERE plan_id=? AND tenant_id=? AND user_id=?""",
            (str(plan_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Application Plan is not owned by the current tenant user.")
        result = dict(row)
        result["snapshot"] = json.loads(result.pop("snapshot_json"))
        result["inputs"] = json.loads(result.pop("inputs_json"))
        result["executable"] = bool(result["executable"])
        result["submission_authority"] = False
        return result
    finally:
        if owns:
            connection.close()


def application_plan_freshness(plan_id: str) -> dict[str, Any]:
    plan = get_application_plan(plan_id)
    inputs = plan["inputs"]
    reasons: list[str] = []
    try:
        current = _capture(
            preflight_package_id=str(inputs["preflight_package_id"]),
            requirements=list(inputs["answer_requirements"]),
            permissions=dict(inputs["permissions"]),
            provider_policy=dict(inputs["provider_policy"]),
            secure_refs=dict(inputs["secure_resolver_refs"]),
            resume_format=str(inputs["resume_format"]),
        )
        current_digest = sha256_json(plan_digest_payload(current))
        if current_digest != str(plan["plan_digest"]):
            reasons.append("plan_inputs_changed")
    except (LookupError, RuntimeError, ValueError) as error:
        current_digest = ""
        reasons.append(f"revalidation_failed:{type(error).__name__}")
    return {
        "plan_id": str(plan_id),
        "fresh": not reasons,
        "reasons": reasons,
        "stored_plan_digest": str(plan["plan_digest"]),
        "current_plan_digest": current_digest,
        "executable": bool(plan["executable"]) and not reasons,
        "submission_authority": False,
    }


def executable_plan(plan_id: str) -> dict[str, Any]:
    plan = get_application_plan(plan_id)
    freshness = application_plan_freshness(plan_id)
    if plan["executable"] is not True or str(plan["status"]) != "READY_TO_APPLY":
        raise ValueError("Application Plan is not execution-ready.")
    if freshness["fresh"] is not True:
        raise RuntimeError("Application Plan is stale.")
    return plan
