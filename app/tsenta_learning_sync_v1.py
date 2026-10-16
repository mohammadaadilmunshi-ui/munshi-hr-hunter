"""Explicit, authorized, read-only Tsenta observation intake across ATSs.

Imports up to 15 already-existing Tsenta account applications per call. Uses
MCP read tools only; no external submit, retry, or candidate answer storage.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app import tsenta_observation_v1 as observations
from app.tsenta_mcp_adapter_v1 import _unwrap
from app.tsenta_oauth_v1 import TsentaOAuthError, _token_for_current_owner

MAX_PAGE = 20
MAX_BATCH = 15
_APPLICATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _as_mapping(raw: Any, tool: str) -> dict[str, Any]:
    unwrapped = _unwrap(raw, tool_name=tool)
    if not isinstance(unwrapped, Mapping):
        raise TsentaOAuthError("Tsenta returned an unsupported application response.")
    return dict(unwrapped)


def _ats(record: Mapping[str, Any], status: Mapping[str, Any]) -> str:
    for row in (status, record):
        for key in ("atsType", "ats", "platform", "applicationPlatform"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                return observations._ats_slug(value)
    return "unknown"


def sync_observations_from_mcp(*, mcp: Any, page: int = 1,
                               limit: int = MAX_BATCH) -> dict[str, Any]:
    """Boundary supports injected read-only clients for deterministic tests."""
    if not 1 <= int(page) <= MAX_PAGE or not 1 <= int(limit) <= MAX_BATCH:
        raise ValueError("Tsenta learning batches must be small and paginated.")
    listed = _as_mapping(
        mcp.call_read("list_applications", {
            "filter": "all", "includeCounts": False,
            "page": int(page), "limit": int(limit),
        }),
        "list_applications",
    )
    records = listed.get("applications")
    if not isinstance(records, list) or len(records) > int(limit):
        raise TsentaOAuthError("Tsenta application list is not safely bounded.")

    incoming: list[dict[str, Any]] = []
    status_observed = reviews_observed = unavailable_reviews = skipped = 0
    seen: set[str] = set()
    for row in records:
        if not isinstance(row, Mapping):
            skipped += 1
            continue
        app_id = row.get("applicationId") or row.get("id")
        if not isinstance(app_id, str) or not _APPLICATION_ID.fullmatch(app_id):
            skipped += 1
            continue
        if app_id in seen:
            continue
        seen.add(app_id)
        try:
            status = _as_mapping(
                mcp.call_read("get_application_status", {"applicationId": app_id}),
                "get_application_status",
            )
            ats = _ats(row, status)
            incoming.append(
                observations.observation_from_status(
                    provider_application_id=app_id, ats_type=ats, status=status,
                )
            )
            status_observed += 1
        except (RuntimeError, ValueError, TypeError, KeyError):
            skipped += 1
            continue

        try:
            review = _as_mapping(
                mcp.call_read("get_application_review", {"applicationId": app_id}),
                "get_application_review",
            )
            fields = observations.observations_from_review(
                provider_application_id=app_id, ats_type=ats, review=review,
            )
            incoming.extend(fields)
            if review.get("awaitingReview") is True:
                reviews_observed += 1
            else:
                unavailable_reviews += 1
        except (RuntimeError, ValueError, TypeError, KeyError):
            # A completed provider application often has no review screen.
            # Never invent fields for it or reinterpret status as a receipt.
            unavailable_reviews += 1
    result = observations.persist_observations(incoming) if incoming else {
        "inserted": 0, "replayed": 0,
    }
    return {
        "version": "munshi-tsenta-authorized-learning-sync-v1",
        "page": int(page),
        "listed_applications": len(records),
        "distinct_applications_checked": len(seen),
        "status_observations": status_observed,
        "reviews_with_fields": reviews_observed,
        "reviews_unavailable": unavailable_reviews,
        "skipped": skipped,
        "new_observation_rows": result["inserted"],
        "replayed_observation_rows": result["replayed"],
        "employer_receipts_verified": 0,
        "candidate_answers_stored": False,
        "read_only_provider_calls": True,
    }


def sync_authorized_learning(*, page: int = 1, limit: int = MAX_BATCH,
                             client: Any = None) -> dict[str, Any]:
    """Access requires current tenant's explicitly consented backend grant."""
    from app.tsenta_mcp_http_v1 import TsentaMcpReadClient

    token = _token_for_current_owner(client)
    with TsentaMcpReadClient(token, client=client) as mcp:
        mcp.initialize()
        return sync_observations_from_mcp(mcp=mcp, page=page, limit=limit)
