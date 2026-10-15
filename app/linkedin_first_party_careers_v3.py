"""First-party employer careers evidence — no guessed employer sites.

A verified employer-owned careers page can give us ATS board hints. URLs found
in untrusted LinkedIn job descriptions remain hints, never verified destinations.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.linkedin_employer_careers_v2 import (
    BoardHint,
    board_hints_from_official_careers_html,
)
from app.linkedin_employer_link_resolver_v1 import (
    ats_from_url,
    hostname_matches,
    normalize,
)

URL_RE = re.compile(r"https://[^\s<>'\"\]\[\\]+", re.IGNORECASE)
MAX_HTML_BYTES = 3_000_000
MAX_SEARCH_QUERY = 180


def strict_public_hostname(value: str) -> str | None:
    """Return a strictly validated public DNS hostname, never an IP literal."""
    host = str(value or "").strip().lower().rstrip(".")
    if (
        not host
        or len(host) > 253
        or not re.fullmatch(r"[a-z0-9.-]+", host)
        or "." not in host
        or host.startswith(".")
        or ".." in host
        or host.endswith(
            (".localhost", ".local", ".internal", ".onion", ".test", ".invalid")
        )
    ):
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    for label in host.split("."):
        if not 1 <= len(label) <= 63 or label.startswith("-") or label.endswith("-"):
            return None
    return host


def validated_first_party_url(value: Any, approved_domain: str) -> str | None:
    """A URI is first party only if the HTTPS host is on the approved domain."""
    approved = strict_public_hostname(approved_domain)
    try:
        url = str(value or "").strip()
        parts = urlsplit(url)
        host = strict_public_hostname(parts.hostname or "")
        if (
            not approved
            or not host
            or not hostname_matches(host, approved)
            or parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
            or len(url) > 4096
        ):
            return None
        return url
    except ValueError:
        return None


@dataclass(frozen=True)
class ApprovedEmployerSite:
    """Approved only after independent owner verification outside this module.

    approval_reference must be the durable identifier of the human/signed
    registry approval. Search results and LinkedIn text CANNOT set approval.
    """

    company_name: str
    official_domain: str
    careers_url: str
    approval_source: str
    approval_reference: str


@dataclass(frozen=True)
class CareerSiteResult:
    company_name: str
    careers_url: str
    board_hints: list[BoardHint]
    evidence_status: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_approved_site(site: ApprovedEmployerSite) -> str:
    if site.approval_source not in {"human_verified", "signed_registry"}:
        raise ValueError("Unapproved employer-domain provenance")
    if not site.approval_reference or len(site.approval_reference) > 200:
        raise ValueError("Missing employer approval audit reference")
    if not normalize(site.company_name):
        raise ValueError("Employer identity missing")
    url = validated_first_party_url(site.careers_url, site.official_domain)
    if not url:
        raise ValueError("Careers URL is not on the verified first-party domain")
    return url


def discover_from_approved_html(
    site: ApprovedEmployerSite, html_text: str
) -> CareerSiteResult:
    """Offline inspect an approved first-party HTML snapshot; no HTTP here."""
    verified_url = validate_approved_site(site)
    if len(html_text.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("Careers snapshot too large")
    hints = board_hints_from_official_careers_html(
        html_text,
        verified_careers_url=verified_url,
        verified_employer_host=site.official_domain,
    )
    return CareerSiteResult(
        company_name=site.company_name,
        careers_url=verified_url,
        board_hints=hints,
        evidence_status="HINTS_ONLY",
        evidence={
            "approval_reference": site.approval_reference,
            "source": site.approval_source,
            "hint_count": len(hints),
            "application_url_verified": False,
        },
    )


def description_url_hints(
    description: Any, *, max_urls: int = 30
) -> list[dict[str, str]]:
    """Extract public HTTPS URL hints from LinkedIn descriptions; never verify."""
    data = unescape(str(description or ""))[:120_000]
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in URL_RE.findall(data):
        url = item.rstrip(".,;:!)}")
        try:
            parts = urlsplit(url)
            host = strict_public_hostname(parts.hostname or "")
        except ValueError:
            continue
        if (
            not host
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
        ):
            continue
        if host in {"linkedin.com", "www.linkedin.com"}:
            continue
        if url in seen:
            continue
        seen.add(url)
        output.append(
            {
                "url": url,
                "host": host,
                "ats_provider_hint": ats_from_url(url) or "",
                "confidence": "UNVERIFIED_DESCRIPTION_HINT",
            }
        )
        if len(output) >= max_urls:
            break
    return output


def employer_search_queries(company_name: str, job_title: str) -> tuple[str, ...]:
    """Create plain search phrases for a separately authorized search provider.

    This does not issue requests or conclude a company is the official employer.
    """
    company = " ".join(str(company_name or "").split())[:70]
    title = " ".join(str(job_title or "").split())[:80]
    if not company or not title:
        return ()
    return (
        f"{company} official careers {title}"[:MAX_SEARCH_QUERY],
        f"{company} {title} apply site:jobs.ashbyhq.com OR site:job-boards.greenhouse.io"[
            :MAX_SEARCH_QUERY
        ],
    )


def candidate_job_urls_from_official_html(
    site: ApprovedEmployerSite, html_text: str
) -> list[dict[str, str]]:
    """Recognize first-party job URL candidates in anchors, not Apply targets.

    Official job-page link is distinct from the final application POST or
    Apply button target; this never outputs a verified apply_url.
    """
    from html.parser import HTMLParser

    approved_url = validate_approved_site(site)

    class Anchors(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.links = []

        def handle_starttag(self, tag, attrs):
            if tag != "a":
                return
            props = dict(attrs)
            href = props.get("href")
            if href:
                self.links.append(href)

    if len(html_text.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("Careers snapshot too large")
    parser = Anchors()
    parser.feed(html_text)
    output = []
    seen = set()
    for href in parser.links[:500]:
        candidate = urljoin(approved_url, href)
        accepted = validated_first_party_url(candidate, site.official_domain)
        if (
            accepted
            and accepted not in seen
            and re.search(
                r"/(jobs?|careers?|positions?|opportunities)/",
                urlsplit(accepted).path.lower() + "/",
            )
        ):
            seen.add(accepted)
            output.append(
                {
                    "employer_job_url": accepted,
                    "employer_apply_url": "",
                    "status": "CANDIDATE",
                    "source": "approved_careers_html_anchor",
                }
            )
    return output


def resolve_linkedin_job_from_approved_careers(
    linkedin_job: dict[str, Any],
    site: ApprovedEmployerSite,
    *,
    allow_network: bool = False,
    approved_site_fetcher: Any = None,
    ats_opener: Any = None,
):
    """Resolve exactly one job via verified employer website -> documented ATS API.

    Production must authorize the ApprovedEmployerSite using a signed registry
    before calling. No result can be used by auto-apply without lifecycle
    preflight, active-posting recheck, and candidate approval.
    """
    from app.linkedin_employer_link_resolver_v1 import LinkResolution, _base
    from app.linkedin_official_site_fetch_v3 import fetch_approved_careers_page
    from app.linkedin_public_ats_feed_v1 import resolve_from_verified_board

    validate_approved_site(site)
    if normalize(linkedin_job.get("company_name")) != normalize(site.company_name):
        return _base(
            linkedin_job,
            "UNAVAILABLE",
            "verified_first_party_careers",
            {"reason": "linkedin_employer_identity_mismatch"},
        )
    if not allow_network:
        return _base(
            linkedin_job,
            "UNKNOWN",
            "verified_first_party_careers",
            {"reason": "network_opt_in_required"},
        )
    fetcher = approved_site_fetcher or fetch_approved_careers_page
    try:
        html = fetcher(site)
        hints = discover_from_approved_html(site, html).board_hints
    except (OSError, ValueError) as error:
        return _base(
            linkedin_job,
            "UNKNOWN",
            "verified_first_party_careers",
            {
                "reason": "official_careers_site_read_failed",
                "error_type": type(error).__name__,
            },
        )
    if not hints:
        return _base(
            linkedin_job,
            "UNKNOWN",
            "verified_first_party_careers",
            {
                "reason": "no_public_static_ats_board_hints",
                "needs_controlled_renderer": True,
                "official_careers_url": site.careers_url,
            },
        )
    if len(hints) != 1:
        return _base(
            linkedin_job,
            "AMBIGUOUS",
            "verified_first_party_careers",
            {"reason": "multiple_public_ats_boards", "board_count": len(hints)},
        )
    hint = hints[0]
    try:
        candidate = resolve_from_verified_board(
            linkedin_job,
            provider=hint.provider,
            board_name=hint.board_name,
            verified_employer_name=site.company_name,
            opener=ats_opener,
        )
    except (OSError, ValueError) as error:
        return _base(
            linkedin_job,
            "UNKNOWN",
            "verified_first_party_careers",
            {
                "reason": "official_ats_job_read_failed",
                "error_type": type(error).__name__,
            },
        )
    return LinkResolution(
        linkedin_job_url=candidate.linkedin_job_url,
        employer_job_url=candidate.employer_job_url,
        employer_apply_url=candidate.employer_apply_url,
        ats_platform=candidate.ats_platform,
        resolution_status=candidate.resolution_status,
        resolution_source="verified_first_party_careers",
        evidence={
            **candidate.evidence,
            "official_careers_url": site.careers_url,
            "employer_approval_reference": site.approval_reference,
            "ats_board_from_official_website": True,
            "submit_authorized": False,
        },
    )
