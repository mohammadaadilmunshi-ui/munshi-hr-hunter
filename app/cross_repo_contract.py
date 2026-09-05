"""Pure cross-repository contract primitives for MUNSHI Career OS.

This module is deliberately inert. It performs validation, canonicalization,
digesting, correlation checks, and CRM projection only. It has no database,
HTTP, browser, provider, credential, Gmail, or n8n execution authority.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Final

PROFILE_SNAPSHOT_VERSION: Final = "munshi-candidate-profile-snapshot-v1"
EXECUTION_RECEIPT_VERSION: Final = "munshi-apply-execution-receipt-v1"
PROFILE_AUTHORITY: Final = "munshi-hr-hunter"
APPLY_EVENT_SOURCE: Final = "munshi-apply"
PROFILE_REVISION_SCOPE: Final = "SOURCE_EXTRACTION"

TRUST_LEVELS: Final = frozenset(
    {
        "VERIFIED",
        "USER_CONFIRMED",
        "DOCUMENT_CONFIRMED",
        "DERIVED",
        "GENERATED",
        "LEARNED",
        "UNKNOWN",
    }
)

FACT_CATEGORIES: Final = frozenset(
    {
        "IDENTITY",
        "CONTACT",
        "ADDRESS",
        "EDUCATION",
        "EMPLOYMENT",
        "PROJECT",
        "SKILL",
        "CERTIFICATION",
        "LANGUAGE",
        "AVAILABILITY",
        "WORK_PREFERENCE",
        "WORK_AUTHORIZATION",
        "SPONSORSHIP",
        "VOLUNTARY_DEMOGRAPHIC",
        "SAVED_ANSWER",
        "WRITING_PREFERENCE",
    }
)

# These names intentionally match MUNSHI Apply's contract vocabulary.
APPLY_EXECUTION_EVENT_TYPES: Final = frozenset(
    {
        "APPLICATION_READY",
        "CHECKPOINT_REQUIRED",
        "SECURITY_CHECKPOINT",
        "INTERACTION_FAILED",
        "RECOVERY_SUCCEEDED",
        "APPLICATION_SUBMITTED",
        "APPLICATION_CONFIRMED",
        "APPLICATION_COMPLETED",
    }
)

_SUBMISSION_EVENTS: Final = frozenset(
    {"APPLICATION_SUBMITTED", "APPLICATION_CONFIRMED", "APPLICATION_COMPLETED"}
)


def canonical_json(value: Any) -> str:
    """Serialize a contract object deterministically for hashing/signing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, field: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise ValueError(f"{field} is too long")
    return cleaned


def _sha256(value: Any, field: str = "sha256") -> str:
    cleaned = _text(value, field, max_length=64)
    if len(cleaned) != 64 or any(ch not in "0123456789abcdef" for ch in cleaned):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return cleaned


def _iso_datetime(value: Any, field: str) -> str:
    cleaned = _text(value, field, max_length=64)
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return cleaned


def _nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def profile_digest_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the stable profile state used for cross-repository digesting.

    `generated_at` is observational metadata, not candidate truth. Excluding it
    keeps identical truth snapshots content-addressable across repeated exports.
    """
    return {
        key: value
        for key, value in snapshot.items()
        if key not in {"generated_at", "profile_digest"}
    }


def validate_profile_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate an immutable Hunter-owned profile projection for Apply.

    Protected facts must travel by reference rather than embedding plaintext in
    this generic bridge contract. This prevents the contract from becoming a
    second credential/sensitive-data store.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("profile snapshot must be an object")
    if snapshot.get("contract_version") != PROFILE_SNAPSHOT_VERSION:
        raise ValueError("unsupported profile snapshot version")
    if snapshot.get("authority") != PROFILE_AUTHORITY:
        raise ValueError("profile authority must remain munshi-hr-hunter")
    if snapshot.get("projection_mode") != "READ_ONLY":
        raise ValueError("profile projection must be READ_ONLY")
    if snapshot.get("revision_scope") != PROFILE_REVISION_SCOPE:
        raise ValueError("profile revision scope must remain SOURCE_EXTRACTION")

    normalized: dict[str, Any] = {
        "contract_version": PROFILE_SNAPSHOT_VERSION,
        "authority": PROFILE_AUTHORITY,
        "projection_mode": "READ_ONLY",
        "revision_scope": PROFILE_REVISION_SCOPE,
        "tenant_id": _text(snapshot.get("tenant_id"), "tenant_id", max_length=128),
        "user_id": _text(snapshot.get("user_id"), "user_id", max_length=128),
        "profile_id": _text(snapshot.get("profile_id"), "profile_id", max_length=128),
        "profile_revision": _nonnegative_int(snapshot.get("profile_revision"), "profile_revision"),
        "override_revision": _nonnegative_int(snapshot.get("override_revision"), "override_revision"),
        "candidate_details_revision": _nonnegative_int(
            snapshot.get("candidate_details_revision"), "candidate_details_revision"
        ),
        "source_extraction_id": _text(
            snapshot.get("source_extraction_id"), "source_extraction_id", max_length=128
        ),
        "source_profile_sha256": _sha256(
            snapshot.get("source_profile_sha256"), "source_profile_sha256"
        ),
        "source_resume_sha256": _sha256(
            snapshot.get("source_resume_sha256"), "source_resume_sha256"
        ),
        "generated_at": _iso_datetime(snapshot.get("generated_at"), "generated_at"),
    }
    if normalized["profile_revision"] < 1:
        raise ValueError("profile_revision must be >= 1")

    raw_facts = snapshot.get("facts")
    if not isinstance(raw_facts, list):
        raise ValueError("facts must be a list")
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    facts: list[dict[str, Any]] = []
    for raw in raw_facts:
        if not isinstance(raw, dict):
            raise ValueError("each profile fact must be an object")
        fact_id = _text(raw.get("fact_id"), "fact_id", max_length=128)
        key = _text(raw.get("key"), "key", max_length=256)
        if fact_id in seen_ids:
            raise ValueError("duplicate fact_id")
        if key in seen_keys:
            raise ValueError("duplicate profile fact key")
        seen_ids.add(fact_id)
        seen_keys.add(key)
        category = _text(raw.get("category"), "category", max_length=64)
        trust = _text(raw.get("trust_level"), "trust_level", max_length=64)
        if category not in FACT_CATEGORIES:
            raise ValueError(f"unsupported fact category: {category}")
        if trust not in TRUST_LEVELS:
            raise ValueError(f"unsupported trust level: {trust}")
        protected = raw.get("protected")
        if not isinstance(protected, bool):
            raise ValueError("protected must be boolean")
        fact: dict[str, Any] = {
            "fact_id": fact_id,
            "key": key,
            "category": category,
            "trust_level": trust,
            "protected": protected,
            "source": _text(raw.get("source"), "source", max_length=256),
        }
        if protected:
            if "value" in raw:
                raise ValueError("protected profile facts must not embed plaintext value")
            fact["value_reference"] = _text(
                raw.get("value_reference"), "value_reference", max_length=256
            )
        else:
            if "value_reference" in raw:
                raise ValueError("non-protected facts must carry value, not value_reference")
            value = raw.get("value")
            if not isinstance(value, (str, int, float, bool, list)) or isinstance(value, dict):
                raise ValueError("unsupported profile fact value")
            if isinstance(value, list) and not all(isinstance(item, str) for item in value):
                raise ValueError("profile fact list values must contain strings only")
            fact["value"] = value
        facts.append(fact)

    normalized["facts"] = sorted(facts, key=lambda item: (item["key"], item["fact_id"]))
    computed = sha256_json(profile_digest_payload(normalized))
    supplied = snapshot.get("profile_digest")
    if supplied is not None and _sha256(supplied, "profile_digest") != computed:
        raise ValueError("profile_digest does not match canonical snapshot")
    normalized["profile_digest"] = computed
    return normalized


def validate_resume_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    """Validate an immutable resume reference prepared by Hunter for Apply."""
    if not isinstance(artifact, dict):
        raise ValueError("resume artifact must be an object")
    normalized = {
        "artifact_id": _text(artifact.get("artifact_id"), "artifact_id", max_length=128),
        "kind": _text(artifact.get("kind"), "kind", max_length=64),
        "sha256": _sha256(artifact.get("sha256")),
        "mime_type": _text(artifact.get("mime_type"), "mime_type", max_length=128),
        "source_preparation_id": _text(
            artifact.get("source_preparation_id"), "source_preparation_id", max_length=128
        ),
        "source_extraction_id": _text(
            artifact.get("source_extraction_id"), "source_extraction_id", max_length=128
        ),
        "profile_revision": _nonnegative_int(artifact.get("profile_revision"), "profile_revision"),
        "profile_digest": _sha256(artifact.get("profile_digest"), "profile_digest"),
        "job_id": _text(artifact.get("job_id"), "job_id", max_length=128),
    }
    if normalized["profile_revision"] < 1:
        raise ValueError("profile_revision must be >= 1")
    if "size_bytes" in artifact:
        size = artifact["size_bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        normalized["size_bytes"] = size
    return normalized


def validate_execution_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Validate an Apply-owned execution event before Hunter CRM projection."""
    if not isinstance(receipt, dict):
        raise ValueError("execution receipt must be an object")
    if receipt.get("contract_version") != EXECUTION_RECEIPT_VERSION:
        raise ValueError("unsupported execution receipt version")
    if receipt.get("source") != APPLY_EVENT_SOURCE:
        raise ValueError("execution receipt source must be munshi-apply")

    event_type = _text(receipt.get("event_type"), "event_type", max_length=64)
    if event_type not in APPLY_EXECUTION_EVENT_TYPES:
        raise ValueError("unsupported Apply execution event")

    normalized: dict[str, Any] = {
        "contract_version": EXECUTION_RECEIPT_VERSION,
        "source": APPLY_EVENT_SOURCE,
        "event_id": _text(receipt.get("event_id"), "event_id", max_length=128),
        "correlation_id": _text(receipt.get("correlation_id"), "correlation_id", max_length=128),
        "tenant_id": _text(receipt.get("tenant_id"), "tenant_id", max_length=128),
        "user_id": _text(receipt.get("user_id"), "user_id", max_length=128),
        "handoff_id": _text(receipt.get("handoff_id"), "handoff_id", max_length=128),
        "handoff_body_sha256": _sha256(
            receipt.get("handoff_body_sha256"), "handoff_body_sha256"
        ),
        "preparation_id": _text(receipt.get("preparation_id"), "preparation_id", max_length=128),
        "application_id": _text(receipt.get("application_id"), "application_id", max_length=128),
        "runtime_application_id": _text(
            receipt.get("runtime_application_id"), "runtime_application_id", max_length=256
        ),
        "provider": _text(receipt.get("provider"), "provider", max_length=64),
        "event_type": event_type,
        "occurred_at": _iso_datetime(receipt.get("occurred_at"), "occurred_at"),
    }
    payload = receipt.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("receipt payload must be an object")
    normalized["payload"] = payload

    if event_type == "APPLICATION_SUBMITTED":
        if payload.get("submit_attempted") is not True or payload.get("submit_succeeded") is not True:
            raise ValueError("APPLICATION_SUBMITTED requires verified successful submit evidence")
    elif payload.get("submit_succeeded") is True:
        raise ValueError("non-submission events cannot assert successful submission")

    if event_type in {"APPLICATION_CONFIRMED", "APPLICATION_COMPLETED"}:
        if payload.get("confirmation_observed") is not True:
            raise ValueError(f"{event_type} requires confirmation evidence")
    elif payload.get("confirmation_observed") is True:
        raise ValueError("non-confirmation events cannot assert confirmation evidence")

    normalized["receipt_digest"] = sha256_json(normalized)
    return normalized


def validate_receipt_correlation(
    receipt: dict[str, Any],
    *,
    tenant_id: str,
    user_id: str,
    handoff_id: str,
    handoff_body_sha256: str,
    preparation_id: str,
    application_id: str,
) -> dict[str, Any]:
    """Validate a receipt and bind it to the exact Hunter handoff context."""
    validated = validate_execution_receipt(receipt)
    expected = {
        "tenant_id": _text(tenant_id, "tenant_id", max_length=128),
        "user_id": _text(user_id, "user_id", max_length=128),
        "handoff_id": _text(handoff_id, "handoff_id", max_length=128),
        "handoff_body_sha256": _sha256(handoff_body_sha256, "handoff_body_sha256"),
        "preparation_id": _text(preparation_id, "preparation_id", max_length=128),
        "application_id": _text(application_id, "application_id", max_length=128),
    }
    for field, value in expected.items():
        if validated[field] != value:
            raise ValueError(f"execution receipt {field} does not match the Hunter handoff")
    return validated


def crm_projection_for_receipt(receipt: dict[str, Any]) -> str:
    """Return the highest CRM projection a validated Apply event can justify.

    This function intentionally has no transition side effect. The caller may
    persist the returned projection only after tenant/correlation checks.
    """
    validated = validate_execution_receipt(receipt)
    event_type = validated["event_type"]
    if event_type == "APPLICATION_READY":
        return "READY_FOR_REVIEW"
    if event_type in {"CHECKPOINT_REQUIRED", "SECURITY_CHECKPOINT"}:
        return "NEEDS_INPUT"
    if event_type == "INTERACTION_FAILED":
        return "APPLY_RUNTIME_FAILED"
    if event_type == "RECOVERY_SUCCEEDED":
        return "APPLY_RUNTIME_ACTIVE"
    if event_type == "APPLICATION_SUBMITTED":
        return "SUBMITTED"
    if event_type in {"APPLICATION_CONFIRMED", "APPLICATION_COMPLETED"}:
        return "SUBMITTED_CONFIRMED"
    raise ValueError("event has no CRM projection")


def event_can_assert_submission(receipt: dict[str, Any]) -> bool:
    """True only for a validated Apply-owned submission/confirmation event."""
    validated = validate_execution_receipt(receipt)
    return validated["event_type"] in _SUBMISSION_EVENTS
