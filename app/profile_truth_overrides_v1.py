"""Encrypted candidate-confirmed overrides for the permanent MUNSHI profile.

The confirmed Resume Studio extraction remains immutable evidence. Candidate edits are
stored separately as AES-GCM ciphertext and are resolved over that evidence at read
time. This keeps provenance explicit and lets a candidate undo edits without mutating
the original Master Resume extraction.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app import native_resume_service_v3 as v3
from app.secure_vault import read_secret, store_secret, vault_available

PROFILE_OVERRIDE_SECRET_TYPE = "candidate_profile_overrides_v1"
PROFILE_OVERRIDE_SCHEMA = "candidate-profile-overrides-v1"
MAX_HISTORY = 20
EDITABLE_SECTIONS = (
    "professional_summary",
    "contact",
    "education",
    "experience",
    "projects",
    "skills",
    "certifications",
    "languages",
)


class OverrideHistoryItem(BaseModel):
    revision: int = Field(ge=1)
    section: str
    changed_at: str
    previous_value: Any = None


class ProfileOverrideEnvelope(BaseModel):
    schema_version: Literal[PROFILE_OVERRIDE_SCHEMA] = PROFILE_OVERRIDE_SCHEMA
    extraction_id: str
    revision: int = Field(default=0, ge=0)
    updated_at: str = ""
    sections: dict[str, Any] = Field(default_factory=dict)
    history: list[OverrideHistoryItem] = Field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _owner_label(extraction_id: str) -> str:
    connection = v3.v2.v1.get_connection()
    try:
        v3.ensure_schema(connection)
        owner = v3.v2.v1.current_owner(connection)
        return f"{owner.tenant_id}:{owner.user_id}:profile-overrides:{extraction_id}"
    finally:
        connection.close()


def override_encryption_available() -> bool:
    return vault_available()


def _empty(extraction_id: str) -> ProfileOverrideEnvelope:
    return ProfileOverrideEnvelope(extraction_id=str(extraction_id))


def _persist_envelope(envelope: ProfileOverrideEnvelope) -> None:
    """Persist revision metadata even when all overrides were reset.

    Deleting the last ciphertext used to make the next load appear as revision 0.
    Keeping an empty encrypted envelope preserves monotonic revision/provenance while
    the immutable Master Resume evidence remains untouched.
    """
    store_secret(
        PROFILE_OVERRIDE_SECRET_TYPE,
        json.dumps(envelope.model_dump(), ensure_ascii=False, sort_keys=True),
        account_label=_owner_label(envelope.extraction_id),
    )


def load_profile_overrides(extraction_id: str) -> ProfileOverrideEnvelope:
    extraction_id = str(extraction_id or "").strip()
    if not extraction_id:
        raise ValueError("A profile extraction ID is required.")
    if not vault_available():
        return _empty(extraction_id)
    raw = read_secret(PROFILE_OVERRIDE_SECRET_TYPE, account_label=_owner_label(extraction_id))
    if not raw:
        return _empty(extraction_id)
    try:
        model = ProfileOverrideEnvelope.model_validate_json(raw)
    except Exception as error:
        raise RuntimeError("Encrypted profile edits could not be decoded.") from error
    if model.extraction_id != extraction_id:
        raise RuntimeError("Encrypted profile edits do not match this profile extraction.")
    return model


def _validated_section(base_profile: dict[str, Any], section: str, value: Any) -> Any:
    if section not in EDITABLE_SECTIONS:
        raise ValueError(f"Unsupported profile section: {section}")
    base = v3.CandidateProfileExtract.model_validate(base_profile).model_dump()
    base[section] = value
    validated = v3.CandidateProfileExtract.model_validate(base)
    return copy.deepcopy(validated.model_dump()[section])


def resolve_profile(extracted: dict[str, Any]) -> dict[str, Any]:
    """Return confirmed/extracted evidence with candidate edits layered on top."""
    profile = v3.CandidateProfileExtract.model_validate(extracted.get("profile") or {}).model_dump()
    extraction_id = str(extracted.get("extraction_id") or "").strip()
    if not extraction_id or not vault_available():
        return profile
    envelope = load_profile_overrides(extraction_id)
    for section, value in envelope.sections.items():
        if section in EDITABLE_SECTIONS:
            profile[section] = _validated_section(profile, section, value)
    return v3.CandidateProfileExtract.model_validate(profile).model_dump()


def save_profile_override(
    extracted: dict[str, Any],
    section: str,
    value: Any,
) -> ProfileOverrideEnvelope:
    """Save one candidate-confirmed section override without touching evidence."""
    if not vault_available():
        raise RuntimeError(
            "Encrypted profile editing requires MUNSHI_VAULT_KEY. Candidate edits are never saved in plaintext."
        )
    extraction_id = str(extracted.get("extraction_id") or "").strip()
    if not extraction_id:
        raise ValueError("A profile extraction ID is required before editing.")
    base_profile = v3.CandidateProfileExtract.model_validate(extracted.get("profile") or {}).model_dump()
    value = _validated_section(base_profile, section, value)
    envelope = load_profile_overrides(extraction_id)
    previous = copy.deepcopy(envelope.sections.get(section, base_profile.get(section)))
    next_revision = envelope.revision + 1
    envelope.history.append(
        OverrideHistoryItem(
            revision=next_revision,
            section=section,
            changed_at=_now(),
            previous_value=previous,
        )
    )
    envelope.history = envelope.history[-MAX_HISTORY:]
    envelope.sections[section] = value
    envelope.revision = next_revision
    envelope.updated_at = _now()
    _persist_envelope(envelope)
    return envelope


def reset_profile_section(extracted: dict[str, Any], section: str) -> ProfileOverrideEnvelope:
    if section not in EDITABLE_SECTIONS:
        raise ValueError(f"Unsupported profile section: {section}")
    if not vault_available():
        raise RuntimeError("Encrypted profile editing is not configured.")
    extraction_id = str(extracted.get("extraction_id") or "").strip()
    envelope = load_profile_overrides(extraction_id)
    if section not in envelope.sections:
        return envelope
    next_revision = envelope.revision + 1
    envelope.history.append(
        OverrideHistoryItem(
            revision=next_revision,
            section=section,
            changed_at=_now(),
            previous_value=copy.deepcopy(envelope.sections[section]),
        )
    )
    envelope.history = envelope.history[-MAX_HISTORY:]
    envelope.sections.pop(section, None)
    envelope.revision = next_revision
    envelope.updated_at = _now()
    _persist_envelope(envelope)
    return envelope


def reset_all_profile_overrides(extracted: dict[str, Any]) -> bool:
    if not vault_available():
        raise RuntimeError("Encrypted profile editing is not configured.")
    extraction_id = str(extracted.get("extraction_id") or "").strip()
    if not extraction_id:
        return False
    envelope = load_profile_overrides(extraction_id)
    if not envelope.sections:
        return False

    next_revision = envelope.revision + 1
    changed_at = _now()
    for section in sorted(envelope.sections):
        envelope.history.append(
            OverrideHistoryItem(
                revision=next_revision,
                section=section,
                changed_at=changed_at,
                previous_value=copy.deepcopy(envelope.sections[section]),
            )
        )
    envelope.history = envelope.history[-MAX_HISTORY:]
    envelope.sections.clear()
    envelope.revision = next_revision
    envelope.updated_at = changed_at
    _persist_envelope(envelope)
    return True
