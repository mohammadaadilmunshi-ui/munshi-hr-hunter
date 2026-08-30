from __future__ import annotations

import argparse
from functools import lru_cache
import html
import json
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

import requests

from app.database import get_connection, get_setting, initialize_database
from app.dashboard_targeting_gate import (
    filter_dashboard_jobs,
    record_source_metrics,
)
from app.job_store import save_job
from app.source_runtime import get_source_runtime_state
from app.telegram_auto_dispatch import dispatch_unsent_jobs

try:
    from app.source_run_notifier import emit_source_run_result, run_guarded_main
except Exception:
    def emit_source_run_result(payload: dict[str, Any]) -> None:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))

    def run_guarded_main(_source_name: str, fn: Callable[[], None]) -> None:
        fn()


MARKER = "AADIL_HR_HUNTER_BROAD_MARKET_COVERAGE_V2_6"
MAX_DESCRIPTION = 40000


@dataclass(frozen=True)
class SourceSpec:
    key: str
    display_name: str
    provider_used: str
    source_prefix: str
    fetcher: str
    configuration_required: bool = False


SOURCES: dict[str, SourceSpec] = {
    "remote_ok": SourceSpec(
        "remote_ok", "Remote OK", "remote_ok_public_json",
        "Remote OK", "fetch_remote_ok",
    ),
    "remotive": SourceSpec(
        "remotive", "Remotive", "remotive_public_api",
        "Remotive", "fetch_remotive",
    ),
    "jobicy": SourceSpec(
        "jobicy", "Jobicy", "jobicy_public_api",
        "Jobicy", "fetch_jobicy",
    ),
    "arbeitnow": SourceSpec(
        "arbeitnow", "Arbeitnow", "arbeitnow_public_api",
        "Arbeitnow", "fetch_arbeitnow",
    ),
    "workable": SourceSpec(
        "workable", "Workable", "workable_global_xml",
        "Workable", "fetch_workable",
    ),
    "recruitee": SourceSpec(
        "recruitee", "Recruitee", "recruitee_public_careers_api",
        "Recruitee/", "fetch_recruitee", True,
    ),
    "schema_jobposting": SourceSpec(
        "schema_jobposting", "Schema JobPosting",
        "schema_org_json_ld", "Schema JobPosting/", "fetch_schema_jobposting", True,
    ),
    "rss_job_feeds": SourceSpec(
        "rss_job_feeds", "RSS Job Feeds",
        "rss_atom_xml", "RSS Job Feeds/", "fetch_rss_job_feeds", True,
    ),
    "usajobs": SourceSpec(
        "usajobs", "USAJobs", "usajobs_official_api",
        "USAJobs", "fetch_usajobs", True,
    ),
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--no-store-probe", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--telegram-limit", type=int, default=None)
    parser.add_argument("--max-jobs", type=int, default=None)
    parser.add_argument("--max-boards", type=int, default=None)
    return parser.parse_args()


def _clean_html(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _flatten(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return _clean_html(value)
    if isinstance(value, dict):
        preferred: list[str] = []
        for key in (
            "name", "title", "label", "value", "description",
            "content", "requirements", "text", "department",
            "location", "address", "streetAddress", "addressLocality",
            "addressRegion", "addressCountry",
        ):
            if key in value:
                preferred.append(_flatten(value.get(key)))
        if preferred:
            return " ".join(filter(None, preferred))
        return " ".join(filter(None, (_flatten(item) for item in value.values())))
    if isinstance(value, (list, tuple, set)):
        return " ".join(filter(None, (_flatten(item) for item in value)))
    return str(value)


def _pick(mapping: Any, *names: str, default: Any = "") -> Any:
    if not isinstance(mapping, dict):
        return default
    for name in names:
        value = mapping.get(name)
        if value not in (None, "", [], {}):
            return value
    return default


@lru_cache(maxsize=1)
def _provider_runtime() -> dict[str, Any]:
    value = get_setting("provider_runtime", {})
    if not isinstance(value, dict):
        raise RuntimeError("Canonical provider_runtime configuration is missing.")
    return dict(value)


def _request_timeout(provider: str | None = None) -> float:
    policy = _provider_runtime()
    overrides = policy.get("request_timeout_overrides") or {}
    raw = overrides.get(provider) if provider and isinstance(overrides, dict) else None
    if raw is None:
        raw = policy.get("request_timeout_seconds")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise RuntimeError("Canonical provider request timeout is missing or invalid.") from None
    if value <= 0:
        raise RuntimeError("Canonical provider request timeout must be positive.")
    return value


@lru_cache(maxsize=None)
def _configured_source_tier(source: str) -> int:
    source_name = str(source).split("/", 1)[0]
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT source_tier FROM source_health WHERE lower(source_name)=lower(?)",
            (source_name,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError(f"Market source is not registered: {source_name}")
    return int(row[0])


def _request(
    url: str,
    *,
    timeout: float | None = None,
    accept: str = "application/json, application/xml, text/xml, text/html, */*",
    stream: bool = False,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> requests.Response:
    merged = {
        "User-Agent": str(_provider_runtime().get("user_agent") or "").strip(),
        "Accept": accept,
    }
    if headers:
        merged.update(headers)
    response = requests.get(
        url,
        headers=merged,
        params=params,
        timeout=_request_timeout() if timeout is None else float(timeout),
        stream=stream,
    )
    response.raise_for_status()
    return response


def _remote_type(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values).casefold()
    if "hybrid" in text:
        return "Hybrid"
    if any(token in text for token in ("remote", "work from home", "telecommute")):
        return "Remote"
    if any(token in text for token in ("on-site", "onsite", "in office")):
        return "Onsite"
    return "Not specified"


def _normalize(
    *,
    source: str,
    title: Any,
    company: Any,
    location: Any,
    apply_url: Any,
    description: Any = "",
    job_id: Any = None,
    date_posted: Any = None,
    employment_type: Any = None,
    salary: Any = None,
    remote_type: Any = None,
    country: Any = None,
    city: Any = None,
    state: Any = None,
    qualifications: Any = None,
    responsibilities: Any = None,
    work_authorization: Any = None,
) -> dict[str, Any] | None:
    title_text = _clean_html(title)
    company_text = _clean_html(company) or "Unknown Company"
    url_text = str(apply_url or "").strip()
    if not title_text or not url_text:
        return None

    location_text = _clean_html(location) or "Not specified"
    description_text = _clean_html(description)[:MAX_DESCRIPTION]

    return {
        "source": source,
        "source_tier": _configured_source_tier(source),
        "ats_job_id": str(job_id) if job_id not in (None, "") else None,
        "company_name": company_text,
        "title": title_text,
        "location_raw": location_text,
        "city": _clean_html(city) or None,
        "state": _clean_html(state) or None,
        "country": _clean_html(country) or None,
        "remote_type": (
            _clean_html(remote_type)
            if remote_type not in (None, "")
            else _remote_type(location_text, description_text)
        ),
        "employment_type": _clean_html(employment_type) or "Not specified",
        "job_url": url_text,
        "apply_url": url_text,
        "description_raw": description_text or "Not specified",
        "salary_raw": _clean_html(salary) or None,
        "date_posted": str(date_posted or "").strip() or None,
        "qualifications": _clean_html(qualifications) or None,
        "responsibilities": _clean_html(responsibilities) or None,
        "work_authorization": _clean_html(work_authorization) or None,
    }


def _dedupe_raw(jobs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job in jobs:
        url = str(job.get("apply_url") or job.get("job_url") or "").strip().casefold()
        fallback = "|".join(
            str(job.get(key) or "").strip().casefold()
            for key in ("company_name", "title", "location_raw")
        )
        key = url or fallback
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(job)
    return result


def fetch_remote_ok(args: argparse.Namespace) -> dict[str, Any]:
    response = _request("https://remoteok.com/api")
    payload = response.json()
    records = payload if isinstance(payload, list) else []
    jobs: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict) or not item.get("position"):
            continue
        normalized = _normalize(
            source="Remote OK",
            job_id=_pick(item, "id", "slug"),
            title=_pick(item, "position", "title"),
            company=_pick(item, "company", "company_name"),
            location=_pick(item, "location", default="Remote"),
            apply_url=_pick(item, "apply_url", "url"),
            description=_pick(item, "description"),
            date_posted=_pick(item, "date", "epoch"),
            employment_type=_pick(item, "type", "employment_type"),
            salary=" - ".join(
                str(value) for value in (
                    item.get("salary_min"),
                    item.get("salary_max"),
                )
                if value not in (None, "")
            ),
            remote_type="Remote",
            country=None,
        )
        if normalized:
            jobs.append(normalized)
        if len(jobs) >= args.max_jobs:
            break
    return {
        "jobs": jobs,
        "http_status": response.status_code,
        "requests_made": 1,
        "configuration_required": False,
    }


def fetch_remotive(args: argparse.Namespace) -> dict[str, Any]:
    response = _request("https://remotive.com/api/remote-jobs")
    payload = response.json()
    records = payload.get("jobs") if isinstance(payload, dict) else []
    jobs: list[dict[str, Any]] = []
    for item in records or []:
        normalized = _normalize(
            source="Remotive",
            job_id=_pick(item, "id"),
            title=_pick(item, "title"),
            company=_pick(item, "company_name", "company"),
            location=_pick(item, "candidate_required_location", default="Remote"),
            apply_url=_pick(item, "url"),
            description=_pick(item, "description"),
            date_posted=_pick(item, "publication_date"),
            employment_type=_pick(item, "job_type"),
            salary=_pick(item, "salary"),
            remote_type="Remote",
            country=None,
        )
        if normalized:
            jobs.append(normalized)
        if len(jobs) >= args.max_jobs:
            break
    return {
        "jobs": jobs,
        "http_status": response.status_code,
        "requests_made": 1,
        "configuration_required": False,
    }


def fetch_jobicy(args: argparse.Namespace) -> dict[str, Any]:
    response = _request(
        "https://jobicy.com/api/v2/remote-jobs",
        params={"count": min(max(args.max_jobs, 1), 50), "geo": "usa"},
    )
    payload = response.json()
    records = (
        _pick(payload, "jobs", "data", default=[])
        if isinstance(payload, dict)
        else payload
    )
    jobs: list[dict[str, Any]] = []
    for item in records or []:
        if not isinstance(item, dict):
            continue
        normalized = _normalize(
            source="Jobicy",
            job_id=_pick(item, "id", "jobId"),
            title=_pick(item, "jobTitle", "title"),
            company=_pick(item, "companyName", "company"),
            location=_pick(item, "jobGeo", "location", default="Remote"),
            apply_url=_pick(item, "url", "jobUrl"),
            description=_pick(item, "jobDescription", "description"),
            date_posted=_pick(item, "pubDate", "datePosted"),
            employment_type=_pick(item, "jobType", "employmentType"),
            salary=_pick(item, "annualSalaryMin", "salary"),
            remote_type="Remote",
            country="US",
        )
        if normalized:
            jobs.append(normalized)
    return {
        "jobs": jobs[: args.max_jobs],
        "http_status": response.status_code,
        "requests_made": 1,
        "configuration_required": False,
    }


def fetch_arbeitnow(args: argparse.Namespace) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    page = 1
    requests_made = 0
    last_status = 200
    while len(jobs) < args.max_jobs and page <= 5:
        response = _request(
            "https://www.arbeitnow.com/api/job-board-api",
            params={"page": page},
        )
        requests_made += 1
        last_status = response.status_code
        payload = response.json()
        records = payload.get("data") if isinstance(payload, dict) else []
        if not records:
            break
        for item in records:
            tags = _pick(item, "tags", default=[])
            normalized = _normalize(
                source="Arbeitnow",
                job_id=_pick(item, "slug"),
                title=_pick(item, "title"),
                company=_pick(item, "company_name", "company"),
                location=_pick(item, "location", default="Not specified"),
                apply_url=_pick(item, "url"),
                description=_pick(item, "description"),
                date_posted=_pick(item, "created_at"),
                employment_type=" ".join(tags) if isinstance(tags, list) else tags,
                remote_type=("Remote" if item.get("remote") else None),
                country="DE",
            )
            if normalized:
                jobs.append(normalized)
            if len(jobs) >= args.max_jobs:
                break
        if not payload.get("links", {}).get("next"):
            break
        page += 1
    return {
        "jobs": jobs,
        "http_status": last_status,
        "requests_made": requests_made,
        "configuration_required": False,
    }


def _xml_text(element: ET.Element, *names: str) -> str:
    normalized = {name.casefold() for name in names}
    for child in list(element):
        tag = child.tag.rsplit("}", 1)[-1].casefold()
        if tag in normalized:
            return _flatten(child.text)
    return ""


def fetch_workable(args: argparse.Namespace) -> dict[str, Any]:
    response = _request(
        "https://www.workable.com/boards/workable.xml",
        stream=True,
        accept="application/xml, text/xml, */*",
        timeout=_request_timeout("Workable"),
    )
    response.raw.decode_content = True
    jobs: list[dict[str, Any]] = []
    for _event, element in ET.iterparse(response.raw, events=("end",)):
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        if tag != "job":
            continue
        title = _xml_text(element, "title", "position", "name")
        company = _xml_text(element, "company", "company_name")
        url = _xml_text(element, "url", "apply_url", "application_url")
        location = _xml_text(element, "location", "city")
        normalized = _normalize(
            source=f"Workable/{company or 'Unknown Company'}",
            job_id=_xml_text(element, "id", "shortcode"),
            title=title,
            company=company,
            location=location,
            city=_xml_text(element, "city"),
            state=_xml_text(element, "state", "region"),
            country=_xml_text(element, "country") or None,
            apply_url=url,
            description=_xml_text(element, "description", "content"),
            date_posted=_xml_text(element, "date", "created_at", "published"),
            employment_type=_xml_text(element, "employment_type", "type"),
            salary=_xml_text(element, "salary"),
        )
        element.clear()
        if normalized:
            jobs.append(normalized)
        if len(jobs) >= args.max_jobs:
            break
    response.close()
    return {
        "jobs": jobs,
        "http_status": response.status_code,
        "requests_made": 1,
        "configuration_required": False,
    }


def _board_rows(
    *,
    providers: tuple[str, ...],
    kinds: tuple[str, ...] = (),
    limit: int,
) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        rows: list[dict[str, Any]] = []
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "market_public_boards" in tables:
            clauses: list[str] = []
            params: list[Any] = []
            if providers:
                clauses.append(
                    "lower(COALESCE(provider,'')) IN ("
                    + ",".join("?" for _ in providers)
                    + ")"
                )
                params.extend(value.casefold() for value in providers)
            if kinds:
                clauses.append(
                    "lower(COALESCE(source_kind,'')) IN ("
                    + ",".join("?" for _ in kinds)
                    + ")"
                )
                params.extend(value.casefold() for value in kinds)
            where = " AND ".join(["enabled=1", *clauses])
            result = connection.execute(
                f"""
                SELECT *
                FROM market_public_boards
                WHERE {where}
                ORDER BY priority_weight DESC, company_name COLLATE NOCASE
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            rows.extend(dict(row) for row in result)
        return rows
    finally:
        connection.close()


def fetch_recruitee(args: argparse.Namespace) -> dict[str, Any]:
    boards = _board_rows(
        providers=("recruitee",),
        kinds=("api", "ats"),
        limit=args.max_boards,
    )
    if not boards:
        return {
            "jobs": [],
            "http_status": 200,
            "requests_made": 0,
            "configuration_required": True,
            "configuration_message": (
                "Add a Recruitee company subdomain or careers URL "
                "in Market Coverage."
            ),
        }

    jobs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    requests_made = 0
    for board in boards:
        locator = str(board.get("board_locator") or "").strip()
        url = str(board.get("board_url") or "").strip()
        if not locator and url:
            host = urlparse(url).netloc.casefold()
            if host.endswith(".recruitee.com"):
                locator = host.split(".", 1)[0]
        if not locator:
            continue
        endpoint = f"https://{locator}.recruitee.com/api/offers/"
        try:
            response = _request(endpoint)
            requests_made += 1
            payload = response.json()
            records = _pick(payload, "offers", "data", default=payload)
            if isinstance(records, dict):
                records = records.get("offers") or []
            for item in records or []:
                normalized = _normalize(
                    source=f"Recruitee/{board.get('company_name') or locator}",
                    job_id=_pick(item, "id", "slug"),
                    title=_pick(item, "title"),
                    company=_pick(
                        item,
                        "company_name",
                        default=board.get("company_name") or locator,
                    ),
                    location=_flatten(_pick(item, "location", "locations")),
                    apply_url=_pick(
                        item,
                        "careers_apply_url",
                        "careers_url",
                        "url",
                    ),
                    description=" ".join(
                        filter(
                            None,
                            (
                                _flatten(_pick(item, "description")),
                                _flatten(_pick(item, "requirements")),
                            ),
                        )
                    ),
                    date_posted=_pick(item, "published_at", "created_at"),
                    employment_type=_pick(item, "employment_type", "kind"),
                    remote_type=_pick(item, "remote", "workplace_type"),
                    country=None,
                )
                if normalized:
                    jobs.append(normalized)
                if len(jobs) >= args.max_jobs:
                    break
        except Exception as error:
            errors.append(
                {
                    "company": board.get("company_name"),
                    "endpoint": endpoint,
                    "error": str(error),
                }
            )
        if len(jobs) >= args.max_jobs:
            break

    return {
        "jobs": jobs,
        "http_status": 200 if not errors else None,
        "requests_made": requests_made,
        "configuration_required": False,
        "errors": errors,
    }


def _jsonld_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if value.get("@type") == "JobPosting" or (
            isinstance(value.get("@type"), list)
            and "JobPosting" in value.get("@type")
        ):
            yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _jsonld_objects(item)
        for key, item in value.items():
            if key == "@graph":
                continue
            if isinstance(item, (dict, list)):
                yield from _jsonld_objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _jsonld_objects(item)


def _parse_jsonld_jobs(
    page_url: str,
    text: str,
    *,
    company_fallback: str,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    scripts = re.findall(
        r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        text,
    )
    for raw in scripts:
        cleaned = html.unescape(raw).strip()
        try:
            payload = json.loads(cleaned)
        except Exception:
            continue
        for item in _jsonld_objects(payload):
            org = item.get("hiringOrganization") or {}
            location = item.get("jobLocation") or item.get("jobLocationType") or ""
            if isinstance(location, list):
                location_text = " | ".join(_flatten(entry) for entry in location)
            else:
                location_text = _flatten(location)
            applicant_locations = _flatten(
                item.get("applicantLocationRequirements")
            )
            if applicant_locations:
                location_text = " | ".join(
                    filter(None, (location_text, applicant_locations))
                )
            url = (
                item.get("url")
                or item.get("sameAs")
                or page_url
            )
            normalized = _normalize(
                source=f"Schema JobPosting/{company_fallback}",
                job_id=item.get("identifier"),
                title=item.get("title"),
                company=_pick(org, "name", default=company_fallback),
                location=location_text or "Not specified",
                apply_url=urljoin(page_url, str(url)),
                description=item.get("description"),
                date_posted=item.get("datePosted"),
                employment_type=item.get("employmentType"),
                salary=_flatten(item.get("baseSalary")),
                remote_type=(
                    "Remote"
                    if str(item.get("jobLocationType") or "").casefold()
                    == "telecommute"
                    else None
                ),
                country=None,
                qualifications=item.get("qualifications"),
                responsibilities=item.get("responsibilities"),
            )
            if normalized:
                jobs.append(normalized)
    return jobs


def fetch_schema_jobposting(args: argparse.Namespace) -> dict[str, Any]:
    boards = _board_rows(
        providers=(),
        kinds=("schema", "career_page", "html"),
        limit=args.max_boards,
    )
    if not boards:
        return {
            "jobs": [],
            "http_status": 200,
            "requests_made": 0,
            "configuration_required": True,
            "configuration_message": (
                "Add public company career-page URLs in Market Coverage."
            ),
        }
    jobs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    requests_made = 0
    for board in boards:
        url = str(board.get("board_url") or "").strip()
        if not url:
            continue
        try:
            response = _request(url, accept="text/html, application/xhtml+xml, */*")
            requests_made += 1
            parsed = _parse_jsonld_jobs(
                url,
                response.text,
                company_fallback=str(
                    board.get("company_name") or urlparse(url).netloc
                ),
            )
            jobs.extend(parsed)
        except Exception as error:
            errors.append(
                {
                    "company": board.get("company_name"),
                    "url": url,
                    "error": str(error),
                }
            )
        if len(jobs) >= args.max_jobs:
            break
    return {
        "jobs": jobs[: args.max_jobs],
        "http_status": 200 if not errors else None,
        "requests_made": requests_made,
        "configuration_required": False,
        "errors": errors,
    }


def _rss_items(root: ET.Element) -> Iterable[ET.Element]:
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        if tag in {"item", "entry", "job"}:
            yield element


def fetch_rss_job_feeds(args: argparse.Namespace) -> dict[str, Any]:
    boards = _board_rows(
        providers=(),
        kinds=("rss", "atom", "xml"),
        limit=args.max_boards,
    )
    if not boards:
        return {
            "jobs": [],
            "http_status": 200,
            "requests_made": 0,
            "configuration_required": True,
            "configuration_message": (
                "Add public RSS, Atom, or XML job-feed URLs in Market Coverage."
            ),
        }
    jobs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    requests_made = 0
    for board in boards:
        url = str(board.get("board_url") or "").strip()
        if not url:
            continue
        try:
            response = _request(url, accept="application/rss+xml, application/atom+xml, application/xml, text/xml, */*")
            requests_made += 1
            root = ET.fromstring(response.content)
            for item in _rss_items(root):
                title = _xml_text(item, "title", "position", "name")
                link = _xml_text(item, "link", "url", "apply_url")
                if not link:
                    for child in list(item):
                        if child.tag.rsplit("}", 1)[-1].casefold() == "link":
                            link = str(child.attrib.get("href") or "").strip()
                            if link:
                                break
                normalized = _normalize(
                    source=f"RSS Job Feeds/{board.get('company_name') or urlparse(url).netloc}",
                    job_id=_xml_text(item, "guid", "id"),
                    title=title,
                    company=board.get("company_name") or _xml_text(item, "company", "author"),
                    location=_xml_text(item, "location", "city", "region"),
                    apply_url=urljoin(url, link),
                    description=_xml_text(item, "description", "summary", "content"),
                    date_posted=_xml_text(item, "pubDate", "published", "updated", "date"),
                    employment_type=_xml_text(item, "employment_type", "type"),
                    country=None,
                )
                if normalized:
                    jobs.append(normalized)
                if len(jobs) >= args.max_jobs:
                    break
        except Exception as error:
            errors.append(
                {
                    "company": board.get("company_name"),
                    "url": url,
                    "error": str(error),
                }
            )
        if len(jobs) >= args.max_jobs:
            break
    return {
        "jobs": jobs[: args.max_jobs],
        "http_status": 200 if not errors else None,
        "requests_made": requests_made,
        "configuration_required": False,
        "errors": errors,
    }


def fetch_usajobs(args: argparse.Namespace) -> dict[str, Any]:
    from app.secure_credentials import get_usajobs_credentials
    api_key, email = get_usajobs_credentials()
    # AADIL_USAJOBS_DASHBOARD_CREDENTIALS_POST_V18_V19
    if not api_key or not email:
        return {
            "jobs": [],
            "http_status": 200,
            "requests_made": 0,
            "configuration_required": True,
            "configuration_message": (
                "Set USAJOBS_API_KEY and USAJOBS_EMAIL before enabling USAJobs."
            ),
        }

    from app.dashboard_targeting_gate import load_dashboard_targeting_rules

    rules = load_dashboard_targeting_rules()
    roles = list(rules.target_roles or rules.matching_roles)[:8]
    if not roles:
        roles = ["Human Resources"]
    jobs: list[dict[str, Any]] = []
    requests_made = 0
    last_status = 200
    headers = {
        "Authorization-Key": api_key,
        "User-Agent": email,
        "Host": "data.usajobs.gov",
    }
    for role in roles:
        response = _request(
            "https://data.usajobs.gov/api/search",
            headers=headers,
            params={
                "Keyword": role,
                "ResultsPerPage": min(args.max_jobs, 100),
            },
        )
        requests_made += 1
        last_status = response.status_code
        payload = response.json()
        records = (
            payload.get("SearchResult", {})
            .get("SearchResultItems", [])
        )
        for record in records:
            descriptor = record.get("MatchedObjectDescriptor") or {}
            details = descriptor.get("UserArea", {}).get("Details", {})
            position_locations = descriptor.get("PositionLocation") or []
            location = " | ".join(
                _flatten(item.get("LocationName"))
                for item in position_locations
            )
            remuneration = descriptor.get("PositionRemuneration") or []
            salary = " | ".join(
                " ".join(
                    filter(
                        None,
                        (
                            str(item.get("MinimumRange") or ""),
                            str(item.get("MaximumRange") or ""),
                            str(item.get("RateIntervalCode") or ""),
                        ),
                    )
                )
                for item in remuneration
            )
            apply_url = ""
            uris = descriptor.get("ApplyURI") or []
            if isinstance(uris, list) and uris:
                apply_url = str(uris[0])
            normalized = _normalize(
                source="USAJobs",
                job_id=descriptor.get("PositionID"),
                title=descriptor.get("PositionTitle"),
                company=descriptor.get("OrganizationName") or descriptor.get("DepartmentName"),
                location=location,
                apply_url=apply_url or descriptor.get("PositionURI"),
                description=" ".join(
                    filter(
                        None,
                        (
                            _flatten(descriptor.get("QualificationSummary")),
                            _flatten(details.get("JobSummary")),
                            _flatten(details.get("Requirements")),
                            _flatten(details.get("Evaluations")),
                        ),
                    )
                ),
                date_posted=descriptor.get("PublicationStartDate"),
                employment_type=_flatten(descriptor.get("PositionSchedule")),
                salary=salary,
                country="US",
                work_authorization="Federal posting; review citizenship and eligibility requirements.",
            )
            if normalized:
                jobs.append(normalized)
            if len(jobs) >= args.max_jobs:
                break
        if len(jobs) >= args.max_jobs:
            break
    return {
        "jobs": jobs,
        "http_status": last_status,
        "requests_made": requests_made,
        "configuration_required": False,
    }


def _source_columns(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(source_health)")
    }


def _update_health(
    source_name: str,
    *,
    success: bool,
    raw_jobs: int,
    eligible_jobs: int,
    inserted_jobs: int,
    duplicate_jobs: int,
    rejected_jobs: int,
    provider_used: str,
    elapsed_ms: float,
    http_status: int | None,
    configuration_required: bool,
    error: str | None,
    filter_summary: dict[str, Any] | None,
) -> None:
    connection = get_connection()
    try:
        columns = _source_columns(connection)
        values: dict[str, Any] = {
            "last_run_at": utc_timestamp(),
            "average_response_ms": round(elapsed_ms, 2),
            "jobs_found_last_run": raw_jobs,
            "health_status": (
                "configuration_required"
                if configuration_required
                else "degraded"
                if success and bool(str(error or "").strip())
                else "healthy"
                if success
                else "unhealthy"
            ),
            "last_error": error,
            "updated_at": utc_timestamp(),
        }
        if success or configuration_required:
            values["last_success_at"] = utc_timestamp()
            values["consecutive_failures"] = 0
        else:
            values["last_failure_at"] = utc_timestamp()
        if http_status is not None:
            values["last_http_status"] = http_status

        optional = {
            "raw_jobs_last_run": raw_jobs,
            "eligible_jobs_last_run": eligible_jobs,
            "inserted_jobs_last_run": inserted_jobs,
            "duplicate_jobs_last_run": duplicate_jobs,
            "rejected_jobs_last_run": rejected_jobs,
            "provider_used_last_run": provider_used,
            "filter_summary_json": json.dumps(
                filter_summary or {},
                ensure_ascii=False,
                default=str,
            )[:20000],
            "targeting_rules_hash": (
                (filter_summary or {}).get("targeting_rules_hash")
            ),
        }
        for key, value in optional.items():
            if key in columns:
                values[key] = value

        assignments = []
        parameters: list[Any] = []
        for key, value in values.items():
            if key not in columns:
                continue
            if key == "consecutive_failures" and not (success or configuration_required):
                assignments.append(
                    "consecutive_failures=COALESCE(consecutive_failures,0)+1"
                )
                continue
            assignments.append(f"{key}=?")
            parameters.append(value)
        parameters.append(source_name)
        connection.execute(
            f"""
            UPDATE source_health
            SET {", ".join(assignments)}
            WHERE lower(source_name)=lower(?)
            """,
            parameters,
        )
        connection.commit()
    finally:
        connection.close()


def _configuration_skip(
    spec: SourceSpec,
    state: dict[str, Any],
    message: str,
    elapsed_ms: float,
) -> None:
    _update_health(
        spec.display_name,
        success=True,
        raw_jobs=0,
        eligible_jobs=0,
        inserted_jobs=0,
        duplicate_jobs=0,
        rejected_jobs=0,
        provider_used=spec.provider_used,
        elapsed_ms=elapsed_ms,
        http_status=200,
        configuration_required=True,
        error=message,
        filter_summary={},
    )
    emit_source_run_result(
        {
            "success": True,
            "mode": "broad-market-coverage-v2.6",
            "source": spec.display_name,
            "worker_action": "configuration_required",
            "configuration_required": True,
            "configuration_message": message,
            "source_state": state,
            "network_request_made": False,
            "raw_jobs_found": 0,
            "eligible_jobs_found": 0,
            "jobs_inserted": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
        }
    )


def _self_test(spec: SourceSpec) -> None:
    fixture = [
        _normalize(
            source=spec.display_name,
            title="People Analytics Analyst",
            company="Fixture Company",
            location="New York, NY, US",
            city="New York",
            state="NY",
            country="US",
            apply_url="https://example.com/jobs/1",
            description="Analyze workforce data and HR reporting.",
            remote_type="Hybrid",
        ),
        _normalize(
            source=spec.display_name,
            title="Senior Software Engineering Manager",
            company="Fixture Company",
            location="New York, NY, US",
            apply_url="https://example.com/jobs/2",
            description="Engineering management.",
        ),
    ]
    fixture = [item for item in fixture if item]
    print(
        json.dumps(
            {
                "success": True,
                "self_test": True,
                "marker": MARKER,
                "source": spec.display_name,
                "normalized_jobs": len(fixture),
                "network_request_made": False,
                "database_writes": 0,
                "telegram_messages": 0,
                "n8n_calls": 0,
            },
            indent=2,
        )
    )


def cli_main(source_key: str) -> None:
    if source_key not in SOURCES:
        raise KeyError(f"Unknown market source: {source_key}")
    spec = SOURCES[source_key]
    args = parse_args()

    runtime = _provider_runtime()
    contract = get_setting("downstream_contract", {}) or {}
    try:
        args.max_jobs = max(1, int(
            args.max_jobs if args.max_jobs is not None
            else runtime["market_source_run_job_limit"]
        ))
        args.max_boards = max(1, int(
            args.max_boards if args.max_boards is not None
            else runtime["market_source_board_limit"]
        ))
        configured_telegram_limit = int(contract["telegram_default_batch_limit"])
        telegram_max = int(contract["telegram_max_batch_size"])
        args.telegram_limit = max(1, min(
            int(args.telegram_limit) if args.telegram_limit is not None
            else configured_telegram_limit,
            telegram_max,
        ))
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Canonical market-source execution limits are incomplete.") from None

    if args.self_test:
        _self_test(spec)
        return

    initialize_database()
    state = get_source_runtime_state(spec.display_name)

    if not state.get("exists"):
        emit_source_run_result(
            {
                "success": True,
                "source": spec.display_name,
                "worker_action": "skip",
                "skip_reason": "source_not_configured",
                "network_request_made": False,
                "n8n_calls": 0,
            }
        )
        return

    if not state.get("enabled"):
        emit_source_run_result(
            {
                "success": True,
                "source": spec.display_name,
                "worker_action": "skip",
                "skip_reason": "source_disabled",
                "network_request_made": False,
                "n8n_calls": 0,
            }
        )
        return

    if not state.get("due") and not args.run_now and not args.no_store_probe:
        emit_source_run_result(
            {
                "success": True,
                "source": spec.display_name,
                "worker_action": "skip",
                "skip_reason": "cadence_not_due",
                "source_state": state,
                "network_request_made": False,
                "n8n_calls": 0,
            }
        )
        return

    started = time.perf_counter()
    run_started_at = datetime.now(timezone.utc).isoformat()
    fetcher = globals()[spec.fetcher]

    try:
        fetched = fetcher(args)
        fetch_duration_ms = (time.perf_counter() - started) * 1000
        configuration_required = bool(fetched.get("configuration_required"))
        if configuration_required:
            _configuration_skip(
                spec,
                state,
                str(
                    fetched.get("configuration_message")
                    or "Source configuration is required."
                ),
                fetch_duration_ms,
            )
            return

        raw_jobs = _dedupe_raw(fetched.get("jobs") or [])[: args.max_jobs]
        for job in raw_jobs:
            job.setdefault("_query_name", "Configured provider feed")
        filtered = filter_dashboard_jobs(raw_jobs)
        eligible = list(filtered.get("eligible_jobs") or [])

        stored_results: list[dict[str, Any]] = []
        if not args.no_store_probe:
            connection = get_connection()
            try:
                for job in eligible:
                    stored_results.append(
                        save_job(
                            connection,
                            job,
                            actor=f"{source_key}_worker",
                        )
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

        inserted = sum(
            1 for item in stored_results if item.get("inserted")
        )
        rejected_at_store = sum(
            1
            for item in stored_results
            if item.get("status") == "rejected_by_dashboard_targeting"
        )
        duplicates = (
            len(stored_results) - inserted - rejected_at_store
            if stored_results
            else 0
        )
        rejected = (
            int(filtered.get("excluded_by_role") or 0)
            + int(filtered.get("excluded_by_location") or 0)
            + int(filtered.get("excluded_by_hard_reject") or 0)
            + int(filtered.get("excluded_by_company_blacklist") or 0)
            + int(filtered.get("excluded_by_other_targeting") or 0)
            + rejected_at_store
        )
        filtered["request_count"] = int(fetched.get("requests_made") or 0)
        filtered["duration_ms"] = round(fetch_duration_ms, 2)
        filtered["errors"] = list(fetched.get("errors") or [])
        filtered["query_requests"] = [{
            "query_name": "Configured provider feed",
            "role_family": "",
            "requests": int(fetched.get("requests_made") or 0),
            "raw": len(raw_jobs),
            "errors": len(fetched.get("errors") or []),
            "duration_ms": filtered["duration_ms"],
        }]

        telegram = {
            "telegram_enabled": False,
            "eligible_jobs": 0,
            "telegram_messages_sent": 0,
            "errors": [],
        }
        if not args.no_store_probe:
            telegram = dispatch_unsent_jobs(
                source_prefix=spec.source_prefix,
                limit=args.telegram_limit,
            )
        filtered["telegram_messages"] = int(
            telegram.get("telegram_messages_sent") or 0
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        filtered["run_started_at"] = run_started_at
        filtered["duration_ms"] = round(elapsed_ms, 2)
        filtered.setdefault("_stage_durations_ms", {})["FETCH"] = round(
            fetch_duration_ms, 2
        )
        filtered["query_requests"][0]["duration_ms"] = round(
            fetch_duration_ms, 2
        )

        if not args.no_store_probe:
            _update_health(
                spec.display_name,
                success=True,
                raw_jobs=len(raw_jobs),
                eligible_jobs=len(eligible),
                inserted_jobs=inserted,
                duplicate_jobs=duplicates,
                rejected_jobs=rejected,
                provider_used=spec.provider_used,
                elapsed_ms=elapsed_ms,
                http_status=fetched.get("http_status"),
                configuration_required=False,
                error=(
                    json.dumps(fetched.get("errors"), ensure_ascii=False)[:2000]
                    if fetched.get("errors")
                    else None
                ),
                filter_summary=filtered,
            )
            record_source_metrics(
                spec.display_name,
                raw_jobs=len(raw_jobs),
                eligible_jobs=len(eligible),
                inserted_jobs=inserted,
                duplicate_jobs=duplicates,
                rejected_jobs=rejected,
                provider_used=spec.provider_used,
                filter_summary=filtered,
            )

        emit_source_run_result(
            {
                "success": True,
                "partial_success": bool(fetched.get("errors")),
                "mode": "broad-market-coverage-v2.6",
                "source": spec.display_name,
                "worker_action": (
                    "no_store_probe" if args.no_store_probe else "run"
                ),
                "configuration_source": "SQLite dashboard",
                "dashboard_targeting_gate": True,
                "final_job_store_gate": True,
                "network_request_made": bool(
                    fetched.get("requests_made")
                ),
                "requests_made": int(fetched.get("requests_made") or 0),
                "provider_used": spec.provider_used,
                "raw_jobs_found": len(raw_jobs),
                "excluded_by_role": int(filtered.get("excluded_by_role") or 0),
                "excluded_by_location": int(filtered.get("excluded_by_location") or 0),
                "excluded_by_hard_reject": int(filtered.get("excluded_by_hard_reject") or 0),
                "excluded_by_company_blacklist": int(filtered.get("excluded_by_company_blacklist") or 0),
                "duplicates_within_run": int(filtered.get("duplicates_within_run") or 0),
                "unique_jobs_ready": len(eligible),
                "jobs_inserted": inserted,
                "database_duplicates": duplicates,
                "targeting_rules_hash": filtered.get("targeting_rules_hash"),
                "auto_telegram_enabled": telegram.get("telegram_enabled"),
                "telegram_pending_before": telegram.get("eligible_jobs"),
                "telegram_messages": telegram.get("telegram_messages_sent"),
                "telegram_dispatch_errors": telegram.get("errors"),
                "no_store": bool(args.no_store_probe),
                "paid_api_calls": 0,
                "n8n_calls": 0,
                "errors": fetched.get("errors") or [],
                "elapsed_ms": round(elapsed_ms, 2),
            }
        )
    except Exception as error:
        elapsed_ms = (time.perf_counter() - started) * 1000
        if not args.no_store_probe:
            _update_health(
                spec.display_name,
                success=False,
                raw_jobs=0,
                eligible_jobs=0,
                inserted_jobs=0,
                duplicate_jobs=0,
                rejected_jobs=0,
                provider_used=spec.provider_used,
                elapsed_ms=elapsed_ms,
                http_status=None,
                configuration_required=False,
                error=str(error)[:2000],
                filter_summary={},
            )
        emit_source_run_result(
            {
                "success": False,
                "mode": "broad-market-coverage-v2.6",
                "source": spec.display_name,
                "worker_action": "failed",
                "network_request_made": True,
                "error": str(error),
                "no_store": bool(args.no_store_probe),
                "telegram_messages": 0,
                "paid_api_calls": 0,
                "n8n_calls": 0,
                "elapsed_ms": round(elapsed_ms, 2),
            }
        )
        raise


def guarded_cli(source_key: str) -> None:
    spec = SOURCES[source_key]
    run_guarded_main(spec.display_name, lambda: cli_main(source_key))
