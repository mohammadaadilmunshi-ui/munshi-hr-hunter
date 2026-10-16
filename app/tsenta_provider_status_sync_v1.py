"""Bounded, privacy-safe Tsenta application status synchronization.

Reconcile an already-owned application using one-way hashed provider IDs, never
storing Tsenta credentials, raw IDs, applicant details, employer URLs or answers.

This module is read-only toward Tsenta.  It will not create applications,
approve forms, upload files, retry jobs, or claim independent employer receipts.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from typing import Any

from app import application_provider_router_v1 as routes
from app.tsenta_mcp_adapter_v1 import TsentaMcpAdapter

MAX_PAGES = 5
PAGE_LIMIT = 25
_STATUS = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


def sync_existing_application_status(
    *,
    route_id: str,
    adapter: TsentaMcpAdapter,
    max_pages: int = 3,
) -> dict[str, Any]:
    """Return privacy-safe route state after bounded, authorized MCP reads.

    Requires a previously bound provider application ID hash; never guesses a
    match from company/title/URL or creates a new application.
    """
    if not 1 <= int(max_pages) <= MAX_PAGES:
        raise ValueError("Status sync pagination must be bounded.")
    route = routes.get_route(route_id)
    if route["provider"] != "TSENTA":
        raise PermissionError("Only Tsenta-owned routes may be synced with Tsenta.")
    if route["state"] == "EMPLOYER_CONFIRMED":
        return {
            "route_id": route_id,
            "state": "EMPLOYER_CONFIRMED",
            "matched": False,
            "pages_checked": 0,
            "independent_receipt_verified": True,
        }
    target_hash = str(route.get("provider_application_id_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", target_hash):
        raise RuntimeError("Provider application identity was not securely bound.")

    for page in range(1, int(max_pages) + 1):
        payload = adapter.list_applications(page=page, limit=PAGE_LIMIT)
        records = payload.get("applications")
        if not isinstance(records, list):
            raise TypeError("Tsenta application listing has unsupported shape.")
        for record in records:
            if not isinstance(record, Mapping):
                continue
            provider_id = record.get("applicationId") or record.get("id")
            if not isinstance(provider_id, str) or not provider_id:
                continue
            candidate_hash = hashlib.sha256(provider_id.encode()).hexdigest()
            if not hmac.compare_digest(candidate_hash, target_hash):
                continue
            state = str(record.get("status") or "").strip().upper()
            if not _STATUS.fullmatch(state):
                raise RuntimeError("Tsenta provided an invalid machine status.")
            error_code = record.get("errorCode")
            safe = routes.record_provider_status(
                route_id=route_id,
                provider_status=state,
                provider_application_id=provider_id,
                error_code=error_code if isinstance(error_code, str) else None,
            )
            return {
                "route_id": route_id,
                "state": str(safe["state"]),
                "provider_status": str(safe["provider_status"]),
                "matched": True,
                "pages_checked": page,
                "independent_receipt_verified": safe["state"] == "EMPLOYER_CONFIRMED",
            }
        # There is no further page when fewer than limit records were returned.
        if len(records) < PAGE_LIMIT:
            break
    return {
        "route_id": route_id,
        "state": str(route["state"]),
        "matched": False,
        "pages_checked": min(int(max_pages), page),
        "independent_receipt_verified": False,
    }
