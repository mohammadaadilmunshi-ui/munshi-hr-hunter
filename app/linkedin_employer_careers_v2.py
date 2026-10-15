"""Employer careers/board discovery V2: offline HTML parsing and verified registry bridge.

No LinkedIn access, no guessed company domains, no database mutation.
Only sources independently established as employer-owned may enroll a board.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.linkedin_employer_link_resolver_v1 import (
    LinkResolution,
    _base,
    hostname_matches,
    normalize,
)
from app.linkedin_public_ats_feed_v1 import (
    BOARD_NAME_RE,
    resolve_from_verified_board,
)

KNOWN_ATS_BOARD_HOSTS = {
    "Greenhouse": ("job-boards.greenhouse.io", "boards.greenhouse.io"),
    "Lever": ("jobs.lever.co", "jobs.eu.lever.co"),
    "Ashby": ("jobs.ashbyhq.com",),
    "SmartRecruiters": ("jobs.smartrecruiters.com",),
}
EXPECTED_SOURCE_HOSTS = {
    "Greenhouse": (*KNOWN_ATS_BOARD_HOSTS["Greenhouse"],),
    "Lever": (*KNOWN_ATS_BOARD_HOSTS["Lever"],),
    "Ashby": (*KNOWN_ATS_BOARD_HOSTS["Ashby"],),
    "SmartRecruiters": (*KNOWN_ATS_BOARD_HOSTS["SmartRecruiters"],),
}


@dataclass(frozen=True)
class BoardHint:
    provider: str
    board_name: str
    source_url: str
    confidence: str  # hint, not proof of company ownership

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _board_from_hosted_url(value: Any) -> BoardHint | None:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme != "https" or parsed.username or parsed.password:
            return None
    except ValueError:
        return None
    for provider, valid_hosts in KNOWN_ATS_BOARD_HOSTS.items():
        if host not in valid_hosts:
            continue
        pieces = [p for p in parsed.path.split("/") if p]
        if pieces and BOARD_NAME_RE.fullmatch(pieces[0]):
            return BoardHint(provider, pieces[0], raw, "hint")
    return None


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"a", "iframe"}:
            return
        properties = dict(attrs)
        url = properties.get("href") if tag == "a" else properties.get("src")
        if url:
            self.urls.append(str(url))


def board_hints_from_official_careers_html(
    html: str,
    *,
    verified_careers_url: str,
    verified_employer_host: str,
) -> list[BoardHint]:
    """Return ATS candidates from a *verified* employer career-page HTML snapshot.

    The caller must verify first-party domain provenance before invoking.
    These are discovery hints, never auto-authorized application targets.
    """
    if len(html) > 2_000_000:
        raise ValueError("Careers page HTML exceeds maximum inspection size")
    parts = urlsplit(verified_careers_url)
    host = (parts.hostname or "").casefold().rstrip(".")
    expected_host = verified_employer_host.casefold().rstrip(".")
    if (
        parts.scheme != "https"
        or not host
        or not expected_host
        or not hostname_matches(host, expected_host)
        or parts.username
        or parts.password
    ):
        raise ValueError("Careers page must have verified employer domain provenance")
    collector = _Links()
    collector.feed(html)
    discovered: dict[tuple[str, str], BoardHint] = {}
    for raw in collector.urls:
        url = urljoin(verified_careers_url, raw)
        result = _board_from_hosted_url(url)
        if result:
            discovered[(result.provider, result.board_name.casefold())] = result
    return sorted(
        discovered.values(), key=lambda x: (x.provider, x.board_name.casefold())
    )


def board_binding_from_ats_company_registry(
    record: Mapping[str, Any],
) -> BoardHint | None:
    """Use existing Hunter ATS company registry only when healthy and enabled."""
    if int(record.get("enabled") or 0) != 1:
        return None
    if str(record.get("health_status") or "").casefold() != "healthy":
        return None
    provider_alias = {
        "greenhouse": "Greenhouse",
        "lever": "Lever",
        "ashby": "Ashby",
        "smartrecruiters": "SmartRecruiters",
    }
    provider = provider_alias.get(str(record.get("ats_type") or "").casefold())
    name = str(record.get("board_token") or "").strip()
    if not provider or not BOARD_NAME_RE.fullmatch(name):
        return None
    try:
        page = urlsplit(str(record.get("careers_url") or ""))
        host = (page.hostname or "").casefold().rstrip(".")
        actual_board_name = next((p for p in page.path.split("/") if p), "")
    except ValueError:
        return None
    if (
        page.scheme != "https"
        or page.username
        or page.password
        or host not in EXPECTED_SOURCE_HOSTS[provider]
        or actual_board_name.casefold() != name.casefold()
    ):
        return None
    return BoardHint(provider, name, page.geturl(), "registry_healthy")


def resolve_from_known_company_boards(
    linkedin_job: Mapping[str, Any],
    registered_boards: Iterable[Mapping[str, Any]],
    *,
    provider_opener: Any = None,
    allow_network: bool = False,
) -> LinkResolution:
    """Find the employer's official board while avoiding duplicate/live submission.

    Offline returns UNKNOWN (even if a known board exists) until network is
    explicitly enabled. For multiple boards, fail closed instead of choosing.
    """
    company = normalize(linkedin_job.get("company_name"))
    valid: dict[tuple[str, str], BoardHint] = {}
    for row in registered_boards:
        if normalize(row.get("company_name")) != company:
            continue
        binding = board_binding_from_ats_company_registry(row)
        if binding:
            valid[(binding.provider, binding.board_name.casefold())] = binding

    if not valid:
        return _base(
            linkedin_job,
            "UNKNOWN",
            "ats_company_registry",
            {"reason": "no_verified_board_registry_entry"},
        )
    if len(valid) != 1:
        return _base(
            linkedin_job,
            "AMBIGUOUS",
            "ats_company_registry",
            {
                "reason": "more_than_one_verified_company_board",
                "board_count": len(valid),
            },
        )
    selected = next(iter(valid.values()))
    if not allow_network:
        return _base(
            linkedin_job,
            "UNKNOWN",
            "ats_company_registry",
            {
                "reason": "board_known_but_not_queried",
                "provider": selected.provider,
            },
        )
    try:
        result = resolve_from_verified_board(
            dict(linkedin_job),
            provider=selected.provider,
            board_name=selected.board_name,
            verified_employer_name=str(linkedin_job.get("company_name")),
            opener=provider_opener,
        )
    except (OSError, ValueError) as error:
        # Public board timeouts and over-size responses must never create
        # guessed or automatically submittable employer destinations.
        return _base(
            linkedin_job,
            "UNKNOWN",
            "official_public_ats",
            {
                "reason": "public_board_read_failed",
                "provider": selected.provider,
                "error_type": type(error).__name__,
            },
        )
    return result


def registered_company_board_coverage(
    linkedin_jobs: Iterable[Mapping[str, Any]],
    ats_company_rows: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    indexed: dict[str, set[tuple[str, str]]] = {}
    for row in ats_company_rows:
        binding = board_binding_from_ats_company_registry(row)
        if binding:
            company = normalize(row.get("company_name"))
            indexed.setdefault(company, set()).add(
                (binding.provider, binding.board_name.casefold())
            )
    total = matched = ambiguous = 0
    for row in linkedin_jobs:
        total += 1
        choices = indexed.get(normalize(row.get("company_name")), set())
        if len(choices) == 1:
            matched += 1
        elif len(choices) > 1:
            ambiguous += 1
    return {
        "total_linkedin": total,
        "linked_to_one_healthy_known_board": matched,
        "linked_to_multiple_healthy_known_boards": ambiguous,
        "board_unknown_or_not_healthy": total - matched - ambiguous,
        "public_api_requests": 0,
        "database_writes": 0,
    }
