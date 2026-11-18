"""Offline MUNSHI Relay-account observation and MUNSHI application handoff contracts.

A connected user's authorized MUNSHI Relay MCP/export data can be normalized only
AFTER the caller separately authenticates and obtains explicit read access.
No API endpoints, MCP tool names, login automation or hidden MUNSHI Relay internals
are assumed. This module cannot submit an application or mutate Candidate Truth.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
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

_TSENTA_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "given_name": (("personalInformation", "firstName"), ("personalInfo", "firstName")),
    "family_name": (("personalInformation", "lastName"), ("personalInfo", "lastName")),
    "email": (("personalInformation", "email"), ("personalInfo", "email")),
    "phone": (("personalInformation", "phone"), ("personalInfo", "phone")),
    "city": (("personalInformation", "city"), ("personalInfo", "city")),
    "state": (
        ("personalInformation", "state"),
        ("personalInformation", "region"),
        ("personalInfo", "state"),
    ),
    "country": (("personalInformation", "country"), ("personalInfo", "country")),
    "linkedin": (
        ("personalInformation", "linkedinUrl"),
        ("personalInformation", "linkedInUrl"),
        ("personalInfo", "linkedinUrl"),
    ),
    "work_authorization_country": (
        ("workAuthorization", "workAuthorizations"),
        ("workAuthorization", "citizenshipCountries"),
    ),
    "visa_or_permit": (("workAuthorization", "visaType"),),
    "authorization_status": (("workAuthorization", "optStatus"),),
    "authorized_to_work": (("workAuthorization", "isAuthorizedToWork"),),
    "sponsorship_required": (("workAuthorization", "needsSponsorship"),),
    "gender": (("additionalInfo", "gender"), ("backgroundCheck", "gender")),
    "ethnicity": (
        ("additionalInfo", "ethnicity"),
        ("backgroundCheck", "ethnicity"),
    ),
    "veteran": (("additionalInfo", "veteran"), ("backgroundCheck", "veteran")),
    "disability": (
        ("additionalInfo", "disability"),
        ("backgroundCheck", "disability"),
    ),
}

_CANDIDATE_FACT_KEYS = {
    "given_name": "contact.first_name",
    "family_name": "contact.last_name",
    "email": "contact.email",
    "phone": "contact.phone",
    "city": "contact.city",
    "state": "contact.region",
    "country": "contact.country",
    "linkedin": "contact.linkedin",
}

_PROTECTED_DETAIL_KEYS = frozenset(
    {
        "work_authorization_country",
        "authorization_basis",
        "visa_or_permit",
        "authorization_status",
        "authorized_to_work",
        "sponsorship_required",
        "gender",
        "race_ethnicity",
        "ethnicity",
        "hispanic_latino",
        "veteran",
        "disability",
    }
)


def _normalize(value: Any) -> str:
    """Compare in memory; never expose raw profile values in the result."""
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _nested(root: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = root
    for segment in path:
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _first_present(root: Mapping[str, Any], paths: Sequence[Sequence[str]]) -> Any:
    for path in paths:
        value = _nested(root, path)
        if value not in (None, "", [], {}):
            return value
    return None


def _canonical_country(value: Any) -> str:
    text = " ".join(str(value or "").casefold().split())
    return {
        "us": "united states",
        "u.s.": "united states",
        "usa": "united states",
        "united states of america": "united states",
    }.get(text, text)


def _canonical_profile_value(field: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if field in {"authorized_to_work", "sponsorship_required"}:
        if isinstance(value, bool):
            return value
        text = " ".join(str(value).casefold().split())
        if text in {"yes", "true", "1", "authorized", "required"}:
            return True
        if text in {"no", "false", "0", "not authorized", "not required"}:
            return False
        return text
    if field == "work_authorization_country":
        values = (
            value
            if isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
            else [value]
        )
        countries = []
        for item in values:
            if isinstance(item, Mapping):
                item = (
                    item.get("country")
                    or item.get("countryCode")
                    or item.get("name")
                    or item.get("value")
                )
            if item not in (None, ""):
                countries.append(_canonical_country(item))
        return sorted(set(countries))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sorted(
            {
                str(_canonical_profile_value(field, item))
                for item in value
                if item not in (None, "")
            }
        )
    text = " ".join(str(value).casefold().split())
    if field == "country":
        return _canonical_country(text)
    if field in {"visa_or_permit", "authorization_status"}:
        return re.sub(r"[^a-z0-9]+", "", text)
    if field == "phone":
        return re.sub(r"\D", "", text)
    if field == "linkedin":
        return text.rstrip("/")
    return text


def _candidate_projection(
    candidate_snapshot: Mapping[str, Any],
    protected_candidate_values: Mapping[str, Any],
) -> dict[str, Any]:
    facts = {
        str(item.get("key") or ""): item.get("value")
        for item in candidate_snapshot.get("facts") or []
        if isinstance(item, Mapping) and item.get("protected") is not True
    }
    projected = {
        field: facts.get(fact_key) for field, fact_key in _CANDIDATE_FACT_KEYS.items()
    }
    for key in _PROTECTED_DETAIL_KEYS:
        if key in protected_candidate_values:
            projected[key] = protected_candidate_values.get(key)
    education = [
        value
        for key, value in facts.items()
        if re.fullmatch(r"education\.\d+\.institution", key) and value
    ]
    skill_values: list[Any] = []
    for key, value in facts.items():
        if re.fullmatch(r"skills\.\d+\.skills", key):
            if isinstance(value, Sequence) and not isinstance(
                value, (str, bytes, bytearray)
            ):
                skill_values.extend(value)
            elif value:
                skill_values.append(value)
    projected["education_institutions"] = education
    projected["skills"] = skill_values
    return projected


def _tsenta_projection(profile: Mapping[str, Any]) -> dict[str, Any]:
    root = profile.get("data") or profile.get("profile") or profile
    if not isinstance(root, Mapping):
        raise ValueError("MUNSHI Relay resume profile data is not an object.")
    projected = {
        field: _first_present(root, paths) for field, paths in _TSENTA_PATHS.items()
    }
    education = root.get("education") or []
    projected["education_institutions"] = [
        item.get("institution") or item.get("school") or item.get("schoolName")
        for item in education
        if isinstance(item, Mapping)
        and (item.get("institution") or item.get("school") or item.get("schoolName"))
    ]
    skill_categories = root.get("skillCategories") or root.get("skills") or []
    skills: list[Any] = []
    for item in skill_categories:
        if isinstance(item, Mapping):
            value = item.get("skills") or item.get("items") or []
            if isinstance(value, Sequence) and not isinstance(
                value, (str, bytes, bytearray)
            ):
                skills.extend(value)
        elif item:
            skills.append(item)
    projected["skills"] = skills
    return projected


def compare_candidate_truth_snapshot(
    candidate_snapshot: Mapping[str, Any],
    protected_candidate_values: Mapping[str, Any],
    tsenta_resume_profile: Mapping[str, Any],
    *,
    source: str,
    explicit_profile_read_approval: bool = False,
) -> dict[str, Any]:
    """Compare the live profile shapes in memory and return statuses only.

    Protected Candidate Truth values must come from the trusted encrypted detail
    reader.  Vault references are never mistaken for missing values and neither
    side's value is copied into this report.
    """
    if not explicit_profile_read_approval or source != "authorized_tsenta_mcp":
        raise PermissionError(
            "Authenticated MUNSHI Relay profile comparison requires approval."
        )
    if not all(
        isinstance(item, Mapping)
        for item in (
            candidate_snapshot,
            protected_candidate_values,
            tsenta_resume_profile,
        )
    ):
        raise TypeError("Candidate and MUNSHI Relay profile evidence must be mappings.")
    candidate = _candidate_projection(candidate_snapshot, protected_candidate_values)
    tsenta = _tsenta_projection(tsenta_resume_profile)
    statuses: dict[str, str] = {}
    for field in sorted(set(candidate) | set(tsenta)):
        left = _canonical_profile_value(field, candidate.get(field))
        right = _canonical_profile_value(field, tsenta.get(field))
        if right in (None, [], {}):
            statuses[field] = (
                "MISSING_TSENTA" if left not in (None, [], {}) else "UNAVAILABLE"
            )
        elif left in (None, [], {}):
            statuses[field] = "NEEDS_MUNSHI_REVIEW"
        elif left == right:
            statuses[field] = "MATCH"
        else:
            statuses[field] = "CONFLICT_REQUIRES_REVIEW"
    return {
        "contract": "munshi-tsenta-profile-comparison-v2",
        "source": source,
        "candidate_profile_revision": int(
            candidate_snapshot.get("profile_revision") or 0
        ),
        "field_statuses": statuses,
        "matched_fields": sum(value == "MATCH" for value in statuses.values()),
        "conflicts": sum(
            value == "CONFLICT_REQUIRES_REVIEW" for value in statuses.values()
        ),
        "missing_tsenta": sum(value == "MISSING_TSENTA" for value in statuses.values()),
        "needs_munshi_review": sum(
            value == "NEEDS_MUNSHI_REVIEW" for value in statuses.values()
        ),
        "raw_profile_values_in_output": False,
        "candidate_truth_modified": False,
        "tsenta_account_modified": False,
        "application_submitted": False,
    }


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
        raise PermissionError("MUNSHI Relay profile read requires user authorization")
    if not isinstance(confirmed_munshi_facts, Mapping) or not isinstance(
        observed_tsenta_fields, Mapping
    ):
        raise TypeError("Profile evidence must be mappings")
    if any(
        any(blocked in str(key).casefold() for blocked in _SENSITIVE_NAMES)
        for key in observed_tsenta_fields
    ):
        raise ValueError("Sensitive credential or ID field in MUNSHI Relay observation")
    if len(observed_tsenta_fields) > 100:
        raise ValueError("MUNSHI Relay field observations exceed read-only budget")
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

    Never send to MUNSHI Relay until its *actual* authorized capabilities, endpoint,
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
