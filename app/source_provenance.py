from __future__ import annotations

import re
from typing import Any, Mapping

VERSION = "source-provenance-v1.0.0"


def _value(job: Any, *keys: str) -> str:
    for key in keys:
        value: Any = None
        try:
            if isinstance(job, Mapping):
                value = job.get(key)
            else:
                value = job[key]
        except Exception:
            try:
                value = getattr(job, key, None)
            except Exception:
                value = None
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def format_adapter_display(job: Any) -> str:
    """Return a stable human-readable adapter/provider label for a job card."""
    explicit = _value(
        job,
        "adapter_display",
        "source_adapter",
        "adapter_name",
        "discovery_adapter",
    )
    provider = _value(job, "provider", "source_provider", "discovery_provider")
    source = _value(job, "source", "source_name", "job_source", "source_key")

    explicit_l = explicit.casefold()
    provider_l = provider.casefold()
    source_l = source.casefold()
    combined = " ".join((explicit_l, provider_l, source_l))

    if explicit:
        # Preserve a specific adapter label already written by the worker.
        if explicit_l not in {"jobspy", "google", "linkedin", "indeed"}:
            return _clean(explicit)

    if "linkedin" in combined:
        return "LinkedIn Jobs (JobSpy)"
    if "indeed" in combined:
        return "Indeed Jobs (JobSpy)"
    if "google" in combined:
        if "serpapi" in combined or "serp api" in combined:
            return "Google Jobs · SerpAPI"
        if "jobspy" in combined:
            return "Google Jobs · JobSpy fallback"
        return "Google Jobs"
    if "greenhouse" in combined:
        return "Greenhouse"
    if "smartrecruiters" in combined or "smart recruiters" in combined:
        return "SmartRecruiters"
    if "ashby" in combined:
        return "Ashby"
    if "lever" in combined:
        return "Lever"
    if "dice" in combined:
        return "Dice"
    if "jobvite" in combined:
        return "Jobvite"
    if "teamtailor" in combined:
        return "Teamtailor"
    if "recruitee" in combined:
        return "Recruitee"
    if "workable" in combined:
        return "Workable"
    if "bamboo" in combined:
        return "BambooHR"
    if "icims" in combined:
        return "iCIMS"
    if "adzuna" in combined:
        return "Adzuna"
    if "apify" in combined:
        return "Apify"
    if "jobspy" in combined:
        return "JobSpy"

    return _clean(explicit or source or provider or "Unknown adapter")
