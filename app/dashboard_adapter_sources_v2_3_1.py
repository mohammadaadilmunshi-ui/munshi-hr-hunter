from __future__ import annotations

import argparse
from functools import lru_cache
import html
import importlib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable
from urllib.parse import quote, urlparse

import requests

from app.database import get_connection, get_setting
from app.job_store import save_job
from app.telegram_auto_dispatch import (
    attribute_dispatch_to_current_jobs,
    dispatch_unsent_jobs,
)
from app.runtime_config import telegram_batch_limit

MARKER = "AADIL_HR_HUNTER_ALL_ADAPTERS_ACTIVATION_V2_4"
BOARD_SOURCES = {"Personio", "Pinpoint", "Comeet"}
_REQUEST_METRICS: dict[str, int] = {"attempts": 0}


@dataclass(frozen=True)
class SourceSpec:
    key: str
    display_name: str
    module_name: str
    fetcher: str


SOURCE_SPECS: dict[str, SourceSpec] = {
    "nyc_open_data": SourceSpec(
        "nyc_open_data", "NYC Open Data",
        "app.nyc_open_data_worker", "fetch_nyc_open_data",
    ),
    "we_work_remotely": SourceSpec(
        "we_work_remotely", "We Work Remotely",
        "app.we_work_remotely_worker", "fetch_we_work_remotely",
    ),
    "personio": SourceSpec(
        "personio", "Personio",
        "app.personio_worker", "fetch_personio",
    ),
    "pinpoint": SourceSpec(
        "pinpoint", "Pinpoint",
        "app.pinpoint_worker", "fetch_pinpoint",
    ),
    "comeet": SourceSpec(
        "comeet", "Comeet",
        "app.comeet_worker", "fetch_comeet",
    ),
}




DEFAULT_ACTIVATION_CONFIG: dict[str, Any] = {
    "excluded_title_terms": [],
    "nyc_search_terms": [],
}


def load_activation_config() -> dict[str, Any]:
    value = get_setting("adapter_activation", {})
    if not isinstance(value, dict):
        value = {}
    merged = dict(DEFAULT_ACTIVATION_CONFIG)
    merged.update(value)
    return merged


def apply_dashboard_title_guard(
    jobs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return list(jobs), []


def _table_columns(connection, table_name: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    }


def _first_existing(columns: set[str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in columns:
            return name
    return None


def sync_public_boards_from_registry() -> dict[str, Any]:
    """Import matching public Personio/Pinpoint/Comeet URLs from the
    existing ATS company registry. No network call is made.
    """
    connection = get_connection()
    inserted = 0
    scanned = 0
    detected: dict[str, int] = {
        "Personio": 0,
        "Pinpoint": 0,
        "Comeet": 0,
    }
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ats_company_registry'"
        ).fetchone()
        board_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='public_adapter_boards'"
        ).fetchone()
        if table is None or board_table is None:
            return {
                "success": True,
                "scanned": 0,
                "inserted": 0,
                "detected": detected,
                "reason": "registry_or_board_table_missing",
            }

        columns = _table_columns(connection, "ats_company_registry")
        company_col = _first_existing(columns, ("company_name", "name", "company"))
        url_col = _first_existing(columns, ("careers_url", "career_url", "board_url", "url"))
        token_col = _first_existing(columns, ("board_token", "public_token", "token"))
        enabled_col = _first_existing(columns, ("enabled", "is_enabled", "active"))
        if not company_col or not url_col:
            return {
                "success": True,
                "scanned": 0,
                "inserted": 0,
                "detected": detected,
                "reason": "registry_columns_not_supported",
            }

        select_parts = [
            f'"{company_col}" AS company_name',
            f'"{url_col}" AS careers_url',
        ]
        if token_col:
            select_parts.append(f'"{token_col}" AS board_token')
        else:
            select_parts.append("NULL AS board_token")
        where = ""
        if enabled_col:
            where = f' WHERE COALESCE("{enabled_col}",1)=1'
        rows = connection.execute(
            f"SELECT {', '.join(select_parts)} FROM ats_company_registry{where}"
        ).fetchall()

        for row in rows:
            scanned += 1
            company = str(row["company_name"] or "").strip()
            raw_url = str(row["careers_url"] or "").strip()
            registry_token = str(row["board_token"] or "").strip()
            if not company or not raw_url:
                continue
            try:
                parsed = urlparse(raw_url)
            except Exception:
                continue
            host = parsed.netloc.lower().split(":", 1)[0]
            path = parsed.path or ""
            query = parsed.query or ""
            source_name = ""
            locator = ""
            board_url = ""
            public_token = None
            company_uid = None

            if ".jobs.personio." in host:
                source_name = "Personio"
                locator = host.split(".", 1)[0]
                board_url = f"https://{host}/xml?language=en"
            elif host.endswith(".pinpointhq.com"):
                source_name = "Pinpoint"
                locator = host.split(".", 1)[0]
                board_url = f"https://{host}/postings.json"
            elif "comeet.co" in host:
                uid_match = re.search(
                    r"/(?:careers-api/2\\.0/company|company|jobs)/([^/?#]+)",
                    path,
                    flags=re.IGNORECASE,
                )
                token_match = re.search(r"(?:^|&)token=([^&]+)", query)
                company_uid = uid_match.group(1) if uid_match else None
                public_token = token_match.group(1) if token_match else (registry_token or None)
                if company_uid:
                    source_name = "Comeet"
                    locator = company_uid
                    board_url = (
                        "https://www.comeet.co/careers-api/2.0/company/"
                        f"{company_uid}/positions"
                    )

            if not source_name:
                continue
            detected[source_name] += 1
            exists = connection.execute(
                """
                SELECT 1 FROM public_adapter_boards
                WHERE lower(source_name)=lower(?)
                  AND lower(company_name)=lower(?)
                  AND COALESCE(board_url,'')=COALESCE(?, '')
                LIMIT 1
                """,
                (source_name, company, board_url),
            ).fetchone()
            if exists:
                connection.execute(
                    """
                    UPDATE public_adapter_boards
                    SET enabled=1, updated_at=CURRENT_TIMESTAMP
                    WHERE lower(source_name)=lower(?)
                      AND lower(company_name)=lower(?)
                      AND COALESCE(board_url,'')=COALESCE(?, '')
                    """,
                    (source_name, company, board_url),
                )
                continue
            connection.execute(
                """
                INSERT INTO public_adapter_boards(
                    source_name, company_name, board_locator, board_url,
                    public_token, company_uid, enabled, notes
                ) VALUES(?,?,?,?,?,?,1,?)
                """,
                (
                    source_name,
                    company,
                    locator or None,
                    board_url or None,
                    public_token,
                    company_uid,
                    "Auto-imported from existing ATS company registry by V2.4",
                ),
            )
            inserted += 1

        connection.commit()
        return {
            "success": True,
            "scanned": scanned,
            "inserted": inserted,
            "detected": detected,
            "network_request_made": False,
        }
    finally:
        connection.close()


def _clean_html(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
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
            "name", "title", "label", "value", "description", "content",
            "requirements", "text", "department", "location",
        ):
            if key in value:
                preferred.append(_flatten(value.get(key)))
        if preferred:
            return " ".join(filter(None, preferred))
        return " ".join(filter(None, (_flatten(v) for v in value.values())))
    if isinstance(value, (list, tuple, set)):
        return " ".join(filter(None, (_flatten(v) for v in value)))
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
def _runtime_policy() -> dict[str, Any]:
    value = get_setting("provider_runtime", {})
    if not isinstance(value, dict):
        raise RuntimeError("Canonical provider_runtime configuration is missing.")
    return dict(value)


def _request_timeout(provider: str | None = None) -> float:
    policy = _runtime_policy()
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
def _configured_source_tier(source_name: str) -> int:
    row = _source_row(source_name)
    if row is None:
        raise RuntimeError(f"Adapter source is not registered: {source_name}")
    return int(row["source_tier"])


def _request(url: str, *, timeout: float | None = None, **kwargs: Any) -> requests.Response:
    _REQUEST_METRICS["attempts"] = int(_REQUEST_METRICS.get("attempts") or 0) + 1
    user_agent = str(_runtime_policy().get("user_agent") or "").strip()
    if not user_agent:
        raise RuntimeError("Canonical provider user_agent is not configured.")
    headers = {"User-Agent": user_agent, "Accept": "application/json, application/xml, text/xml, */*"}
    custom = kwargs.pop("headers", None) or {}
    headers.update(custom)
    response = requests.get(
        url,
        headers=headers,
        timeout=_request_timeout() if timeout is None else float(timeout),
        **kwargs,
    )
    response.raise_for_status()
    return response


def _source_row(source_name: str) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM source_health WHERE lower(source_name)=lower(?) LIMIT 1",
            (source_name,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        connection.close()


def load_enabled_boards(source_name: str) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='public_adapter_boards'"
        ).fetchone()
        if table is None:
            return []
        rows = connection.execute(
            """
            SELECT *
            FROM public_adapter_boards
            WHERE lower(source_name)=lower(?) AND enabled=1
            ORDER BY company_name, id
            """,
            (source_name,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def make_job(
    spec: SourceSpec,
    *,
    company: Any,
    title: Any,
    location: Any,
    job_url: Any,
    description: Any = "",
    posted_at: Any = None,
    ats_job_id: Any = None,
    apply_url: Any = None,
    remote_type: Any = None,
    employment_type: Any = None,
    salary_raw: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    company_text = str(company or "Unknown Company").strip()
    title_text = str(title or "Unknown Position").strip()
    location_text = str(location or "Not specified").strip()
    url_text = str(job_url or apply_url or "").strip()
    source_suffix = re.sub(r"\s+", " ", company_text)[:100]
    job: dict[str, Any] = {
        "source": f"{spec.display_name}/{source_suffix}",
        "source_tier": _configured_source_tier(spec.display_name),
        "ats_job_id": str(ats_job_id).strip() if ats_job_id not in (None, "") else None,
        "company_name": company_text,
        "company": company_text,
        "title": title_text,
        "location_raw": location_text,
        "location": location_text,
        "remote_type": str(remote_type or "Not specified").strip(),
        "employment_type": str(employment_type or "Not specified").strip(),
        "job_url": url_text or None,
        "url": url_text or None,
        "apply_url": str(apply_url or url_text or "").strip() or None,
        "description_raw": _clean_html(description) or "Not specified",
        "description": _clean_html(description) or "Not specified",
        "salary_raw": str(salary_raw).strip() if salary_raw not in (None, "") else None,
        "date_posted": str(posted_at).strip() if posted_at not in (None, "") else None,
        "posted_at": str(posted_at).strip() if posted_at not in (None, "") else None,
        "entry_path": "adapter_discovery",
        "adapter_name": spec.display_name,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        for key, value in extra.items():
            if value not in (None, "", [], {}):
                job[key] = value
    return job


def fetch_nyc_open_data(spec: SourceSpec, limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    from app.dashboard_targeting_gate import build_dashboard_search_queries
    search_terms, _rules_hash = build_dashboard_search_queries(terms_per_query=1, max_queries=24)
    search_terms = [term.strip('"') for term in search_terms]

    params: dict[str, Any] = {
        "$limit": max(1, min(limit, 3000)),
        "$order": "posting_date DESC",
    }
    if search_terms:
        clauses: list[str] = []
        for raw_term in search_terms[:24]:
            term = raw_term.upper().replace("'", "''")
            for field in (
                "business_title",
                "civil_service_title",
                "job_category",
            ):
                clauses.append(
                    f"upper({field}) like '%{term}%'"
                )
        params["$where"] = "(" + " OR ".join(clauses) + ")"

    try:
        response = _request(
            "https://data.cityofnewyork.us/resource/kpav-sd4t.json",
            params=params,
        )
    except Exception as targeted_error:
        if "$where" not in params:
            raise
        errors.append(
            "Targeted NYC query fell back to latest postings: "
            f"{type(targeted_error).__name__}: {targeted_error}"
        )
        response = _request(
            "https://data.cityofnewyork.us/resource/kpav-sd4t.json",
            params={
                "$limit": max(1, min(limit, 3000)),
                "$order": "posting_date DESC",
            },
        )

    rows = response.json()
    jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in rows if isinstance(rows, list) else []:
        title = _pick(item, "business_title", "job_title", "civil_service_title", "title")
        job_id = _pick(item, "job_id", "job_opening_id", "id")
        dedupe_key = str(job_id or title or "").strip().lower()
        if dedupe_key and dedupe_key in seen_ids:
            continue
        if dedupe_key:
            seen_ids.add(dedupe_key)
        company = _pick(item, "agency", "agency_name", default="City of New York")
        location = _pick(item, "work_location", "work_location_1", "location", default="New York, NY")
        url = _pick(item, "job_link", "external_job_link", "link", "url")
        if not url:
            url = "https://cityjobs.nyc.gov/jobsearch?keyword=" + quote(str(title or job_id or ""))
        description = " ".join(filter(None, [
            _flatten(_pick(item, "job_description", "description")),
            _flatten(_pick(item, "minimum_qual_requirements", "minimum_qualification_requirements")),
            _flatten(_pick(item, "preferred_skills")),
            _flatten(_pick(item, "additional_information")),
            _flatten(_pick(item, "to_apply")),
        ]))
        salary = " - ".join(filter(None, [
            str(_pick(item, "salary_range_from", "salary_from") or "").strip(),
            str(_pick(item, "salary_range_to", "salary_to") or "").strip(),
        ])) or _pick(item, "salary_frequency", "salary")
        jobs.append(make_job(
            spec,
            company=company,
            title=title,
            location=location,
            job_url=url,
            apply_url=url,
            description=description,
            posted_at=_pick(item, "posting_date", "post_until", "posting_updated"),
            ats_job_id=job_id,
            employment_type=_pick(item, "full_time_part_time_indicator", "employment_type"),
            salary_raw=salary,
            extra={
                "agency": company,
                "civil_service_title": _pick(item, "civil_service_title"),
                "nyc_targeted_query": bool(search_terms),
            },
        ))
    return jobs, errors


def fetch_we_work_remotely(spec: SourceSpec, limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    response = _request("https://weworkremotely.com/remote-jobs.rss", headers={"Accept": "application/rss+xml, application/xml, text/xml"})
    root = ET.fromstring(response.content)
    jobs: list[dict[str, Any]] = []
    for item in root.findall(".//item")[: max(1, min(limit, 500))]:
        values = {child.tag.rsplit("}", 1)[-1].lower(): (child.text or "").strip() for child in list(item)}
        raw_title = values.get("title", "")
        company = values.get("creator") or values.get("company") or "We Work Remotely employer"
        title = raw_title
        if ":" in raw_title and company == "We Work Remotely employer":
            possible_company, possible_title = raw_title.split(":", 1)
            if possible_company.strip() and possible_title.strip():
                company, title = possible_company.strip(), possible_title.strip()
        location = values.get("region") or values.get("location") or "Remote"
        description = values.get("encoded") or values.get("description") or ""
        jobs.append(make_job(
            spec,
            company=company,
            title=title,
            location=f"Remote - {location}" if "remote" not in location.lower() else location,
            job_url=values.get("link") or values.get("guid"),
            description=description,
            posted_at=values.get("pubdate") or values.get("date"),
            ats_job_id=values.get("guid") or values.get("link"),
            remote_type="Remote",
            extra={"categories": [c.text for c in item.findall("category") if c.text]},
        ))
    return jobs, []


def _board_url(board: dict[str, Any], source: str) -> str:
    explicit = str(board.get("board_url") or "").strip()
    locator = str(board.get("board_locator") or "").strip()
    if explicit:
        return explicit
    if locator.startswith("http://") or locator.startswith("https://"):
        return locator
    locator = locator.strip("/ ")
    if source == "Personio":
        return f"https://{locator}.jobs.personio.de/xml?language=en"
    if source == "Pinpoint":
        return f"https://{locator}.pinpointhq.com/postings.json"
    if source == "Comeet":
        company_uid = str(board.get("company_uid") or locator).strip()
        return f"https://www.comeet.co/careers-api/2.0/company/{company_uid}/positions"
    return explicit


def _xml_text(node: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1].lower() in wanted and (child.text or "").strip():
            return (child.text or "").strip()
    return ""


def _fair_board_run_limits(
    boards: list[dict[str, Any]],
    limit: int,
) -> tuple[int, int]:
    run_limit = max(1, int(limit))
    board_count = max(1, len(boards))
    return run_limit, max(1, (run_limit + board_count - 1) // board_count)


def fetch_personio(spec: SourceSpec, limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    boards = load_enabled_boards(spec.display_name)
    run_limit, per_board_limit = _fair_board_run_limits(boards, limit)
    for board in boards:
        remaining = run_limit - len(jobs)
        if remaining <= 0:
            break
        company = str(board.get("company_name") or "Personio employer").strip()
        try:
            url = _board_url(board, "Personio")
            response = _request(url, headers={"Accept": "application/xml, text/xml"})
            root = ET.fromstring(response.content)
            nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"position", "job"}]
            for node in nodes[: min(remaining, per_board_limit, 2000)]:
                job_id = _xml_text(node, "id", "positionid")
                title = _xml_text(node, "name", "title")
                location = " / ".join(filter(None, [_xml_text(node, "office"), _xml_text(node, "location")])) or "Not specified"
                description_parts = []
                for child in node.iter():
                    local = child.tag.rsplit("}", 1)[-1].lower()
                    if local in {"jobdescription", "description", "value", "requirements", "tasks", "profile"}:
                        cleaned = _clean_html(child.text or "")
                        if cleaned:
                            description_parts.append(cleaned)
                base = url.split("/xml", 1)[0]
                job_url = f"{base}/job/{job_id}?language=en" if job_id else base
                jobs.append(make_job(
                    spec,
                    company=company,
                    title=title,
                    location=location,
                    job_url=job_url,
                    description=" ".join(dict.fromkeys(description_parts)),
                    posted_at=_xml_text(node, "createdat", "created_at", "publicationdate", "date"),
                    ats_job_id=job_id,
                    employment_type=_xml_text(node, "employmenttype", "employment_type", "schedule"),
                    extra={"department": _xml_text(node, "department"), "recruiting_category": _xml_text(node, "recruitingcategory")},
                ))
        except Exception as exc:
            errors.append(f"{company}: {type(exc).__name__}: {exc}")
    return jobs, errors


def fetch_pinpoint(spec: SourceSpec, limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    boards = load_enabled_boards(spec.display_name)
    run_limit, per_board_limit = _fair_board_run_limits(boards, limit)
    for board in boards:
        remaining = run_limit - len(jobs)
        if remaining <= 0:
            break
        company_default = str(board.get("company_name") or "Pinpoint employer").strip()
        try:
            url = _board_url(board, "Pinpoint")
            payload = _request(url).json()
            if isinstance(payload, list):
                rows = payload
            elif isinstance(payload, dict):
                rows = payload.get("data") or payload.get("postings") or payload.get("jobs") or []
            else:
                rows = []
            for item in rows[: min(remaining, per_board_limit, 2000)]:
                if not isinstance(item, dict):
                    continue
                location = _flatten(_pick(item, "location", "locations")) or "Not specified"
                remote = _flatten(_pick(item, "workplace_type", "remote_type"))
                if "remote" in remote.lower() and "remote" not in location.lower():
                    location = f"Remote - {location}"
                jobs.append(make_job(
                    spec,
                    company=_pick(item, "company_name", "company", default=company_default),
                    title=_pick(item, "title", "name"),
                    location=location,
                    job_url=_pick(item, "url", "apply_url", "absolute_url"),
                    apply_url=_pick(item, "apply_url", "url", "absolute_url"),
                    description=" ".join(filter(None, [_flatten(item.get("description")), _flatten(item.get("requirements")), _flatten(item.get("content"))])),
                    posted_at=_pick(item, "published_at", "created_at", "updated_at"),
                    ats_job_id=_pick(item, "id", "uid", "slug"),
                    remote_type=remote or "Not specified",
                    employment_type=_flatten(_pick(item, "employment_type", "contract_type")),
                    extra={"department": _flatten(item.get("department"))},
                ))
        except Exception as exc:
            errors.append(f"{company_default}: {type(exc).__name__}: {exc}")
    return jobs, errors


def fetch_comeet(spec: SourceSpec, limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    boards = load_enabled_boards(spec.display_name)
    run_limit, per_board_limit = _fair_board_run_limits(boards, limit)
    # `default_source_run_job_limit` is a source-cycle bound, not a per-board
    # allowance. Share it across configured boards so one large provider does
    # not multiply the canonical limit while later employers remain sampled.
    for board in boards:
        remaining = run_limit - len(jobs)
        if remaining <= 0:
            break
        company_default = str(board.get("company_name") or "Comeet employer").strip()
        try:
            url = _board_url(board, "Comeet")
            token = str(board.get("public_token") or "").strip()
            params = {"details": "true"}
            if token:
                params["token"] = token
            payload = _request(
                url, params=params, timeout=_request_timeout("Comeet")
            ).json()
            if isinstance(payload, list):
                rows = payload
            elif isinstance(payload, dict):
                rows = payload.get("positions") or payload.get("data") or payload.get("jobs") or []
            else:
                rows = []
            for item in rows[: min(remaining, per_board_limit, 2000)]:
                if not isinstance(item, dict):
                    continue
                location = _flatten(item.get("location")) or "Not specified"
                workplace = _flatten(item.get("workplace_type"))
                if "remote" in workplace.lower() and "remote" not in location.lower():
                    location = f"Remote - {location}"
                jobs.append(make_job(
                    spec,
                    company=_pick(item, "company_name", default=company_default),
                    title=_pick(item, "name", "title"),
                    location=location,
                    job_url=_pick(item, "url_active_page", "url_recruit_hosted_page", "position_url", "url"),
                    description=" ".join(filter(None, [_flatten(item.get("description")), _flatten(item.get("requirements")), _flatten(item.get("details"))])),
                    posted_at=_pick(item, "time_updated", "created_at", "published_at"),
                    ats_job_id=_pick(item, "uid", "id"),
                    remote_type=workplace or "Not specified",
                    employment_type=_flatten(item.get("employment_type")),
                    extra={"department": _flatten(item.get("department")), "experience_level": _flatten(item.get("experience_level"))},
                ))
        except Exception as exc:
            errors.append(f"{company_default}: {type(exc).__name__}: {exc}")
    return jobs, errors


def canonical_dashboard_filter() -> tuple[Callable[[list[dict[str, Any]]], dict[str, Any]], str]:
    from app.dashboard_targeting_gate import filter_dashboard_jobs
    return filter_dashboard_jobs, "app.dashboard_targeting_gate.filter_dashboard_jobs"


def _update_source_health(source_name: str, *, success: bool, raw_count: int, elapsed_ms: float, error_text: str | None, configuration_required: bool = False) -> None:
    connection = get_connection()
    try:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(source_health)").fetchall()}
        values: dict[str, Any] = {
            "last_run_at": "CURRENT_TIMESTAMP",
            "jobs_found_last_run": raw_count,
            "average_response_ms": round(elapsed_ms, 2),
            "health_status": (
                "configuration_required"
                if configuration_required
                else "degraded"
                if success and bool(str(error_text or "").strip())
                else "healthy"
                if success
                else "failed"
            ),
            "last_error": error_text,
            "error_count_last_run": int(bool(str(error_text or "").strip())),
            "updated_at": "CURRENT_TIMESTAMP",
        }
        if success:
            values.update({"last_success_at": "CURRENT_TIMESTAMP", "consecutive_failures": 0, "last_http_status": 200})
        else:
            values.update({"last_failure_at": "CURRENT_TIMESTAMP", "last_http_status": None})
        assignments: list[str] = []
        parameters: list[Any] = []
        for key, value in values.items():
            if key not in columns:
                continue
            if value == "CURRENT_TIMESTAMP":
                assignments.append(f'"{key}"=CURRENT_TIMESTAMP')
            elif key == "consecutive_failures" and not success:
                assignments.append('"consecutive_failures"=COALESCE("consecutive_failures",0)+1')
            else:
                assignments.append(f'"{key}"=?')
                parameters.append(value)
        if not success and "consecutive_failures" in columns and not any("consecutive_failures" in item for item in assignments):
            assignments.append('"consecutive_failures"=COALESCE("consecutive_failures",0)+1')
        if assignments:
            parameters.append(source_name)
            connection.execute(f'UPDATE source_health SET {", ".join(assignments)} WHERE lower(source_name)=lower(?)', parameters)
        connection.execute(
            """
            INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
            VALUES(NULL,?,?,?,?)
            """,
            (
                "dashboard_adapter_source_run",
                "dashboard_adapter_v2_4",
                "completed" if success else "failed",
                json.dumps({"source": source_name, "raw_jobs_found": raw_count, "elapsed_ms": round(elapsed_ms,2), "error": error_text}, ensure_ascii=False),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _self_test(spec: SourceSpec) -> dict[str, Any]:
    filter_function, provider = canonical_dashboard_filter()
    samples = [
        make_job(spec, company="Self Test", title="Human Resources Intern", location="New York, NY", job_url="https://example.invalid/hr-intern", description="Recruiting onboarding HR operations"),
        make_job(spec, company="Self Test", title="Senior Software Engineer", location="Remote - United States", job_url="https://example.invalid/software", description="Engineering"),
    ]
    filtered = filter_function(samples)
    return {
        "success": True,
        "self_test": True,
        "marker": MARKER,
        "source": spec.display_name,
        "configuration_source": "SQLite dashboard",
        "dashboard_filter_provider": provider,
        "dashboard_filter_invoked": True,
        "filter_result_keys": sorted(filtered.keys()) if isinstance(filtered, dict) else [],
        "sample_jobs": len(samples),
        "network_request_made": False,
        "database_writes": 0,
        "telegram_messages": 0,
        "provider_calls": 0,
        "n8n_calls": 0,
        "personal_rules_hardcoded": False,
    }


def run_source(source_key: str, *, no_store: bool = False, force: bool = False, limit: int = 500) -> dict[str, Any]:
    _REQUEST_METRICS["attempts"] = 0
    spec = SOURCE_SPECS[source_key]
    source_state = _source_row(spec.display_name)
    enabled = bool(source_state and int(source_state.get("enabled") or 0) == 1)
    if not enabled and not (no_store and force):
        return {
            "success": True,
            "source": spec.display_name,
            "worker_action": "skipped",
            "reason": "disabled_in_dashboard",
            "configuration_source": "SQLite dashboard",
            "network_request_made": False,
            "jobs_inserted": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
        }

    fetcher = globals()[spec.fetcher]
    started = time.perf_counter()
    run_started_at = datetime.now(timezone.utc).isoformat()
    raw_jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    configuration_required = False
    discovery_active = False
    discovery_sync: dict[str, Any] = {
        "success": True,
        "inserted": 0,
        "updated": 0,
        "board_counts": {},
        "network_request_made": False,
    }
    board_sync: dict[str, Any] = {
        "success": True,
        "scanned": 0,
        "inserted": 0,
        "detected": {},
        "network_request_made": False,
    }
    try:
        if spec.display_name in BOARD_SOURCES and not no_store:
            board_sync = sync_public_boards_from_registry()
            from app.employer_board_discovery import (
                sync_validated_boards_to_runtime,
            )
            discovery_sync = sync_validated_boards_to_runtime(
                providers={spec.display_name.casefold()}
            )
        if (
            spec.display_name in BOARD_SOURCES
            and not load_enabled_boards(spec.display_name)
        ):
            discovery_active = True
        else:
            raw_jobs, errors = fetcher(spec, limit)
        fetch_duration_ms = (time.perf_counter() - started) * 1000

        for job in raw_jobs:
            job.setdefault("_query_name", "Configured provider boards")
        guarded_jobs, title_guard_excluded = apply_dashboard_title_guard(raw_jobs)
        filter_function, filter_provider = canonical_dashboard_filter()
        filtered = filter_function(guarded_jobs)
        eligible_jobs = list(filtered.get("eligible_jobs") or [])
        stored_results: list[dict[str, Any]] = []
        telegram_result: dict[str, Any] = {
            "telegram_enabled": False,
            "eligible_jobs": 0,
            "telegram_messages_sent": 0,
            "errors": [],
        }
        if not no_store:
            connection = get_connection()
            try:
                for job in eligible_jobs:
                    stored_results.append(save_job(connection, job, actor=f"{source_key}_worker"))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            telegram_result = dispatch_unsent_jobs(
                source_prefix=f"{spec.display_name}/",
                limit=telegram_batch_limit(),
            )
        inserted_job_ids = [
            int(item["job_id"])
            for item in stored_results
            if item.get("inserted") and item.get("job_id") is not None
        ]
        telegram_attribution = attribute_dispatch_to_current_jobs(
            telegram_result,
            inserted_job_ids,
        )
        filtered["telegram_messages"] = int(
            telegram_result.get("telegram_messages_sent") or 0
        )
        filtered["query_telegram_counts"] = {
            "Configured provider boards": telegram_attribution[
                "current_run_messages"
            ]
        }
        filtered["telegram_backlog_messages"] = telegram_attribution[
            "backlog_messages"
        ]

        elapsed_ms = (time.perf_counter() - started) * 1000
        inserted_count = sum(1 for item in stored_results if item.get("inserted"))
        duplicate_count = len(stored_results) - inserted_count
        success = not errors or bool(raw_jobs) or configuration_required
        from app.dashboard_targeting_gate import record_source_metrics
        rejected_count = (
            int(filtered.get("excluded_by_role") or 0)
            + int(filtered.get("excluded_by_location") or 0)
            + int(filtered.get("excluded_by_hard_reject") or 0)
            + int(filtered.get("excluded_by_company_blacklist") or 0)
            + int(filtered.get("excluded_by_other_targeting") or 0)
            + len(title_guard_excluded)
        )
        filtered["request_count"] = int(_REQUEST_METRICS["attempts"])
        filtered["run_started_at"] = run_started_at
        filtered["errors"] = list(errors)
        filtered["duration_ms"] = round(elapsed_ms, 2)
        filtered.setdefault("_stage_durations_ms", {})["FETCH"] = round(
            fetch_duration_ms, 2
        )
        filtered["query_requests"] = [
            {
                "query_name": "Configured provider boards",
                "role_family": "",
                "requests": int(_REQUEST_METRICS["attempts"]),
                "raw": len(raw_jobs),
                "errors": len(errors),
                "duration_ms": round(fetch_duration_ms, 2),
            }
        ]
        record_source_metrics(
            spec.display_name,
            raw_jobs=len(raw_jobs),
            eligible_jobs=len(eligible_jobs),
            inserted_jobs=inserted_count,
            duplicate_jobs=duplicate_count,
            rejected_jobs=rejected_count,
            provider_used=spec.key,
            filter_summary=filtered,
        )

        output = {
            "success": success,
            "partial_success": bool(errors and raw_jobs),
            "mode": "dashboard-controlled-adapter-v2.4",
            "source": spec.display_name,
            "worker_action": "no_store_probe" if no_store else "run",
            "configuration_source": "SQLite dashboard",
            "dashboard_filter_provider": filter_provider,
            "personal_rules_hardcoded": False,
            "configuration_required": configuration_required,
            "discovery_active": discovery_active,
            "board_sync": board_sync,
            "discovery_sync": discovery_sync,
            "network_request_made": bool(_REQUEST_METRICS["attempts"]) and not configuration_required,
            "raw_jobs_found": len(raw_jobs),
            "excluded_by_adapter_title_guard": len(title_guard_excluded),
            "excluded_by_role": int(filtered.get("excluded_by_role") or 0),
            "excluded_by_location": int(filtered.get("excluded_by_location") or 0),
            "excluded_by_hard_reject": int(filtered.get("excluded_by_hard_reject") or 0),
            "excluded_by_company_blacklist": int(filtered.get("excluded_by_company_blacklist") or 0),
            "targeting_rules_hash": filtered.get("targeting_rules_hash"),
            "duplicates_within_run": int(filtered.get("duplicates_within_run") or 0),
            "unique_jobs_ready": len(eligible_jobs),
            "jobs_inserted": inserted_count,
            "inserted_job_ids": [item.get("job_id") for item in stored_results if item.get("inserted")],
            "database_duplicates": duplicate_count,
            "auto_telegram_enabled": bool(telegram_result.get("telegram_enabled")),
            "telegram_pending_before": int(telegram_result.get("eligible_jobs") or 0),
            "telegram_messages": int(telegram_result.get("telegram_messages_sent") or 0),
            "telegram_current_run_messages": telegram_attribution[
                "current_run_messages"
            ],
            "telegram_backlog_messages": telegram_attribution[
                "backlog_messages"
            ],
            "telegram_dispatch_errors": list(telegram_result.get("errors") or []),
            "elapsed_ms": round(elapsed_ms, 2),
            "no_store": no_store,
            "provider_calls": int(_REQUEST_METRICS["attempts"]),
            "paid_api_calls": 0,
            "n8n_calls": 0,
            "errors": errors,
        }
        if not no_store:
            _update_source_health(
                spec.display_name,
                success=success,
                raw_count=len(raw_jobs),
                elapsed_ms=elapsed_ms,
                error_text=(
                    "; ".join(errors)[:2000]
                    if errors
                    else (
                        "Automatic public employer-board discovery is active; "
                        "no live-validated public board is available yet."
                        if discovery_active
                        else (
                            "Source enabled; add at least one public board "
                            "in the Adapter Expansion dashboard."
                            if configuration_required
                            else None
                        )
                    )
                ),
                configuration_required=configuration_required,
            )
            if discovery_active:
                from app.employer_board_discovery import (
                    mark_adapter_discovery_active,
                )
                mark_adapter_discovery_active(
                    spec.display_name,
                    discovery_sync,
                )
        return output
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        error = f"{type(exc).__name__}: {exc}"
        if not no_store:
            _update_source_health(spec.display_name, success=False, raw_count=len(raw_jobs), elapsed_ms=elapsed_ms, error_text=error)
        return {
            "success": False,
            "source": spec.display_name,
            "worker_action": "no_store_probe" if no_store else "run",
            "configuration_source": "SQLite dashboard",
            "network_request_made": bool(raw_jobs),
            "raw_jobs_found": len(raw_jobs),
            "jobs_inserted": 0,
            "telegram_messages": 0,
            "provider_calls": 0,
            "paid_api_calls": 0,
            "n8n_calls": 0,
            "errors": [error],
        }


def cli_main(source_key: str) -> None:
    parser = argparse.ArgumentParser(description=f"Dashboard-controlled {SOURCE_SPECS[source_key].display_name} worker")
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--quiet-start", action="store_true")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-store", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args, _unknown = parser.parse_known_args()
    spec = SOURCE_SPECS[source_key]
    if args.limit is None:
        try:
            job_limit = int(_runtime_policy()["default_source_run_job_limit"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError(
                "Canonical provider_runtime.default_source_run_job_limit is missing or invalid."
            ) from None
    else:
        job_limit = int(args.limit)
    result = _self_test(spec) if args.self_test else run_source(
        source_key,
        no_store=args.no_store,
        force=args.force,
        limit=max(1, job_limit),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result.get("success"):
        raise SystemExit(1)

# AADIL_EMPLOYER_BOARD_AUTO_DISCOVERY_V2_7
