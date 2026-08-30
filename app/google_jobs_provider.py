from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from app.database import ROOT_DIR
from app.source_network_retry import google_requests_get_with_retry, google_urlopen_with_retry
# AADIL_GOOGLE_TIMEOUT_RETRY_FALLBACK_V1

PROJECT = ROOT_DIR
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
SERPAPI_NAMES = (
    "SERPAPI_API_KEY",
    "SERPAPI_KEY",
    "SERP_API_KEY",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _valid_key(value: Any) -> bool:
    text = _clean(value)
    return bool(len(text) >= 20 and re.fullmatch(r"[A-Za-z0-9_-]+", text))


def _env_file_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("'\"")
        values[name] = value
    return values


def discover_serpapi_key() -> tuple[str, str]:
    for name in SERPAPI_NAMES:
        value = os.getenv(name, "")
        if _valid_key(value):
            return _clean(value), f"environment:{name}"

    env_values = _env_file_values(PROJECT / ".env")
    for name in SERPAPI_NAMES:
        value = env_values.get(name, "")
        if _valid_key(value):
            return _clean(value), f"project_env:{name}"

    return "", "missing"


def provider_status() -> dict[str, Any]:
    key, source = discover_serpapi_key()
    return {
        "serpapi_available": bool(key),
        "credential_source": source,
        "credential_value_logged": False,
    }


def _first_apply_url(job: dict[str, Any]) -> str:
    for option in job.get("apply_options") or []:
        if isinstance(option, dict):
            link = _clean(option.get("link"))
            if link:
                return link
    for item in job.get("related_links") or []:
        if isinstance(item, dict):
            link = _clean(item.get("link"))
            if link:
                return link
    return _clean(job.get("share_link"))


def _employment_type(job: dict[str, Any]) -> str:
    values = [str(value) for value in job.get("extensions") or []]
    labels = (
        "Full-time",
        "Part-time",
        "Internship",
        "Contractor",
        "Temporary",
    )
    lowered = " ".join(values).lower()
    for label in labels:
        if label.lower() in lowered:
            return label
    return "Not specified"


def _normalize_result(job: dict[str, Any]) -> dict[str, Any]:
    detected = job.get("detected_extensions") or {}
    location = _clean(job.get("location")) or "Not specified"
    remote = bool(detected.get("work_from_home")) or "remote" in location.lower()
    apply_url = _first_apply_url(job)
    return {
        "source": "Google Jobs/SerpAPI",
        "source_tier": 2,
        "ats_job_id": _clean(job.get("job_id")) or apply_url,
        "company_name": _clean(job.get("company_name")) or "Unknown Company",
        "title": _clean(job.get("title")) or "Unknown Position",
        "location_raw": location,
        "city": None,
        "state": None,
        "country": "US",
        "remote_type": "Remote" if remote else "Not specified",
        "employment_type": _employment_type(job),
        "job_url": apply_url,
        "apply_url": apply_url,
        "description_raw": _clean(job.get("description")) or "Not specified",
        "salary_raw": None,
        "date_posted": _clean(detected.get("posted_at")) or None,
    }


def serpapi_google_jobs(
    *,
    query: str,
    location: str,
    timeout_seconds: int = 45,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key, credential_source = discover_serpapi_key()
    if not key:
        raise RuntimeError(
            "SerpAPI credential was not found in environment or project .env."
        )

    parameters = {
        "engine": "google_jobs",
        "q": query,
        "location": location,
        "gl": "us",
        "hl": "en",
        "api_key": key,
    }
    request = Request(
        SERPAPI_ENDPOINT + "?" + urlencode(parameters),
        headers={
            "Accept": "application/json",
            "User-Agent": "Aadil-HR-Hunter/Google-Jobs-V5.2",
        },
    )
    with google_urlopen_with_retry(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))

    metadata = payload.get("search_metadata") or {}
    status = _clean(metadata.get("status"))
    if status and status.lower() not in {"success", "cached"}:
        raise RuntimeError(f"SerpAPI search status: {status}")

    jobs = [
        _normalize_result(item)
        for item in payload.get("jobs_results") or []
        if isinstance(item, dict)
    ]
    return jobs, {
        "provider": "serpapi",
        "credential_source": credential_source,
        "credential_value_logged": False,
        "search_status": status or "Success",
        "raw_jobs_found": len(jobs),
        "query": query,
        "location": location,
        "cached_response_possible": True,
    }
