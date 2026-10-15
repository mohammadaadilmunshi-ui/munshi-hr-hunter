"""Cryptographically verified employer-domain approval for public ATS discovery.

Ed25519 signature establishes a trusted operator's approval of the exact site
fields; it does NOT itself prove domain ownership or authorize job submission.
No private signing keys belong in this repository or production app process.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.linkedin_first_party_careers_v3 import (
    ApprovedEmployerSite,
    validate_approved_site,
)

_APPROVAL_FIELDS = frozenset(
    {
        "company_name",
        "official_domain",
        "careers_url",
        "evidence_url",
        "reviewer_id",
        "reviewed_at",
        "expires_at",
    }
)
_SCHEMA = "munshi-linkedin-employer-domain-approval-v1"


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("Approval timestamp must be ISO8601 UTC")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid approval timestamp") from error
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("Approval timestamp must be explicitly UTC")
    return result.astimezone(timezone.utc)


def canonical_approval_bytes(payload: Mapping[str, Any]) -> bytes:
    """Encode exact signed site attributes; reject unknown fields and schema."""
    if not isinstance(payload, Mapping) or set(payload) != _APPROVAL_FIELDS:
        raise ValueError("Employer approval has missing or unexpected fields")
    if any(
        not isinstance(payload[field], str) or not payload[field].strip()
        for field in payload
    ):
        raise ValueError("Employer approval contains empty/invalid fields")
    for field in ("reviewer_id", "evidence_url"):
        if len(payload[field]) > 2048:
            raise ValueError("Employer approval evidence field too long")
    if len(payload["company_name"]) > 200 or len(payload["official_domain"]) > 253:
        raise ValueError("Employer identity is oversized")
    reviewed = _parse_utc(payload["reviewed_at"])
    expires = _parse_utc(payload["expires_at"])
    if expires <= reviewed or expires - reviewed > timedelta(days=90):
        raise ValueError("Employer approval lifetime must be at most 90 days")
    site = ApprovedEmployerSite(
        company_name=payload["company_name"],
        official_domain=payload["official_domain"],
        careers_url=payload["careers_url"],
        approval_source="signed_registry",
        approval_reference="pending-signature",
    )
    validate_approved_site(site)
    from urllib.parse import urlsplit

    evidence = urlsplit(payload["evidence_url"])
    if (
        evidence.scheme != "https"
        or not evidence.hostname
        or evidence.username
        or evidence.password
    ):
        raise ValueError("Employer domain approval lacks HTTPS ownership evidence")
    return json.dumps(
        {"schema": _SCHEMA, "site": dict(payload)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _base64url(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("Malformed approval signature")
    try:
        return base64.b64decode(value, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Malformed approval signature") from error


def verify_signed_employer_approval(
    record: Mapping[str, Any],
    trusted_public_keys: Mapping[str, bytes],
    *,
    at: datetime | None = None,
    revoked_approval_ids: frozenset[str] = frozenset(),
) -> ApprovedEmployerSite:
    """Check operator signature, exact domain binding, freshness and revocation.

    Caller must configure trust roots outside the repo; no API key or private key
    is loaded or used in this verifier. Persist only signed approvals and audit log.
    """
    if not isinstance(record, Mapping) or set(record) != {
        "schema",
        "key_id",
        "site",
        "signature",
    }:
        raise ValueError("Invalid signed approval envelope")
    if record["schema"] != _SCHEMA:
        raise ValueError("Unsupported approval schema")
    key_id = record["key_id"]
    if not isinstance(key_id, str) or key_id not in trusted_public_keys:
        raise ValueError("Unknown employer approval signing authority")
    payload = record["site"]
    message = canonical_approval_bytes(payload)
    approval_id = hashlib.sha256(message).hexdigest()
    if approval_id in revoked_approval_ids:
        raise ValueError("Employer domain approval has been revoked")
    timestamp = at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("Verification time must be timezone-aware")
    timestamp = timestamp.astimezone(timezone.utc)
    reviewed = _parse_utc(payload["reviewed_at"])
    expires = _parse_utc(payload["expires_at"])
    if timestamp < reviewed or timestamp >= expires:
        raise ValueError("Employer approval is not yet valid or has expired")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(trusted_public_keys[key_id])
        public_key.verify(_base64url(record["signature"]), message)
    except (InvalidSignature, ValueError) as error:
        raise ValueError("Employer approval signature is invalid") from error
    return ApprovedEmployerSite(
        company_name=payload["company_name"],
        official_domain=payload["official_domain"],
        careers_url=payload["careers_url"],
        approval_source="signed_registry",
        approval_reference=approval_id,
    )


def resolve_from_signed_approval(
    linkedin_job: dict[str, Any],
    signed_record: Mapping[str, Any],
    trusted_public_keys: Mapping[str, bytes],
    *,
    at: datetime | None = None,
    revoked_approval_ids: frozenset[str] = frozenset(),
    allow_network: bool = False,
    approved_site_fetcher: Any = None,
    ats_opener: Any = None,
    allow_offline_js: bool = False,
    offline_js_renderer: Any = None,
):
    """Production-intended gateway: only cryptographically approved sites enter."""
    from app.linkedin_first_party_careers_v3 import (
        resolve_linkedin_job_from_approved_careers,
    )

    site = verify_signed_employer_approval(
        signed_record,
        trusted_public_keys,
        at=at,
        revoked_approval_ids=revoked_approval_ids,
    )
    from dataclasses import replace

    result = resolve_linkedin_job_from_approved_careers(
        linkedin_job,
        site,
        allow_network=allow_network,
        approved_site_fetcher=approved_site_fetcher,
        ats_opener=ats_opener,
        allow_offline_js=allow_offline_js,
        offline_js_renderer=offline_js_renderer,
    )
    if result.resolution_status == "VERIFIED":
        return replace(
            result,
            evidence={**result.evidence, "signed_approval_validated": True},
        )
    return result
