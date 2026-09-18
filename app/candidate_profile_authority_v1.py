"""One read model for MUNSHI's Master Resume and Candidate Truth profile.

Historically the product Settings card read a legacy generated-artifact
"master resume" designation while Profile/Resume Studio used the active Native
Resume source.  That split could report ``Master resume: not configured`` even
when the candidate had already uploaded and confirmed a Master Resume.

This module does not create or mutate profile data.  It projects the authority
already used by Profile V3, Candidate Truth, and Native Resume into one stable
product read model.
"""
from __future__ import annotations

from typing import Any

from app import native_resume_service_v3 as resume_v3
from app import phase45_truth_binding as truth_binding
from app import profile_truth_overrides_v1 as profile_overrides

AUTHORITY_VERSION = "candidate-profile-authority-v1"


def _meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _draft_fact_count(profile: dict[str, Any]) -> int:
    """Count useful leaf values without pretending a draft is Candidate Truth."""
    total = 0

    def walk(value: Any) -> None:
        nonlocal total
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "extraction_warnings":
                    continue
                walk(child)
            return
        if isinstance(value, list):
            for child in value:
                walk(child)
            return
        if _meaningful(value):
            total += 1

    walk(profile)
    return total


def _latest_profile_for_source(source: dict[str, Any]) -> dict[str, Any]:
    if not source:
        return {}
    try:
        return resume_v3.latest_profile_for_source(str(source["source_id"])) or {}
    except (LookupError, RuntimeError, ValueError):
        return {}


def authority_snapshot() -> dict[str, Any]:
    """Return the canonical user-facing profile/master-resume state.

    ``master_resume.configured`` is based on the active candidate-confirmed
    source used by Resume Studio, not on the retired n8n artifact designation.
    ``structured_fact_count`` comes from the confirmed Candidate Truth snapshot
    whenever possible.  Draft extraction values are reported separately.
    """
    source = resume_v3.v2.v1.active_source()
    extracted = _latest_profile_for_source(source)
    resolved_profile: dict[str, Any] = {}
    if extracted:
        try:
            resolved_profile = profile_overrides.resolve_profile(extracted)
        except (KeyError, LookupError, RuntimeError, TypeError, ValueError):
            # Read-only readiness must remain visible even if an optional override
            # record is malformed. Candidate Truth confirmation still remains a
            # separate authoritative gate below.
            resolved_profile = dict(extracted.get("profile") or {})

    truth: dict[str, Any] | None = None
    truth_error: str | None = None
    if source:
        try:
            truth = truth_binding.current_candidate_profile_snapshot()
        except (LookupError, RuntimeError, ValueError) as error:
            truth_error = str(error)

    confirmed = bool(truth)
    draft_fact_count = _draft_fact_count(resolved_profile) if resolved_profile else 0
    structured_fact_count = len(truth.get("facts") or []) if truth else 0

    if not source:
        state = "NEEDS_MASTER_RESUME"
    elif not extracted:
        state = "NEEDS_PROFILE_BUILD"
    elif not confirmed:
        state = "NEEDS_PROFILE_CONFIRMATION"
    else:
        state = "READY"

    warnings: list[str] = []
    if source and extracted and str(extracted.get("source_sha256") or "") != str(
        source.get("content_sha256") or ""
    ):
        warnings.append("Profile extraction does not match the active Master Resume digest.")
    if truth_error and state != "READY":
        warnings.append(truth_error)

    return {
        "version": AUTHORITY_VERSION,
        "state": state,
        "ready_for_resume_generation": state == "READY",
        "ready_for_application_planning": state == "READY",
        "master_resume": {
            "configured": bool(source),
            "source_id": source.get("source_id") if source else None,
            "label": source.get("label") if source else None,
            "source_kind": source.get("source_kind") if source else None,
            "sha256": source.get("content_sha256") if source else None,
            "updated_at": source.get("updated_at") if source else None,
        },
        "profile": {
            "extraction_id": extracted.get("extraction_id") if extracted else None,
            "status": str(extracted.get("status") or "") if extracted else "",
            "confirmed": confirmed,
            "profile_revision": truth.get("profile_revision") if truth else None,
            "profile_digest": truth.get("profile_digest") if truth else None,
            "structured_fact_count": structured_fact_count,
            "draft_fact_count": draft_fact_count,
        },
        "structured_fact_count": structured_fact_count,
        "warnings": warnings,
    }


def master_resume() -> dict[str, Any]:
    """Compatibility-shaped view of the actual active Master Resume source."""
    record = authority_snapshot()["master_resume"]
    if not record["configured"]:
        return {}
    return {
        "source_id": record["source_id"],
        "label": record["label"] or "Master Resume",
        "source_kind": record["source_kind"],
        "sha256": record["sha256"],
        "updated_at": record["updated_at"],
        "source": "native_resume_sources",
    }


def candidate_facts() -> list[dict[str, Any]]:
    """Return current Candidate Truth facts for product status/read-only UI."""
    try:
        snapshot = truth_binding.current_candidate_profile_snapshot()
    except (LookupError, RuntimeError, ValueError):
        return []
    return [dict(item) for item in snapshot.get("facts") or []]
