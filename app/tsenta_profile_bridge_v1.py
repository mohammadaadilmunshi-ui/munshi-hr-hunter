"""Offline Tsenta-account observation and MUNSHI application handoff contracts.

A connected user's authorized Tsenta MCP/export data can be normalized only
AFTER the caller separately authenticates and obtains explicit read access.
No API endpoints, MCP tool names, login automation or hidden Tsenta internals
are assumed. This module cannot submit an application or mutate Candidate Truth.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from app.linkedin_employer_link_resolver_v1 import external_url
from app.linkedin_url_contract import canonical_linkedin_job_url

_PROFILE_FIELDS = frozenset(
    {
        "given_name",
        "family_name",
        "email",
        "phone",
        "city",
        "state",
        "country",
        "education",
        "employment_history",
        "skills",
        "work_authorization",
        "sponsorship_required",
        "resume",
    }
)
_SENSITIVE_NAMES = (
    "password",
    "oauth",
    "token",
    "api_key",
    "cookie",
    "ssn",
    "social_security",
    "passport",
    "bank",
    "birth_date",
    "date_of_birth",
    "credit_card",
)
_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _normalize(value: Any) -> str:
    """Compare in memory; never expose raw profile values in the result."""
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compare_authorized_profile(
    confirmed_munshi_facts: Mapping[str, Any],
    observed_tsenta_fields: Mapping[str, Any],
    *,
    source: str,
    explicit_profile_read_approval: bool = False,
) -> dict[str, Any]:
    """Return field-level *statuses only*, not private values or auto-promotions."""
    if not explicit_profile_read_approval or source not in {
        "authorized_tsenta_mcp",
        "user_exported_tsenta_profile",
    }:
        raise PermissionError("Tsenta profile read requires user authorization")
    if not isinstance(confirmed_munshi_facts, Mapping) or not isinstance(
        observed_tsenta_fields, Mapping
    ):
        raise TypeError("Profile evidence must be mappings")
    if any(
        any(blocked in str(key).casefold() for blocked in _SENSITIVE_NAMES)
        for key in observed_tsenta_fields
    ):
        raise ValueError("Sensitive credential or ID field in Tsenta observation")
    if len(observed_tsenta_fields) > 100:
        raise ValueError("Tsenta field observations exceed read-only budget")
    statuses: dict[str, str] = {}
    unknown_count = 0
    for key, val in observed_tsenta_fields.items():
        if key not in _PROFILE_FIELDS:
            unknown_count += 1
            continue
        if val is None or val == "":
            statuses[key] = "MISSING_TSENTA"
        elif key not in confirmed_munshi_facts or confirmed_munshi_facts[key] in (
            None,
            "",
        ):
            statuses[key] = "NEEDS_MUNSHI_REVIEW"
        elif _normalize(val) == _normalize(confirmed_munshi_facts[key]):
            statuses[key] = "MATCH"
        else:
            statuses[key] = "CONFLICT_REQUIRES_REVIEW"
    return {
        "contract": "munshi-tsenta-profile-comparison-v1",
        "source": source,
        "field_statuses": statuses,
        "matched_fields": sum(s == "MATCH" for s in statuses.values()),
        "conflicts": sum(s == "CONFLICT_REQUIRES_REVIEW" for s in statuses.values()),
        "unmapped_external_fields": unknown_count,
        "raw_profile_values_in_output": False,
        "candidate_truth_modified": False,
        "tsenta_account_modified": False,
        "application_submitted": False,
        "note": "User review required before changes to MUNSHI Candidate Truth.",
    }


def build_inert_tsenta_handoff(
    *,
    job_id: int,
    original_linkedin_url: str | None,
    verified_employer_url: str,
    candidate_truth_sha256: str,
    resume_sha256: str,
    prepare_package_sha256: str,
    capability_inventory_confirmed: bool = False,
) -> dict[str, Any]:
    """Prepare a provider-neutral, non-executable request envelope.

    Never send to Tsenta until its *actual* authorized capabilities, endpoint,
    OAuth scope and per-action approval contract have been inspected.
    """
    if not isinstance(job_id, int) or job_id <= 0:
        raise ValueError("An existing positive MUNSHI job ID is required")
    employer = external_url(verified_employer_url)
    if not employer:
        raise ValueError("A verified external employer destination is required")
    original = None
    if original_linkedin_url is not None:
        original = canonical_linkedin_job_url(original_linkedin_url)
        if not original:
            raise ValueError("Invalid original LinkedIn URL")
    for field, digest in (
        ("Candidate Truth", candidate_truth_sha256),
        ("resume", resume_sha256),
        ("preparation package", prepare_package_sha256),
    ):
        if not isinstance(digest, str) or not _SHA_PATTERN.fullmatch(digest):
            raise ValueError(f"{field} requires a content digest")
    payload = {
        "version": "munshi-tsenta-inert-handoff-v1",
        "job_id": job_id,
        "original_linkedin_url": original,
        "verified_employer_url": employer,
        "candidate_truth_sha256": candidate_truth_sha256,
        "resume_sha256": resume_sha256,
        "prepare_package_sha256": prepare_package_sha256,
        "capability_inventory_confirmed": bool(capability_inventory_confirmed),
        "submit_authorized": False,
        "contains_candidate_personal_data": False,
        "network_requests": 0,
    }
    payload["handoff_idempotency_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload
