"""Read-only employer application-link recovery for already-discovered LinkedIn jobs.

No LinkedIn requests, employer submissions, DB writes, or best-guess URL synthesis.
A cross-source match is only a CANDIDATE until independently corroborated.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from app.linkedin_url_contract import canonical_linkedin_job_url

AGGREGATORS = ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com")
ATS_HOSTS = {
    "greenhouse.io": "Greenhouse",
    "greenhouse.com": "Greenhouse",
    "lever.co": "Lever",
    "ashbyhq.com": "Ashby",
    "smartrecruiters.com": "SmartRecruiters",
    "icims.com": "iCIMS",
    "myworkdayjobs.com": "Workday",
    "myworkdaysite.com": "Workday",
    "workable.com": "Workable",
    "jobvite.com": "Jobvite",
    "oraclecloud.com": "Oracle",
    "taleo.net": "Oracle",
    "successfactors.com": "SAP SuccessFactors",
}
STATUSES = frozenset(
    {
        "UNKNOWN",
        "CANDIDATE",
        "VERIFIED",
        "AMBIGUOUS",
        "CLOSED",
        "EASY_APPLY",
        "UNAVAILABLE",
    }
)


def normalize(value: Any) -> str:
    """Conservative identity normalization; no fuzzy job-title guesses."""
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def hostname_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def external_url(value: Any) -> str | None:
    """Require an HTTPS public-name destination; reject aggregator or unsafe URLs."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 4096:
        return None
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return None
        if not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host:
            return None
        if host in {"localhost", "localhost.localdomain"} or host.endswith(
            (".local", ".internal", ".test", ".invalid")
        ):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        if any(hostname_matches(host, a) for a in AGGREGATORS):
            return None
        if parsed.port not in (None, 443):
            return None
    except (ValueError, AttributeError):
        return None
    return raw


def ats_from_url(value: str | None) -> str | None:
    if not value:
        return None
    host = (urlsplit(value).hostname or "").casefold()
    for suffix, provider in ATS_HOSTS.items():
        if hostname_matches(host, suffix):
            return provider
    return None


_STATE_NAMES = {
    "california": "ca",
    "new york": "ny",
    "new jersey": "nj",
    "pennsylvania": "pa",
    "texas": "tx",
    "florida": "fl",
    "massachusetts": "ma",
    "illinois": "il",
    "washington": "wa",
    "virginia": "va",
    "georgia": "ga",
    "colorado": "co",
    "north carolina": "nc",
    "south carolina": "sc",
}


def normalize_location(value: Any) -> str:
    """Conservative city/state normalization; never equate different cities."""
    result = normalize(value)
    if result in {"new york city", "nyc"}:
        return "new york ny"
    for country in ("united states of america", "united states", "usa", "us"):
        if result.endswith(" " + country):
            result = result[: -(len(country) + 1)]
            break
    for full, abbreviation in _STATE_NAMES.items():
        if result.endswith(" " + full):
            return result[: -(len(full) + 1)] + " " + abbreviation
    return result


def location_agrees(first: Any, second: Any) -> bool:
    a, b = normalize_location(first), normalize_location(second)
    if not a or not b or a in {"remote", "nationwide"} or b in {"remote", "nationwide"}:
        return False
    return a == b


def _req(row: Mapping[str, Any]) -> str:
    for key in ("requisition_id", "external_requisition_id", "ref_number"):
        value = normalize(row.get(key))
        if value:
            return value
    return ""


def _tokens(value: Any) -> set[str]:
    ignored = {
        "and",
        "the",
        "for",
        "with",
        "this",
        "that",
        "from",
        "your",
        "you",
        "our",
        "are",
        "will",
    }
    return {
        token
        for token in normalize(value).split()
        if len(token) >= 4 and token not in ignored
    }


def description_overlap(first: Any, second: Any) -> float:
    a, b = _tokens(first), _tokens(second)
    if len(a) < 12 or len(b) < 12:
        return 0.0
    return round(len(a & b) / min(len(a), len(b)), 4)


@dataclass(frozen=True)
class LinkResolution:
    linkedin_job_url: str | None
    employer_job_url: str | None
    employer_apply_url: str | None
    ats_platform: str | None
    resolution_status: str
    resolution_source: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _base(
    job: Mapping[str, Any], status: str, source: str, evidence: dict[str, Any]
) -> LinkResolution:
    return LinkResolution(
        linkedin_job_url=canonical_linkedin_job_url(
            job.get("job_url") or job.get("linkedin_job_url"),
            job_id=job.get("ats_job_id"),
        ),
        employer_job_url=None,
        employer_apply_url=None,
        ats_platform=None,
        resolution_status=status,
        resolution_source=source,
        evidence=evidence,
    )


def resolve_stored(
    job: Mapping[str, Any],
    other_jobs: Iterable[Mapping[str, Any]],
    *,
    official: bool = False,
    official_provider: str | None = None,
) -> LinkResolution:
    """Recover candidate destinations from stored jobs or a trusted official feed.

    Never infer LinkedIn's Apply redirect; identify an equivalent employer posting.
    CANDIDATE results are not auto-submittable. VERIFIED requires one unique
    public ATS posting with requisition agreement OR strong description identity.
    """
    base = _base(job, "UNKNOWN", "none", {})
    if not base.linkedin_job_url:
        return _base(
            job,
            "UNAVAILABLE",
            "invalid_linkedin_posting",
            {"reason": "linkedin_job_url_missing"},
        )
    if not normalize(job.get("company_name")) or not normalize(job.get("title")):
        return _base(job, "UNKNOWN", "none", {"reason": "job_identity_incomplete"})

    # Direct URL may already exist, but merely having an HTTPS URL is not proof.
    direct = external_url(job.get("apply_url") or job.get("direct_apply_url"))
    candidates: dict[str, dict[str, Any]] = {}
    if direct:
        candidates[direct] = {
            "url": direct,
            "job_url": direct,
            "platform": ats_from_url(direct),
            "source": "stored_direct_url",
            "location_agrees": False,
            "requisition_agrees": False,
            "description_overlap": 0.0,
        }
    for candidate in other_jobs:
        if normalize(candidate.get("company_name")) != normalize(
            job.get("company_name")
        ):
            continue
        if normalize(candidate.get("title")) != normalize(job.get("title")):
            continue
        target = external_url(
            candidate.get("apply_url") or candidate.get("employer_apply_url")
        )
        if not target:
            continue
        # Two different concrete locations are not automatically the same job.
        first_location = normalize(job.get("location_raw"))
        candidate_location = normalize(
            candidate.get("location_raw") or candidate.get("location")
        )
        same_location = location_agrees(first_location, candidate_location)
        if first_location and candidate_location and not same_location:
            continue
        expected_req, candidate_req = _req(job), _req(candidate)
        if expected_req and candidate_req and expected_req != candidate_req:
            continue
        overlap = description_overlap(
            job.get("description_raw"),
            candidate.get("description_raw") or candidate.get("description"),
        )
        candidates[target] = {
            "url": target,
            "job_url": external_url(candidate.get("job_url")) or target,
            "platform": official_provider or ats_from_url(target),
            "source": "official_public_ats" if official else "stored_cross_source",
            "location_agrees": same_location,
            "requisition_agrees": bool(
                expected_req and candidate_req and expected_req == candidate_req
            ),
            "description_overlap": overlap,
        }
    if not candidates:
        return _base(
            job, "UNKNOWN", "none", {"reason": "no_corresponding_employer_job"}
        )
    if len(candidates) != 1:
        return _base(
            job,
            "AMBIGUOUS",
            "candidate_comparison",
            {
                "candidate_count": len(candidates),
                "platforms": sorted(
                    {str(c["platform"] or "unknown") for c in candidates.values()}
                ),
                "reason": "multiple_distinct_employer_destinations",
            },
        )
    selected = next(iter(candidates.values()))
    # Require a unique official record with strong matching evidence to verify.
    strong_identity = selected["requisition_agrees"] or (
        selected["location_agrees"] and selected["description_overlap"] >= 0.65
    )
    verified = bool(
        official
        and selected["source"] == "official_public_ats"
        and selected["platform"]
        and strong_identity
    )
    return LinkResolution(
        linkedin_job_url=base.linkedin_job_url,
        employer_job_url=selected["job_url"],
        employer_apply_url=selected["url"],
        ats_platform=selected["platform"],
        resolution_status="VERIFIED" if verified else "CANDIDATE",
        resolution_source=selected["source"],
        evidence={
            "matching_fields": ["company_name", "title"]
            + (["location"] if selected["location_agrees"] else [])
            + (["requisition_id"] if selected["requisition_agrees"] else []),
            "description_overlap": selected["description_overlap"],
            "identity_evidence": "strong" if strong_identity else "partial",
            "needs_employer_confirmation": not verified,
        },
    )


def official_feed_jobs(
    provider: str, data: Any, company_name: str
) -> list[dict[str, Any]]:
    """Normalize trusted public ATS list responses; no URL guessing."""
    if provider == "Greenhouse" or provider == "Ashby":
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
    elif provider == "SmartRecruiters":
        jobs = data.get("content", []) if isinstance(data, dict) else []
    elif provider == "Lever":
        jobs = data if isinstance(data, list) else []
    else:
        raise ValueError("Unsupported official ATS feed")
    normalized: list[dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        if provider == "Greenhouse":
            title = item.get("title")
            location = (
                (item.get("location") or {}).get("name")
                if isinstance(item.get("location"), dict)
                else ""
            )
            application = item.get("absolute_url")
            job_url = application
            description = item.get("content")
            requisition = None
        elif provider == "Lever":
            title = item.get("text")
            location = (item.get("categories") or {}).get("location")
            application = item.get("applyUrl")
            job_url = item.get("hostedUrl") or application
            description = item.get("descriptionPlain")
            requisition = None
        elif provider == "Ashby":
            if item.get("isListed") is False:
                continue
            title = item.get("title")
            location = item.get("location")
            application = item.get("applyUrl")
            job_url = item.get("jobUrl") or application
            description = item.get("descriptionPlain")
            requisition = None
        else:
            title = item.get("name")
            location_value = item.get("location") or {}
            location = (
                ", ".join(
                    str(location_value.get(k))
                    for k in ("city", "region", "country")
                    if location_value.get(k)
                )
                if isinstance(location_value, dict)
                else ""
            )
            application = item.get("applyUrl")
            job_url = item.get("jobAdUrl") or application
            description = (
                item.get("jobAd", {})
                .get("sections", {})
                .get("jobDescription", {})
                .get("text")
                if isinstance(item.get("jobAd"), dict)
                else None
            )
            requisition = item.get("refNumber")
        if not external_url(application):
            continue
        normalized.append(
            {
                "company_name": company_name,
                "title": title,
                "location_raw": location,
                "job_url": job_url,
                "apply_url": application,
                "description_raw": description,
                "requisition_id": requisition,
            }
        )
    return normalized
