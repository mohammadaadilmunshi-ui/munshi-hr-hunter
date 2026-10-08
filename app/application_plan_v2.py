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
from urllib.parse import urlsplit
from uuid import uuid4

from app import (
    answer_brain_v2,
    application_execution_artifact,
    cover_letter_artifact_v1,
    native_resume_artifact_v5,
)
from app import application_preflight_package_v1 as preflight_v1
from app import application_secure_answer_resolver_v1 as secure_answers
from app import candidate_knowledge_graph_v1 as universal_graph
from app import candidate_ontology_v1 as universal_ontology
from app import universal_answer_resolver_v1 as universal_answers
from app import universal_question_resolver_v1 as universal_questions
from app import application_semantic_binding_v1 as universal_binding
from app import native_resume_service_v5 as resume_v5
from app.database import get_connection
from app.phase45_truth_binding import canonical_question_key
from app.phase67_common import safe_owned_job_snapshot, sha256_json
from app.tenant_foundation import (
    current_owner,
    current_owner_readonly,
    ensure_schema as ensure_tenant_schema,
)

PLAN_VERSION = "munshi-application-plan-v2"
PLAN_ENV = "MUNSHI_APPLICATION_PLAN_V2_ENABLED"
RUNTIME_RESOLUTION_ENV = "MUNSHI_APPLICATION_PLAN_RUNTIME_RESOLUTION_ENABLED"
RUNTIME_SUPERSESSION_VERSION = "munshi-application-plan-supersession-v1"
_RUNTIME_REVISION_KIND = "runtime_apply_needs_input_v1"
KNOWN_PROVIDER_RECIPES = frozenset(
    {"GREENHOUSE", "LEVER", "ASHBY", "SMARTRECRUITERS", "WORKDAY", "AGILE_ATS"}
)
# Backward-compatible export: this is now a recipe catalog, not an eligibility gate.
SUPPORTED_PROVIDERS = KNOWN_PROVIDER_RECIPES
SENSITIVITY_CLASSES = frozenset(
    {"NORMAL", "PROTECTED", "SELF_ID", "CREDENTIAL", "POST_OFFER"}
)
_RESOLVER_RE = re.compile(r"^hunter-secure://[A-Za-z0-9._~:/?#@!$&'()*+,;=%-]{1,1000}$")
_PROVIDER_HOSTS = {
    "GREENHOUSE": ("greenhouse.io",),
    "LEVER": ("lever.co",),
    "ASHBY": ("ashbyhq.com",),
    "SMARTRECRUITERS": ("smartrecruiters.com",),
    "WORKDAY": ("myworkdayjobs.com", "workdayjobs.com"),
    "AGILE_ATS": ("agile-ats.com",),
}
_PROVIDER_MAILBOX_POLICIES = {
    # Workday SMTP delivery uses the myworkday.com sending domain. Candidate
    # verification links remain on the provider's external career-site hosts.
    "WORKDAY": {
        "sender_domains": ["myworkday.com", "workday.com"],
        "link_hosts": ["myworkdayjobs.com", "workdayjobs.com"],
    },
}

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
    """CREATE TABLE IF NOT EXISTS application_plan_revisions_v2 (
        revision_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        prior_plan_id TEXT NOT NULL,
        prior_plan_digest TEXT NOT NULL CHECK(length(prior_plan_digest)=64),
        replacement_plan_id TEXT,
        replacement_plan_digest TEXT
          CHECK(replacement_plan_digest IS NULL OR length(replacement_plan_digest)=64),
        idempotency_key TEXT NOT NULL,
        resolution_digest TEXT NOT NULL CHECK(length(resolution_digest)=64),
        resolutions_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(
            (replacement_plan_id IS NULL AND replacement_plan_digest IS NULL)
            OR (replacement_plan_id IS NOT NULL AND replacement_plan_digest IS NOT NULL)
        ),
        UNIQUE(tenant_id,user_id,idempotency_key),
        UNIQUE(tenant_id,user_id,prior_plan_id),
        UNIQUE(tenant_id,user_id,replacement_plan_id),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(prior_plan_id) REFERENCES application_plans_v2(plan_id) ON DELETE RESTRICT,
        FOREIGN KEY(replacement_plan_id) REFERENCES application_plans_v2(plan_id) ON DELETE RESTRICT
    );""",
)


def application_plan_v2_enabled() -> bool:
    return str(os.getenv(PLAN_ENV) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def runtime_application_plan_resolution_enabled() -> bool:
    return str(os.getenv(RUNTIME_RESOLUTION_ENV) or "").strip().casefold() in {
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
        raise ValueError(
            f"{label} is required and must be at most {maximum} characters."
        )
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
    encoded = json.dumps(
        result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    if len(encoded) > 4000:
        raise ValueError("Application answer conditions are too large.")
    return result


def _infer_sensitivity(family: str, key: str, question: str, explicit: Any) -> str:
    inferred = "NORMAL"
    if family == "voluntary_self_identification":
        inferred = "SELF_ID"
    elif family == "credential_requirement":
        inferred = "CREDENTIAL"
    elif family == "post_offer_sensitive":
        inferred = "POST_OFFER"
    elif family == "work_authorization":
        inferred = "PROTECTED"
    elif any(
        token in key.casefold()
        for token in ("ssn", "social_security", "passport", "date_of_birth")
    ):
        inferred = "POST_OFFER"
    else:
        inferred = {
            "voluntary_self_identification": "SELF_ID",
            "credential_requirement": "CREDENTIAL",
            "post_offer_sensitive": "POST_OFFER",
            "work_authorization": "PROTECTED",
        }.get(answer_brain_v2.classify_question(question), inferred)
    if explicit is None:
        return inferred
    value = str(explicit).strip().upper()
    if value not in SENSITIVITY_CLASSES:
        raise ValueError("Unsupported application answer sensitivity class.")
    # Callers may add caution, never relax the category inferred from the field.
    ranks = {
        "NORMAL": 0,
        "PROTECTED": 1,
        "SELF_ID": 2,
        "CREDENTIAL": 3,
        "POST_OFFER": 4,
    }
    return value if ranks[value] >= ranks[inferred] else inferred


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
    sensitivity = _infer_sensitivity(
        family, key, normalized_question, raw.get("sensitivity_class")
    )
    conditions = _conditions(raw.get("conditions"))
    if sensitivity != "NORMAL" and conditions:
        raise ValueError(
            "Non-normal application questions cannot carry plaintext conditions."
        )

    universal_concept_id = str(raw.get("universal_concept_id") or "").strip().casefold() or None
    universal_confidence: float | None = None
    universal_lane: str | None = None
    universal_reason: str | None = None
    universal_binding_fingerprint: str | None = None
    if universal_concept_id is not None:
        concept = universal_ontology.require_concept(universal_concept_id)
        try:
            universal_confidence = float(raw.get("universal_semantic_confidence") or 0.0)
        except (TypeError, ValueError) as error:
            raise ValueError("Universal semantic confidence must be numeric.") from error
        if not 0.0 <= universal_confidence <= 1.0:
            raise ValueError("Universal semantic confidence must be between 0 and 1.")
        universal_lane = _text(raw.get("universal_classification_lane") or "UNKNOWN", "Universal classification lane", 120)
        universal_reason = _text(raw.get("universal_reason_code") or "UNKNOWN", "Universal reason code", 160)
        ub_sensitivity = str(raw.get("universal_sensitivity") or concept.sensitivity).strip().upper()
        ranks = {"NORMAL": 0, "PROTECTED": 1, "SELF_ID": 2, "CREDENTIAL": 3, "POST_OFFER": 4}
        if ub_sensitivity not in ranks:
            raise ValueError("Unsupported Universal Brain sensitivity.")
        # Semantic metadata may make a requirement stricter, never less sensitive.
        if ranks[ub_sensitivity] > ranks[sensitivity]:
            sensitivity = ub_sensitivity
        fingerprint = str(raw.get("universal_binding_fingerprint") or "").strip().lower()
        if fingerprint:
            if len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint):
                raise ValueError("Universal browser binding fingerprint must be SHA-256.")
            universal_binding_fingerprint = fingerprint

    return {
        "question_key": key,
        "question_family": family,
        "normalized_question": normalized_question,
        "required": required,
        "profile_fact_key": profile_fact_key,
        "conditions": conditions,
        "sensitivity_class": sensitivity,
        "universal_concept_id": universal_concept_id,
        "universal_semantic_confidence": universal_confidence,
        "universal_classification_lane": universal_lane,
        "universal_reason_code": universal_reason,
        "universal_binding_fingerprint": universal_binding_fingerprint,
    }


def _normalize_requirements(
    values: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("Application answer requirements must be a list.")
    result = [_normalize_requirement(value) for value in values]
    keys = [value["question_key"] for value in result]
    if len(keys) != len(set(keys)):
        raise ValueError(
            "Application answer requirements must have unique question keys."
        )
    return sorted(result, key=lambda value: value["question_key"])


def _normalize_permissions(value: Mapping[str, Any] | None) -> dict[str, bool]:
    raw = dict(value or {})
    names = (
        "background_prepare",
        "resume_upload",
        "cover_letter_upload",
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
        raise ValueError(
            "Unsupported Application Plan permissions: " + ", ".join(unknown)
        )
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
        parsed = urlsplit(reference)
        expected_path = "/" + key.replace(".", "/")
        if (
            parsed.scheme != "hunter-secure"
            or parsed.netloc != "candidate-truth"
            or parsed.path != expected_path
        ):
            raise ValueError(
                f"Secure resolver reference for {key} is outside its Candidate Truth scope."
            )
        if (
            parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or parsed.port
        ):
            raise ValueError(
                f"Secure resolver reference for {key} is outside its Candidate Truth scope."
            )
        result[key] = reference
    return result


def _scoped_secure_refs(
    requirements: Sequence[Mapping[str, Any]], refs: Mapping[str, str]
) -> dict[str, str]:
    protected = {
        str(requirement["question_key"])
        for requirement in requirements
        if str(requirement["sensitivity_class"]) in {"PROTECTED", "SELF_ID"}
    }
    unexpected = sorted(set(refs) - protected)
    if unexpected:
        raise ValueError(
            "Secure resolver references require a matching protected application question."
        )
    if refs:
        by_key = {
            str(requirement["question_key"]): requirement
            for requirement in requirements
        }
        for key, reference in refs.items():
            requirement = by_key.get(key)
            if requirement is None:
                raise ValueError(
                    "Secure resolver reference has no matching application requirement."
                )
            # URI syntax alone is never authority. The reference must resolve
            # value-free against the current owner-bound protected Candidate Truth.
            secure_answers.validate_secure_reference(requirement, reference)
    return dict(refs)


def _application_target_host(job: Mapping[str, Any]) -> str:
    for field in ("apply_url", "job_url"):
        value = str(job.get(field) or "").strip()
        if not value:
            continue
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() != "https"
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            continue
        host = (parsed.hostname or "").casefold().rstrip(".")
        if host:
            return host
    return ""


def _provider_from_job(job: Mapping[str, Any]) -> str:
    host = _application_target_host(job)
    if not host:
        return "GENERIC"
    for provider, suffixes in _PROVIDER_HOSTS.items():
        if any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
            return provider
    return "GENERIC"


def _normalize_provider_policy(
    value: Mapping[str, Any] | None,
    *,
    provider: str,
    allowed_hosts: tuple[str, ...] = (),
) -> dict[str, Any]:
    raw = dict(value or {})
    unknown = sorted(set(raw) - {"permitted", "authentication_mode", "captcha_policy"})
    if unknown:
        raise ValueError("Unsupported provider policy fields: " + ", ".join(unknown))
    effective_hosts = tuple(
        dict.fromkeys(
            str(host).casefold().rstrip(".")
            for host in (
                _PROVIDER_HOSTS.get(provider, ())
                if provider in KNOWN_PROVIDER_RECIPES
                else allowed_hosts
            )
            if str(host).strip()
        )
    )
    permitted = raw.get("permitted", bool(effective_hosts))
    if not isinstance(permitted, bool):
        raise ValueError("Provider policy permitted must be boolean.")
    authentication_mode = (
        str(raw.get("authentication_mode") or "PAUSE_IF_REQUIRED").strip().upper()
    )
    if authentication_mode not in {"NONE", "PAUSE_IF_REQUIRED"}:
        raise ValueError("Unsupported provider authentication mode.")
    captcha_policy = str(raw.get("captcha_policy") or "PAUSE").strip().upper()
    if captcha_policy != "PAUSE":
        raise ValueError(
            "Application Plan V2 never permits automatic CAPTCHA handling."
        )
    normalized = {
        "provider": provider,
        "provider_recipe": provider if provider in KNOWN_PROVIDER_RECIPES else None,
        "permitted": permitted,
        "authentication_mode": authentication_mode,
        "captcha_policy": "PAUSE",
        "mfa_policy": "PAUSE",
        "credentials_authority": False,
        "allowed_hosts": list(effective_hosts),
    }
    mailbox_policy = _PROVIDER_MAILBOX_POLICIES.get(provider)
    if mailbox_policy is not None:
        normalized["mailbox_verification"] = copy.deepcopy(mailbox_policy)
    return normalized


def _owner_binding(package_snapshot: Mapping[str, Any]) -> dict[str, str]:
    connection = get_connection()
    try:
        ensure_tenant_schema(connection)
        owner = current_owner(connection)
    finally:
        connection.close()
    truth = dict(package_snapshot.get("application_truth") or {})
    if (
        str(truth.get("tenant_id") or "") != owner.tenant_id
        or str(truth.get("user_id") or "") != owner.user_id
    ):
        raise PermissionError(
            "Application preflight package owner does not match the current tenant user."
        )
    identity = {"tenant_id": owner.tenant_id, "user_id": owner.user_id}
    return {**identity, "owner_digest": sha256_json(identity)}


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
    answer_overrides: Mapping[str, Mapping[str, str]] | None = None,
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
        "universal_concept_id": requirement.get("universal_concept_id"),
        "universal_binding_fingerprint": requirement.get("universal_binding_fingerprint"),
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
            "credential_or_post_offer_value_requires_explicit_flow"
            if required
            else None,
        )

    if sensitivity in {"PROTECTED", "SELF_ID"}:
        reference = secure_refs.get(key)
        permission_name = (
            "self_id_execution"
            if sensitivity == "SELF_ID"
            else "protected_fact_execution"
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

    override = dict((answer_overrides or {}).get(key) or {})
    if override:
        if sensitivity != "NORMAL":
            raise ValueError("Only normal application questions may use an explicit resolution.")
        permitted = bool(permissions.get("normal_answer_autofill"))
        return (
            {
                **common,
                "execution_value": override["canonical_answer"] if permitted else None,
                "secure_resolver_ref": None,
                "display_value": _display_value(override["canonical_answer"]),
                "source": "explicit_candidate_resolution",
                "confidence": 1.0,
                "provenance": override["provenance"],
                "autofill_allowed": permitted,
                "requires_review": True,
                "current_truth_binding": None,
            },
            None
            if permitted or not required
            else "normal_answer_autofill_permission_missing",
        )

    universal_concept_id = str(requirement.get("universal_concept_id") or "").strip().casefold()
    if (
        sensitivity == "NORMAL"
        and universal_concept_id
        and universal_graph.universal_normal_autofill_enabled()
    ):
        universal_question = {
            "state": "RESOLVED",
            "concept_id": universal_concept_id,
            "confidence": float(requirement.get("universal_semantic_confidence") or 0.0),
            "classification_lane": str(requirement.get("universal_classification_lane") or "UNKNOWN"),
            "sensitivity": "NORMAL",
            "reason_code": str(requirement.get("universal_reason_code") or "UNKNOWN"),
            "conditions": copy.deepcopy(requirement.get("conditions") or {}),
            "candidate_values_exposed": False,
        }
        universal = universal_answers.resolve_concept(universal_question)
        status = str(universal.get("answer_status") or "MISSING_FACT")
        if status in {"ANSWERED", "DERIVED", "POLICY_RESOLVED"}:
            permitted = bool(permissions.get("normal_answer_autofill"))
            approved = bool(universal.get("autofill_allowed")) and permitted
            value = universal.get("value") if approved else None
            reason = None
            if required and not approved:
                reason = (
                    "normal_answer_autofill_permission_missing"
                    if not permitted
                    else "universal_answer_not_autofill_approved"
                )
            return (
                {
                    **common,
                    "execution_value": value,
                    "secure_resolver_ref": None,
                    "universal_value_ref": universal.get("value_ref"),
                    "universal_answer_status": status,
                    "universal_answer_mode": universal.get("answer_mode"),
                    "knowledge_revision": universal.get("knowledge_revision"),
                    "display_value": _display_value(universal.get("value")),
                    "source": universal.get("answer_source") or "universal_brain",
                    "confidence": universal.get("confidence"),
                    "provenance": universal.get("reason"),
                    "autofill_allowed": approved,
                    "requires_review": not approved or family in {"salary", "open_ended_job_specific"},
                    "current_truth_binding": None,
                    "evidence_ids": list(universal.get("evidence_ids") or []),
                },
                reason,
            )
        # Once a high-confidence normal concept is owned by Universal Brain, a
        # conflict or exhausted knowledge search must fail closed. Do not bypass
        # it through a weaker legacy answer path.
        if status in {
            "CONFLICT", "MISSING_FACT", "REQUIRES_EXPLICIT_USER",
            "BLOCKED_BY_POLICY", "AMBIGUOUS_CONCEPT",
        }:
            return (
                {
                    **common,
                    "execution_value": None,
                    "secure_resolver_ref": None,
                    "universal_value_ref": universal.get("value_ref"),
                    "universal_answer_status": status,
                    "universal_answer_mode": universal.get("answer_mode"),
                    "knowledge_revision": universal.get("knowledge_revision"),
                    "display_value": None,
                    "source": "universal_brain",
                    "confidence": universal.get("confidence"),
                    "provenance": universal.get("reason"),
                    "autofill_allowed": False,
                    "requires_review": True,
                    "current_truth_binding": None,
                    "evidence_ids": list(universal.get("evidence_ids") or []),
                },
                str(universal.get("reason") or status.casefold()) if required else None,
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
            "requires_review": not executable
            or family in {"salary", "open_ended_job_specific"},
            "current_truth_binding": answer.get("candidate_truth_binding"),
        },
        reason,
    )


def _submitted_duplicate(
    connection: sqlite3.Connection, application_id: str, job_id: int
) -> bool:
    job = connection.execute(
        "SELECT already_applied FROM jobs WHERE id=?", (int(job_id),)
    ).fetchone()
    if job is not None and int(job[0] or 0) == 1:
        return True
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
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
    cover_letter_id: str | None = None,
    answer_overrides: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    package = preflight_v1.get_preflight_package(preflight_package_id)
    freshness = preflight_v1.preflight_package_freshness(preflight_package_id)
    if freshness.get("fresh") is not True:
        raise RuntimeError("Application preflight package is stale.")
    if str(package.get("status")) != "READY_TO_APPLY":
        raise ValueError(
            "Application Plan requires a READY_TO_APPLY preflight package."
        )

    package_snapshot = dict(package["snapshot"])
    owner_binding = _owner_binding(package_snapshot)
    job = dict(package_snapshot.get("job") or {})
    job_id = int(package["job_id"])
    owned_job = safe_owned_job_snapshot(job_id)
    current_job_digest = str(owned_job["job_snapshot_sha256"])
    immutable_continuation = (
        freshness.get("immutable_inflight_continuation") is True
    )
    job_digest = (
        str(package["job_snapshot_sha256"])
        if immutable_continuation
        else current_job_digest
    )
    if current_job_digest != str(package["job_snapshot_sha256"]) and not immutable_continuation:
        raise RuntimeError("Application Plan job snapshot is stale.")

    resume_version_id = str(package["resume_version_id"])
    resume = resume_v5.get_version(resume_version_id)
    if resume.get("stage_b_bound") is not True:
        raise ValueError(
            "Execution-ready Application Plan requires a Stage B-bound Native Resume V5 version."
        )
    if str(resume.get("html_sha256") or "") != str(package["resume_sha256"]):
        raise RuntimeError("Preflight and Native V5 rendered resume digests differ.")

    # The Native execution artifact is persisted before a Plan references it.
    # This is preparation evidence only and never authorizes upload/submission.
    execution_pdf = application_execution_artifact.native(
        application_id=str(package["application_id"]), version_id=resume_version_id
    )
    materialized = native_resume_artifact_v5.materialize(
        resume_version_id,
        application_id=str(package["application_id"]),
    )
    selected_kind = str(resume_format or "PDF").strip().upper()
    if selected_kind not in {"PDF", "DOCX"}:
        raise ValueError("Resume format must be PDF or DOCX.")
    selected = dict(materialized[selected_kind.casefold()])
    if selected_kind == "PDF":
        selected = {
            **selected,
            "artifact_id": execution_pdf["artifact_id"],
            "object_reference": execution_pdf["artifact_reference"],
            "sha256": execution_pdf["artifact_sha256"],
            "filename": execution_pdf["filename"],
            "mime_type": execution_pdf["mime_type"],
            "page_count": execution_pdf["page_count"],
        }

    projection = dict(package_snapshot.get("application_truth") or {})
    candidate_binding = dict(projection.get("candidate_profile_binding") or {})
    resume_binding = dict(resume.get("candidate_truth_binding") or {})
    for field in ("source_extraction_id", "profile_revision", "profile_digest"):
        if str(candidate_binding.get(field) or "") != str(
            resume_binding.get(field) or ""
        ):
            raise RuntimeError(
                "Application Plan resume Candidate Truth binding is stale."
            )

    cover_letter: dict[str, Any] | None = None
    if cover_letter_id:
        cover = cover_letter_artifact_v1.get_artifact(str(cover_letter_id))
        cover_freshness = cover_letter_artifact_v1.freshness(str(cover_letter_id))
        if cover_freshness.get("fresh") is not True:
            raise RuntimeError("Cover-letter artifact is stale.")
        checks = (
            str(cover["application_id"]) == str(package["application_id"]),
            str(cover["preflight_package_id"]) == str(preflight_package_id),
            str(cover["preflight_package_digest"]) == str(package["package_digest"]),
            int(cover["job_id"]) == job_id,
            str(cover["job_snapshot_sha256"]) == job_digest,
            str(cover["candidate_profile_digest"])
            == str(candidate_binding["profile_digest"]),
            str(cover["resume_version_id"]) == resume_version_id,
            str(cover["resume_artifact_sha256"]) == str(selected["sha256"]),
            cover.get("submission_authority") is False,
        )
        if not all(checks):
            raise RuntimeError(
                "Cover-letter artifact does not match current Application Plan bindings."
            )
        cover_letter_artifact_v1.artifact_bytes(str(cover_letter_id))
        cover_letter = {
            "version": str(cover["version"]),
            "artifact_id": str(cover["artifact_id"]),
            "artifact_reference": str(cover["artifact_reference"]),
            "artifact_sha256": str(cover["artifact_sha256"]),
            "content_sha256": str(cover["content_sha256"]),
            "filename": str(cover["filename"]),
            "mime_type": str(cover["mime_type"]),
            "byte_count": int(cover["byte_count"]),
            "page_count": int(cover["page_count"]),
            "preflight_package_digest": str(cover["preflight_package_digest"]),
            "job_snapshot_sha256": str(cover["job_snapshot_sha256"]),
            "candidate_profile_digest": str(cover["candidate_profile_digest"]),
            "resume_version_id": str(cover["resume_version_id"]),
            "resume_artifact_sha256": str(cover["resume_artifact_sha256"]),
            "submission_authority": False,
        }

    provider = _provider_from_job(job)
    target_host = _application_target_host(job)
    policy = _normalize_provider_policy(
        provider_policy,
        provider=provider,
        allowed_hosts=((target_host,) if target_host else ()),
    )
    answers: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for requirement in requirements:
        answer, reason = _resolve_requirement(
            requirement,
            permissions=permissions,
            secure_refs=secure_refs,
            answer_overrides=answer_overrides,
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
    if cover_letter is not None and not permissions.get("cover_letter_upload"):
        global_blockers.append("cover_letter_upload_permission_missing")
    if not policy["permitted"] or not policy["allowed_hosts"]:
        global_blockers.append("provider_not_permitted_or_target_invalid")

    connection = get_connection()
    try:
        ensure_schema(connection)
        if _submitted_duplicate(connection, str(package["application_id"]), job_id):
            global_blockers.append("duplicate_submitted_application")
    finally:
        connection.close()

    executable = not unresolved and not global_blockers
    result = {
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
        "owner_binding": owner_binding,
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
    if cover_letter is not None:
        result["cover_letter"] = cover_letter
    return result


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
    cover_letter_id: str | None = None,
    _answer_overrides: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Create one immutable, idempotent, revalidated Application Plan V2."""
    if not application_plan_v2_enabled():
        raise RuntimeError("Application Plan V2 is disabled.")
    package_id = _text(preflight_package_id, "Preflight package id", 200)
    key = _text(idempotency_key, "Application Plan idempotency key", 240)
    requirements = _normalize_requirements(answer_requirements)
    normalized_permissions = _normalize_permissions(permissions)
    secure_refs = _scoped_secure_refs(
        requirements, _normalize_secure_refs(secure_resolver_refs)
    )
    resume_format = str(resume_format or "PDF").strip().upper()
    normalized_cover_letter_id = str(cover_letter_id or "").strip() or None
    normalized_overrides = {
        canonical_question_key(str(key)): dict(value)
        for key, value in dict(_answer_overrides or {}).items()
    }

    inputs = {
        "preflight_package_id": package_id,
        "answer_requirements": requirements,
        "permissions": normalized_permissions,
        "provider_policy": copy.deepcopy(dict(provider_policy or {})),
        "secure_resolver_refs": secure_refs,
        "resume_format": resume_format,
        "answer_overrides": normalized_overrides,
    }
    if normalized_cover_letter_id is not None:
        inputs["cover_letter_id"] = normalized_cover_letter_id
    first = _capture(
        preflight_package_id=package_id,
        requirements=requirements,
        permissions=normalized_permissions,
        provider_policy=provider_policy,
        secure_refs=secure_refs,
        resume_format=resume_format,
        cover_letter_id=normalized_cover_letter_id,
        answer_overrides=normalized_overrides,
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
        cover_letter_id=normalized_cover_letter_id,
        answer_overrides=normalized_overrides,
    )
    second_digest = sha256_json(plan_digest_payload(second))
    if second_digest != digest:
        raise RuntimeError("Application Plan inputs changed during creation.")

    connection = get_connection()
    try:
        ensure_schema(connection)
        # SQLite's unique indexes are the final arbiter, but an immediate
        # transaction makes idempotency/read-before-insert deterministic under
        # concurrent local workers.
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        owner = current_owner(connection)
        prior = connection.execute(
            """SELECT * FROM application_plans_v2
               WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if prior is not None:
            if str(prior["plan_digest"]) != digest:
                raise ValueError(
                    "Idempotency key belongs to a different Application Plan."
                )
            return get_application_plan(str(prior["plan_id"]), connection=connection)

        same = connection.execute(
            """SELECT plan_id FROM application_plans_v2
               WHERE tenant_id=? AND user_id=? AND plan_digest=?""",
            (owner.tenant_id, owner.user_id, digest),
        ).fetchone()
        if same is not None:
            return get_application_plan(str(same["plan_id"]), connection=connection)

        plan_id = f"application-plan-{uuid4()}"
        snapshot = {
            **second,
            "plan_id": plan_id,
            "idempotency_key": key,
            "plan_digest": digest,
        }
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
                json.dumps(
                    snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ),
                json.dumps(
                    inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ),
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


def _resolution_inputs(
    plan: Mapping[str, Any], values: Sequence[Mapping[str, Any]]
) -> list[dict[str, str]]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("Application answer resolutions must be a list.")
    unresolved = {
        str(item["question_key"])
        for item in list(plan["snapshot"].get("unresolved") or [])
        if isinstance(item, Mapping)
    }
    requirements = {
        str(item["question_key"]): item
        for item in list(plan["inputs"].get("answer_requirements") or [])
        if isinstance(item, Mapping)
    }
    result: list[dict[str, str]] = []
    for raw in values:
        if not isinstance(raw, Mapping) or set(raw) != {"question_key", "canonical_answer"}:
            raise ValueError("Each answer resolution must contain question_key and canonical_answer.")
        key = canonical_question_key(_text(raw.get("question_key"), "Question key", 160))
        requirement = requirements.get(key)
        if key not in unresolved or requirement is None:
            raise ValueError("Answer resolution is not for a current unresolved question.")
        if str(requirement.get("sensitivity_class")) != "NORMAL":
            raise ValueError("Protected, self-ID, credential, and post-offer questions require their separate policy.")
        result.append(
            {
                "question_key": key,
                "canonical_answer": _text(raw.get("canonical_answer"), "Canonical answer", 8000),
                "provenance": "candidate_explicit_resolution",
            }
        )
    if {item["question_key"] for item in result} != unresolved or len(result) != len(unresolved):
        raise ValueError("Every current unresolved question requires one explicit answer resolution.")
    return sorted(result, key=lambda item: item["question_key"])


def reprepare_needs_input_plan(
    *,
    prior_plan_id: str,
    expected_prior_plan_digest: str,
    idempotency_key: str,
    answer_resolutions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve normal NEEDS_INPUT answers into a new immutable Plan revision."""
    if not application_plan_v2_enabled():
        raise RuntimeError("Application Plan V2 is disabled.")
    prior = get_application_plan(_text(prior_plan_id, "Prior Application Plan id", 240))
    if str(prior["status"]) != "NEEDS_INPUT" or bool(prior["executable"]):
        raise ValueError("Only a non-executable NEEDS_INPUT Application Plan can be reprepared.")
    if prior.get("submission_authority") is not False or str(prior["snapshot"].get("expected_state")) != "NEEDS_INPUT":
        raise ValueError("Application Plan has an invalid NEEDS_INPUT authority state.")
    if str(expected_prior_plan_digest) != str(prior["plan_digest"]):
        raise ValueError("Expected Application Plan digest does not match the current immutable plan.")
    if sha256_json(plan_digest_payload(prior["snapshot"])) != str(prior["plan_digest"]):
        raise RuntimeError("Stored Application Plan snapshot digest is invalid.")
    if not list(prior["snapshot"].get("unresolved") or []) or list(prior["snapshot"].get("global_blockers") or []):
        raise ValueError("Application Plan has unresolved non-answer blockers and cannot be safely reprepared.")
    if application_plan_freshness(str(prior["plan_id"])).get("fresh") is not True:
        raise RuntimeError("Application Plan is stale and cannot be reprepared.")
    key = _text(idempotency_key, "Application Plan revision idempotency key", 240)
    if key == str(prior["idempotency_key"]):
        raise ValueError("Application Plan revision requires a new idempotency key.")
    resolutions = _resolution_inputs(prior, answer_resolutions)
    resolution_digest = sha256_json(resolutions)

    revision_id = f"application-plan-revision-{uuid4()}"
    connection = get_connection()
    try:
        ensure_schema(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        owner = current_owner(connection)
        existing = connection.execute(
            """SELECT * FROM application_plan_revisions_v2
               WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["prior_plan_id"]) != str(prior["plan_id"])
                or str(existing["prior_plan_digest"]) != str(prior["plan_digest"])
                or str(existing["resolution_digest"]) != resolution_digest
            ):
                raise ValueError(
                    "Idempotency key belongs to a different Application Plan revision."
                )
            existing_replacement_id = str(existing["replacement_plan_id"] or "")
            existing_replacement_digest = str(existing["replacement_plan_digest"] or "")
            connection.commit()
            if existing_replacement_id:
                existing_plan = get_application_plan(existing_replacement_id)
                if str(existing_plan["plan_digest"]) != existing_replacement_digest:
                    raise RuntimeError(
                        "Stored Application Plan revision replacement digest is invalid."
                    )
                existing_freshness = application_plan_freshness(existing_replacement_id)
                if (
                    existing_freshness.get("fresh") is not True
                    or existing_freshness.get("executable") is not True
                ):
                    raise RuntimeError(
                        "Resolved Application Plan revision is stale or no longer execution-ready."
                    )
                return existing_plan
        else:
            prior_revision = connection.execute(
                """SELECT revision_id FROM application_plan_revisions_v2
                   WHERE tenant_id=? AND user_id=? AND prior_plan_id=?""",
                (owner.tenant_id, owner.user_id, str(prior["plan_id"])),
            ).fetchone()
            if prior_revision is not None:
                raise ValueError(
                    "Application Plan already has an immutable replacement revision."
                )
            occupied_plan_key = connection.execute(
                """SELECT plan_id FROM application_plans_v2
                   WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
                (owner.tenant_id, owner.user_id, key),
            ).fetchone()
            if occupied_plan_key is not None:
                raise ValueError(
                    "Application Plan revision idempotency key already belongs to an Application Plan."
                )
            connection.execute(
                """INSERT INTO application_plan_revisions_v2(
                       revision_id,tenant_id,user_id,prior_plan_id,prior_plan_digest,
                       replacement_plan_id,replacement_plan_digest,idempotency_key,
                       resolution_digest,resolutions_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    revision_id,
                    owner.tenant_id,
                    owner.user_id,
                    prior["plan_id"],
                    prior["plan_digest"],
                    None,
                    None,
                    key,
                    resolution_digest,
                    json.dumps(
                        resolutions,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                ),
            )
            connection.commit()
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise ValueError(
            "Application Plan already has an immutable replacement revision."
        ) from error
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    replacement = prepare_application_plan(
        preflight_package_id=str(prior["inputs"]["preflight_package_id"]),
        idempotency_key=key,
        answer_requirements=list(prior["inputs"]["answer_requirements"]),
        permissions=dict(prior["inputs"]["permissions"]),
        provider_policy=dict(prior["inputs"]["provider_policy"]),
        secure_resolver_refs=dict(prior["inputs"]["secure_resolver_refs"]),
        resume_format=str(prior["inputs"]["resume_format"]),
        cover_letter_id=prior["inputs"].get("cover_letter_id"),
        _answer_overrides={item["question_key"]: item for item in resolutions},
    )
    if str(replacement["plan_id"]) == str(prior["plan_id"]):
        raise RuntimeError("Application Plan revision did not create a distinct immutable snapshot.")
    if str(replacement["status"]) != "READY_TO_APPLY" or replacement["executable"] is not True:
        raise RuntimeError("Resolved Application Plan did not become execution-ready.")
    replacement_freshness = application_plan_freshness(str(replacement["plan_id"]))
    if (
        replacement_freshness.get("fresh") is not True
        or replacement_freshness.get("executable") is not True
    ):
        raise RuntimeError(
            "Resolved Application Plan is stale or no longer execution-ready."
        )

    connection = get_connection()
    try:
        ensure_schema(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        owner = current_owner(connection)
        existing = connection.execute(
            """SELECT * FROM application_plan_revisions_v2
               WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if existing is None:
            raise RuntimeError("Application Plan revision reservation disappeared.")
        if (
            str(existing["prior_plan_id"]) != str(prior["plan_id"])
            or str(existing["prior_plan_digest"]) != str(prior["plan_digest"])
            or str(existing["resolution_digest"]) != resolution_digest
        ):
            raise RuntimeError("Application Plan revision reservation binding changed.")
        existing_replacement_id = str(existing["replacement_plan_id"] or "")
        existing_replacement_digest = str(existing["replacement_plan_digest"] or "")
        if existing_replacement_id:
            if (
                existing_replacement_id != str(replacement["plan_id"])
                or existing_replacement_digest != str(replacement["plan_digest"])
            ):
                raise RuntimeError(
                    "Application Plan revision already finalized to a different replacement."
                )
            connection.commit()
            return get_application_plan(existing_replacement_id)

        updated = connection.execute(
            """UPDATE application_plan_revisions_v2
               SET replacement_plan_id=?, replacement_plan_digest=?
               WHERE revision_id=? AND tenant_id=? AND user_id=?
                 AND replacement_plan_id IS NULL AND replacement_plan_digest IS NULL""",
            (
                replacement["plan_id"],
                replacement["plan_digest"],
                existing["revision_id"],
                owner.tenant_id,
                owner.user_id,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeError("Application Plan revision finalization lost its reservation.")
        connection.commit()
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise ValueError(
            "Application Plan already has an immutable replacement revision."
        ) from error
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return replacement


def _runtime_question_requirement(
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    allowed = {
        "question_key",
        "control_id",
        "question",
        "semantic_type",
        "sensitivity_class",
        "question_observation",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(
            "Unsupported runtime NEEDS_INPUT question fields: " + ", ".join(unknown)
        )
    key = canonical_question_key(
        _text(raw.get("question_key"), "Runtime question key", 160)
    )
    control_id = _text(raw.get("control_id"), "Runtime control id", 512)
    question = _text(raw.get("question"), "Runtime application question", 2000)
    semantic_type = _text(
        raw.get("semantic_type") or "unknown", "Runtime semantic type", 128
    )
    explicit_sensitivity = _text(
        raw.get("sensitivity_class"), "Runtime sensitivity class", 32
    ).upper()
    if explicit_sensitivity != "NORMAL":
        raise ValueError(
            "Runtime protected, self-ID, credential, work-authorization, and "
            "post-offer questions require their separate policy."
        )

    raw_observation = raw.get("question_observation")
    observation = dict(raw_observation) if isinstance(raw_observation, Mapping) else {}
    observation.setdefault("raw_question", question)
    observation.setdefault("label", question)
    observation.setdefault("control_id", control_id)
    observation.setdefault("apply_semantic_type", semantic_type)
    semantic = universal_questions.resolve_question(observation)

    legacy_family = answer_brain_v2.classify_question(question)
    universal_concept_id: str | None = None
    if semantic.get("state") == "RESOLVED":
        universal_concept_id = str(semantic.get("concept_id") or "").strip().casefold() or None
        semantic_sensitivity = str(semantic.get("sensitivity") or "NORMAL").upper()
        if semantic_sensitivity != "NORMAL":
            raise ValueError(
                "Runtime protected or sensitive question requires its separate policy after Universal Brain classification."
            )
        if universal_concept_id:
            legacy_family = universal_ontology.legacy_question_family(universal_concept_id)

    # Legacy classification remains accepted. Truly unclassified normal wording is
    # accepted only when Universal Brain has produced a safe canonical concept.
    if (
        legacy_family not in answer_brain_v2.NORMAL_QUESTION_FAMILIES
        or legacy_family == "unknown"
    ) and universal_concept_id is None:
        raise ValueError(
            "Runtime unclassified normal question remains unresolved after Universal Brain."
        )

    requirement_payload: dict[str, Any] = {
        "question_key": key,
        "question_family": legacy_family,
        "normalized_question": question,
        "required": True,
        "sensitivity_class": "NORMAL",
    }
    if universal_concept_id:
        requirement_payload.update(
            {
                "universal_concept_id": universal_concept_id,
                "universal_semantic_confidence": float(semantic.get("confidence") or 0.0),
                "universal_classification_lane": str(semantic.get("classification_lane") or "UNKNOWN"),
                "universal_reason_code": str(semantic.get("reason_code") or "UNKNOWN"),
                "universal_sensitivity": "NORMAL",
            }
        )
        try:
            requirement_payload["universal_binding_fingerprint"] = universal_binding.fingerprint_parts(observation)[
                "logical_field_fingerprint"
            ]
        except ValueError:
            requirement_payload["universal_binding_fingerprint"] = None

    requirement = _normalize_requirement(requirement_payload)
    if str(requirement["sensitivity_class"]) != "NORMAL":
        raise ValueError(
            "Runtime protected, self-ID, credential, work-authorization, and "
            "post-offer questions require their separate policy."
        )
    question_record: dict[str, Any] = {
        "question_key": key,
        "control_id": control_id,
        "question": question,
        "semantic_type": semantic_type,
        "sensitivity_class": "NORMAL",
    }
    if observation:
        question_record["question_observation"] = observation
    return question_record, requirement


def _normalize_runtime_needs_input_context(
    value: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(value, Mapping):
        raise TypeError("Runtime NEEDS_INPUT context must be an object.")
    allowed = {
        "session_id",
        "checkpoint_id",
        "browser_form_digest",
        "unresolved_questions",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            "Unsupported runtime NEEDS_INPUT context fields: " + ", ".join(unknown)
        )
    session_id = _text(value.get("session_id"), "Apply session id", 240)
    checkpoint_id = _text(value.get("checkpoint_id"), "Apply checkpoint id", 240)
    browser_form_digest = str(value.get("browser_form_digest") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", browser_form_digest) is None:
        raise ValueError("Runtime browser form digest must be a SHA-256 digest.")
    unresolved = value.get("unresolved_questions")
    if (
        isinstance(unresolved, (str, bytes))
        or not isinstance(unresolved, Sequence)
        or not unresolved
        or len(unresolved) > 50
    ):
        raise ValueError(
            "Runtime NEEDS_INPUT context requires one to fifty unresolved questions."
        )

    questions: list[dict[str, str]] = []
    requirements: list[dict[str, Any]] = []
    for raw in unresolved:
        if not isinstance(raw, Mapping):
            raise TypeError("Runtime unresolved questions must be objects.")
        question, requirement = _runtime_question_requirement(raw)
        questions.append(question)
        requirements.append(requirement)

    keys = [item["question_key"] for item in questions]
    controls = [item["control_id"] for item in questions]
    if len(keys) != len(set(keys)) or len(controls) != len(set(controls)):
        raise ValueError(
            "Runtime NEEDS_INPUT questions require unique question and control identities."
        )
    questions.sort(key=lambda item: item["question_key"])
    requirements.sort(key=lambda item: item["question_key"])
    return (
        {
            "session_id": session_id,
            "checkpoint_id": checkpoint_id,
            "browser_form_digest": browser_form_digest,
            "unresolved_questions": questions,
        },
        requirements,
    )


def _runtime_resolution_inputs(
    runtime_context: Mapping[str, Any],
    values: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("Runtime application answer resolutions must be a list.")
    unresolved = {
        str(item["question_key"])
        for item in list(runtime_context.get("unresolved_questions") or [])
        if isinstance(item, Mapping)
    }
    allowed_provenance = {
        "candidate_explicit_runtime_resolution",
        "universal_brain_existing_knowledge",
        "universal_brain_derived_evidence",
        "universal_brain_policy_resolution",
        "universal_brain_generated_evidence",
        "prior_approved_normal_referral_answer",
    }
    result: list[dict[str, str]] = []
    for raw in values:
        if not isinstance(raw, Mapping):
            raise ValueError("Each runtime answer resolution must be an object.")
        unknown = sorted(set(raw) - {"question_key", "canonical_answer", "provenance"})
        if unknown:
            raise ValueError(
                "Unsupported runtime answer resolution fields: " + ", ".join(unknown)
            )
        if "question_key" not in raw or "canonical_answer" not in raw:
            raise ValueError(
                "Each runtime answer resolution must contain question_key and canonical_answer."
            )
        key = canonical_question_key(
            _text(raw.get("question_key"), "Runtime question key", 160)
        )
        if key not in unresolved:
            raise ValueError(
                "Runtime answer resolution is not for the current browser question set."
            )
        provenance = str(
            raw.get("provenance") or "candidate_explicit_runtime_resolution"
        ).strip()
        if provenance not in allowed_provenance:
            raise ValueError("Unsupported runtime answer provenance.")
        result.append(
            {
                "question_key": key,
                "canonical_answer": _text(
                    raw.get("canonical_answer"), "Canonical answer", 8000
                ),
                "provenance": provenance,
            }
        )
    if {item["question_key"] for item in result} != unresolved or len(result) != len(
        unresolved
    ):
        raise ValueError(
            "Every current runtime unresolved question requires one answer."
        )
    return sorted(result, key=lambda item: item["question_key"])


def _merge_runtime_requirements(
    prior: Mapping[str, Any],
    runtime_requirements: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged = {
        str(item["question_key"]): copy.deepcopy(dict(item))
        for item in list(prior["inputs"].get("answer_requirements") or [])
        if isinstance(item, Mapping)
    }
    for observed in runtime_requirements:
        key = str(observed["question_key"])
        existing = merged.get(key)
        if existing is not None:
            if (
                str(existing.get("sensitivity_class")) != "NORMAL"
                or str(existing.get("question_family"))
                != str(observed.get("question_family"))
            ):
                raise ValueError(
                    "Runtime browser question conflicts with the immutable plan question identity."
                )
            existing["normalized_question"] = str(observed["normalized_question"])
            existing["required"] = True
            merged[key] = existing
        else:
            merged[key] = copy.deepcopy(dict(observed))
    return [merged[key] for key in sorted(merged)]


def _runtime_revision_payload_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def reprepare_runtime_needs_input_plan(
    *,
    prior_plan_id: str,
    expected_prior_plan_digest: str,
    idempotency_key: str,
    runtime_context: Mapping[str, Any],
    answer_resolutions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create one replacement Plan for browser-discovered NORMAL questions."""
    if not application_plan_v2_enabled():
        raise RuntimeError("Application Plan V2 is disabled.")
    if not runtime_application_plan_resolution_enabled():
        raise RuntimeError("Runtime Application Plan resolution is disabled.")

    prior = get_application_plan(_text(prior_plan_id, "Prior Application Plan id", 240))
    if str(prior["status"]) != "READY_TO_APPLY" or prior["executable"] is not True:
        raise ValueError(
            "Runtime NEEDS_INPUT resolution requires the exact previously executable plan."
        )
    if (
        prior.get("submission_authority") is not False
        or str(prior["snapshot"].get("expected_state")) != "READY_TO_APPLY"
    ):
        raise ValueError("Prior Application Plan has invalid execution authority.")
    if str(expected_prior_plan_digest) != str(prior["plan_digest"]):
        raise ValueError(
            "Expected Application Plan digest does not match the current immutable plan."
        )
    if sha256_json(plan_digest_payload(prior["snapshot"])) != str(prior["plan_digest"]):
        raise RuntimeError("Stored Application Plan snapshot digest is invalid.")
    if application_plan_freshness(str(prior["plan_id"])).get("fresh") is not True:
        raise RuntimeError("Application Plan is stale and cannot be runtime-revised.")

    normalized_context, runtime_requirements = _normalize_runtime_needs_input_context(
        runtime_context
    )
    resolutions = _runtime_resolution_inputs(normalized_context, answer_resolutions)
    merged_requirements = _merge_runtime_requirements(prior, runtime_requirements)

    prior_overrides = {
        canonical_question_key(str(raw_key)): copy.deepcopy(dict(value))
        for raw_key, value in dict(prior["inputs"].get("answer_overrides") or {}).items()
    }
    new_overrides = {
        item["question_key"]: {
            "canonical_answer": item["canonical_answer"],
            "provenance": item["provenance"],
        }
        for item in resolutions
    }
    combined_overrides = {**prior_overrides, **new_overrides}

    revision_payload = {
        "kind": _RUNTIME_REVISION_KIND,
        "runtime_context": normalized_context,
        "runtime_requirements": runtime_requirements,
        "resolutions": resolutions,
    }
    resolution_digest = sha256_json(revision_payload)
    revision_json = _runtime_revision_payload_json(revision_payload)
    key = _text(idempotency_key, "Application Plan revision idempotency key", 240)
    if key == str(prior["idempotency_key"]):
        raise ValueError("Application Plan revision requires a new idempotency key.")

    revision_id = f"application-plan-revision-{uuid4()}"
    connection = get_connection()
    try:
        ensure_schema(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        owner = current_owner(connection)
        existing = connection.execute(
            """SELECT * FROM application_plan_revisions_v2
               WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["prior_plan_id"]) != str(prior["plan_id"])
                or str(existing["prior_plan_digest"]) != str(prior["plan_digest"])
                or str(existing["resolution_digest"]) != resolution_digest
                or str(existing["resolutions_json"]) != revision_json
            ):
                raise ValueError(
                    "Idempotency key belongs to a different Application Plan revision."
                )
            replacement_id = str(existing["replacement_plan_id"] or "")
            replacement_digest = str(existing["replacement_plan_digest"] or "")
            connection.commit()
            if replacement_id:
                replacement = get_application_plan(replacement_id)
                if str(replacement["plan_digest"]) != replacement_digest:
                    raise RuntimeError(
                        "Stored runtime Application Plan replacement digest is invalid."
                    )
                freshness = application_plan_freshness(replacement_id)
                if freshness.get("fresh") is not True or freshness.get("executable") is not True:
                    raise RuntimeError(
                        "Runtime Application Plan replacement is stale or non-executable."
                    )
                return replacement
        else:
            prior_revision = connection.execute(
                """SELECT * FROM application_plan_revisions_v2
                   WHERE tenant_id=? AND user_id=? AND prior_plan_id=?""",
                (owner.tenant_id, owner.user_id, str(prior["plan_id"])),
            ).fetchone()
            if prior_revision is not None:
                if str(prior_revision["replacement_plan_id"] or ""):
                    raise ValueError(
                        "Application Plan already has an immutable replacement revision."
                    )
                rebound = connection.execute(
                    """UPDATE application_plan_revisions_v2
                          SET idempotency_key=?,resolution_digest=?,resolutions_json=?,
                              created_at=CURRENT_TIMESTAMP
                        WHERE revision_id=? AND tenant_id=? AND user_id=?
                          AND replacement_plan_id IS NULL
                          AND replacement_plan_digest IS NULL
                          AND created_at<=datetime('now','-5 minutes')""",
                    (
                        key,
                        resolution_digest,
                        revision_json,
                        str(prior_revision["revision_id"]),
                        owner.tenant_id,
                        owner.user_id,
                    ),
                )
                if rebound.rowcount != 1:
                    raise ValueError(
                        "Application Plan already has an active replacement revision reservation."
                    )
                revision_id = str(prior_revision["revision_id"])
                connection.commit()
            else:
                occupied_plan_key = connection.execute(
                    """SELECT plan_id FROM application_plans_v2
                       WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
                    (owner.tenant_id, owner.user_id, key),
                ).fetchone()
                if occupied_plan_key is not None:
                    raise ValueError(
                        "Application Plan revision idempotency key already belongs to an Application Plan."
                    )
                connection.execute(
                    """INSERT INTO application_plan_revisions_v2(
                           revision_id,tenant_id,user_id,prior_plan_id,prior_plan_digest,
                           replacement_plan_id,replacement_plan_digest,idempotency_key,
                           resolution_digest,resolutions_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        revision_id,
                        owner.tenant_id,
                        owner.user_id,
                        prior["plan_id"],
                        prior["plan_digest"],
                        None,
                        None,
                        key,
                        resolution_digest,
                        revision_json,
                    ),
                )
                connection.commit()
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise ValueError(
            "Application Plan already has an immutable replacement revision."
        ) from error
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    replacement = prepare_application_plan(
        preflight_package_id=str(prior["inputs"]["preflight_package_id"]),
        idempotency_key=key,
        answer_requirements=merged_requirements,
        permissions=dict(prior["inputs"]["permissions"]),
        provider_policy=dict(prior["inputs"]["provider_policy"]),
        secure_resolver_refs=dict(prior["inputs"]["secure_resolver_refs"]),
        resume_format=str(prior["inputs"]["resume_format"]),
        cover_letter_id=prior["inputs"].get("cover_letter_id"),
        _answer_overrides=combined_overrides,
    )
    if str(replacement["plan_id"]) == str(prior["plan_id"]):
        raise RuntimeError(
            "Runtime Application Plan revision did not create a distinct snapshot."
        )
    if str(replacement["status"]) != "READY_TO_APPLY" or replacement["executable"] is not True:
        raise RuntimeError(
            "Runtime-resolved Application Plan did not become execution-ready."
        )
    replacement_freshness = application_plan_freshness(str(replacement["plan_id"]))
    if (
        replacement_freshness.get("fresh") is not True
        or replacement_freshness.get("executable") is not True
    ):
        raise RuntimeError(
            "Runtime-resolved Application Plan is stale or non-executable."
        )

    connection = get_connection()
    try:
        ensure_schema(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        owner = current_owner(connection)
        existing = connection.execute(
            """SELECT * FROM application_plan_revisions_v2
               WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if existing is None:
            raise RuntimeError("Application Plan revision reservation disappeared.")
        if (
            str(existing["prior_plan_id"]) != str(prior["plan_id"])
            or str(existing["prior_plan_digest"]) != str(prior["plan_digest"])
            or str(existing["resolution_digest"]) != resolution_digest
            or str(existing["resolutions_json"]) != revision_json
        ):
            raise RuntimeError(
                "Application Plan runtime revision reservation binding changed."
            )
        existing_replacement_id = str(existing["replacement_plan_id"] or "")
        existing_replacement_digest = str(existing["replacement_plan_digest"] or "")
        if existing_replacement_id:
            if (
                existing_replacement_id != str(replacement["plan_id"])
                or existing_replacement_digest != str(replacement["plan_digest"])
            ):
                raise RuntimeError(
                    "Application Plan runtime revision finalized to another replacement."
                )
            connection.commit()
            return get_application_plan(existing_replacement_id)

        updated = connection.execute(
            """UPDATE application_plan_revisions_v2
               SET replacement_plan_id=?, replacement_plan_digest=?
               WHERE revision_id=? AND tenant_id=? AND user_id=?
                 AND replacement_plan_id IS NULL AND replacement_plan_digest IS NULL""",
            (
                replacement["plan_id"],
                replacement["plan_digest"],
                existing["revision_id"],
                owner.tenant_id,
                owner.user_id,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeError(
                "Application Plan runtime revision finalization lost its reservation."
            )
        connection.commit()
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise ValueError(
            "Application Plan already has an immutable replacement revision."
        ) from error
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return replacement


def runtime_plan_supersession(plan_id: str) -> dict[str, Any] | None:
    """Return transport lineage only for post-handoff runtime revisions."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM application_plan_revisions_v2
               WHERE tenant_id=? AND user_id=? AND replacement_plan_id=?""",
            (owner.tenant_id, owner.user_id, str(plan_id)),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["resolutions_json"]))
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "Stored Application Plan revision payload is invalid."
            ) from error
        if not isinstance(payload, Mapping) or payload.get("kind") != _RUNTIME_REVISION_KIND:
            return None
        if sha256_json(payload) != str(row["resolution_digest"]):
            raise RuntimeError(
                "Stored runtime Application Plan revision digest is invalid."
            )
        replacement = connection.execute(
            """SELECT plan_digest FROM application_plans_v2
               WHERE plan_id=? AND tenant_id=? AND user_id=?""",
            (str(plan_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        prior = connection.execute(
            """SELECT plan_digest FROM application_plans_v2
               WHERE plan_id=? AND tenant_id=? AND user_id=?""",
            (str(row["prior_plan_id"]), owner.tenant_id, owner.user_id),
        ).fetchone()
        if replacement is None or prior is None:
            raise RuntimeError(
                "Runtime Application Plan revision references a missing plan."
            )
        if (
            str(replacement["plan_digest"]) != str(row["replacement_plan_digest"])
            or str(prior["plan_digest"]) != str(row["prior_plan_digest"])
        ):
            raise RuntimeError(
                "Runtime Application Plan revision lineage digest is invalid."
            )
        context = payload.get("runtime_context")
        if not isinstance(context, Mapping):
            raise RuntimeError(
                "Runtime Application Plan revision lacks browser context."
            )
        return {
            "version": RUNTIME_SUPERSESSION_VERSION,
            "prior_plan_id": str(row["prior_plan_id"]),
            "prior_plan_digest": str(row["prior_plan_digest"]),
            "replacement_plan_id": str(row["replacement_plan_id"]),
            "replacement_plan_digest": str(row["replacement_plan_digest"]),
            "resolution_digest": str(row["resolution_digest"]),
            "session_id": str(context["session_id"]),
            "checkpoint_id": str(context["checkpoint_id"]),
            "browser_form_digest": str(context["browser_form_digest"]),
            "unresolved_questions": copy.deepcopy(
                list(context["unresolved_questions"])
            ),
        }
    finally:
        connection.close()


def _runtime_replacement_plan_id(prior_plan_id: str) -> str | None:
    """Return a finalized immutable replacement; reservations do not revoke."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT replacement_plan_id FROM application_plan_revisions_v2
               WHERE tenant_id=? AND user_id=? AND prior_plan_id=?
                 AND replacement_plan_id IS NOT NULL
                 AND replacement_plan_digest IS NOT NULL""",
            (owner.tenant_id, owner.user_id, str(prior_plan_id)),
        ).fetchone()
        return str(row["replacement_plan_id"]) if row is not None else None
    finally:
        connection.close()


def runtime_plan_replacement(prior_plan_id: str) -> dict[str, Any] | None:
    """Return finalized runtime lineage when given its superseded prior plan."""
    replacement_id = _runtime_replacement_plan_id(prior_plan_id)
    if replacement_id is None:
        return None
    return runtime_plan_supersession(replacement_id)


def runtime_plan_is_superseded(plan_id: str) -> bool:
    return _runtime_replacement_plan_id(plan_id) is not None

def get_application_plan(
    plan_id: str,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        owner = current_owner_readonly(connection)
        row = connection.execute(
            """SELECT * FROM application_plans_v2
               WHERE plan_id=? AND tenant_id=? AND user_id=?""",
            (str(plan_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError(
                "Application Plan is not owned by the current tenant user."
            )
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
            cover_letter_id=(str(inputs.get("cover_letter_id") or "").strip() or None),
            answer_overrides=dict(inputs.get("answer_overrides") or {}),
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
    if runtime_plan_is_superseded(plan_id):
        raise RuntimeError("Application Plan is superseded by a runtime resolution.")
    freshness = application_plan_freshness(plan_id)
    if plan["executable"] is not True or str(plan["status"]) != "READY_TO_APPLY":
        raise ValueError("Application Plan is not execution-ready.")
    if freshness["fresh"] is not True:
        raise RuntimeError("Application Plan is stale.")
    plan["runtime_supersession"] = runtime_plan_supersession(plan_id)
    return plan
