"""Read-only signed employer-domain registry: no runtime enrollment or signing.

The registry manifest is untrusted data. Every selected approval is checked
against externally provisioned Ed25519 trust roots and revoked IDs. Private
signing keys must never enter this service, GitHub, or environment logs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from app.linkedin_employer_link_resolver_v1 import normalize
from app.linkedin_first_party_careers_v3 import ApprovedEmployerSite
from app.linkedin_signed_domain_approvals_v4 import verify_signed_employer_approval


def load_signed_registry(path: Path, *, max_bytes: int = 512_000) -> tuple[dict, ...]:
    """Load a size-capped JSON collection only; approval verification is separate."""
    if not isinstance(path, Path) or not path.is_file():
        raise ValueError("Signed employer registry manifest unavailable")
    if path.stat().st_size > max_bytes:
        raise ValueError("Signed employer registry exceeds size cap")
    try:
        manifest = json.loads(path.read_bytes())
    except (ValueError, UnicodeError) as error:
        raise ValueError("Malformed employer registry manifest") from error
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "approvals"}:
        raise ValueError("Unknown employer registry envelope")
    if manifest["schema"] != "munshi-approved-employer-domains-v1":
        raise ValueError("Unsupported employer registry version")
    records = manifest["approvals"]
    if not isinstance(records, list) or len(records) > 1000:
        raise ValueError("Invalid employer-domain registry records")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("Signed employer registry contains non-record entries")
    return tuple(records)


def select_verified_company_site(
    company_name: str,
    signed_records: tuple[Mapping[str, Any], ...],
    trusted_public_keys: Mapping[str, bytes],
    *,
    at: datetime | None = None,
    revoked_ids: frozenset[str] = frozenset(),
) -> ApprovedEmployerSite:
    """No known company => fail closed. Multiple valid sites => ambiguity.

    A tampered claimed approval matching the requested company is an error;
    it is never silently skipped in favor of a different valid record.
    """
    target = normalize(company_name)
    if not target:
        raise ValueError("Employer identity missing")
    matches: dict[str, ApprovedEmployerSite] = {}
    for record in signed_records:
        payload = record.get("site") if isinstance(record, Mapping) else None
        if (
            not isinstance(payload, Mapping)
            or normalize(payload.get("company_name")) != target
        ):
            continue
        site = verify_signed_employer_approval(
            record,
            trusted_public_keys,
            at=at,
            revoked_approval_ids=revoked_ids,
        )
        if site.careers_url in matches:
            raise ValueError("Duplicate signed employer domain approval")
        matches[site.careers_url] = site
    if not matches:
        raise KeyError("No signed employer-domain approval for requested company")
    if len(matches) > 1:
        raise ValueError("Ambiguous signed employer-domain approvals")
    return next(iter(matches.values()))
