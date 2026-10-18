"""Extract only LinkedIn's own Apply control, never description/related-job links.

Parsing is offline. The optional reader follows only same-job LinkedIn redirects,
performs GETs, and reports sign-in/rate limits rather than inventing a destination.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from html import unescape
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from app.linkedin_employer_link_resolver_v1 import external_url
from app.linkedin_url_contract import canonical_linkedin_job_url

MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class ApplyButtonResult:
    linkedin_job_url: str
    employer_apply_url: str | None
    status: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def unwrap_apply_url(value: Any) -> str | None:
    """Decode LinkedIn offsite wrappers without unquoting an employer's query."""
    raw = unescape(str(value or "").strip())
    raw = raw.removeprefix("<!--").removesuffix("-->").strip()
    try:
        decoded = json.loads(raw)
        if isinstance(decoded, str):
            raw = decoded
    except (ValueError, TypeError):
        pass
    raw = raw.replace(r"\/", "/")
    # A completely encoded URL differs from an employer URL with encoded values.
    if not raw.startswith(("https://", "http://", "/")):
        raw = unquote(raw)
    for _ in range(3):
        parsed = urlsplit(urljoin("https://www.linkedin.com", raw))
        host = (parsed.hostname or "").lower().rstrip(".")
        if host not in {"linkedin.com", "www.linkedin.com"}:
            return external_url(raw)
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        if parsed.path.rstrip("/") not in {
            "/jobs/view/externalApply",
            "/redir/redirect",
        }:
            return None
        query = parse_qs(parsed.query)
        target = query.get("url") or query.get("urlEncoded")
        if not target or len(target) != 1:
            return None
        raw = target[0]
        if not raw.startswith("https://"):
            raw = unquote(raw)
    return None


def parse_apply_button(html: str, posting_url: str) -> ApplyButtonResult:
    posting = canonical_linkedin_job_url(posting_url)
    if not posting:
        raise ValueError("A numeric LinkedIn posting URL is required")
    if len(html.encode("utf-8")) > MAX_BYTES:
        raise ValueError("LinkedIn response exceeds inspection limit")
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.find("link", rel="canonical")
    if (
        canonical
        and canonical.get("href")
        and canonical_linkedin_job_url(canonical["href"]) != posting
    ):
        return ApplyButtonResult(posting, None, "IDENTITY_MISMATCH", {})
    text = soup.get_text(" ", strip=True).casefold()
    if any(
        marker in text
        for marker in (
            "no longer accepting applications",
            "this job is no longer available",
        )
    ):
        return ApplyButtonResult(posting, None, "CLOSED", {"submit_authorized": False})
    candidates: dict[str, list[str]] = {}

    def add(value: Any, source: str) -> None:
        try:
            target = unwrap_apply_url(value)
        except (ValueError, TypeError):
            return
        if target:
            candidates.setdefault(target, []).append(source)

    hidden = soup.find("code", id="applyUrl")
    if hidden:
        add(hidden.decode_contents().strip(), "code#applyUrl")
    # Explicit Apply controls only. No arbitrary links in descriptions or cards.
    for control in soup.select(
        "a.apply-button, a.jobs-apply-button, a[data-tracking-control-name], button[data-apply-url]"
    ):
        tracking = str(control.get("data-tracking-control-name") or "")
        classes = set(control.get("class") or [])
        if (
            classes & {"apply-button", "jobs-apply-button"}
            or re.fullmatch(r"public_jobs_apply-link(?:-offsite)?", tracking)
            or control.has_attr("data-apply-url")
        ):
            add(control.get("data-apply-url") or control.get("href"), "apply_control")
    if len(candidates) > 1:
        return ApplyButtonResult(
            posting, None, "AMBIGUOUS", {"candidate_count": len(candidates)}
        )
    if candidates:
        target, sources = next(iter(candidates.items()))
        return ApplyButtonResult(
            posting,
            target,
            "CAPTURED",
            {
                "capture_sources": sources,
                "response_sha256": hashlib.sha256(html.encode()).hexdigest(),
                "submit_authorized": False,
            },
        )
    text = soup.get_text(" ", strip=True).casefold()
    if any(
        marker in text
        for marker in (
            "no longer accepting applications",
            "this job is no longer available",
        )
    ):
        status = "CLOSED"
    elif soup.select_one(
        '[data-impression-id*="apply-link-offsite_contextual-sign-in-modal"]'
    ):
        status = "AUTH_REQUIRED"
    elif soup.select_one(".jobs-apply-button") and "easy apply" in text:
        status = "EASY_APPLY"
    elif "sign in" in text and not soup.select_one(
        'script[type="application/ld+json"]'
    ):
        status = "AUTH_REQUIRED"
    else:
        status = "UNAVAILABLE"
    return ApplyButtonResult(posting, None, status, {"submit_authorized": False})


def fetch_apply_button(posting_url: str, *, session: Any = None) -> ApplyButtonResult:
    import requests

    posting = canonical_linkedin_job_url(posting_url)
    if not posting:
        raise ValueError("A numeric LinkedIn posting URL is required")
    client = session or requests.Session()
    url = posting
    try:
        for _ in range(4):
            # Never follow an external redirect, login URL, or another posting.
            with client.get(
                url, timeout=(5, 12), allow_redirects=False, stream=True
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    target = urljoin(url, response.headers.get("Location", ""))
                    parsed_target = urlsplit(target)
                    if (
                        canonical_linkedin_job_url(target) != posting
                        or parsed_target.scheme != "https"
                        or parsed_target.username
                        or parsed_target.password
                        or parsed_target.port not in (None, 443)
                    ):
                        return ApplyButtonResult(posting, None, "AUTH_REQUIRED", {})
                    url = target
                    continue
                if response.status_code in (429, 999):
                    return ApplyButtonResult(posting, None, "RATE_LIMITED", {})
                if response.status_code in (401, 403):
                    return ApplyButtonResult(posting, None, "AUTH_REQUIRED", {})
                if response.status_code in (404, 410):
                    return ApplyButtonResult(posting, None, "CLOSED", {})
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        return ApplyButtonResult(
                            posting, None, "RESPONSE_TOO_LARGE", {}
                        )
                return parse_apply_button(
                    body.decode("utf-8", errors="replace"), posting
                )
        return ApplyButtonResult(posting, None, "REDIRECT_LIMIT", {})
    except requests.RequestException as error:
        return ApplyButtonResult(
            posting, None, "FETCH_FAILED", {"error_type": type(error).__name__}
        )
    finally:
        if session is None:
            client.close()


def install_jobspy_apply_parser() -> bool:
    """Upgrade the pinned JobSpy parser in memory without changing its runner."""
    from jobspy.linkedin import LinkedIn

    original = LinkedIn._parse_job_url_direct
    if getattr(original, "_munshi_apply_button_v7", False):
        return True

    def parse(self: Any, soup: Any) -> str | None:
        canonical = soup.find("link", rel="canonical")
        # JobSpy calls this only after fetching the exact job's detail page.
        # The placeholder permits fragment parsing; it grants no verified proof.
        posting = (
            canonical.get("href")
            if canonical
            else "https://www.linkedin.com/jobs/view/100000"
        )
        result = parse_apply_button(str(soup), posting)
        return result.employer_apply_url

    parse._munshi_apply_button_v7 = True
    parse._munshi_original = original
    LinkedIn._parse_job_url_direct = parse
    return True
