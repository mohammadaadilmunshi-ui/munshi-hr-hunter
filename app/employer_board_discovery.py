from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

import requests

from app.database import get_connection


MARKER = "AADIL_EMPLOYER_BOARD_AUTO_DISCOVERY_V2_7"
USER_AGENT = (
    "Aadil-HR-Hunter/2.7 public-employer-board-discovery "
    "(low-frequency; public endpoints only)"
)
DEFAULT_TIMEOUT = 30
MAX_BODY_BYTES = 5_000_000
DISCOVERY_SOURCE_NAME = "Employer Board Discovery"

DIRECT_ADAPTER_PROVIDERS = {
    "personio": "Personio",
    "pinpoint": "Pinpoint",
    "comeet": "Comeet",
}
MARKET_ADAPTER_PROVIDERS = {
    "recruitee": "Recruitee",
}

COMMONCRAWL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("personio", "*.jobs.personio.de/*"),
    ("personio", "*.jobs.personio.com/*"),
    ("personio", "*.jobs.personio.eu/*"),
    ("pinpoint", "*.pinpointhq.com/postings.json"),
    ("pinpoint", "*.pinpointhq.com/jobs.rss"),
    ("recruitee", "*.recruitee.com/api/offers/*"),
    ("recruitee", "*.recruitee.com/*"),
    ("comeet", "www.comeet.com/jobs/*"),
    ("comeet", "www.comeet.co/jobs/*"),
)

PROVIDER_HOST_RULES = {
    "personio": lambda host: ".jobs.personio." in host,
    "pinpoint": lambda host: host.endswith(".pinpointhq.com"),
    "recruitee": lambda host: host.endswith(".recruitee.com"),
    "comeet": lambda host: host in {
        "www.comeet.co",
        "www.comeet.com",
        "comeet.co",
        "comeet.com",
    },
}


@dataclass
class Candidate:
    provider: str
    company_name: str
    board_locator: str | None
    board_url: str
    raw_url: str
    discovery_source: str
    company_uid: str | None = None
    public_token: str | None = None
    source_kind: str = "api"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _slug_to_company(value: str) -> str:
    value = unquote(str(value or "")).strip().strip("/")
    value = re.sub(r"[-_]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.title() if value else "Unknown Employer"


def _redact_secrets(value: Any) -> str:
    text = str(value or "")
    text = re.sub(
        r"(?i)([?&](?:token|api_key|apikey|key|secret)=)[^&\s]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)((?:token|api_key|apikey|secret)\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    return text


def _redact_url(value: str) -> str:
    parsed = urlparse(str(value or ""))
    if not parsed.scheme or not parsed.netloc:
        return str(value or "")
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key in list(query):
        if key.lower() in {"token", "api_key", "apikey", "key", "secret"}:
            query[key] = ["[REDACTED]"]
    query_text = "&".join(
        f"{key}={item}"
        for key, values in query.items()
        for item in values
    )
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            query_text,
            parsed.fragment,
        )
    )


def _safe_provider_url(provider: str, url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if not host or host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return False
    except ValueError:
        pass
    rule = PROVIDER_HOST_RULES.get(provider)
    return bool(rule and rule(host))


def _extract_token(value: str) -> str | None:
    text = html.unescape(unquote(str(value or "")))
    parsed = urlparse(text)
    token = parse_qs(parsed.query).get("token", [None])[0]
    if token:
        return str(token).strip()
    for pattern in (
        r'(?i)["\'](?:company_)?token["\']\s*[:=]\s*["\']([A-Za-z0-9._-]{8,})',
        r'(?i)(?:token=|token%3D)([A-Za-z0-9._-]{8,})',
        r'(?i)token\\u003d([A-Za-z0-9._-]{8,})',
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _extract_comeet_uid(value: str) -> str | None:
    text = html.unescape(unquote(str(value or ""))).replace("\\/", "/")
    for pattern in (
        r"(?i)/careers-api/2\.0/company/([^/?#]+)/positions",
        r"(?i)/(?:jobs)/[^/]+/([^/?#]+)(?:/|$)",
        r'(?i)["\']company_uid["\']\s*[:=]\s*["\']([^"\']+)',
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None


def detect_candidate(
    url: str,
    *,
    company_hint: str = "",
    discovery_source: str = "unknown",
) -> Candidate | None:
    raw_url = str(url or "").strip()
    parsed = urlparse(raw_url)
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if parsed.scheme not in {"http", "https"} or not host:
        return None
    company = str(company_hint or "").strip()

    if ".jobs.personio." in host:
        locator = host.split(".", 1)[0]
        return Candidate(
            provider="personio",
            company_name=company or _slug_to_company(locator),
            board_locator=locator,
            board_url=f"https://{host}/xml?language=en",
            raw_url=raw_url,
            discovery_source=discovery_source,
        )

    if host.endswith(".pinpointhq.com"):
        locator = host.split(".", 1)[0]
        return Candidate(
            provider="pinpoint",
            company_name=company or _slug_to_company(locator),
            board_locator=locator,
            board_url=f"https://{host}/postings.json",
            raw_url=raw_url,
            discovery_source=discovery_source,
        )

    if host.endswith(".recruitee.com"):
        locator = host.split(".", 1)[0]
        return Candidate(
            provider="recruitee",
            company_name=company or _slug_to_company(locator),
            board_locator=locator,
            board_url=f"https://{host}/api/offers/",
            raw_url=raw_url,
            discovery_source=discovery_source,
        )

    if host in {"www.comeet.co", "www.comeet.com", "comeet.co", "comeet.com"}:
        company_uid = _extract_comeet_uid(raw_url)
        if not company_uid:
            return None
        slug_match = re.search(r"(?i)/jobs/([^/]+)/", parsed.path)
        slug = slug_match.group(1) if slug_match else company_uid
        return Candidate(
            provider="comeet",
            company_name=company or _slug_to_company(slug),
            board_locator=company_uid,
            board_url=(
                "https://www.comeet.co/careers-api/2.0/company/"
                f"{company_uid}/positions"
            ),
            raw_url=raw_url,
            discovery_source=discovery_source,
            company_uid=company_uid,
            public_token=_extract_token(raw_url),
        )

    return None


def initialize_tables(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS employer_board_discovery_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            company_name TEXT NOT NULL,
            board_locator TEXT,
            board_url TEXT NOT NULL,
            raw_url TEXT,
            discovery_source TEXT NOT NULL,
            source_kind TEXT NOT NULL DEFAULT 'api',
            company_uid TEXT,
            public_token TEXT,
            validation_status TEXT NOT NULL DEFAULT 'pending',
            validation_error TEXT,
            last_http_status INTEGER,
            visible_jobs INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_validated_at TEXT,
            UNIQUE(provider, board_url)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_employer_board_discovery_candidates_status
        ON employer_board_discovery_candidates(
            provider,validation_status,enabled
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS employer_board_discovery_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS employer_board_discovery_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            success INTEGER NOT NULL DEFAULT 0,
            commoncrawl_index TEXT,
            local_urls_scanned INTEGER NOT NULL DEFAULT 0,
            commoncrawl_urls_seen INTEGER NOT NULL DEFAULT 0,
            candidates_inserted INTEGER NOT NULL DEFAULT 0,
            candidates_updated INTEGER NOT NULL DEFAULT 0,
            candidates_validated INTEGER NOT NULL DEFAULT 0,
            candidates_valid INTEGER NOT NULL DEFAULT 0,
            runtime_boards_inserted INTEGER NOT NULL DEFAULT 0,
            runtime_boards_updated INTEGER NOT NULL DEFAULT 0,
            error_text TEXT
        )
        """
    )


def _table_columns(connection, table: str) -> list[str]:
    return [
        str(row[1])
        for row in connection.execute(
            f'PRAGMA table_info("{table}")'
        ).fetchall()
    ]


def _state_get(connection, key: str, default: str = "") -> str:
    row = connection.execute(
        """
        SELECT state_value
        FROM employer_board_discovery_state
        WHERE state_key=?
        """,
        (key,),
    ).fetchone()
    return str(row[0]) if row else default


def _state_set(connection, key: str, value: Any) -> None:
    connection.execute(
        """
        INSERT INTO employer_board_discovery_state(
            state_key,state_value,updated_at
        ) VALUES(?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(state_key) DO UPDATE SET
            state_value=excluded.state_value,
            updated_at=CURRENT_TIMESTAMP
        """,
        (key, str(value)),
    )


def _upsert_candidate(connection, candidate: Candidate) -> tuple[bool, bool]:
    existing = connection.execute(
        """
        SELECT id
        FROM employer_board_discovery_candidates
        WHERE lower(provider)=lower(?)
          AND lower(board_url)=lower(?)
        """,
        (candidate.provider, candidate.board_url),
    ).fetchone()
    if existing:
        connection.execute(
            """
            UPDATE employer_board_discovery_candidates
            SET company_name=CASE
                    WHEN company_name='Unknown Employer'
                    THEN ? ELSE company_name
                END,
                board_locator=COALESCE(?,board_locator),
                raw_url=COALESCE(?,raw_url),
                discovery_source=?,
                company_uid=COALESCE(?,company_uid),
                public_token=COALESCE(?,public_token),
                enabled=1,last_seen_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                candidate.company_name,
                candidate.board_locator,
                candidate.raw_url,
                candidate.discovery_source,
                candidate.company_uid,
                candidate.public_token,
                existing["id"],
            ),
        )
        return False, True
    connection.execute(
        """
        INSERT INTO employer_board_discovery_candidates(
            provider,company_name,board_locator,board_url,raw_url,
            discovery_source,source_kind,company_uid,public_token,
            validation_status,enabled
        ) VALUES(?,?,?,?,?,?,?,?,?,'pending',1)
        """,
        (
            candidate.provider,
            candidate.company_name,
            candidate.board_locator,
            candidate.board_url,
            candidate.raw_url,
            candidate.discovery_source,
            candidate.source_kind,
            candidate.company_uid,
            candidate.public_token,
        ),
    )
    return True, False


def _iter_table_urls(connection, table: str) -> Iterable[tuple[str, str]]:
    columns = set(_table_columns(connection, table))
    company_column = next(
        (
            name
            for name in ("company_name", "company", "name", "employer_name")
            if name in columns
        ),
        None,
    )
    url_columns = [
        name
        for name in columns
        if any(token in name.casefold() for token in ("url", "link", "uri"))
    ]
    if not url_columns:
        return []
    selected = [
        (
            f'"{company_column}" AS company_name'
            if company_column
            else "NULL AS company_name"
        )
    ] + [f'"{name}" AS "{name}"' for name in url_columns]
    rows = connection.execute(
        f"SELECT {', '.join(selected)} FROM {table}"
    ).fetchall()
    output: list[tuple[str, str]] = []
    for row in rows:
        company = str(row["company_name"] or "").strip()
        for column in url_columns:
            value = str(row[column] or "").strip()
            if value.startswith(("http://", "https://")):
                output.append((company, value))
    return output


def discover_local_candidates(connection) -> dict[str, Any]:
    initialize_tables(connection)
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    scanned = inserted = updated = 0
    detected: dict[str, int] = {}
    for table in (
        "ats_company_registry",
        "market_public_boards",
        "public_adapter_boards",
        "jobs",
    ):
        if table not in tables:
            continue
        for company, url in _iter_table_urls(connection, table):
            scanned += 1
            candidate = detect_candidate(
                url,
                company_hint=company,
                discovery_source=f"local:{table}",
            )
            if not candidate:
                continue
            was_inserted, was_updated = _upsert_candidate(connection, candidate)
            inserted += int(was_inserted)
            updated += int(was_updated)
            detected[candidate.provider] = detected.get(candidate.provider, 0) + 1
    return {
        "scanned": scanned,
        "inserted": inserted,
        "updated": updated,
        "detected": detected,
    }


def _request_limited(
    session: requests.Session,
    url: str,
    *,
    params: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
    accept: str = "*/*",
) -> tuple[requests.Response, bytes]:
    response = session.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
        timeout=timeout,
        stream=True,
        allow_redirects=True,
    )
    response.raise_for_status()
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise RuntimeError(
                f"Response exceeded {MAX_BODY_BYTES} bytes"
            )
        chunks.append(chunk)
    return response, b"".join(chunks)


def _latest_commoncrawl_api(
    session: requests.Session,
) -> tuple[str, str]:
    _response, body = _request_limited(
        session,
        "https://index.commoncrawl.org/collinfo.json",
        accept="application/json",
    )
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("Common Crawl returned no index list")
    first = payload[0]
    api = str(first.get("cdx-api") or "").strip()
    index_id = str(first.get("id") or "").strip()
    if not api:
        raise RuntimeError("Latest Common Crawl index has no cdx-api")
    return index_id, api


def _parse_cdx_lines(body: bytes) -> list[dict[str, Any]]:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]
    except json.JSONDecodeError:
        pass
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def query_commoncrawl_pattern(
    session: requests.Session,
    *,
    api_url: str,
    pattern: str,
    page: int,
    page_size: int,
) -> list[dict[str, Any]]:
    params: list[tuple[str, Any]] = [
        ("url", pattern),
        ("output", "json"),
        ("filter", "status:200"),
        ("collapse", "urlkey"),
        ("fl", "url,status,mime,timestamp"),
        ("page", max(0, page)),
        ("pageSize", max(10, min(page_size, 1000))),
    ]
    try:
        _response, body = _request_limited(
            session,
            api_url,
            params=params,
            timeout=90,
            accept="application/json,text/plain",
        )
        return _parse_cdx_lines(body)
    except Exception:
        fallback = [
            ("url", pattern),
            ("output", "json"),
            ("filter", "status:200"),
            ("collapse", "urlkey"),
            ("fl", "url,status,mime,timestamp"),
            ("limit", max(10, min(page_size, 1000))),
        ]
        _response, body = _request_limited(
            session,
            api_url,
            params=fallback,
            timeout=90,
            accept="application/json,text/plain",
        )
        return _parse_cdx_lines(body)


def discover_commoncrawl_candidates(
    connection,
    session: requests.Session,
    *,
    max_records_per_pattern: int = 300,
    request_delay_seconds: float = 1.0,
    providers: set[str] | None = None,
) -> dict[str, Any]:
    index_id, api_url = _latest_commoncrawl_api(session)
    seen = inserted = updated = 0
    errors: list[str] = []
    pattern_results: dict[str, int] = {}
    selected_patterns = [
        (provider, pattern)
        for provider, pattern in COMMONCRAWL_PATTERNS
        if not providers or provider in providers
    ]
    for pattern_index, (provider, pattern) in enumerate(selected_patterns):
        state_key = (
            "commoncrawl_page:"
            + hashlib.sha256(pattern.encode()).hexdigest()[:16]
        )
        try:
            page = int(_state_get(connection, state_key, "0") or 0)
        except ValueError:
            page = 0
        try:
            rows = query_commoncrawl_pattern(
                session,
                api_url=api_url,
                pattern=pattern,
                page=page,
                page_size=max_records_per_pattern,
            )
            if not rows and page > 0:
                page = 0
                rows = query_commoncrawl_pattern(
                    session,
                    api_url=api_url,
                    pattern=pattern,
                    page=0,
                    page_size=max_records_per_pattern,
                )
            pattern_results[pattern] = len(rows)
            for row in rows:
                url = str(row.get("url") or "").strip()
                if not url:
                    continue
                seen += 1
                candidate = detect_candidate(
                    url,
                    discovery_source=f"commoncrawl:{index_id}:{pattern}",
                )
                if not candidate:
                    continue
                was_inserted, was_updated = _upsert_candidate(
                    connection,
                    candidate,
                )
                inserted += int(was_inserted)
                updated += int(was_updated)
            _state_set(connection, state_key, page + 1 if rows else 0)
        except Exception as exc:
            errors.append(
                f"{provider}:{pattern}: {type(exc).__name__}: {exc}"
            )
        if pattern_index < len(selected_patterns) - 1:
            time.sleep(max(0.5, request_delay_seconds))
    _state_set(connection, "commoncrawl_latest_index", index_id)
    return {
        "index_id": index_id,
        "api_url": api_url,
        "urls_seen": seen,
        "inserted": inserted,
        "updated": updated,
        "patterns": pattern_results,
        "errors": errors,
    }


def _json_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("positions", "postings", "offers", "jobs", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            for nested_key in ("offers", "jobs", "data"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return [
                        item for item in nested
                        if isinstance(item, dict)
                    ]
    return []


def _company_from_rows(
    rows: list[dict[str, Any]],
    fallback: str,
) -> str:
    for item in rows:
        for key in ("company_name", "company", "organization"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return _clean_text(value)
            if isinstance(value, dict) and value.get("name"):
                return _clean_text(value["name"])
    return fallback


def _extract_comeet_credentials_from_page(
    body: bytes,
    raw_url: str,
) -> tuple[str | None, str | None]:
    text = body.decode("utf-8", errors="replace")
    normalized = (
        html.unescape(text)
        .replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("\\u003d", "=")
    )
    return (
        _extract_comeet_uid(raw_url) or _extract_comeet_uid(normalized),
        _extract_token(raw_url) or _extract_token(normalized),
    )


def validate_candidate(
    session: requests.Session,
    row: dict[str, Any],
) -> dict[str, Any]:
    provider = str(row.get("provider") or "").casefold()
    board_url = str(row.get("board_url") or "").strip()
    raw_url = str(row.get("raw_url") or board_url).strip()
    company = str(row.get("company_name") or "Unknown Employer").strip()
    token = str(row.get("public_token") or "").strip() or None
    company_uid = str(row.get("company_uid") or "").strip() or None

    if not _safe_provider_url(provider, board_url):
        raise RuntimeError(
            f"URL failed provider allowlist: {_redact_url(board_url)}"
        )

    if provider == "personio":
        response, body = _request_limited(
            session,
            board_url,
            accept="application/xml,text/xml",
        )
        root = ET.fromstring(body)
        nodes = [
            node
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1].casefold()
            in {"position", "job"}
        ]
        return {
            "valid": True,
            "http_status": response.status_code,
            "visible_jobs": len(nodes),
            "company_name": company,
        }

    if provider in {"pinpoint", "recruitee"}:
        response, body = _request_limited(
            session,
            board_url,
            accept="application/json",
        )
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, (list, dict)):
            raise RuntimeError("Public endpoint did not return JSON")
        rows = _json_rows(payload)
        return {
            "valid": True,
            "http_status": response.status_code,
            "visible_jobs": len(rows),
            "company_name": _company_from_rows(rows, company),
        }

    if provider == "comeet":
        if not company_uid or not token:
            if not _safe_provider_url("comeet", raw_url):
                raise RuntimeError(
                    "Comeet candidate has no public token and no "
                    "allowlisted hosted page"
                )
            _response, page_body = _request_limited(
                session,
                raw_url,
                accept="text/html,application/xhtml+xml",
            )
            extracted_uid, extracted_token = (
                _extract_comeet_credentials_from_page(
                    page_body,
                    raw_url,
                )
            )
            company_uid = company_uid or extracted_uid
            token = token or extracted_token
        if not company_uid or not token:
            return {
                "valid": False,
                "unresolved": True,
                "error": (
                    "Public Comeet page did not expose both "
                    "company UID and public company token."
                ),
                "visible_jobs": 0,
                "company_name": company,
                "company_uid": company_uid,
                "public_token": token,
            }
        api_url = (
            "https://www.comeet.co/careers-api/2.0/company/"
            f"{company_uid}/positions"
        )
        response, body = _request_limited(
            session,
            api_url,
            params={"token": token, "details": "true"},
            timeout=60,
            accept="application/json",
        )
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, (list, dict)):
            raise RuntimeError("Comeet endpoint did not return JSON")
        rows = _json_rows(payload)
        return {
            "valid": True,
            "http_status": response.status_code,
            "visible_jobs": len(rows),
            "company_name": _company_from_rows(rows, company),
            "company_uid": company_uid,
            "public_token": token,
            "board_url": api_url,
        }

    raise RuntimeError(f"Unsupported provider: {provider}")


def validate_pending_candidates(
    connection,
    session: requests.Session,
    *,
    max_candidates: int = 60,
    request_delay_seconds: float = 0.8,
    providers: set[str] | None = None,
) -> dict[str, Any]:
    provider_filter = ""
    params: list[Any] = []
    if providers:
        provider_filter = (
            " AND provider IN ("
            + ",".join("?" for _ in providers)
            + ")"
        )
        params.extend(sorted(providers))
    params.append(max(1, min(max_candidates, 500)))
    rows = connection.execute(
        f"""
        SELECT *
        FROM employer_board_discovery_candidates
        WHERE enabled=1
          AND validation_status IN ('pending','retry','unresolved')
          {provider_filter}
        ORDER BY
            CASE discovery_source
                WHEN 'local:public_adapter_boards' THEN 0
                WHEN 'local:ats_company_registry' THEN 1
                WHEN 'local:market_public_boards' THEN 2
                WHEN 'local:jobs' THEN 3
                ELSE 4
            END,
            last_validated_at IS NOT NULL,
            last_seen_at DESC,id
        LIMIT ?
        """,
        params,
    ).fetchall()

    validated = valid = unresolved = invalid = 0
    errors: list[str] = []
    for index, raw_row in enumerate(rows):
        row = dict(raw_row)
        validated += 1
        try:
            result = validate_candidate(session, row)
            status = (
                "valid"
                if result.get("valid")
                else "unresolved"
                if result.get("unresolved")
                else "invalid"
            )
            valid += int(status == "valid")
            unresolved += int(status == "unresolved")
            invalid += int(status == "invalid")
            connection.execute(
                """
                UPDATE employer_board_discovery_candidates
                SET company_name=?,
                    board_url=COALESCE(?,board_url),
                    company_uid=COALESCE(?,company_uid),
                    public_token=COALESCE(?,public_token),
                    validation_status=?,
                    validation_error=?,
                    last_http_status=?,
                    visible_jobs=?,
                    last_validated_at=CURRENT_TIMESTAMP,
                    last_seen_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    str(
                        result.get("company_name")
                        or row.get("company_name")
                        or "Unknown Employer"
                    ),
                    result.get("board_url"),
                    result.get("company_uid"),
                    result.get("public_token"),
                    status,
                    result.get("error"),
                    result.get("http_status"),
                    int(result.get("visible_jobs") or 0),
                    row["id"],
                ),
            )
        except Exception as exc:
            transient = isinstance(
                exc,
                (requests.Timeout, requests.ConnectionError),
            ) or any(
                token in str(exc).casefold()
                for token in (
                    "429",
                    "500",
                    "502",
                    "503",
                    "504",
                    "timed out",
                    "temporary failure",
                )
            )
            status = "retry" if transient else "invalid"
            invalid += int(not transient)
            error = _redact_secrets(
                f"{row.get('provider')}:{row.get('company_name')}: "
                f"{type(exc).__name__}: {exc}"
            )
            errors.append(error)
            connection.execute(
                """
                UPDATE employer_board_discovery_candidates
                SET validation_status=?,
                    validation_error=?,
                    last_validated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, error[:2000], row["id"]),
            )
        if index < len(rows) - 1:
            time.sleep(max(0.25, request_delay_seconds))
    return {
        "validated": validated,
        "valid": valid,
        "unresolved": unresolved,
        "invalid": invalid,
        "errors": errors,
    }


def _upsert_direct_board(
    connection,
    row: dict[str, Any],
) -> tuple[bool, bool]:
    source_name = DIRECT_ADAPTER_PROVIDERS[
        str(row["provider"]).casefold()
    ]
    existing = connection.execute(
        """
        SELECT id
        FROM public_adapter_boards
        WHERE lower(source_name)=lower(?)
          AND lower(board_url)=lower(?)
        """,
        (source_name, row["board_url"]),
    ).fetchone()
    payload = (
        str(row["company_name"]),
        row["board_locator"],
        row["board_url"],
        row["public_token"],
        row["company_uid"],
        (
            "Auto-discovered and live-validated by V2.7 "
            f"from {row['discovery_source']}"
        ),
    )
    if existing:
        connection.execute(
            """
            UPDATE public_adapter_boards
            SET company_name=?,board_locator=?,board_url=?,
                public_token=COALESCE(?,public_token),
                company_uid=COALESCE(?,company_uid),
                enabled=1,notes=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            payload + (existing["id"],),
        )
        return False, True
    connection.execute(
        """
        INSERT INTO public_adapter_boards(
            source_name,company_name,board_locator,board_url,
            public_token,company_uid,enabled,notes
        ) VALUES(?,?,?,?,?,?,1,?)
        """,
        (source_name,) + payload,
    )
    return True, False


def _upsert_market_board(
    connection,
    row: dict[str, Any],
) -> tuple[bool, bool]:
    existing = connection.execute(
        """
        SELECT id
        FROM market_public_boards
        WHERE lower(company_name)=lower(?)
          AND lower(board_url)=lower(?)
        """,
        (row["company_name"], row["board_url"]),
    ).fetchone()
    if existing:
        connection.execute(
            """
            UPDATE market_public_boards
            SET provider='recruitee',source_kind='api',
                board_locator=?,enabled=1,notes=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                row["board_locator"],
                (
                    "Auto-discovered and live-validated by V2.7 "
                    f"from {row['discovery_source']}"
                ),
                existing["id"],
            ),
        )
        return False, True
    connection.execute(
        """
        INSERT INTO market_public_boards(
            company_name,provider,source_kind,board_locator,
            board_url,enabled,priority_weight,notes
        ) VALUES(?,'recruitee','api',?,?,1,10,?)
        """,
        (
            row["company_name"],
            row["board_locator"],
            row["board_url"],
            (
                "Auto-discovered and live-validated by V2.7 "
                f"from {row['discovery_source']}"
            ),
        ),
    )
    return True, False


def _source_board_counts(connection) -> dict[str, int]:
    counts = {
        "Personio": 0,
        "Pinpoint": 0,
        "Comeet": 0,
        "Recruitee": 0,
    }
    for row in connection.execute(
        """
        SELECT source_name,COUNT(*) AS count_value
        FROM public_adapter_boards
        WHERE enabled=1
        GROUP BY source_name
        """
    ).fetchall():
        name = str(row["source_name"])
        if name in counts:
            counts[name] = int(row["count_value"])
    counts["Recruitee"] = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM market_public_boards
            WHERE enabled=1 AND lower(provider)='recruitee'
            """
        ).fetchone()[0]
    )
    return counts


def _update_runtime_source_states(
    connection,
    counts: dict[str, int],
) -> None:
    for source_name, count in counts.items():
        source = connection.execute(
            """
            SELECT enabled,last_run_at,health_status
            FROM source_health
            WHERE lower(source_name)=lower(?)
            """,
            (source_name,),
        ).fetchone()
        if not source:
            continue
        # Discovery owns board availability, not Aadil's canonical source
        # policy or a worker's health/backoff. Only annotate an adapter that
        # has never run; later syncs must not silently re-enable a source,
        # erase a failure, or pull a source out of scheduler backoff.
        if source["last_run_at"] is not None:
            continue
        enabled = bool(source["enabled"])
        status = (
            "enabled_pending_first_run" if enabled and count > 0
            else "configured_disabled" if not enabled and count > 0
            else "discovery_active_no_validated_boards"
            if source_name in {"Personio", "Pinpoint", "Comeet"}
            else "installed_disabled"
        )
        message = (
            None
            if count > 0
            else (
                "Automatic public employer-board discovery is active. "
                "No live-validated public board has been found yet."
            )
            if source_name in {"Personio", "Pinpoint", "Comeet"}
            else "No live-validated public boards discovered yet."
        )
        connection.execute(
            """
            UPDATE source_health
            SET health_status=?,last_error=?,updated_at=CURRENT_TIMESTAMP
            WHERE lower(source_name)=lower(?)
            """,
            (status, message, source_name),
        )


def sync_validated_boards_to_runtime(
    providers: set[str] | None = None,
) -> dict[str, Any]:
    connection = get_connection()
    inserted = updated = 0
    try:
        initialize_tables(connection)
        provider_filter = ""
        params: list[Any] = []
        if providers:
            provider_filter = (
                " AND provider IN ("
                + ",".join("?" for _ in providers)
                + ")"
            )
            params.extend(sorted(providers))
        rows = connection.execute(
            f"""
            SELECT *
            FROM employer_board_discovery_candidates
            WHERE enabled=1
              AND validation_status='valid'
              {provider_filter}
            ORDER BY provider,company_name,id
            """,
            params,
        ).fetchall()
        for raw_row in rows:
            row = dict(raw_row)
            provider = str(row["provider"]).casefold()
            if provider in DIRECT_ADAPTER_PROVIDERS:
                was_inserted, was_updated = _upsert_direct_board(
                    connection,
                    row,
                )
            elif provider in MARKET_ADAPTER_PROVIDERS:
                was_inserted, was_updated = _upsert_market_board(
                    connection,
                    row,
                )
            else:
                continue
            inserted += int(was_inserted)
            updated += int(was_updated)
        counts = _source_board_counts(connection)
        _update_runtime_source_states(connection, counts)
        connection.commit()
        return {
            "success": True,
            "inserted": inserted,
            "updated": updated,
            "board_counts": counts,
            "network_request_made": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def mark_adapter_discovery_active(
    source_name: str,
    discovery_sync: dict[str, Any] | None = None,
) -> None:
    if source_name not in {"Personio", "Pinpoint", "Comeet"}:
        return
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE source_health
            SET health_status='discovery_active_no_validated_boards',
                last_error=?,
                consecutive_failures=0,
                updated_at=CURRENT_TIMESTAMP
            WHERE lower(source_name)=lower(?)
            """,
            (
                (
                    "Automatic public employer-board discovery is active. "
                    "No live-validated board is available yet."
                ),
                source_name,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _record_discovery_health(
    connection,
    *,
    success: bool,
    candidates_seen: int,
    valid_count: int,
    error_text: str | None,
) -> None:
    columns = set(_table_columns(connection, "source_health"))
    values: dict[str, Any] = {
        "health_status": (
            "degraded"
            if success and bool(str(error_text or "").strip())
            else "healthy"
            if success
            else "failed"
        ),
        "last_error": error_text,
        "error_count_last_run": int(bool(str(error_text or "").strip())),
        "jobs_found_last_run": candidates_seen,
        "raw_jobs_last_run": candidates_seen,
        "eligible_jobs_last_run": valid_count,
        "inserted_jobs_last_run": valid_count,
        "provider_used_last_run": "commoncrawl+public_endpoint_validation",
        "last_run_at": "CURRENT_TIMESTAMP",
        "updated_at": "CURRENT_TIMESTAMP",
    }
    if success:
        values["last_success_at"] = "CURRENT_TIMESTAMP"
        values["consecutive_failures"] = 0
    else:
        values["last_failure_at"] = "CURRENT_TIMESTAMP"
    assignments: list[str] = []
    parameters: list[Any] = []
    for key, value in values.items():
        if key not in columns:
            continue
        if value == "CURRENT_TIMESTAMP":
            assignments.append(f'"{key}"=CURRENT_TIMESTAMP')
        elif key == "consecutive_failures" and not success:
            assignments.append(
                '"consecutive_failures"='
                'COALESCE("consecutive_failures",0)+1'
            )
        else:
            assignments.append(f'"{key}"=?')
            parameters.append(value)
    if (
        not success
        and "consecutive_failures" in columns
        and not any("consecutive_failures" in item for item in assignments)
    ):
        assignments.append(
            '"consecutive_failures"='
            'COALESCE("consecutive_failures",0)+1'
        )
    parameters.append(DISCOVERY_SOURCE_NAME)
    connection.execute(
        f"""
        UPDATE source_health
        SET {", ".join(assignments)}
        WHERE lower(source_name)=lower(?)
        """,
        parameters,
    )


def run_discovery(
    *,
    network: bool = True,
    max_records_per_pattern: int = 300,
    max_validate: int = 60,
    request_delay_seconds: float = 0.8,
    providers: set[str] | None = None,
) -> dict[str, Any]:
    connection = get_connection()
    session = requests.Session()
    run_id = None
    try:
        initialize_tables(connection)
        cursor = connection.execute(
            """
            INSERT INTO employer_board_discovery_runs(
                started_at,success
            ) VALUES(?,0)
            """,
            (_utc_now(),),
        )
        run_id = int(cursor.lastrowid)
        local_result = discover_local_candidates(connection)
        connection.commit()

        commoncrawl_result: dict[str, Any] = {
            "index_id": None,
            "urls_seen": 0,
            "inserted": 0,
            "updated": 0,
            "patterns": {},
            "errors": [],
        }
        validation_result: dict[str, Any] = {
            "validated": 0,
            "valid": 0,
            "unresolved": 0,
            "invalid": 0,
            "errors": [],
        }
        if network:
            commoncrawl_result = discover_commoncrawl_candidates(
                connection,
                session,
                max_records_per_pattern=max_records_per_pattern,
                request_delay_seconds=request_delay_seconds,
                providers=providers,
            )
            connection.commit()
            validation_result = validate_pending_candidates(
                connection,
                session,
                max_candidates=max_validate,
                request_delay_seconds=request_delay_seconds,
                providers=providers,
            )
            connection.commit()

        connection.close()
        connection = None
        sync_result = sync_validated_boards_to_runtime(providers=providers)
        connection = get_connection()
        initialize_tables(connection)

        all_errors = list(commoncrawl_result.get("errors") or []) + list(
            validation_result.get("errors") or []
        )
        success = (
            not all_errors
            or int(validation_result.get("valid") or 0) > 0
            or int(commoncrawl_result.get("urls_seen") or 0) > 0
            or not network
        )
        total_seen = int(local_result.get("scanned") or 0) + int(
            commoncrawl_result.get("urls_seen") or 0
        )
        _record_discovery_health(
            connection,
            success=success,
            candidates_seen=total_seen,
            valid_count=int(validation_result.get("valid") or 0),
            error_text=(
                "; ".join(all_errors)[:2000] if all_errors else None
            ),
        )
        connection.execute(
            """
            UPDATE employer_board_discovery_runs
            SET completed_at=?,success=?,commoncrawl_index=?,
                local_urls_scanned=?,commoncrawl_urls_seen=?,
                candidates_inserted=?,candidates_updated=?,
                candidates_validated=?,candidates_valid=?,
                runtime_boards_inserted=?,runtime_boards_updated=?,
                error_text=?
            WHERE id=?
            """,
            (
                _utc_now(),
                1 if success else 0,
                commoncrawl_result.get("index_id"),
                int(local_result.get("scanned") or 0),
                int(commoncrawl_result.get("urls_seen") or 0),
                int(local_result.get("inserted") or 0)
                + int(commoncrawl_result.get("inserted") or 0),
                int(local_result.get("updated") or 0)
                + int(commoncrawl_result.get("updated") or 0),
                int(validation_result.get("validated") or 0),
                int(validation_result.get("valid") or 0),
                int(sync_result.get("inserted") or 0),
                int(sync_result.get("updated") or 0),
                "; ".join(all_errors)[:4000] if all_errors else None,
                run_id,
            ),
        )
        connection.commit()
        return {
            "success": success,
            "marker": MARKER,
            "source": DISCOVERY_SOURCE_NAME,
            "network_request_made": network,
            "paid_api_calls": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
            "local_discovery": local_result,
            "commoncrawl": commoncrawl_result,
            "validation": validation_result,
            "runtime_sync": sync_result,
        }
    except Exception as exc:
        if connection is None:
            connection = get_connection()
            initialize_tables(connection)
        connection.rollback()
        error = f"{type(exc).__name__}: {exc}"
        try:
            _record_discovery_health(
                connection,
                success=False,
                candidates_seen=0,
                valid_count=0,
                error_text=error,
            )
            if run_id is not None:
                connection.execute(
                    """
                    UPDATE employer_board_discovery_runs
                    SET completed_at=?,success=0,error_text=?
                    WHERE id=?
                    """,
                    (_utc_now(), error[:4000], run_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
        return {
            "success": False,
            "marker": MARKER,
            "source": DISCOVERY_SOURCE_NAME,
            "network_request_made": network,
            "paid_api_calls": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
            "errors": [error],
        }
    finally:
        session.close()
        if connection is not None:
            connection.close()


def self_test() -> dict[str, Any]:
    samples = {
        "personio": detect_candidate(
            "https://example.jobs.personio.de/job/1"
        ),
        "pinpoint": detect_candidate(
            "https://example.pinpointhq.com/postings.json"
        ),
        "recruitee": detect_candidate(
            "https://example.recruitee.com/o/hr-intern"
        ),
        "comeet": detect_candidate(
            "https://www.comeet.com/jobs/example/30.005/hr-intern/87.405"
            "?token=PUBLICTOKEN123"
        ),
    }
    return {
        "success": all(samples.values()),
        "self_test": True,
        "marker": MARKER,
        "detected": {
            key: {
                "provider": value.provider,
                "board_locator": value.board_locator,
                "board_url": _redact_url(value.board_url),
                "company_uid": value.company_uid,
                "has_public_token": bool(value.public_token),
            }
            if value
            else None
            for key, value in samples.items()
        },
        "network_request_made": False,
        "database_writes": 0,
        "paid_api_calls": 0,
        "telegram_messages": 0,
        "n8n_calls": 0,
    }


def cli_main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Automatic public employer-board discovery "
            "for Personio, Pinpoint, Comeet, and Recruitee"
        )
    )
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--max-records-per-pattern", type=int, default=300)
    parser.add_argument("--max-validate", type=int, default=60)
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=0.8,
    )
    parser.add_argument(
        "--provider",
        action="append",
        choices=("personio", "pinpoint", "comeet", "recruitee"),
    )
    parser.add_argument("--quiet-start", action="store_true")
    parser.add_argument("--chat-id", default="")
    args, _unknown = parser.parse_known_args()
    result = (
        self_test()
        if args.self_test
        else run_discovery(
            network=not args.no_network,
            max_records_per_pattern=max(
                25,
                min(args.max_records_per_pattern, 1000),
            ),
            max_validate=max(1, min(args.max_validate, 500)),
            request_delay_seconds=max(
                0.5,
                min(args.request_delay_seconds, 10.0),
            ),
            providers=set(args.provider or []) or None,
        )
    )
    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    if not result.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    cli_main()
