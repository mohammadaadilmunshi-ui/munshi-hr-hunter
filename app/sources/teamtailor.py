"""Official tenant RSS, never the authenticated Teamtailor management API."""
from __future__ import annotations

import re
import time
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit
import xml.etree.ElementTree as ET

from app.ats_common import html_to_text, normalize_country
from app.public_board_contracts import approved_public_contract
from app.public_board_http import PublicBoardClient, validate_board_url

NS = {"tt": "https://teamtailor.com/locations"}


def approved_contract(board: Mapping[str, Any]) -> dict:
    contract = approved_public_contract(board, format="teamtailor_rss")
    if contract["provider"] != "Teamtailor":
        raise ValueError("Public contract provider mismatch")
    return contract


def parse_teamtailor_feed(content: bytes, contract: Mapping[str, Any]) -> dict:
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("DTD/entity declarations are prohibited")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError("Malformed public RSS payload") from error
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError("Teamtailor feed must be RSS with a channel")
    company = str(contract["company_name"])
    if (channel.findtext("title") or "").strip().casefold() != company.casefold():
        raise ValueError("Feed employer differs from independently verified company")
    host = str(urlsplit(str(contract["board_url"])).hostname)
    items = channel.findall("item")
    jobs = []
    rejections: dict[str, int] = {}
    for item in items:
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        try:
            url = validate_board_url(url, {host})
            match = re.fullmatch(re.escape(str(contract["job_path_prefix"])) + r"(\d+)-[^/]+/?", urlsplit(url).path)
            if not title or not match:
                raise ValueError("Missing exact public job identity")
        except ValueError:
            rejections["invalid_identity_or_url"] = rejections.get("invalid_identity_or_url", 0) + 1
            continue
        locations = []
        for loc in item.findall("tt:locations/tt:location", NS):
            city = loc.findtext("tt:city", namespaces=NS)
            country = normalize_country(loc.findtext("tt:country", namespaces=NS)) or None
            label = ", ".join(x for x in (city, country) if x) or loc.findtext("tt:name", namespaces=NS) or "Not specified"
            locations.append({"city": city, "country": country, "location_raw": label})
        locations = locations or [{"location_raw": "Not specified", "country": None}]
        jobs.append({
            "source": f"Teamtailor/{company}", "source_tier": 1,
            "company_name": company, "title": title, "ats_job_id": match[1],
            "job_url": url, "apply_url": url,
            "description_raw": html_to_text(item.findtext("description")) or "Not specified",
            "date_posted": item.findtext("pubDate"),
            "remote_type": {"fully": "Remote", "hybrid": "Hybrid", "none": "Onsite"}.get(
                item.findtext("remoteStatus") or "", "Not specified"),
            **locations[0], "public_location_candidates": locations,
            "entry_path": "adapter_discovery",
        })
    return {"jobs": jobs, "raw_count": len(items), "normalized_count": len(jobs),
            "rejections": rejections, "contract_observed": True}


def fetch_teamtailor_board(board: Mapping[str, Any], runtime: Mapping[str, Any], *, client=None) -> dict:
    contract = approved_contract(board)
    host = str(urlsplit(contract["board_url"]).hostname)
    pages = min(max(1, int(runtime.get("max_pages_per_board", 2))), 3)
    size = min(max(1, int(runtime.get("page_size", 20))), 100)
    job_limit = min(max(1, int(runtime.get("max_jobs_per_board", 60))), 300)
    client = client or PublicBoardClient({host}, max_requests=pages + 1,
                                        timeout=int(runtime.get("request_timeout_seconds", 15)))
    started = time.perf_counter()
    before = client.requests
    jobs = []
    raw = 0
    rejections: dict[str, int] = {}
    seen_pages = set()
    for page in range(pages):
        try:
            response = client.get(contract["board_url"] + "?" + urlencode({"per_page": size, "offset": page * size}))
        except Exception as error:
            error.request_count = client.requests - before
            raise
        parsed = parse_teamtailor_feed(response.content, contract)
        signature = tuple(j["ats_job_id"] for j in parsed["jobs"])
        if signature and signature in seen_pages:
            # A tenant that ignores offset must not inflate raw counts.
            break
        seen_pages.add(signature)
        raw += parsed["raw_count"]
        jobs.extend(parsed["jobs"])
        for reason, count in parsed["rejections"].items():
            rejections[reason] = rejections.get(reason, 0) + count
        if parsed["raw_count"] < size or len(jobs) >= job_limit:
            break
    capped = max(0, len(jobs) - job_limit)
    if capped:
        rejections["run_job_cap"] = capped
    return {"jobs": jobs[:job_limit], "raw_count": raw, "normalized_count": min(len(jobs), job_limit),
            "rejections": rejections, "requests": client.requests - before,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2), "contract_observed": True}
