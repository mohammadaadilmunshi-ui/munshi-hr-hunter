from __future__ import annotations

import time
from typing import Any, Mapping
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import requests


def _text(value: Any) -> str:
    return str(value or "").strip()


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("Workday board_url must be an absolute HTTP(S) URL")
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def workday_api_base(board: Mapping[str, Any]) -> str:
    tenant = _text(board.get("tenant"))
    site_name = _text(board.get("site_name"))
    board_url = _text(board.get("board_url") or board.get("careers_url"))
    if not tenant or not site_name or not board_url:
        raise ValueError("Workday requires configured tenant, site_name, and board_url")
    return (
        f"{_origin(board_url)}/wday/cxs/"
        f"{quote(tenant, safe='')}/{quote(site_name, safe='')}"
    )


def parse_workday_list(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    postings = payload.get("jobPostings") or payload.get("jobs") or []
    return [dict(item) for item in postings if isinstance(item, Mapping)]


def _location(item: Mapping[str, Any], detail: Mapping[str, Any]) -> str:
    values: list[str] = []
    for value in (
        detail.get("location"),
        item.get("locationsText"),
        item.get("location"),
    ):
        text = _text(value)
        if text and text not in values:
            values.append(text)
    additional = detail.get("additionalLocations") or item.get("additionalLocations") or []
    if isinstance(additional, list):
        for value in additional:
            text = _text(value.get("location") if isinstance(value, Mapping) else value)
            if text and text not in values:
                values.append(text)
    return " | ".join(values)


def _country(detail: Mapping[str, Any], item: Mapping[str, Any]) -> str | None:
    candidates: list[Any] = [detail.get("country"), item.get("country")]
    requisition_location = detail.get("jobRequisitionLocation")
    if isinstance(requisition_location, Mapping):
        candidates.append(requisition_location.get("country"))
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            alpha2 = _text(candidate.get("alpha2Code") or candidate.get("code"))
            if alpha2:
                return alpha2
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            value = (
                candidate.get("descriptor")
                or candidate.get("name")
            )
        else:
            value = candidate
        text = _text(value)
        if text:
            return text
    return None


def _apply_url(board: Mapping[str, Any], item: Mapping[str, Any], detail: Mapping[str, Any]) -> str:
    absolute = _text(detail.get("externalUrl") or item.get("externalUrl"))
    if absolute.startswith(("http://", "https://")):
        return absolute
    path = _text(item.get("externalPath") or detail.get("externalPath"))
    careers = _text(board.get("careers_url") or board.get("board_url"))
    if not path:
        return careers
    return urljoin(careers.rstrip("/") + "/", path.lstrip("/"))


def normalize_workday_job(
    board: Mapping[str, Any],
    item: Mapping[str, Any],
    detail_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    detail_payload = detail_payload or {}
    nested = detail_payload.get("jobPostingInfo")
    detail = dict(nested) if isinstance(nested, Mapping) else dict(detail_payload)
    title = _text(detail.get("title") or item.get("title"))
    location = _location(item, detail)
    external_path = _text(item.get("externalPath") or detail.get("externalPath"))
    external_id = _text(
        detail.get("jobReqId")
        or detail.get("jobPostingId")
        or item.get("jobReqId")
        or item.get("jobPostingId")
        or external_path
    )
    country = _country(detail, item)
    return {
        "source": f"Workday/{_text(board.get('company_name'))}",
        "source_tier": 1,
        "company_name": _text(board.get("company_name")),
        "title": title,
        "ats_job_id": external_id,
        "requisition_id": _text(detail.get("jobReqId") or item.get("jobReqId")),
        "external_id": external_id,
        "location_raw": location,
        "country": country,
        "remote_type": _text(detail.get("remoteType") or item.get("remoteType")) or None,
        "date_posted": _text(detail.get("postedOn") or item.get("postedOn")) or None,
        "employment_type": _text(detail.get("timeType") or item.get("timeType")) or None,
        "description_raw": _text(detail.get("jobDescription") or item.get("jobDescription")),
        "qualifications": detail.get("qualifications"),
        "salary_raw": _text(detail.get("salary") or item.get("salary")) or None,
        "apply_url": _apply_url(board, item, detail),
        "job_url": _apply_url(board, item, detail),
        "_provider_country_raw": country,
        "entry_path": "adapter_discovery",
    }


def fetch_workday_board(
    board: Mapping[str, Any],
    runtime: Mapping[str, Any],
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    client = session or requests.Session()
    api_base = workday_api_base(board)
    timeout = float(runtime.get("request_timeout_seconds") or 20)
    page_size = max(1, min(int(runtime.get("page_size") or 20), 100))
    max_pages = max(1, int(runtime.get("max_pages_per_board") or 1))
    max_jobs = max(1, int(runtime.get("max_jobs_per_board") or page_size))
    detail_limit = max(0, int(runtime.get("max_detail_requests_per_board") or 0))
    fetch_details = bool(runtime.get("fetch_job_details", True))
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": _text(runtime.get("user_agent")) or "AADIL-HR-HUNTER public-job-board-client",
    }
    requests_made = 0
    postings: list[dict[str, Any]] = []
    started = time.monotonic()
    for page in range(max_pages):
        offset = page * page_size
        response = client.post(
            f"{api_base}/jobs",
            json={"appliedFacets": {}, "limit": page_size, "offset": offset, "searchText": ""},
            headers=headers,
            timeout=timeout,
        )
        requests_made += 1
        response.raise_for_status()
        batch = parse_workday_list(response.json())
        postings.extend(batch)
        if len(postings) >= max_jobs or len(batch) < page_size:
            break
    postings = postings[:max_jobs]

    jobs: list[dict[str, Any]] = []
    for index, item in enumerate(postings):
        detail: Mapping[str, Any] | None = None
        external_path = _text(item.get("externalPath"))
        if fetch_details and external_path and index < detail_limit:
            response = client.get(
                f"{api_base}{external_path if external_path.startswith('/') else '/' + external_path}",
                headers=headers,
                timeout=timeout,
            )
            requests_made += 1
            response.raise_for_status()
            value = response.json()
            detail = value if isinstance(value, Mapping) else None
        jobs.append(normalize_workday_job(board, item, detail))
    return {
        "jobs": jobs,
        "requests": requests_made,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    }
