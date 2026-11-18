"""Public, bounded BreezyHR employer JSON board discovery; no candidate endpoints."""
from __future__ import annotations

import re
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

from app.ats_common import html_to_text, normalize_country
from app.public_board_contracts import approved_public_contract
from app.public_board_http import PublicBoardClient, validate_board_url


def parse_breezy_jobs(payload: object, contract: Mapping) -> dict:
    if not isinstance(payload, list):
        raise ValueError("Breezy public /json must be an array")
    host = urlsplit(str(contract["board_url"])).hostname
    company = str(contract["company_name"])
    jobs = []
    rejections: dict[str, int] = {}
    seen = set()
    for raw in payload:
        if not isinstance(raw, dict):
            rejections["invalid_record"] = rejections.get("invalid_record", 0) + 1
            continue
        job_id = str(raw.get("id") or "")
        title = str(raw.get("name") or "").strip()
        url = str(raw.get("url") or "")
        friendly_id = str(raw.get("friendly_id") or "")
        try:
            clean_url = validate_board_url(url, {host})
            path = urlsplit(clean_url).path
            if (not title or not re.fullmatch(r"[a-zA-Z0-9]{6,64}", job_id)
                    or not friendly_id.startswith(job_id + "-")
                    or path != "/p/" + friendly_id or job_id in seen):
                raise ValueError("Unverified job identity")
        except ValueError:
            rejections["invalid_identity_or_url"] = rejections.get("invalid_identity_or_url", 0) + 1
            continue
        seen.add(job_id)
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        alternatives = raw.get("locations") if isinstance(raw.get("locations"), list) else []
        candidates = []
        for loc in [location, *alternatives]:
            if not isinstance(loc, dict):
                continue
            country = loc.get("country") if isinstance(loc.get("country"), dict) else {}
            state = loc.get("state") if isinstance(loc.get("state"), dict) else {}
            label = str(loc.get("name") or ", ".join(filter(None, (loc.get("city"), state.get("id"))))).strip()
            candidate = {"city": loc.get("city"), "state": state.get("id"),
                         "country": normalize_country(country.get("id") or country.get("name")) or None,
                         "location_raw": label or "Not specified"}
            if candidate not in candidates:
                candidates.append(candidate)
        primary = candidates[0] if candidates else {"location_raw": "Not specified", "country": None}
        job_type = raw.get("type") if isinstance(raw.get("type"), dict) else {}
        jobs.append({
            "source": f"BreezyHR/{company}", "source_tier": 1, "company_name": company,
            "title": title, "ats_job_id": job_id, "job_url": clean_url,
            "apply_url": clean_url, "date_posted": raw.get("published_date"),
            "employment_type": job_type.get("name") or "Not specified",
            "description_raw": html_to_text(raw.get("description")) or "Not specified",
            "remote_type": "Remote" if location.get("is_remote") is True else "Not specified",
            "public_location_candidates": candidates, **primary
        })
    return {"jobs": jobs, "raw_count": len(payload), "rejections": rejections}


def fetch_breezy_board(board: Mapping, runtime: Mapping) -> dict:
    contract = approved_public_contract(board, format="breezy_public_json")
    if contract["provider"] != "BreezyHR":
        raise ValueError("Wrong provider contract")
    url = str(contract["board_url"])
    host = urlsplit(url).hostname
    if not host or not re.fullmatch(r"[a-z0-9-]+\.breezy\.hr", host):
        raise ValueError("Breezy requires its independently verified public employer host")
    started = time.monotonic()
    client = PublicBoardClient({host}, max_requests=2,
                               timeout=min(20, int(runtime.get("request_timeout_seconds") or 15)),
                               max_bytes=3_000_000)
    try:
        response = client.get("https://" + host + "/json")
        parsed = parse_breezy_jobs(response.json(), contract)
    except Exception as error:
        error.request_count = client.requests
        raise
    return {**parsed, "requests": client.requests,
            "duration_ms": round((time.monotonic()-started)*1000, 2)}
