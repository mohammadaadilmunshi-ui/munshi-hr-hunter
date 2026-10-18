"""Customer-safe job destination and discovery-source projection.

The canonical job record keeps source provenance separate from the URL used to
apply.  This module turns those stored facts into display labels without
inventing an ATS or using employer-specific rules.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit


_KNOWN_PROVIDERS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("Workday", ("myworkdayjobs.com", "myworkdaysite.com", "workday.com"), ()),
    ("Greenhouse", ("greenhouse.io", "greenhouse.com"), ()),
    ("Lever", ("lever.co",), ()),
    ("Ashby", ("ashbyhq.com",), ()),
    ("SmartRecruiters", ("smartrecruiters.com",), ()),
    ("iCIMS", ("icims.com",), ()),
    ("Workable", ("workable.com",), ()),
    ("Jobvite", ("jobvite.com",), ()),
    ("BambooHR", ("bamboohr.com",), ()),
    ("Paylocity", ("paylocity.com",), ()),
    ("ADP", ("adp.com",), ()),
    ("UKG", ("ultipro.com", "ukg.com", "ukg.net"), ()),
    ("Rippling", ("rippling.com",), ()),
    ("Teamtailor", ("teamtailor.com",), ()),
    ("Recruitee", ("recruitee.com",), ()),
    ("SuccessFactors", ("successfactors.com", "successfactors.eu", "jobs.sap.com"), ()),
    ("Oracle / Taleo", ("taleo.net", "oraclecloud.com"), ("/hcmui/", "/careersection/")),
    ("LinkedIn", ("linkedin.com",), ()),
    ("Indeed", ("indeed.com",), ()),
)

_SOURCE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LinkedIn", ("linkedin",)),
    ("Indeed", ("indeed",)),
    ("Google Jobs", ("google jobs", "google_jobs", "googlejobs")),
    ("USAJobs", ("usajobs",)),
    ("Adzuna", ("adzuna",)),
    ("Dice", ("dice",)),
    ("Jobicy", ("jobicy",)),
    ("Greenhouse", ("greenhouse",)),
    ("Lever", ("lever",)),
    ("Ashby", ("ashby",)),
    ("SmartRecruiters", ("smartrecruiters", "smart recruiters")),
    ("Workday", ("workday",)),
)

_AGGREGATORS = ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com")


def _value(job: Any, *names: str) -> str:
    for name in names:
        try:
            value = job.get(name) if isinstance(job, Mapping) else job[name]
        except Exception:
            value = getattr(job, name, None)
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if text and text.casefold() not in {"none", "null", "nan", "<na>"}:
            return text
    return ""


def _safe_http_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    return text


def _host(value: Any) -> str:
    url = _safe_http_url(value)
    if not url:
        return ""
    return str(urlsplit(url).hostname or "").casefold().rstrip(".")


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def _explicit_provider(job: Any) -> str:
    explicit = _value(job, "ats_platform", "ats_provider", "apply_provider")
    if explicit and explicit.casefold() not in {"jobspy", "python-jobspy"}:
        return explicit
    raw = _value(job, "detail_extraction_json")
    if not raw:
        return ""
    try:
        detail = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(detail, Mapping):
        return ""
    explicit = _value(detail, "ats_platform", "ats_provider", "apply_provider")
    return "" if explicit.casefold() in {"jobspy", "python-jobspy"} else explicit


def source_platform(job: Any) -> str:
    """Return discovery provenance, never an inferred application ATS."""
    raw = _value(job, "source_platform", "source", "source_name")
    if not raw:
        try:
            provenance = json.loads(_value(job, "source_provenance_json") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            provenance = []
        if isinstance(provenance, list):
            for item in provenance:
                if isinstance(item, Mapping):
                    raw = _value(item, "source", "source_platform", "source_name")
                    if raw:
                        break
    lowered = raw.casefold()
    for label, markers in _SOURCE_PATTERNS:
        if any(marker in lowered for marker in markers):
            return label
    if lowered in {"jobspy", "python-jobspy"}:
        return "JobSpy"
    return raw or "Source not recorded"


def _provider_from_url(url: str | None) -> tuple[str | None, str, str]:
    if not url:
        return None, "", ""
    parsed = urlsplit(url)
    host = str(parsed.hostname or "").casefold().rstrip(".")
    path = str(parsed.path or "").casefold()
    for label, domains, path_markers in _KNOWN_PROVIDERS:
        if any(_host_matches(host, domain) for domain in domains) or (
            path_markers and any(marker in path for marker in path_markers)
        ):
            return label, host, "url_pattern"
    return None, host, "destination_domain"


def original_job_url(job: Any) -> str | None:
    """Choose the strongest stored exact destination without constructing one."""
    apply_url = _safe_http_url(_value(job, "apply_url"))
    job_url = _safe_http_url(_value(job, "canonical_job_url", "job_url", "source_url"))
    if apply_url and not any(
        _host_matches(_host(apply_url), domain) for domain in _AGGREGATORS
    ):
        return apply_url
    if job_url and not any(
        _host_matches(_host(job_url), domain) for domain in _AGGREGATORS
    ):
        return job_url
    return job_url or apply_url


@dataclass(frozen=True)
class JobDestination:
    provider: str | None
    apply_via: str
    source: str
    original_url: str | None
    destination_domain: str | None
    confidence: str
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def describe_job_destination(job: Any) -> JobDestination:
    """Project generic ATS/source/link facts for one normalized job."""
    exact_url = original_job_url(job)
    explicit = _explicit_provider(job)
    # A discovery source URL is not the employer's application platform.
    stored_apply = _safe_http_url(_value(job, "apply_url", "employer_apply_url"))
    if source_platform(job) == "LinkedIn" and (
        not stored_apply
        or any(_host_matches(_host(stored_apply), a) for a in _AGGREGATORS)
    ):
        return JobDestination(
            provider=None, apply_via="Employer link pending", source="LinkedIn",
            original_url=exact_url, destination_domain=None, confidence="unknown",
            evidence="employer_url_missing",
        )
    if source_platform(job) == "LinkedIn" and explicit in {"LinkedIn", "Indeed"}:
        explicit = ""
    detected, domain, evidence = _provider_from_url(exact_url)
    provider = explicit or detected
    if provider:
        apply_via = provider
        confidence = "stored" if explicit else "high"
        evidence = "stored_provider" if explicit else evidence
    elif domain:
        apply_via = f"Employer’s website · {domain}"
        confidence = "domain_only"
    else:
        apply_via = "Destination not recorded"
        confidence = "unknown"
        evidence = "missing_url"
    return JobDestination(
        provider=provider,
        apply_via=apply_via,
        source=source_platform(job),
        original_url=exact_url,
        destination_domain=domain or None,
        confidence=confidence,
        evidence=evidence,
    )
