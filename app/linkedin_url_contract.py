"""Authoritative LinkedIn posting/direct-apply URL contract.

For LinkedIn records:
- job_url is the canonical LinkedIn posting URL.
- apply_url is an optional external/direct application URL.
- a posting URL may be reconstructed from a numeric LinkedIn job id.
- an unrecoverable posting link is quarantined by callers and must not appear
  in the normal Browse Jobs experience.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable
from urllib.parse import urlsplit


LINKEDIN_LINK_HEALTH_THRESHOLD_PCT = 99.9
LINKEDIN_QUARANTINE_REASON = "linkedin_missing_usable_posting_url"

_AGGREGATOR_HOSTS = frozenset(
    {
        "linkedin.com",
        "indeed.com",
        "glassdoor.com",
        "ziprecruiter.com",
    }
)
_LINKEDIN_VIEW_RE = re.compile(
    r"/jobs/view/(?:[^/?#]*?-)?(?P<job_id>\\d{5,})(?:[/?#]|$)",
    re.IGNORECASE,
)
_NUMERIC_ID_RE = re.compile(r"^(?:li-)?(?P<job_id>\\d{5,})$", re.IGNORECASE)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"nan", "none", "nat", "<na>", "null"} else text


def _host_matches(host: str, parent: str) -> bool:
    normalized = str(host or "").casefold().rstrip(".")
    return normalized == parent or normalized.endswith("." + parent)


def _http_url(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme.casefold() not in {"http", "https"} or not host:
        return None
    return text


def linkedin_job_id(value: Any) -> str | None:
    """Extract a numeric LinkedIn job id from an id value or LinkedIn view URL."""
    text = _text(value)
    if not text:
        return None

    direct = _NUMERIC_ID_RE.fullmatch(text)
    if direct:
        return direct.group("job_id")

    url = _http_url(text)
    if not url:
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not _host_matches(host, "linkedin.com"):
        return None
    match = _LINKEDIN_VIEW_RE.search(parsed.path or "")
    return match.group("job_id") if match else None


def canonical_linkedin_job_url(
    posting_url: Any = None,
    *,
    job_id: Any = None,
    fallback_ids: Iterable[Any] = (),
) -> str | None:
    """Return a tracking-free canonical LinkedIn posting URL when recoverable."""
    posting_id = linkedin_job_id(posting_url)
    if posting_id:
        return f"https://www.linkedin.com/jobs/view/{posting_id}"

    for candidate in (job_id, *tuple(fallback_ids)):
        candidate_id = linkedin_job_id(candidate)
        if candidate_id:
            return f"https://www.linkedin.com/jobs/view/{candidate_id}"
    return None


def direct_application_url(
    value: Any,
    *,
    linkedin_url: Any = None,
) -> str | None:
    """Return an external/direct apply URL, never an aggregator/listing URL."""
    url = _http_url(value)
    if not url:
        return None
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if any(_host_matches(host, parent) for parent in _AGGREGATOR_HOSTS):
        return None

    posting = canonical_linkedin_job_url(linkedin_url)
    if posting and url.rstrip("/") == posting.rstrip("/"):
        return None
    return url


def is_linkedin_source(value: Any) -> bool:
    return "linkedin" in _text(value).casefold()


def normalize_linkedin_record(record: dict[str, Any]) -> dict[str, Any]:
    """Apply the LinkedIn link contract while preserving explicit aliases."""
    normalized = dict(record)

    posting_candidate = (
        normalized.get("linkedin_job_url")
        or normalized.get("job_url")
        or normalized.get("url")
    )
    job_id_candidate = (
        normalized.get("linkedin_job_id")
        or normalized.get("id")
        or normalized.get("job_id")
        or normalized.get("ats_job_id")
    )
    posting = canonical_linkedin_job_url(
        posting_candidate,
        job_id=job_id_candidate,
        fallback_ids=(
            normalized.get("external_id"),
            normalized.get("requisition_id"),
        ),
    )

    direct = None
    for candidate in (
        normalized.get("direct_apply_url"),
        normalized.get("job_url_direct"),
        normalized.get("direct_url"),
        normalized.get("apply_url"),
    ):
        direct = direct_application_url(candidate, linkedin_url=posting)
        if direct:
            break

    normalized["linkedin_job_url"] = posting
    normalized["direct_apply_url"] = direct
    normalized["job_url"] = posting
    normalized["apply_url"] = direct
    normalized["_linkedin_link_quarantined"] = posting is None
    normalized["_linkedin_link_quarantine_reason"] = (
        LINKEDIN_QUARANTINE_REASON if posting is None else None
    )
    return normalized


def linkedin_working_link_percentage(*, total: int, working: int) -> float | None:
    total_count = max(0, int(total or 0))
    working_count = max(0, min(int(working or 0), total_count))
    if total_count == 0:
        return None
    return round((working_count / total_count) * 100.0, 3)


def linkedin_link_health_degraded(*, total: int, working: int) -> bool:
    percentage = linkedin_working_link_percentage(total=total, working=working)
    return (
        percentage is not None
        and percentage < LINKEDIN_LINK_HEALTH_THRESHOLD_PCT
    )
