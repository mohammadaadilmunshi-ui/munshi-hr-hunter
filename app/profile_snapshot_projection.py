"""Read-only Section 1 -> Phase 12 Candidate Truth Profile projection.

The permanent Profile remains Hunter-owned. This module reads the confirmed Master
Resume extraction, encrypted candidate overrides, and encrypted candidate-entered
application details, then emits the inert cross-repository snapshot contract.

It performs no HTTP, browser, provider, Gmail, credential-use, n8n, or submission
action. Protected candidate-entered facts are represented only by opaque Hunter
vault references; their plaintext values do not enter the generic bridge payload.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app import native_resume_service_v3 as v3
from app import profile_truth_overrides_v1 as overrides
from app import resume_profile_details_v31 as profile_details
from app.cross_repo_contract import (
    PROFILE_AUTHORITY,
    PROFILE_REVISION_SCOPE,
    PROFILE_SNAPSHOT_VERSION,
    validate_profile_snapshot,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _owner() -> tuple[str, str]:
    connection = v3.v2.v1.get_connection()
    try:
        v3.ensure_schema(connection)
        owner = v3.v2.v1.current_owner(connection)
        return str(owner.tenant_id), str(owner.user_id)
    finally:
        connection.close()


def _composite_revision(override_revision: int, details_revision: int) -> int:
    """Cantor-pair two monotonic extraction-scoped revision counters."""
    total = override_revision + details_revision
    return (total * (total + 1) // 2) + details_revision + 1


def _fact_id(profile_id: str, key: str) -> str:
    digest = hashlib.sha256(f"{profile_id}\n{key}".encode("utf-8")).hexdigest()
    return f"fact-{digest[:32]}"


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def _plain_fact(
    facts: list[dict[str, Any]],
    *,
    profile_id: str,
    key: str,
    value: Any,
    category: str,
    trust_level: str,
    source: str,
) -> None:
    if not _present(value):
        return
    normalized = value.strip() if isinstance(value, str) else value
    facts.append(
        {
            "fact_id": _fact_id(profile_id, key),
            "key": key,
            "category": category,
            "trust_level": trust_level,
            "protected": False,
            "source": source,
            "value": normalized,
        }
    )


def _protected_fact(
    facts: list[dict[str, Any]],
    *,
    profile_id: str,
    key: str,
    value: Any,
    category: str,
    source: str,
) -> None:
    if not _present(value):
        return
    facts.append(
        {
            "fact_id": _fact_id(profile_id, key),
            "key": key,
            "category": category,
            "trust_level": "USER_CONFIRMED",
            "protected": True,
            "source": source,
            "value_reference": f"hunter-vault://candidate-profile-details-v31/{key}",
        }
    )


def _section_provenance(
    section: str,
    *,
    extraction_id: str,
    override_revision: int,
    overridden_sections: set[str],
) -> tuple[str, str]:
    if section in overridden_sections:
        return "USER_CONFIRMED", f"candidate-profile-overrides-v1:r{override_revision}"
    return "DOCUMENT_CONFIRMED", f"master-resume-extraction:{extraction_id}"


def _add_resume_profile_facts(
    facts: list[dict[str, Any]],
    *,
    profile_id: str,
    extraction_id: str,
    profile: dict[str, Any],
    override_revision: int,
    overridden_sections: set[str],
) -> None:
    trust, source = _section_provenance(
        "professional_summary",
        extraction_id=extraction_id,
        override_revision=override_revision,
        overridden_sections=overridden_sections,
    )
    _plain_fact(
        facts,
        profile_id=profile_id,
        key="profile.professional_summary",
        value=profile.get("professional_summary"),
        category="SAVED_ANSWER",
        trust_level=trust,
        source=source,
    )

    trust, source = _section_provenance(
        "contact",
        extraction_id=extraction_id,
        override_revision=override_revision,
        overridden_sections=overridden_sections,
    )
    contact = profile.get("contact") or {}
    contact_categories = {
        "full_name": "IDENTITY",
        "location": "ADDRESS",
        "email": "CONTACT",
        "phone": "CONTACT",
        "linkedin": "CONTACT",
        "portfolio": "CONTACT",
    }
    for field, category in contact_categories.items():
        _plain_fact(
            facts,
            profile_id=profile_id,
            key=f"contact.{field}",
            value=contact.get(field),
            category=category,
            trust_level=trust,
            source=source,
        )

    trust, source = _section_provenance(
        "education",
        extraction_id=extraction_id,
        override_revision=override_revision,
        overridden_sections=overridden_sections,
    )
    for index, item in enumerate(profile.get("education") or []):
        for field in (
            "institution",
            "degree",
            "field",
            "location",
            "start_date",
            "end_date",
            "gpa",
            "details",
        ):
            _plain_fact(
                facts,
                profile_id=profile_id,
                key=f"education.{index}.{field}",
                value=item.get(field),
                category="EDUCATION",
                trust_level=trust,
                source=source,
            )

    trust, source = _section_provenance(
        "experience",
        extraction_id=extraction_id,
        override_revision=override_revision,
        overridden_sections=overridden_sections,
    )
    for index, item in enumerate(profile.get("experience") or []):
        for field in ("employer", "title", "location", "start_date", "end_date", "bullets"):
            _plain_fact(
                facts,
                profile_id=profile_id,
                key=f"experience.{index}.{field}",
                value=item.get(field),
                category="EMPLOYMENT",
                trust_level=trust,
                source=source,
            )

    trust, source = _section_provenance(
        "projects",
        extraction_id=extraction_id,
        override_revision=override_revision,
        overridden_sections=overridden_sections,
    )
    for index, item in enumerate(profile.get("projects") or []):
        for field in ("name", "description", "tools", "bullets"):
            _plain_fact(
                facts,
                profile_id=profile_id,
                key=f"projects.{index}.{field}",
                value=item.get(field),
                category="PROJECT",
                trust_level=trust,
                source=source,
            )

    trust, source = _section_provenance(
        "skills",
        extraction_id=extraction_id,
        override_revision=override_revision,
        overridden_sections=overridden_sections,
    )
    for index, item in enumerate(profile.get("skills") or []):
        for field in ("category", "skills"):
            _plain_fact(
                facts,
                profile_id=profile_id,
                key=f"skills.{index}.{field}",
                value=item.get(field),
                category="SKILL",
                trust_level=trust,
                source=source,
            )

    trust, source = _section_provenance(
        "certifications",
        extraction_id=extraction_id,
        override_revision=override_revision,
        overridden_sections=overridden_sections,
    )
    for index, item in enumerate(profile.get("certifications") or []):
        for field in ("name", "issuer", "date", "credential_id"):
            _plain_fact(
                facts,
                profile_id=profile_id,
                key=f"certifications.{index}.{field}",
                value=item.get(field),
                category="CERTIFICATION",
                trust_level=trust,
                source=source,
            )

    trust, source = _section_provenance(
        "languages",
        extraction_id=extraction_id,
        override_revision=override_revision,
        overridden_sections=overridden_sections,
    )
    _plain_fact(
        facts,
        profile_id=profile_id,
        key="languages",
        value=profile.get("languages"),
        category="LANGUAGE",
        trust_level=trust,
        source=source,
    )


def _add_candidate_detail_facts(
    facts: list[dict[str, Any]],
    *,
    profile_id: str,
    values: dict[str, Any],
    details_revision: int,
) -> None:
    source = f"candidate-profile-details-v31:r{details_revision}"

    plain_categories = {
        "open_to_work": "AVAILABILITY",
        "in_person_ok": "WORK_PREFERENCE",
        "willing_to_relocate": "WORK_PREFERENCE",
        "start_immediately": "AVAILABILITY",
        "has_transport": "WORK_PREFERENCE",
        "work_modes": "WORK_PREFERENCE",
        "prior_employee": "SAVED_ANSWER",
        "government_clearance": "SAVED_ANSWER",
        "government_ties": "SAVED_ANSWER",
    }
    for field, category in plain_categories.items():
        _plain_fact(
            facts,
            profile_id=profile_id,
            key=f"application_defaults.{field}",
            value=values.get(field),
            category=category,
            trust_level="USER_CONFIRMED",
            source=source,
        )

    protected_categories = {
        "work_authorization_country": "WORK_AUTHORIZATION",
        "authorization_basis": "WORK_AUTHORIZATION",
        "visa_or_permit": "WORK_AUTHORIZATION",
        "authorization_status": "WORK_AUTHORIZATION",
        "authorized_to_work": "WORK_AUTHORIZATION",
        "sponsorship_required": "SPONSORSHIP",
        "needs_accommodations": "VOLUNTARY_DEMOGRAPHIC",
        "gender": "VOLUNTARY_DEMOGRAPHIC",
        "ethnicity": "VOLUNTARY_DEMOGRAPHIC",
        "veteran": "VOLUNTARY_DEMOGRAPHIC",
        "disability": "VOLUNTARY_DEMOGRAPHIC",
    }
    for field, category in protected_categories.items():
        _protected_fact(
            facts,
            profile_id=profile_id,
            key=f"application_defaults.{field}",
            value=values.get(field),
            category=category,
            source=source,
        )


def _verify_immutable_profile_hash(extracted: dict[str, Any]) -> None:
    profile = v3.CandidateProfileExtract.model_validate(extracted.get("profile") or {})
    encoded = json.dumps(profile.model_dump(), ensure_ascii=False, sort_keys=True)
    actual = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    expected = str(extracted.get("profile_sha256") or "").strip()
    if not expected or actual != expected:
        raise RuntimeError("Confirmed Master Resume profile evidence hash does not match its stored metadata.")


def build_candidate_profile_snapshot(extracted: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical read-only Phase 12 snapshot from Section 1 truth.

    Export fails closed if the encrypted candidate-truth vault is unavailable or
    if the supplied extraction is not the confirmed, owner-bound evidence record.
    """
    if not overrides.override_encryption_available() or not profile_details.candidate_profile_details_encryption_available():
        raise RuntimeError(
            "Encrypted Candidate Truth Profile storage must be available before cross-repository projection."
        )

    extraction_id = str(extracted.get("extraction_id") or "").strip()
    if not extraction_id:
        raise ValueError("A profile extraction ID is required for projection.")
    if str(extracted.get("status") or "").upper() != "CONFIRMED":
        raise ValueError("Only a confirmed Master Resume profile extraction may be projected.")

    tenant_id, user_id = _owner()
    if str(extracted.get("tenant_id") or "") != tenant_id or str(extracted.get("user_id") or "") != user_id:
        raise ValueError("Profile extraction owner does not match the active Hunter tenant/user.")

    _verify_immutable_profile_hash(extracted)
    source_profile_sha256 = str(extracted.get("profile_sha256") or "").strip()
    source_resume_sha256 = str(extracted.get("source_sha256") or "").strip()
    if len(source_profile_sha256) != 64 or len(source_resume_sha256) != 64:
        raise ValueError("Confirmed profile/resume evidence hashes are incomplete.")

    override_envelope = overrides.load_profile_overrides(extraction_id)
    details_envelope = profile_details.load_candidate_profile_details_envelope()
    override_revision = int(override_envelope.revision)
    details_revision = int(details_envelope.revision)
    profile_revision = _composite_revision(override_revision, details_revision)
    profile_id = f"candidate-truth:{tenant_id}:{user_id}"

    resolved = overrides.resolve_profile(extracted)
    facts: list[dict[str, Any]] = []
    _add_resume_profile_facts(
        facts,
        profile_id=profile_id,
        extraction_id=extraction_id,
        profile=resolved,
        override_revision=override_revision,
        overridden_sections=set(override_envelope.sections),
    )
    _add_candidate_detail_facts(
        facts,
        profile_id=profile_id,
        values=details_envelope.values.model_dump(),
        details_revision=details_revision,
    )

    snapshot = {
        "contract_version": PROFILE_SNAPSHOT_VERSION,
        "authority": PROFILE_AUTHORITY,
        "projection_mode": "READ_ONLY",
        "revision_scope": PROFILE_REVISION_SCOPE,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "profile_id": profile_id,
        "profile_revision": profile_revision,
        "override_revision": override_revision,
        "candidate_details_revision": details_revision,
        "source_extraction_id": extraction_id,
        "source_profile_sha256": source_profile_sha256,
        "source_resume_sha256": source_resume_sha256,
        "generated_at": _now(),
        "facts": facts,
    }
    return validate_profile_snapshot(snapshot)
