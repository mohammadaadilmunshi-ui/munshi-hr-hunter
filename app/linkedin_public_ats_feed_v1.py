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
        if int(response.headers.get("Content-Length", "0")) > MAX_BYTES:
            raise ValueError("ATS feed exceeded maximum response size")
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
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
