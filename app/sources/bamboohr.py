from __future__ import annotations

import time
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import requests
from app.public_board_http import validate_board_url


def _text(value: Any) -> str:
    return str(value or "").strip()


def bamboo_list_url(board: Mapping[str, Any]) -> str:
    configured = _text(board.get("board_url"))
    tenant = _text(board.get("tenant"))
    if configured:
        parts = urlsplit(configured)
        host = str(parts.hostname or "").lower()
        if not re.fullmatch(r"[a-z0-9-]+\.bamboohr\.com", host):
            raise ValueError("BambooHR requires a verified hosted tenant")
        validate_board_url(configured, {host})
        if tenant and host != f"{tenant.lower()}.bamboohr.com":
            raise ValueError("BambooHR tenant/host mismatch")
        path = parts.path.rstrip("/")
        if path not in ("", "/careers", "/careers/list"):
            raise ValueError("BambooHR public careers path required")
        if not path.endswith("/careers/list"):
            path = f"{path}/list" if path.endswith("/careers") else f"{path}/careers/list"
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,63}", tenant):
        raise ValueError("BambooHR requires configured tenant or board_url")
    return f"https://{tenant}.bamboohr.com/careers/list"


def parse_bamboohr_list(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = next((payload[k] for k in ("result", "jobs", "jobOpenings") if k in payload), None)
    if not isinstance(values, list):
        raise ValueError("BambooHR response does not contain a public job list")
    return [dict(item) for item in values if isinstance(item, Mapping)]


def _location(item: Mapping[str, Any]) -> tuple[str, str | None, str | None, str | None]:
    value = item.get("location") or item.get("atsLocation")
    if isinstance(value, Mapping):
        city = _text(value.get("city")) or None
        state = _text(value.get("state")) or None
        country = _text(value.get("country") or value.get("addressCountry")) or None
        label = _text(value.get("location")) or ", ".join(part for part in (city, state, country) if part)
        return label, city, state, country
    return _text(value), None, None, _text(item.get("country")) or None


def normalize_bamboohr_job(
    board: Mapping[str, Any],
    item: Mapping[str, Any],
    detail_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    detail_payload = detail_payload or {}
    nested = detail_payload.get("result")
    if isinstance(nested, Mapping) and isinstance(nested.get("jobOpening"), Mapping):
        detail = dict(nested["jobOpening"])
    else:
        detail = dict(nested) if isinstance(nested, Mapping) else dict(detail_payload)
    merged = {**dict(item), **detail}
    original_id = _text(item.get("id") or item.get("jobOpeningId"))
    detail_id = _text(detail.get("id") or detail.get("jobOpeningId"))
    if original_id and detail_id and original_id != detail_id:
        raise ValueError("BambooHR detail belongs to a different job")
    location, city, state, country = _location(merged)
    job_id = _text(merged.get("id") or merged.get("jobOpeningId"))
    if not re.fullmatch(r"[0-9]+", job_id):
        raise ValueError("BambooHR exact job ID required")
    tenant = _text(board.get("tenant"))
    public_url = _text(merged.get("jobOpeningShareUrl") or merged.get("url"))
    host = str(urlsplit(bamboo_list_url(board)).hostname)
    public_url = public_url or f"https://{host}/careers/{job_id}"
    public_url = validate_board_url(public_url, {host})
    if urlsplit(public_url).path.rstrip("/") != f"/careers/{job_id}":
        raise ValueError("BambooHR employer/job URL mismatch")
    return {
        "source": f"BambooHR/{_text(board.get('company_name'))}",
        "source_tier": 1,
        "company_name": _text(board.get("company_name")),
        "title": _text(merged.get("jobOpeningName") or merged.get("title")),
        "ats_job_id": job_id,
        "external_id": job_id,
        "location_raw": location,
        "city": city,
        "state": state,
        "country": country,
        "remote_type": (
            "Remote"
            if merged.get("isRemote") is True
            else _text(merged.get("workplaceType") or merged.get("remoteType")) or None
        ),
        "date_posted": _text(merged.get("datePosted") or merged.get("postedDate")) or None,
        "employment_type": _text(
            merged.get("employmentStatusLabel")
            or merged.get("employmentStatus")
            or merged.get("employmentType")
        ) or None,
        "description_raw": _text(merged.get("jobDescription") or merged.get("description")),
        "qualifications": merged.get("minimumExperience") or merged.get("qualifications"),
        "department": _text(merged.get("department")) or None,
        "apply_url": public_url,
        "job_url": public_url,
        "_provider_country_raw": country,
        "entry_path": "adapter_discovery",
    }


def fetch_bamboohr_board(
    board: Mapping[str, Any],
    runtime: Mapping[str, Any],
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    client = session or requests.Session()
    if session is None:
        client.trust_env = False
    list_url = bamboo_list_url(board)
    timeout = float(runtime.get("request_timeout_seconds") or 20)
    max_jobs = min(300, max(1, int(runtime.get("max_jobs_per_board") or 60)))
    detail_limit = min(60, max(0, int(runtime.get("max_detail_requests_per_board") or 0)))
    fetch_details = bool(runtime.get("fetch_job_details", True))
    headers = {
        "Accept": "application/json",
        "User-Agent": _text(runtime.get("user_agent")) or "AADIL-HR-HUNTER public-job-board-client",
    }
    started = time.monotonic()
    response = client.get(list_url, headers=headers, timeout=min(timeout, 30), allow_redirects=False)
    if 300 <= getattr(response, "status_code", 200) < 400:
        raise ValueError("BambooHR board redirect is not approved")
    response.raise_for_status()
    requests_made = 1
    postings = parse_bamboohr_list(response.json())[:max_jobs]
    base = list_url.removesuffix("/list")
    jobs: list[dict[str, Any]] = []
    for index, item in enumerate(postings):
        detail: Mapping[str, Any] | None = None
        job_id = _text(item.get("id") or item.get("jobOpeningId"))
        if not re.fullmatch(r"[0-9]+", job_id):
            raise ValueError("BambooHR exact job ID required before detail request")
        if fetch_details and job_id and index < detail_limit:
            detail_response = client.get(
                f"{base}/{job_id}/detail",
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            )
            requests_made += 1
            if 300 <= getattr(detail_response, "status_code", 200) < 400:
                raise ValueError("BambooHR detail redirect is not approved")
            detail_response.raise_for_status()
            value = detail_response.json()
            detail = value if isinstance(value, Mapping) else None
        jobs.append(normalize_bamboohr_job(board, item, detail))
    return {
        "jobs": jobs,
        "requests": requests_made,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    }
