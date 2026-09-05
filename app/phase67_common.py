"""Shared inert primitives for strengthened Opportunity + Relationship Intelligence.

The helpers in this module only read Hunter-owned records and compute canonical
snapshots/digests. They do not dispatch jobs, generate resumes, access Gmail,
control browsers, call ATS providers, send outreach, or submit applications.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from app.database import get_connection
from app.tenant_foundation import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    current_owner,
    ensure_schema as ensure_tenant_schema,
    tenant_foundation_enabled,
)

JOB_SNAPSHOT_VERSION = "hunter-owned-job-snapshot-v1"
_TOKEN_RE = re.compile(r"[a-z][a-z0-9+#.-]{1,39}", re.I)
_STOPWORDS = frozenset(
    {
        "and", "the", "for", "with", "from", "that", "this", "will", "you", "your",
        "our", "are", "was", "were", "have", "has", "had", "into", "using", "use",
        "job", "role", "work", "team", "years", "year", "preferred", "required",
        "requirements", "responsibilities", "including", "within", "about", "across",
        "ability", "strong", "experience", "skills", "skill", "knowledge", "support",
    }
)

_JOB_FIELDS = (
    "id",
    "job_fingerprint",
    "source",
    "source_tier",
    "company_name",
    "title",
    "location_raw",
    "city",
    "state",
    "country",
    "remote_type",
    "description_raw",
    "salary_raw",
    "normalized_hourly_min",
    "normalized_hourly_max",
    "salary_confidence",
    "target_track",
    "hunter_score",
    "match_label",
    "status",
    "hard_rejection_reason",
    "cpt_trapdoor",
    "ghost_risk_score",
    "date_posted",
    "apply_deadline",
    "employment_type",
    "responsibilities",
    "qualifications",
    "preferred_qualifications",
    "preferred_skills",
    "skills_keywords",
    "work_authorization",
    "industry",
    "company_size",
    "updated_at",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def tokens(value: Any) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(canonical_text(value))
        if len(token) >= 3 and token.casefold() not in _STOPWORDS
    }


def bounded_ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, float(numerator) / float(denominator)))


def safe_owned_job_snapshot(job_id: int) -> dict[str, Any]:
    """Read a job through the existing tenant ownership boundary.

    Legacy singleton jobs created before tenant ownership was introduced remain
    readable only while tenant contexts are disabled and the resolved principal is
    the deterministic local owner. Once tenant mode is enabled, an explicit job
    ownership association is mandatory.
    """
    try:
        resolved_job_id = int(job_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Job id must be an integer.") from error
    if resolved_job_id <= 0:
        raise ValueError("Job id must be positive.")

    connection = get_connection()
    try:
        ensure_tenant_schema(connection)
        owner = current_owner(connection)
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (resolved_job_id,)).fetchone()
        if row is None:
            raise LookupError("Job is unavailable.")
        ownership = connection.execute(
            """SELECT tenant_id,user_id FROM owned_record_owners
               WHERE record_domain='job' AND record_key=?""",
            (str(resolved_job_id),),
        ).fetchone()
        if ownership is not None:
            if ownership["tenant_id"] != owner.tenant_id or ownership["user_id"] != owner.user_id:
                raise LookupError("Job is not owned by the current user.")
        elif (
            tenant_foundation_enabled()
            or owner.tenant_id != DEFAULT_TENANT_ID
            or owner.user_id != DEFAULT_USER_ID
        ):
            raise LookupError("Job requires an explicit current-user ownership association.")

        raw = dict(row)
        snapshot = {
            "snapshot_version": JOB_SNAPSHOT_VERSION,
            "tenant_id": owner.tenant_id,
            "user_id": owner.user_id,
            "job": {field: raw.get(field) for field in _JOB_FIELDS},
        }
        snapshot["job_snapshot_sha256"] = sha256_json(snapshot)
        return snapshot
    finally:
        connection.close()


def safe_mapping_digest(value: Mapping[str, Any], *, label: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object.")
    return sha256_json(dict(value))
