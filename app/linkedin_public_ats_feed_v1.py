"""Bounded public READ-only ATS job-board lookups for link resolution.

Board identifiers must come from already verified employer boards. Never derive
tokens by guessing from a LinkedIn company name. No LinkedIn requests and no POST.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.linkedin_employer_link_resolver_v1 import (
    LinkResolution,
    official_feed_jobs,
    resolve_stored,
)

BOARD_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
MAX_BYTES = 2_000_000
ASHBY_MAX_BYTES = 16_000_000  # large public job boards can exceed 10 MB
PUBLIC_ENDPOINTS = {
    "Greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs",
    "Lever": "https://api.lever.co/v0/postings/{}?mode=json",
    "Ashby": "https://api.ashbyhq.com/posting-api/job-board/{}",
    "SmartRecruiters": "https://api.smartrecruiters.com/v1/companies/{}/postings?destination=PUBLIC&limit=100",
}


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("Public ATS feed attempted a redirect; refusing to follow it")


def verified_board_endpoint(provider: str, board_name: str) -> str:
    """Allowlist fixed HTTPS API origins and strictly validate board identifiers."""
    if provider not in PUBLIC_ENDPOINTS:
        raise ValueError("ATS provider unsupported by public-feed resolver")
    if not BOARD_NAME_RE.fullmatch(board_name):
        raise ValueError("Verified employer board identifier is malformed")
    return PUBLIC_ENDPOINTS[provider].format(board_name)


def fetch_official_jobs(
    provider: str,
    board_name: str,
    company_name: str,
    *,
    opener: Any = None,
) -> list[dict[str, Any]]:
    """Use only documented public GET endpoints; fail closed on redirects/errors."""
    url = verified_board_endpoint(provider, board_name)
    response_cap = ASHBY_MAX_BYTES if provider == "Ashby" else MAX_BYTES
    client = opener if opener is not None else build_opener(NoRedirects())
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "MUNSHI-Employer-Link-Resolver/1.0",
        },
        method="GET",
    )
    with client.open(request, timeout=7) as response:
        if response.status != 200:
            raise ValueError("Public ATS feed did not return HTTP 200")
        if int(response.headers.get("Content-Length", "0")) > response_cap:
            raise ValueError("ATS feed exceeded maximum response size")
        raw = response.read(response_cap + 1)
        if len(raw) > response_cap:
            raise ValueError("ATS feed exceeded maximum response size")
    payload = json.loads(raw)
    return official_feed_jobs(provider, payload, company_name)


def resolve_from_verified_board(
    linkedin_job: dict[str, Any],
    *,
    provider: str,
    board_name: str,
    verified_employer_name: str,
    opener: Any = None,
) -> LinkResolution:
    """Use only a board whose employer identity was separately verified."""
    from app.linkedin_employer_link_resolver_v1 import _base, normalize

    if normalize(linkedin_job.get("company_name")) != normalize(verified_employer_name):
        return _base(
            linkedin_job,
            "UNAVAILABLE",
            "official_public_ats",
            {"reason": "employer_board_mismatch"},
        )
    jobs = fetch_official_jobs(
        provider, board_name, verified_employer_name, opener=opener
    )
    if provider == "Greenhouse":
        from app.linkedin_employer_link_resolver_v1 import location_agrees

        shortlisted = [
            job
            for job in jobs
            if normalize(job.get("title")) == normalize(linkedin_job.get("title"))
            and location_agrees(
                job.get("location_raw"), linkedin_job.get("location_raw")
            )
            and job.get("source_job_id") is not None
        ]
        # Cap follow-up reads. Multiple matching postings remain ambiguous.
        for candidate in shortlisted[:3]:
            try:
                candidate["description_raw"] = fetch_greenhouse_job_detail(
                    board_name, candidate["source_job_id"], opener=opener
                )
            except (OSError, ValueError):
                # Missing/blocked detail means candidate-only, never verified.
                candidate["description_raw"] = None
    return resolve_stored(linkedin_job, jobs, official=True, official_provider=provider)


def verified_board_from_existing_job(
    employer_name: str,
    existing_job: dict[str, Any],
) -> tuple[str, str] | None:
    """Derive known provider board identity from an already-indexed ATS job.

    Never infer a board ID from a LinkedIn name or external search result.
    Both the employer name and provider-hosted URL must agree with the source.
    """
    from urllib.parse import urlsplit

    from app.linkedin_employer_link_resolver_v1 import normalize

    if normalize(existing_job.get("company_name")) != normalize(employer_name):
        return None
    source = str(existing_job.get("source") or "")
    raw_url = str(existing_job.get("job_url") or existing_job.get("apply_url") or "")
    parts = urlsplit(raw_url)
    host = str(parts.hostname or "").casefold()
    slug = parts.path.strip("/").split("/")[0]
    expected = {
        "Greenhouse": ("greenhouse.io", "greenhouse.com"),
        "Lever": ("lever.co",),
        "Ashby": ("ashbyhq.com",),
        "SmartRecruiters": ("smartrecruiters.com",),
    }
    for provider, hosts in expected.items():
        if not source.casefold().startswith(provider.casefold() + "/"):
            continue
        source_slug = source.split("/", 1)[1]
        if (
            BOARD_NAME_RE.fullmatch(source_slug)
            and source_slug.casefold() == slug.casefold()
            and any(host == h or host.endswith("." + h) for h in hosts)
        ):
            return provider, source_slug
    return None


def greenhouse_detail_url(board_name: str, posting_id: Any) -> str:
    """Only construct documented official Greenhouse job-detail GET URLs."""
    if not BOARD_NAME_RE.fullmatch(board_name):
        raise ValueError("Unverified Greenhouse board token")
    value = str(posting_id)
    if not re.fullmatch(r"[0-9]{1,18}", value) or int(value) < 1:
        raise ValueError("Untrusted Greenhouse posting identifier")
    return f"https://boards-api.greenhouse.io/v1/boards/{board_name}/jobs/{value}?content=true"


def fetch_greenhouse_job_detail(
    board_name: str, posting_id: Any, *, opener: Any = None
) -> str | None:
    """Fetch a single published job's description from the documented public API."""
    from html import unescape
    from html.parser import HTMLParser

    class PlainText(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    endpoint = greenhouse_detail_url(board_name, posting_id)
    client = opener if opener is not None else build_opener(NoRedirects())
    request = Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "User-Agent": "MUNSHI-Employer-Link-Resolver/1.0",
        },
        method="GET",
    )
    with client.open(request, timeout=7) as response:
        if response.status != 200:
            raise ValueError("Greenhouse job detail did not return HTTP 200")
        if int(response.headers.get("Content-Length", "0")) > MAX_BYTES:
            raise ValueError("Greenhouse detail response exceeded maximum size")
        payload_bytes = response.read(MAX_BYTES + 1)
    if len(payload_bytes) > MAX_BYTES:
        raise ValueError("Greenhouse detail response exceeded maximum size")
    payload = json.loads(payload_bytes)
    if not isinstance(payload, dict) or str(payload.get("id")) != str(posting_id):
        raise ValueError("Greenhouse returned the wrong posting identity")
    html_content = payload.get("content")
    if not isinstance(html_content, str):
        return None
    parser = PlainText()
    parser.feed(unescape(html_content))
    return " ".join(part.strip() for part in parser.parts if part.strip())[:120_000]
