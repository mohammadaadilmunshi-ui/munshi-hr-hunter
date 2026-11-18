"""Compatibility boundary for retired paid Google discovery.

No credential lookup, HTTP client or paid SDK belongs on this path.
"""
from __future__ import annotations

from typing import Any
from app.discovery_policy import require_free_provider


def discover_serpapi_key() -> tuple[str, str]:
    return "", "disabled_by_policy"


def provider_status() -> dict[str, Any]:
    return {
        "serpapi_available": False,
        "credential_source": "disabled_by_policy",
        "credential_value_logged": False,
        "provider_chain": ["jobspy"],
        "paid_api_calls": 0,
    }


def serpapi_google_jobs(
    *, query: str, location: str, timeout_seconds: int = 45,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    require_free_provider("serpapi")
    raise AssertionError("Paid discovery boundary must never open")
