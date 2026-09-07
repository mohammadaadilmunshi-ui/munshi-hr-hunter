"""Shared, preparation-only resume artifact contract for Apply execution.

Hunter owns this projection.  It deliberately accepts n8n only when its
authoritative callback supplies an immutable, job-bound artifact; it never
infers Native provenance or turns preparation into submission authority.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from app import native_resume_artifact_v5
from app import native_resume_service_v5 as resume_v5


def _required(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"Execution artifact requires {name}.")
    return result


def native(*, application_id: str, version_id: str) -> dict[str, Any]:
    """Normalize one already-validated immutable Native V5 artifact pair."""
    materialized = native_resume_artifact_v5.materialize(version_id)
    version = resume_v5.get_version(version_id)
    pdf = materialized["pdf"]
    return {
        "application_id": _required(application_id, "application_id"),
        "job_id": int(materialized["job_id"]),
        "engine": "native",
        "artifact_id": pdf["artifact_id"],
        "version_id": str(version_id),
        "version_number": int(version["version_number"]),
        "filename": pdf["filename"],
        "mime_type": pdf["mime_type"],
        "artifact_reference": pdf["object_reference"],
        "artifact_sha256": pdf["sha256"],
        "page_count": int(pdf["page_count"]),
        "candidate_truth_binding": materialized["candidate_truth_binding"],
        "job_snapshot_binding": materialized["job_snapshot_binding"],
        "created_at": datetime.now(UTC).isoformat(),
        "submission_authority": False,
    }


def n8n(*, application_id: str, job_id: int, callback: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize an authoritative n8n callback without copying plaintext bytes."""
    if str(callback.get("engine") or "n8n").casefold() != "n8n":
        raise ValueError("n8n execution artifact callback has invalid engine provenance.")
    if int(callback.get("job_id") or 0) != int(job_id):
        raise ValueError("n8n execution artifact is bound to another job.")
    result = {
        "application_id": _required(application_id, "application_id"),
        "job_id": int(job_id),
        "engine": "n8n",
        "artifact_id": _required(callback.get("artifact_id"), "artifact_id"),
        "version_id": _required(callback.get("version_id"), "version_id"),
        "version_number": int(callback.get("version_number") or 0),
        "filename": _required(callback.get("filename"), "filename"),
        "mime_type": _required(callback.get("mime_type"), "mime_type"),
        "artifact_reference": _required(callback.get("artifact_reference"), "artifact_reference"),
        "artifact_sha256": _required(callback.get("artifact_sha256"), "artifact_sha256"),
        "page_count": int(callback.get("page_count") or 0),
        "candidate_truth_binding": callback.get("candidate_truth_binding"),
        "job_snapshot_binding": callback.get("job_snapshot_binding"),
        "created_at": _required(callback.get("created_at"), "created_at"),
        "submission_authority": False,
    }
    if len(result["artifact_sha256"]) != 64 or result["page_count"] != 1:
        raise ValueError("n8n execution artifact must provide an exact one-page immutable PDF.")
    if not isinstance(result["candidate_truth_binding"], Mapping) or not isinstance(
        result["job_snapshot_binding"], Mapping
    ):
        raise ValueError("n8n execution artifact lacks Candidate Truth or job binding.")
    return result
