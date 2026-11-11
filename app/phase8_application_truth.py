"""Phase 8 unified application-truth projection for MUNSHI Apply.

Hunter remains the canonical Candidate Truth authority.  This module produces an
inert, content-addressed projection that Apply can cache and consume without
creating a second candidate profile authority.  Protected facts remain opaque
Hunter vault references; plaintext protected values never cross this generic
contract.

No HTTP, browser, ATS, credential, Gmail, n8n, outreach, or submission authority
exists here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app import phase45_truth_binding
from app.cross_repo_contract import sha256_json, validate_profile_snapshot
from app.phase67_common import safe_owned_job_snapshot

APPLICATION_TRUTH_VERSION = "munshi-application-truth-projection-v1"
APPLICATION_TRUTH_AUTHORITY = "munshi-hr-hunter"

# These are reusable application facts MUNSHI should try to know once and reuse.
# Their absence is informative, not a policy failure: missing facts remain unknown
# until the candidate provides/approves them through the Candidate Truth flow.
BASELINE_APPLICATION_FACT_KEYS = (
    "contact.full_name",
    "contact.email",
    "contact.phone",
    "application_defaults.open_to_work",
    "application_defaults.willing_to_relocate",
    "application_defaults.work_modes",
    "application_defaults.work_authorization_country",
    "application_defaults.authorization_basis",
    "application_defaults.visa_or_permit",
    "application_defaults.authorization_status",
    "application_defaults.authorized_to_work",
    "application_defaults.sponsorship_required",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _binding(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_extraction_id": str(snapshot["source_extraction_id"]),
        "profile_revision": int(snapshot["profile_revision"]),
        "profile_digest": str(snapshot["profile_digest"]),
        "source_profile_sha256": str(snapshot["source_profile_sha256"]),
        "source_resume_sha256": str(snapshot["source_resume_sha256"]),
    }


def projection_digest_payload(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable Phase 8 state, excluding observational timestamp/digest."""
    return {
        key: value
        for key, value in projection.items()
        if key not in {"generated_at", "projection_digest"}
    }


def build_application_truth_projection(
    profile_snapshot: Mapping[str, Any],
    *,
    job_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical read-only Candidate Truth consumer projection.

    The supplied profile must already satisfy Hunter/Apply's shared Phase 12
    snapshot contract.  A job context is optional because the reusable Candidate
    Truth exists independently of any one application.
    """
    profile = validate_profile_snapshot(dict(profile_snapshot))
    facts = [dict(item) for item in profile.get("facts") or []]
    by_key = {str(item["key"]): item for item in facts}

    protected_fact_keys = sorted(
        key for key, fact in by_key.items() if fact.get("protected") is True
    )
    unresolved_fact_keys = sorted(
        key for key in BASELINE_APPLICATION_FACT_KEYS if key not in by_key
    )

    job_context: dict[str, Any] | None = None
    if job_snapshot is not None:
        job = job_snapshot.get("job")
        digest = str(job_snapshot.get("job_snapshot_sha256") or "").strip()
        if not isinstance(job, Mapping):
            raise ValueError("Phase 8 job snapshot must contain canonical job evidence.")
        job_id = job.get("id")
        if not isinstance(job_id, (str, int)) or not str(job_id).strip():
            raise ValueError("Phase 8 job snapshot is missing job.id.")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("Phase 8 job snapshot digest must be a lowercase SHA-256.")
        job_context = {
            "job_id": str(job_id),
            "job_snapshot_sha256": digest,
        }

    projection: dict[str, Any] = {
        "contract_version": APPLICATION_TRUTH_VERSION,
        "authority": APPLICATION_TRUTH_AUTHORITY,
        "projection_mode": "READ_ONLY",
        "tenant_id": profile["tenant_id"],
        "user_id": profile["user_id"],
        "profile_id": profile["profile_id"],
        "candidate_profile_binding": _binding(profile),
        "generated_at": _now(),
        "job_context": job_context,
        "facts": facts,
        "protected_fact_keys": protected_fact_keys,
        "unresolved_fact_keys": unresolved_fact_keys,
        "mutation_authority": False,
        "submission_authority": False,
    }
    projection["projection_digest"] = sha256_json(
        projection_digest_payload(projection)
    )
    return projection


def current_application_truth_projection(*, job_id: int | None = None) -> dict[str, Any]:
    """Read the current canonical Hunter truth and optionally bind a job snapshot."""
    profile = phase45_truth_binding.current_candidate_profile_snapshot()
    job_snapshot = safe_owned_job_snapshot(int(job_id)) if job_id is not None else None
    return build_application_truth_projection(profile, job_snapshot=job_snapshot)


def projection_binding_matches(
    projection: Mapping[str, Any],
    *,
    source_extraction_id: str,
    profile_revision: int,
    profile_digest: str,
) -> bool:
    """True only when a consumer is bound to the exact current Candidate Truth."""
    binding = projection.get("candidate_profile_binding")
    if not isinstance(binding, Mapping):
        return False
    return (
        str(binding.get("source_extraction_id") or "") == str(source_extraction_id)
        and int(binding.get("profile_revision") or 0) == int(profile_revision)
        and str(binding.get("profile_digest") or "") == str(profile_digest)
    )
