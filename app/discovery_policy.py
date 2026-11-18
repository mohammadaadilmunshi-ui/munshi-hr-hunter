"""Fail-closed cost policy; credentials and UI switches never grant permission."""
from __future__ import annotations

import re
from urllib.parse import urlsplit


class DiscoveryPolicyError(ValueError):
    pass


def require_free_provider(provider: str, *, cost_mode: str = "free") -> None:
    name = re.sub(r"[^a-z0-9]", "", str(provider).casefold())
    cost = str(cost_mode).strip().casefold()
    proven_free = cost == "free" or (name == "usajobs" and cost == "free_key_required")
    if (
        any(token in name for token in ("serpapi", "apify"))
        or not proven_free
    ):
        raise DiscoveryPolicyError("Discovery provider disabled by free-only policy")


def require_free_url(url: str) -> None:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if any(host == domain or host.endswith("." + domain) for domain in (
        "serpapi.com", "apify.com", "apify.net",
    )):
        raise DiscoveryPolicyError("Discovery endpoint disabled by free-only policy")
