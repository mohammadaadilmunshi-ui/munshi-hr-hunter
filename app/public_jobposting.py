"""Tenant-scoped public JobPosting parsing; no network or inferred employer trust."""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit

from app.ats_common import html_to_text, normalize_country
from app.public_board_http import validate_board_url


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.current: list[str] = []
        self.values: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = dict(attrs).get("type", "").lower() == "application/ld+json"
            self.current = []

    def handle_data(self, data):
        if self.active:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.values.append("".join(self.current))
            self.active = False


def jobposting_objects(value: Any, depth: int = 0) -> Iterable[dict]:
    if depth > 20:
        return
    if isinstance(value, dict):
        kinds = value.get("@type", [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if isinstance(kinds, list) and any(str(k).rsplit("/", 1)[-1] == "JobPosting" for k in kinds):
            yield value
        else:
            for child in value.values():
                if isinstance(child, (dict, list)):
                    yield from jobposting_objects(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from jobposting_objects(child, depth + 1)


def location_fields(value: Any) -> dict:
    if not isinstance(value, dict):
        return {"location_raw": str(value or "Not specified"), "country": None}
    address = value.get("address", value)
    if not isinstance(address, dict):
        return {"location_raw": str(address or "Not specified"), "country": None}
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    country = normalize_country(country) or None
    city = address.get("addressLocality")
    state = address.get("addressRegion")
    return {"location_raw": ", ".join(str(x) for x in (city, state, country) if x) or "Not specified",
            "city": city, "state": state, "country": country}


def parse_jobposting_html(page_url: str, html: str, *, company: str, provider: str,
                          job_path_prefix: str, now: datetime | None = None) -> dict:
    host = str(urlsplit(page_url).hostname)
    validate_board_url(page_url, {host})
    parser = _Scripts()
    parser.feed(html)
    jobs: list[dict] = []
    rejected: dict[str, int] = {}
    raw_count = 0
    def reject(reason):
        rejected[reason] = rejected.get(reason, 0) + 1
    current = now or datetime.now(timezone.utc)
    for script in parser.values[:100]:
        try:
            payload = json.loads(script)
        except (ValueError, RecursionError):
            reject("malformed_jsonld")
            continue
        for item in jobposting_objects(payload):
            raw_count += 1
            organization = item.get("hiringOrganization") or {}
            name = organization.get("name", "") if isinstance(organization, dict) else ""
            normalized_name = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())
            if normalized_name(name) != normalized_name(company):
                reject("employer_mismatch"); continue
            title = str(item.get("title") or "").strip()
            identifier = item.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value")
            if not title or not isinstance(identifier, (str, int)) or not str(identifier).strip():
                reject("missing_identity"); continue
            try:
                url = validate_board_url(urljoin(page_url, str(item.get("url") or "")), {host})
                if not item.get("url") or not urlsplit(url).path.startswith(job_path_prefix):
                    raise ValueError("not an approved posting path")
                expiry = str(item.get("validThrough") or "")
                if expiry:
                    parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                    if parsed.replace(tzinfo=parsed.tzinfo or timezone.utc) < current:
                        reject("removed_or_expired"); continue
            except (ValueError, TypeError):
                reject("invalid_url_or_expiry"); continue
            locations = item.get("jobLocation") or []
            locations = locations if isinstance(locations, list) else [locations]
            candidates = [location_fields(x) for x in locations] or [{"location_raw": "Not specified", "country": None}]
            remote = str(item.get("jobLocationType", "")).upper() == "TELECOMMUTE"
            if remote:
                requirements = item.get("applicantLocationRequirements") or []
                requirements = requirements if isinstance(requirements, list) else [requirements]
                countries = [x.get("name") for x in requirements if isinstance(x, dict) and x.get("name")]
                if countries:
                    candidates = [{"location_raw": "Remote - " + str(x), "country": normalize_country(x)} for x in countries]
            jobs.append({"source": f"{provider}/{company}", "source_tier": 1,
                         "company_name": company, "title": title, "ats_job_id": str(identifier),
                         "job_url": url, "apply_url": url, "description_raw": html_to_text(item.get("description")) or "Not specified",
                         "employment_type": item.get("employmentType"), "date_posted": item.get("datePosted"),
                         "remote_type": "Remote" if remote else "Not specified",
                         **candidates[0], "public_location_candidates": candidates,
                         "entry_path": "adapter_discovery"})
    # Malformed scripts are diagnostics, not raw job records.
    return {"jobs": jobs, "raw_count": raw_count, "normalized_count": len(jobs),
            "rejections": rejected, "contract_observed": bool(raw_count)}
