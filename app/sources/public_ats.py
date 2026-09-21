from __future__ import annotations

import html as html_lib
import ipaddress
import json
import re
import time
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


PUBLIC_ATS_PROFILES: dict[str, dict[str, Any]] = {
    "Naukri": {
        "key": "naukri",
        "domains": [
            "naukri.com"
        ],
        "job_hints": [
            "/job-listings",
            "/jobs/",
            "/job/"
        ]
    },
    "Rippling": {
        "key": "rippling",
        "domains": [
            "rippling.com"
        ],
        "job_hints": [
            "/jobs/",
            "/job/"
        ]
    },
    "iCIMS": {
        "key": "icims",
        "domains": [
            "icims.com"
        ],
        "job_hints": [
            "/jobs/",
            "/job/"
        ]
    },
    "BrassRing": {
        "key": "brassring",
        "domains": [
            "brassring.com"
        ],
        "job_hints": [
            "/jobs/",
            "/job/",
            "jobid="
        ]
    },
    "JazzHR": {
        "key": "jazzhr",
        "domains": [
            "jazzhr.com",
            "jazz.co",
            "applytojob.com"
        ],
        "job_hints": [
            "/jobs/",
            "/apply/",
            "applytojob.com"
        ]
    },
    "Jobvite": {
        "key": "jobvite",
        "domains": [
            "jobvite.com"
        ],
        "job_hints": [
            "/job/",
            "/jobs/"
        ]
    },
    "BreezyHR": {
        "key": "breezyhr",
        "domains": [
            "breezy.hr"
        ],
        "job_hints": [
            "/p/",
            "/positions/",
            "/jobs/"
        ]
    },
    "Oracle Recruiting": {
        "key": "oracle_recruiting",
        "domains": [
            "oraclecloud.com",
            "taleo.net"
        ],
        "job_hints": [
            "/job/",
            "/jobs/",
            "jobid="
        ]
    },
    "Paylocity": {
        "key": "paylocity",
        "domains": [
            "paylocity.com"
        ],
        "job_hints": [
            "/recruiting/jobs/details/",
            "/jobs/",
            "/job/"
        ]
    },
    "UKG / UltiPro": {
        "key": "ukg_ultipro",
        "domains": [
            "ultipro.com",
            "ukg.com",
            "ukg.net"
        ],
        "job_hints": [
            "/job/",
            "/jobs/",
            "opportunitydetail"
        ]
    },
    "Zoho Recruit": {
        "key": "zoho_recruit",
        "domains": [
            "zohorecruit.com"
        ],
        "job_hints": [
            "/jobs/",
            "/job/"
        ]
    },
    "ADP Recruiting": {
        "key": "adp_recruiting",
        "domains": [
            "adp.com"
        ],
        "job_hints": [
            "/jobs/",
            "/job/",
            "recruitment",
            "career"
        ]
    },
    "Dover": {
        "key": "dover",
        "domains": [
            "dover.com"
        ],
        "job_hints": [
            "/jobs/",
            "/job/"
        ]
    },
    "Gem": {
        "key": "gem",
        "domains": [
            "gem.com"
        ],
        "job_hints": [
            "/jobs/",
            "/job/"
        ]
    },
    "SAP SuccessFactors": {
        "key": "sap_successfactors",
        "domains": [
            "successfactors.com"
        ],
        "job_hints": [
            "/job/",
            "/jobs/"
        ]
    },
    "Teamtailor": {
        "key": "teamtailor",
        "domains": [
            "teamtailor.com"
        ],
        "job_hints": [
            "/jobs/"
        ]
    },
    "Polymer": {
        "key": "polymer",
        "domains": [
            "polymer.co"
        ],
        "job_hints": [
            "/jobs/",
            "/job/"
        ]
    },
    "Phenom": {
        "key": "phenom",
        "domains": [
            "phenom.com",
            "phenompeople.com"
        ],
        "job_hints": [
            "/job/",
            "/jobs/"
        ]
    },
    "Dayforce": {
        "key": "dayforce",
        "domains": [
            "dayforcehcm.com",
            "dayforce.com"
        ],
        "job_hints": [
            "/candidateportal/",
            "/posting/view/",
            "/jobs/",
            "/job/"
        ]
    },
    "JOIN": {
        "key": "join",
        "domains": [
            "join.com"
        ],
        "job_hints": [
            "/jobs/",
            "/job/"
        ]
    },
    "CareerPlug": {
        "key": "careerplug",
        "domains": [
            "careerplug.com"
        ],
        "job_hints": [
            "/jobs/",
            "/job/"
        ]
    },
    "Hireology": {
        "key": "hireology",
        "domains": [
            "hireology.com"
        ],
        "job_hints": [
            "/careers/",
            "/jobs/",
            "/job/"
        ]
    }
}
PUBLIC_ATS_PROVIDER_NAMES = tuple(PUBLIC_ATS_PROFILES)
PUBLIC_ATS_PROVIDER_KEYS = {
    str(profile["key"]): name
    for name, profile in PUBLIC_ATS_PROFILES.items()
}

_BLOCK_MARKERS = (
    "captcha",
    "verify you are human",
    "access denied",
    "unusual traffic",
    "too many requests",
    "temporarily blocked",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def provider_key(provider: str) -> str:
    wanted = _text(provider).casefold()
    for name, profile in PUBLIC_ATS_PROFILES.items():
        if name.casefold() == wanted:
            return str(profile["key"])
    if wanted in PUBLIC_ATS_PROVIDER_KEYS:
        return wanted
    raise KeyError(f"Unknown public ATS provider: {provider}")


def provider_name(provider_or_key: str) -> str:
    value = _text(provider_or_key)
    if value in PUBLIC_ATS_PROVIDER_KEYS:
        return PUBLIC_ATS_PROVIDER_KEYS[value]
    wanted = value.casefold()
    for name in PUBLIC_ATS_PROFILES:
        if name.casefold() == wanted:
            return name
    raise KeyError(f"Unknown public ATS provider: {provider_or_key}")


def provider_host_allowed(provider_or_key: str, host: str) -> bool:
    try:
        name = provider_name(provider_or_key)
    except KeyError:
        return False
    host = _text(host).casefold().split(":", 1)[0]
    if not host:
        return False
    for domain in PUBLIC_ATS_PROFILES[name]["domains"]:
        domain = str(domain).casefold()
        if host == domain or host.endswith("." + domain):
            return True
    return False


def provider_for_url(url: str) -> str | None:
    parsed = urlsplit(_text(url))
    host = (parsed.hostname or "").casefold()
    if not host:
        return None
    for name in PUBLIC_ATS_PROVIDER_NAMES:
        if provider_host_allowed(name, host):
            return name
    return None



def canonical_board_url(provider_or_key: str, url: str) -> str:
    provider = provider_name(provider_or_key)
    value = _safe_public_url(url)
    parts = urlsplit(value)
    path = parts.path or "/"
    segments = [segment for segment in path.split("/") if segment]
    query = list(parse_qsl(parts.query, keep_blank_values=True))

    if provider == "Rippling" and "jobs" in segments:
        idx = segments.index("jobs")
        path = "/" + "/".join(segments[: idx + 1])
        query = []
    elif provider == "iCIMS":
        path = "/jobs/search"
        query = []
    elif provider == "JazzHR":
        if parts.hostname and parts.hostname.endswith("applytojob.com"):
            path = "/"
            query = []
    elif provider == "Jobvite" and "job" in segments:
        idx = segments.index("job")
        path = "/" + "/".join(segments[:idx]) + "/"
        query = []
    elif provider == "BreezyHR":
        path = "/"
        query = []
    elif provider == "Oracle Recruiting" and "sites" in segments:
        idx = segments.index("sites")
        if len(segments) > idx + 1:
            path = "/" + "/".join(segments[: idx + 2] + ["jobs"])
            query = []
    elif provider == "Paylocity":
        lowered = [segment.casefold() for segment in segments]
        if "details" in lowered:
            idx = lowered.index("details")
            path = "/" + "/".join(segments[:idx])
            query = []
    elif provider == "UKG / UltiPro" and "JobBoard" in segments:
        idx = segments.index("JobBoard")
        if len(segments) > idx + 1:
            path = "/" + "/".join(segments[: idx + 2])
            query = []
    elif provider == "Zoho Recruit" and "Careers" in segments:
        idx = segments.index("Careers")
        path = "/" + "/".join(segments[: idx + 1])
        query = []
    elif provider == "ADP Recruiting" and "cx" in segments:
        idx = segments.index("cx")
        path = "/" + "/".join(segments[: idx + 1])
        query = []
    elif provider == "SAP SuccessFactors":
        keep = []
        for key, item in query:
            if key.casefold() in {"company", "career_ns", "lang"}:
                keep.append((key, item))
        query = keep
    elif provider == "Teamtailor" and "jobs" in segments:
        idx = segments.index("jobs")
        path = "/" + "/".join(segments[: idx + 1])
        query = []
    elif provider == "Polymer" and "jobs" in segments:
        idx = segments.index("jobs")
        path = "/" + "/".join(segments[: idx + 1])
        query = []
    elif provider == "Phenom":
        lowered = [segment.casefold() for segment in segments]
        if "job" in lowered:
            idx = lowered.index("job")
            path = "/" + "/".join(segments[:idx] + ["jobs"])
            query = []
    elif provider == "Dayforce":
        lowered = [segment.casefold() for segment in segments]
        if "jobs" in lowered:
            idx = lowered.index("jobs")
            path = "/" + "/".join(segments[:idx])
            query = []
    elif provider == "JOIN" and "companies" in segments:
        idx = segments.index("companies")
        if len(segments) > idx + 1:
            path = "/" + "/".join(segments[: idx + 2])
            query = []
    elif provider == "CareerPlug":
        path = "/"
        query = []
    elif provider == "Hireology" and segments:
        path = "/" + segments[0]
        query = []
    elif provider == "Naukri":
        # Naukri detail URLs are not employer-owned boards. Keep the supplied
        # URL unless a configured search/list URL was already provided.
        pass
    elif provider in {"BrassRing", "Dover", "Gem"}:
        # These platforms often require tenant/query context on the supplied
        # public URL. Preserve it rather than inventing an unverified board.
        pass

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            path or "/",
            urlencode(query, doseq=True),
            "",
        )
    )



def _safe_public_url(url: str) -> str:
    value = _text(url)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("ATS board URL must be absolute HTTP(S)")
    host = (parsed.hostname or "").casefold()
    if not host or host in {"localhost", "localhost.localdomain"}:
        raise ValueError("ATS board URL host is not public")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise ValueError("ATS board URL must not target a private/reserved address")
    return value


def _blocked_page(text: str) -> bool:
    soup = BeautifulSoup(str(text or ""), "html.parser")
    for node in soup(["script", "style", "noscript", "template"]):
        node.decompose()
    visible = " ".join(soup.stripped_strings).casefold()
    return any(marker in visible for marker in _BLOCK_MARKERS)


def _walk_jobposting(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _walk_jobposting(item)
        return
    if not isinstance(value, Mapping):
        return
    raw_type = value.get("@type")
    types = (
        [str(item).casefold() for item in raw_type]
        if isinstance(raw_type, list)
        else [str(raw_type or "").casefold()]
    )
    if "jobposting" in types:
        yield dict(value)
    for key, nested in value.items():
        if key == "@context":
            continue
        if isinstance(nested, (list, dict)):
            yield from _walk_jobposting(nested)


def _jsonld_jobpostings(page_html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(str(page_html or ""), "html.parser")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for script in soup.find_all("script"):
        script_type = str(script.get("type") or "").casefold()
        if "ld+json" not in script_type:
            continue
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(html_lib.unescape(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for item in _walk_jobposting(payload):
            marker = json.dumps(item, sort_keys=True, default=str)
            if marker in seen:
                continue
            seen.add(marker)
            output.append(item)
    return output


def _job_links(provider: str, page_url: str, page_html: str, limit: int) -> list[str]:
    profile = PUBLIC_ATS_PROFILES[provider]
    hints = tuple(str(item).casefold() for item in profile.get("job_hints") or ())
    base_host = (urlsplit(page_url).hostname or "").casefold()
    soup = BeautifulSoup(str(page_html or ""), "html.parser")
    output: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        raw = _text(anchor.get("href"))
        if not raw or raw.casefold().startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(page_url, raw)
        parsed = urlsplit(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        target_host = (parsed.hostname or "").casefold()
        if target_host != base_host and not provider_host_allowed(provider, target_host):
            continue
        lowered = absolute.casefold()
        if hints and not any(hint in lowered for hint in hints):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        output.append(absolute)
        if len(output) >= max(1, int(limit)):
            break
    return output


def inspect_public_page(provider: str, page_url: str, page_html: str) -> dict[str, Any]:
    canonical = provider_name(provider)
    structured = _jsonld_jobpostings(page_html)
    links = _job_links(canonical, page_url, page_html, 100)
    return {
        "provider": canonical,
        "blocked": _blocked_page(page_html),
        "structured_jobs": len(structured),
        "job_links": len(links),
    }


def _address_parts(value: Any) -> tuple[str, str | None]:
    address = value
    if isinstance(value, Mapping) and isinstance(value.get("address"), Mapping):
        address = value.get("address")
    if isinstance(address, list):
        address = address[0] if address else {}
    if not isinstance(address, Mapping):
        return _text(address), None
    parts = [
        _text(address.get("addressLocality")),
        _text(address.get("addressRegion")),
        _text(address.get("addressCountry")),
    ]
    location = ", ".join(part for part in parts if part)
    country = _text(address.get("addressCountry")) or None
    return location, country


def _salary(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (str, int, float)):
        return str(value)
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return str(value)


def _normalize_jsonld_job(
    provider: str,
    board: Mapping[str, Any],
    item: Mapping[str, Any],
    page_url: str,
) -> dict[str, Any]:
    hiring = item.get("hiringOrganization")
    hiring_name = (
        _text(hiring.get("name"))
        if isinstance(hiring, Mapping)
        else ""
    )
    company = hiring_name or _text(board.get("company_name"))
    identifier = item.get("identifier")
    if isinstance(identifier, Mapping):
        external_id = _text(
            identifier.get("value")
            or identifier.get("name")
            or identifier.get("@id")
        )
    else:
        external_id = _text(identifier)
    raw_job_url = _text(item.get("url"))
    job_url = urljoin(page_url, raw_job_url) if raw_job_url else page_url
    location_value = item.get("jobLocation")
    if isinstance(location_value, list):
        locations: list[str] = []
        country = None
        for value in location_value:
            label, current_country = _address_parts(value)
            if label and label not in locations:
                locations.append(label)
            country = country or current_country
        location = " | ".join(locations)
    else:
        location, country = _address_parts(location_value)
    job_location_type = _text(item.get("jobLocationType")).casefold()
    remote_type = "Remote" if "telecommute" in job_location_type or "remote" in job_location_type else None
    description = BeautifulSoup(_text(item.get("description")), "html.parser").get_text(" ", strip=True)
    qualifications = item.get("qualifications")
    return {
        "source": f"{provider}/{company}",
        "source_tier": 1,
        "company_name": company,
        "title": _text(item.get("title")),
        "ats_job_id": external_id or job_url,
        "external_id": external_id or job_url,
        "location_raw": location,
        "country": country,
        "remote_type": remote_type,
        "date_posted": _text(item.get("datePosted")) or None,
        "employment_type": _text(item.get("employmentType")) or None,
        "description_raw": description,
        "qualifications": qualifications,
        "salary_raw": _salary(item.get("baseSalary")),
        "apply_url": job_url,
        "job_url": job_url,
        "_provider_country_raw": country,
        "entry_path": "adapter_discovery",
    }



_GENERIC_JOB_HEADINGS = {
    "jobs",
    "job",
    "careers",
    "career",
    "job search",
    "search jobs",
    "recommended jobs",
    "open positions",
    "available positions",
    "career opportunities",
    "opportunities",
}


def _plausible_job_title(title: str, company_name: str = "") -> bool:
    value = _text(title)
    if len(value) < 3 or len(value) > 220:
        return False
    lowered = value.casefold()
    if lowered in _GENERIC_JOB_HEADINGS:
        return False
    company = _text(company_name).casefold()
    if company and lowered == company:
        return False
    template_markers = (
        "{{",
        "}}",
        "[firstname]",
        "[lastname]",
        "replace(",
        "profiledetails.",
        "<%",
        "%>",
    )
    if any(marker in lowered for marker in template_markers):
        return False
    return True



def _fallback_html_job(
    provider: str,
    board: Mapping[str, Any],
    page_url: str,
    page_html: str,
) -> dict[str, Any] | None:
    soup = BeautifulSoup(str(page_html or ""), "html.parser")
    title_node = soup.find("h1")
    title = _text(title_node.get_text(" ", strip=True) if title_node else "")
    company = _text(board.get("company_name"))
    if not _plausible_job_title(title, company):
        return None
    description_node = (
        soup.select_one("[class*='job-description']")
        or soup.select_one("[id*='job-description']")
        or soup.select_one("[data-qa*='description']")
        or soup.find("main")
        or soup.find("article")
    )
    description = _text(
        description_node.get_text(" ", strip=True)
        if description_node is not None
        else ""
    )
    return {
        "source": f"{provider}/{company}",
        "source_tier": 1,
        "company_name": company,
        "title": title,
        "ats_job_id": page_url,
        "external_id": page_url,
        "location_raw": "",
        "country": None,
        "remote_type": None,
        "date_posted": None,
        "employment_type": None,
        "description_raw": description,
        "qualifications": None,
        "salary_raw": None,
        "apply_url": page_url,
        "job_url": page_url,
        "_provider_country_raw": None,
        "entry_path": "adapter_discovery",
    }


def _render_page(url: str, runtime: Mapping[str, Any]) -> str | None:
    if not bool(runtime.get("public_ats_browser_fallback_enabled", True)):
        return None
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    wait_ms = max(0, min(int(runtime.get("public_ats_render_wait_ms") or 900), 5000))
    timeout_ms = max(1000, int(float(runtime.get("request_timeout_seconds") or 20) * 1000))
    try:
        with sync_playwright() as playwright:
            launch: dict[str, Any] = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-dev-shm-usage"],
            }
            for candidate in ("/usr/bin/chromium", "/usr/bin/chromium-browser"):
                try:
                    from pathlib import Path
                    if Path(candidate).is_file():
                        launch["executable_path"] = candidate
                        break
                except Exception:
                    pass
            browser = playwright.chromium.launch(**launch)
            try:
                page = browser.new_page(
                    user_agent=_text(runtime.get("user_agent"))
                    or "AADIL-HR-HUNTER/3.0 public-job-board-client"
                )
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if wait_ms:
                    page.wait_for_timeout(wait_ms)
                return page.content()
            finally:
                browser.close()
    except Exception:
        return None



def render_public_page(
    url: str,
    *,
    timeout_seconds: float = 20,
    wait_ms: int = 900,
    user_agent: str = "AADIL-HR-HUNTER/3.0 public-job-board-client",
) -> str | None:
    return _render_page(
        _safe_public_url(url),
        {
            "request_timeout_seconds": timeout_seconds,
            "public_ats_browser_fallback_enabled": True,
            "public_ats_render_wait_ms": wait_ms,
            "user_agent": user_agent,
        },
    )


def _fetch_html(
    client: requests.Session,
    url: str,
    runtime: Mapping[str, Any],
) -> tuple[str, int]:
    timeout = float(runtime.get("request_timeout_seconds") or 20)
    headers = {
        "Accept": "text/html,application/xhtml+xml,*/*",
        "User-Agent": _text(runtime.get("user_agent"))
        or "AADIL-HR-HUNTER/3.0 public-job-board-client",
    }
    response = client.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    text = response.text
    if _blocked_page(text):
        raise RuntimeError("Public ATS page returned CAPTCHA/access denied/anti-bot challenge")
    return text, 1


def fetch_public_ats_board(
    provider: str,
    board: Mapping[str, Any],
    runtime: Mapping[str, Any],
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    canonical = provider_name(provider)
    board_url = _safe_public_url(
        _text(board.get("board_url") or board.get("careers_url"))
    )
    if not board_url:
        raise ValueError(f"{canonical} requires board_url or careers_url")
    board_host = (urlsplit(board_url).hostname or "").casefold()
    if not provider_host_allowed(canonical, board_host):
        raise ValueError(
            f"{canonical} board URL host is outside the provider allowlist: "
            f"{board_host or '<missing>'}"
        )
    client = session or requests.Session()
    max_jobs = max(1, int(runtime.get("max_jobs_per_board") or 60))
    detail_limit = max(
        0,
        min(
            int(runtime.get("max_detail_requests_per_board") or 0),
            max_jobs,
        ),
    )
    started = time.monotonic()
    page_html, requests_made = _fetch_html(client, board_url, runtime)
    structured = _jsonld_jobpostings(page_html)
    links = _job_links(canonical, board_url, page_html, max_jobs)
    browser_renders = 0

    if not structured and not links:
        rendered = _render_page(board_url, runtime)
        if rendered:
            browser_renders += 1
            if _blocked_page(rendered):
                raise RuntimeError("Rendered ATS page returned CAPTCHA/access denied/anti-bot challenge")
            page_html = rendered
            structured = _jsonld_jobpostings(page_html)
            links = _job_links(canonical, board_url, page_html, max_jobs)

    jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for item in structured:
        job = _normalize_jsonld_job(canonical, board, item, board_url)
        marker = _text(job.get("job_url"))
        if marker and marker in seen_urls:
            continue
        if marker:
            seen_urls.add(marker)
        if job.get("title"):
            jobs.append(job)
        if len(jobs) >= max_jobs:
            break

    remaining = max(0, min(detail_limit, max_jobs - len(jobs)))
    for link in links[:remaining]:
        if link in seen_urls:
            continue
        detail_html, made = _fetch_html(client, _safe_public_url(link), runtime)
        requests_made += made
        detail_jobs = _jsonld_jobpostings(detail_html)
        fallback = None
        if not detail_jobs:
            fallback = _fallback_html_job(
                canonical,
                board,
                link,
                detail_html,
            )
        if not detail_jobs and fallback is None:
            rendered_detail = _render_page(link, runtime)
            if rendered_detail:
                browser_renders += 1
                if _blocked_page(rendered_detail):
                    continue
                detail_html = rendered_detail
                detail_jobs = _jsonld_jobpostings(detail_html)
                if not detail_jobs:
                    fallback = _fallback_html_job(
                        canonical,
                        board,
                        link,
                        detail_html,
                    )
        if detail_jobs:
            for item in detail_jobs:
                job = _normalize_jsonld_job(canonical, board, item, link)
                marker = _text(job.get("job_url")) or link
                if marker in seen_urls:
                    continue
                seen_urls.add(marker)
                if job.get("title"):
                    jobs.append(job)
                if len(jobs) >= max_jobs:
                    break
        elif fallback and link not in seen_urls:
            title = _text(fallback.get("title")).casefold()
            if not any(
                phrase in title
                for phrase in (
                    "oops",
                    "page not found",
                    "job not found",
                    "position not found",
                    "we must have moved it",
                )
            ):
                seen_urls.add(link)
                jobs.append(fallback)
        if len(jobs) >= max_jobs:
            break

    return {
        "jobs": jobs[:max_jobs],
        "requests": requests_made,
        "browser_renders": browser_renders,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    }
