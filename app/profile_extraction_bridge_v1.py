"""Profile build bridge: deterministic local extraction over Resume Studio V3 persistence."""
from __future__ import annotations

from typing import Any

from app import native_resume_service_v3 as v3
from app.profile_local_extractor_v1 import extract_profile_data

LOCAL_PROFILE_MODEL = "munshi-local-evidence-profile-v1"


def extract_profile_from_source(source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build and persist a reviewable profile without requiring an AI credential.

    The source remains the candidate-confirmed Master Resume. This function only
    restructures explicit resume evidence and persists it through V3's existing
    owner-scoped profile snapshot path. Voluntary self-ID and unsupported ATS
    defaults are never inferred.
    """
    source = source or v3.active_source()
    if not source or not str(source.get("content_text") or "").strip():
        raise ValueError("Save a confirmed Master Resume source before building a profile.")

    profile = v3.CandidateProfileExtract.model_validate(
        extract_profile_data(str(source["content_text"]))
    )
    return v3._persist_profile(
        source=source,
        profile=profile,
        model=LOCAL_PROFILE_MODEL,
        response_id="",
    )


__all__ = ["LOCAL_PROFILE_MODEL", "extract_profile_from_source"]
