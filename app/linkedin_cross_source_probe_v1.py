"""Bounded, read-only official ATS page inspection for unresolved cross-source leads.

A successful page fetch is *not* a verified exact-job link. Independent signer
approval and exact-job evidence validation remain required before persistence.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from urllib.parse import urlsplit

from app.linkedin_employer_link_resolver_v1 import ats_from_url, external_url, normalize
from app.linkedin_public_ats_feed_v1 import NoRedirects

MAX_BYTES = 262144
MAX_PROBES = 25
TIMEOUT_SECONDS = 6


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.headings: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() == "meta":
            props = {str(k).lower(): v for k, v in attrs}
            if props.get("property") in {"og:title", "twitter:title"} and props.get("content"):
                self.headings.append(str(props["content"])[:300])

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.headings.append(data[:300])


def inspect_candidate(url: str, *, title: str, opener: Any = None) -> dict[str, Any]:
    """Never follow redirects, post, authenticate, or fetch arbitrary employer URLs."""
    target = external_url(url)
    platform = ats_from_url(target) if target else None
    result: dict[str, Any] = {
        "platform": platform,
        "status": "UNSUPPORTED_OR_UNTRUSTED_HOST",
        "verified": False,
        "submission_authorized": False,
        "title_hint_matches": False,
    }
    if not target or not platform:
        return result
    request = Request(
        target,
        headers={"User-Agent": "MUNSHI-Employer-Link-Checker/1.0", "Accept": "text/html"},
        method="GET",
    )
    client = opener if opener is not None else build_opener(NoRedirects())
    try:
        with client.open(request, timeout=TIMEOUT_SECONDS) as response:
            if int(getattr(response, "status", 200)) != 200:
                result["status"] = "HTTP_UNAVAILABLE"
                return result
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                result["status"] = "RESPONSE_TOO_LARGE"
                return result
            response_url = response.geturl() if hasattr(response, "geturl") else target
            if response_url != target:
                result["status"] = "REDIRECT_NOT_ALLOWED"
                return result
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        result["status"] = "PAGE_BLOCKED_OR_UNAVAILABLE"
        return result
    parser = _TitleParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    expected = normalize(title)
    result["title_hint_matches"] = bool(
        expected and len(expected) >= 5 and any(
            expected in normalize(h) for h in parser.headings
        )
    )
    result["status"] = (
        "TITLE_HINT_FOUND_NEEDS_SIGNED_REVIEW"
        if result["title_hint_matches"]
        else "PAGE_REACHABLE_NEEDS_SIGNED_REVIEW"
    )
    return result
