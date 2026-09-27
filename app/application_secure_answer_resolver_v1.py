"""Plan-bound resolver for protected/self-ID application values.

This module is deliberately provider-neutral. It never inspects Workday, ATS
selectors, employer names, or job-specific answer text. Hunter remains the
candidate-truth authority; Apply receives one ephemeral value only after the
immutable Application Plan has already authorized that exact protected field.

Protected plaintext never enters Application Plan JSON, normal Answer Brain
storage, logs, or public traces.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app import candidate_ontology_v1 as ontology
from app import phase45_truth_binding as truth_binding
from app import resume_profile_details_v31 as profile_details

RESOLVER_VERSION = "application-secure-answer-resolver-v1"
ALLOWED_SENSITIVITIES = frozenset({"PROTECTED", "SELF_ID"})
SECURE_SCHEME_PREFIX = "hunter-secure://candidate-truth/"
VAULT_REFERENCE_PREFIX = "hunter-vault://candidate-profile-details-v31/"


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _sensitivity(requirement: Mapping[str, Any]) -> str:
    universal = str(requirement.get("universal_sensitivity") or "").strip().upper()
    current = str(requirement.get("sensitivity_class") or "").strip().upper()
    if universal in {"SELF_ID", "PROTECTED", "POST_OFFER", "CREDENTIAL", "SECRET"}:
        return universal
    return current or "NORMAL"


def _candidate_fact_paths(requirement: Mapping[str, Any]) -> tuple[str, ...]:
    paths: list[str] = []

    concept_id = str(requirement.get("universal_concept_id") or "").strip()
    if concept_id:
        try:
            concept = ontology.require_concept(concept_id)
        except LookupError:
            concept = None
        if concept is not None:
            paths.extend(
                str(item).strip()
                for item in concept.candidate_fact_paths
                if str(item).strip()
            )

    profile_fact_key = str(requirement.get("profile_fact_key") or "").strip()
    if profile_fact_key:
        paths.append(profile_fact_key)

    question_key = str(requirement.get("question_key") or "").strip()
    if question_key.startswith("application_defaults."):
        paths.append(question_key)

    # Preserve declaration order but remove duplicates.
    return tuple(dict.fromkeys(paths))


def _current_snapshot() -> dict[str, Any]:
    return truth_binding.current_candidate_profile_snapshot()


def _fact_for_requirement(
    requirement: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
) -> dict[str, Any] | None:
    if _sensitivity(requirement) not in ALLOWED_SENSITIVITIES:
        return None

    paths = _candidate_fact_paths(requirement)
    if not paths:
        return None

    matches: list[dict[str, Any]] = []
    for raw in snapshot.get("facts") or []:
        if not isinstance(raw, Mapping):
            continue
        fact = dict(raw)
        if str(fact.get("key") or "") not in paths:
            continue
        if fact.get("protected") is not True:
            continue
        if str(fact.get("trust_level") or "").upper() != "USER_CONFIRMED":
            continue
        reference = str(fact.get("value_reference") or "")
        expected = VAULT_REFERENCE_PREFIX + str(fact.get("key") or "")
        if reference != expected:
            continue
        matches.append(fact)

    # Ambiguous protected sources fail closed instead of selecting one.
    if len(matches) != 1:
        return None
    return matches[0]


def secure_reference_for_requirement(
    requirement: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any] | None = None,
) -> str | None:
    """Return a value-free secure execution ref only for an existing protected fact."""
    sensitivity = _sensitivity(requirement)
    if sensitivity not in ALLOWED_SENSITIVITIES:
        return None
    question_key = str(requirement.get("question_key") or "").strip()
    if not question_key:
        return None
    active = dict(snapshot or _current_snapshot())
    fact = _fact_for_requirement(requirement, snapshot=active)
    if fact is None:
        return None
    return SECURE_SCHEME_PREFIX + question_key.replace(".", "/")


def automatic_secure_references(
    requirements: Sequence[Mapping[str, Any]],
    *,
    permissions: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Build only refs the current plan permissions and Candidate Truth can support."""
    active = dict(snapshot or _current_snapshot())
    result: dict[str, str] = {}
    for requirement in requirements:
        sensitivity = _sensitivity(requirement)
        allowed = (
            sensitivity == "PROTECTED"
            and permissions.get("protected_fact_execution") is True
        ) or (
            sensitivity == "SELF_ID"
            and permissions.get("self_id_execution") is True
        )
        if not allowed:
            continue
        reference = secure_reference_for_requirement(
            requirement,
            snapshot=active,
        )
        if reference:
            result[str(requirement["question_key"])] = reference
    return result


def validate_secure_reference(
    requirement: Mapping[str, Any],
    reference: str,
    *,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a secure ref without exposing protected plaintext."""
    active = dict(snapshot or _current_snapshot())
    expected = secure_reference_for_requirement(requirement, snapshot=active)
    if not expected or str(reference or "") != expected:
        raise PermissionError(
            "Secure application answer reference is not backed by current Candidate Truth."
        )
    fact = _fact_for_requirement(requirement, snapshot=active)
    if fact is None:
        raise PermissionError("Protected Candidate Truth fact is unavailable.")
    return {
        "resolver_version": RESOLVER_VERSION,
        "question_key": str(requirement["question_key"]),
        "sensitivity": _sensitivity(requirement),
        "fact_key": str(fact["key"]),
        "fact_id": str(fact.get("fact_id") or ""),
        "value_reference": str(fact["value_reference"]),
        "profile_revision": int(active["profile_revision"]),
        "profile_digest": str(active["profile_digest"]),
        "candidate_details_revision": int(active.get("candidate_details_revision") or 0),
    }


def resolve_value(
    requirement: Mapping[str, Any],
    reference: str,
    *,
    expected_candidate_truth_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve exactly one protected value after immutable plan binding checks.

    The caller must already have authenticated/authorized the exact Application
    Plan. This function additionally proves Candidate Truth has not changed.
    """
    active = _current_snapshot()
    for field in ("source_extraction_id", "profile_revision", "profile_digest"):
        if str(active.get(field) or "") != str(
            expected_candidate_truth_binding.get(field) or ""
        ):
            raise RuntimeError("Protected Candidate Truth changed after plan creation.")

    meta = validate_secure_reference(requirement, reference, snapshot=active)
    details = profile_details.load_candidate_profile_details_envelope()
    if int(details.revision) != int(active.get("candidate_details_revision") or 0):
        raise RuntimeError("Encrypted candidate-details revision changed after plan creation.")

    fact_key = str(meta["fact_key"])
    prefix = "application_defaults."
    if not fact_key.startswith(prefix):
        raise PermissionError("Protected resolver only serves application-default facts.")
    field = fact_key[len(prefix):]
    values = details.values.model_dump()
    if field not in values:
        raise LookupError("Protected Candidate Truth field is unsupported.")
    value = values[field]
    if not _present(value):
        raise LookupError("Protected Candidate Truth value is not configured.")

    return {
        **meta,
        "value": value,
        "value_type": type(value).__name__,
        "provenance": "candidate_truth_protected_domain",
    }


def public_metadata(result: Mapping[str, Any]) -> dict[str, Any]:
    """Value-free observability projection."""
    return {
        key: value
        for key, value in dict(result).items()
        if key != "value"
    } | {"value_exposed_in_trace": False}
