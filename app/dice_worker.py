# AADIL_DICE_DYNAMIC_REJECTION_COUNTER_V1_1
# AADIL_DICE_DEFENSIVE_RESULT_KEYS_V1
from __future__ import annotations
# AADIL_DICE_MAIN_LIFECYCLE_METRICS_CONTRACT_V1
# AADIL_DICE_ROLE_MATCH_COMPAT_V1
from app.free_adapter_policy_v1 import dice_role_match_compat as _role_match

import argparse
import html as html_module
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from app.database import ROOT_DIR, get_connection
from app.job_store import save_job
from app.dashboard_targeting_gate import (
    build_dashboard_search_queries,
    evaluate_dashboard_job,
    load_dashboard_targeting_rules,
    record_source_metrics,
)
from app.source_run_notifier import emit_source_run_result
from app.source_runtime import get_source_runtime_state
from app.telegram_auto_dispatch import dispatch_unsent_jobs
from app.runtime_config import provider_runtime, telegram_batch_limit
from app.targeting import within_run_identity

load_dotenv(ROOT_DIR / ".env", override=False)

SOURCE_NAME = "Dice"
SOURCE_PREFIX = "Dice/"
DICE_STATE_TABLE = "dice_query_rotation_state"
DICE_LINK_PATTERN = re.compile(
    r"https?://(?:www\.)?dice\.com/(?:job-detail|jobs/detail|job-detail/)[^\s\"'<>]+",
    re.IGNORECASE,
)
BLOCK_PATTERNS = (
    "captcha",
    "access denied",
    "verify you are human",
    "unusual traffic",
    "temporarily blocked",
    "robot or human",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_browser_executable() -> Path | None:
    configured = str(os.getenv("DICE_BROWSER_EXECUTABLE") or "").strip()
    if not configured:
        dice = provider_runtime().get("dice") or {}
        configured = str(dice.get("browser_executable") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return path
    return None


def _strip_html(value: Any) -> str:
    if value in (None, ""):
        return ""
    soup = BeautifulSoup(str(value), "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _organization_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    return str(value or "").strip()


def _address_text(value: Any) -> str:
    parts: list[str] = []
    for location in _as_list(value):
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if isinstance(address, dict):
            for key in (
                "addressLocality",
                "addressRegion",
                "postalCode",
                "addressCountry",
            ):
                item = address.get(key)
                if isinstance(item, dict):
                    item = item.get("name")
                text = str(item or "").strip()
                if text and text not in parts:
                    parts.append(text)
        elif address:
            text = str(address).strip()
            if text and text not in parts:
                parts.append(text)
    return ", ".join(parts)


def _salary_fields(value: Any) -> tuple[Any, Any, str]:
    if not isinstance(value, dict):
        return None, None, ""
    unit = str(value.get("unitText") or "").strip().lower()
    nested = value.get("value")
    if isinstance(nested, dict):
        low = nested.get("minValue")
        high = nested.get("maxValue")
        if low is None and high is None:
            low = high = nested.get("value")
    else:
        low = high = nested
    return low, high, unit


def _identifier(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("value") or value.get("name") or "").strip()
    return str(value or "").strip()


def parse_jobposting_nodes(
    page_html: str,
    page_url: str,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(page_html, "html.parser")
    jobs: list[dict[str, Any]] = []

    for script in soup.find_all("script"):
        script_type = str(script.get("type") or "").lower()
        if "ld+json" not in script_type:
            continue
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue

        for node in _walk_json(payload):
            node_type = node.get("@type")
            types = _as_list(node_type)
            if not any(str(item).lower() == "jobposting" for item in types):
                continue

            title = str(node.get("title") or node.get("name") or "").strip()
            company = _organization_name(node.get("hiringOrganization"))
            location = _address_text(node.get("jobLocation"))
            if str(node.get("jobLocationType") or "").upper() == "TELECOMMUTE":
                location = location or "Remote"

            url = str(
                node.get("url")
                or node.get("sameAs")
                or page_url
                or ""
            ).strip()
            if url:
                url = urljoin(page_url, url)

            low, high, interval = _salary_fields(node.get("baseSalary"))
            jobs.append(
                {
                    "site": "dice",
                    "source": "Dice/direct",
                    "title": title,
                    "company": company,
                    "location": location,
                    "description": _strip_html(node.get("description")),
                    "job_url": url,
                    "job_url_direct": url,
                    "date_posted": node.get("datePosted"),
                    "valid_through": node.get("validThrough"),
                    "job_type": node.get("employmentType"),
                    "min_amount": low,
                    "max_amount": high,
                    "interval": interval,
                    "id": _identifier(node.get("identifier")),
                    "is_remote": (
                        str(node.get("jobLocationType") or "").upper()
                        == "TELECOMMUTE"
                    ),
                }
            )

    return jobs


# AADIL_DICE_ROLE_MATCH_RETURN_CONTRACT_V1
def _aadil_role_match_tuple_v1(
    value: str,
    roles: list[str],
) -> tuple[bool, Any, Any]:
    result = _role_match(value, roles)
    if isinstance(result, tuple):
        padded = tuple(result) + (None, None, None)
        return bool(padded[0]), padded[1], padded[2]
    return bool(result), None, "boolean_role_match_contract"


def extract_dice_links(
    page_html: str,
    page_url: str,
    roles: list[str],
) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    scored: dict[str, tuple[int, int]] = {}
    sequence = 0

    def remember(url: str, score: int) -> None:
        nonlocal sequence
        clean = html_module.unescape(url).split("#", 1)[0].strip()
        if not clean:
            return
        current = scored.get(clean)
        if current is None:
            scored[clean] = (score, sequence)
            sequence += 1
        elif score > current[0]:
            scored[clean] = (score, current[1])

    for anchor in soup.find_all("a", href=True):
        url = urljoin(page_url, str(anchor.get("href") or "").strip())
        parsed = urlparse(url)
        if "dice.com" not in parsed.netloc.lower():
            continue
        lowered = parsed.path.lower()
        if "job-detail" not in lowered and "/jobs/detail" not in lowered:
            continue

        anchor_text = " ".join(anchor.get_text(" ", strip=True).split())
        parent_text = ""
        parent = anchor.parent
        if parent is not None:
            parent_text = " ".join(parent.get_text(" ", strip=True).split())[:800]
        context_text = f"{anchor_text} {parent_text}".strip()

        score = 10
        role_ok, _role_name, _role_reason = _aadil_role_match_tuple_v1(context_text, roles)
        if role_ok:
            score += 200
        else:
            lowered_context = context_text.casefold()
            for role in roles:
                role_text = str(role or "").strip().casefold()
                if role_text and role_text in lowered_context:
                    score += 160
                    break
        if anchor_text:
            score += 5
        remember(url, score)

    for match in DICE_LINK_PATTERN.findall(page_html):
        remember(match, 1)

    return [
        url
        for url, _meta in sorted(
            scored.items(),
            key=lambda item: (-item[1][0], item[1][1]),
        )
    ]


def _blocked(page_html: str) -> str | None:
    visible_text = BeautifulSoup(
        page_html,
        "html.parser",
    ).get_text(" ", strip=True).lower()
    return next(
        (text for text in BLOCK_PATTERNS if text in visible_text),
        None,
    )


def _fallback_dom_job(page_html: str, page_url: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(page_html, "html.parser")
    title_node = soup.find("h1")
    title = " ".join(title_node.get_text(" ", strip=True).split()) if title_node else ""
    if not title:
        return None

    company = ""
    location = ""
    description = ""

    for selector in (
        "[data-cy='companyNameLink']",
        "[data-testid='company-name']",
        ".company-name",
        "a[href*='/company-profile/']",
    ):
        node = soup.select_one(selector)
        if node:
            company = " ".join(node.get_text(" ", strip=True).split())
            break

    for selector in (
        "[data-cy='location']",
        "[data-testid='job-location']",
        ".job-location",
    ):
        node = soup.select_one(selector)
        if node:
            location = " ".join(node.get_text(" ", strip=True).split())
            break

    for selector in (
        "[data-cy='jobDescription']",
        "[data-testid='job-description']",
        "#jobDescription",
        ".job-description",
    ):
        node = soup.select_one(selector)
        if node:
            description = " ".join(node.get_text(" ", strip=True).split())
            break

    return {
        "site": "dice",
        "source": "Dice/direct",
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "job_url": page_url,
        "job_url_direct": page_url,
    }


def _has_meaningful_description(job: dict[str, Any] | None) -> bool:
    if not job:
        return False
    text = str(job.get("description") or "").strip()
    return bool(
        len(text) >= 80
        and text.casefold() not in {
            "not specified", "not listed", "not available", "n/a"
        }
    )


def _prefer_richer_job(
    current: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    if current is None:
        return candidate
    current_description = str(current.get("description") or "").strip()
    candidate_description = str(candidate.get("description") or "").strip()
    preferred = candidate if len(candidate_description) > len(current_description) else current
    other = current if preferred is candidate else candidate
    merged = dict(other)
    merged.update({key: value for key, value in preferred.items() if value not in (None, "")})
    return merged


def _canonical_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def _recent_enough(value: Any, hours_old: int) -> bool:
    if value in (None, ""):
        return True
    text = str(value).strip().replace("Z", "+00:00")
    try:
        posted = datetime.fromisoformat(text)
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return posted >= datetime.now(timezone.utc) - timedelta(hours=hours_old)


def _dashboard_candidate_match(
    job: dict[str, Any],
    roles: list[str],
    plans: list[dict[str, Any]],
) -> tuple[bool, str, str | None, list[dict[str, Any]], str]:
    result = evaluate_dashboard_job(job)
    job["_targeting_decision"] = result
    if not result.get("accepted"):
        return (
            False,
            str(result.get("reason") or "dashboard_rejected"),
            result.get("matched_target_role"),
            [],
            str(result.get("primary_category") or "REJECT_OTHER_TARGETING"),
        )
    # Persist exactly the canonical object that passed targeting. Dice JSON-LD
    # uses provider field names (company/location/description); saving that raw
    # object would replace verified metadata with store defaults and make a
    # later Telegram recheck reach a contradictory decision.
    canonical = dict(result.get("normalized_job") or {})
    if canonical:
        job.update(canonical)
    job["company_name"] = str(
        job.get("company_name") or job.get("company") or ""
    ).strip()
    job["location_raw"] = str(
        job.get("location_raw") or job.get("location") or ""
    ).strip()
    job["description_raw"] = str(
        job.get("description_raw") or job.get("description") or ""
    ).strip()
    job["apply_url"] = str(
        job.get("apply_url") or job.get("job_url_direct") or job.get("job_url") or ""
    ).strip()
    job["ats_job_id"] = str(
        job.get("ats_job_id") or job.get("id") or ""
    ).strip() or None
    job["_targeting_decision"] = result
    return (
        True,
        str(result.get("role_match_reason") or "dashboard_targeting_match"),
        result.get("matched_target_role"),
        list(result.get("location_matches") or []),
        "ELIGIBLE",
    )


def _job_rejection_reason(
    job: dict[str, Any],
    hours_old: int,
    roles: list[str],
    plans: list[dict[str, Any]],
) -> tuple[str, str | None, list[dict[str, Any]], str]:
    matched, reason, role_name, location_matches, category = _dashboard_candidate_match(
        job,
        roles,
        plans,
    )
    if not matched:
        return reason, role_name, location_matches, category
    if not _recent_enough(job.get("date_posted"), hours_old):
        return "older_than_hours_limit", role_name, location_matches, "REJECT_OTHER_TARGETING"
    return "", role_name, location_matches, "ELIGIBLE"


def _count_canonical_result(
    diagnostics: dict[str, Any],
    category: str,
    job: dict[str, Any],
) -> str:
    category = str(category or "REJECT_OTHER_TARGETING")
    decision = dict(job.get("_targeting_decision") or {})
    normalized = dict(decision.get("normalized_job") or job)
    identity = within_run_identity(normalized)
    seen = diagnostics.setdefault("_eligible_identities_seen", [])
    if category == "ELIGIBLE":
        if identity in seen:
            category = "DUPLICATE"
        else:
            seen.append(identity)
    counts = diagnostics.setdefault("canonical_primary_counts", {})
    counts[category] = int(counts.get(category, 0)) + 1
    diagnostics["canonical_evaluations"] = int(
        diagnostics.get("canonical_evaluations") or 0
    ) + 1
    diagnostics.setdefault("_decision_rows", []).append({
        "run_id": diagnostics["run_id"],
        "source_name": SOURCE_NAME,
        "external_id": str(
            normalized.get("ats_job_id") or normalized.get("id") or ""
        )[:300],
        "job_identity": identity,
        "title": str(normalized.get("title") or "")[:500],
        "company_name": str(
            normalized.get("company_name") or normalized.get("company") or ""
        )[:500],
        "location_raw": str(
            normalized.get("location_raw") or normalized.get("location") or ""
        )[:1000],
        "primary_category": category,
        "secondary_reasons": list(decision.get("secondary_reasons") or []),
        "evidence": {
            "reason": (
                "duplicate_within_run"
                if category == "DUPLICATE"
                else decision.get("reason")
            ),
            "role": decision.get("role_evidence"),
            "experience": decision.get("experience_evidence"),
            "hard_requirement": decision.get("hard_requirement_evidence"),
            "company": decision.get("company_evidence"),
            "location": decision.get("location_evidence"),
            "preference": decision.get("preference"),
        },
        "rules_version": decision.get("rules_version"),
        "rules_hash": decision.get("rules_hash"),
        "query_name": "Configured Dice query rotation",
        "role_family": str(decision.get("target_family") or ""),
    })
    return category


def _candidate_diagnostic(

    job: dict[str, Any],
    *,
    origin: str,
    rejection_reason: str,
    target_role: str | None = None,
    location_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "origin": origin,
        "title": str(job.get("title") or "")[:180],
        "company": str(job.get("company") or "")[:180],
        "date_posted": str(job.get("date_posted") or "")[:80],
        "description_chars": len(str(job.get("description") or "").strip()),
        "rejection_reason": rejection_reason or "accepted",
        "target_role": target_role,
        "location_match_count": len(location_matches or []),
        "has_job_url": bool(str(job.get("job_url") or "").strip()),
    }


def _ensure_dice_state(connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DICE_STATE_TABLE}(
            source_name TEXT PRIMARY KEY,
            query_cursor INTEGER NOT NULL DEFAULT 0,
            targeting_rules_hash TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _load_dice_cursor(rules_hash: str) -> int:
    connection = get_connection()
    try:
        _ensure_dice_state(connection)
        row = connection.execute(
            f"SELECT query_cursor,targeting_rules_hash FROM {DICE_STATE_TABLE} WHERE source_name=?",
            (SOURCE_NAME,),
        ).fetchone()
        if row is None or str(row["targeting_rules_hash"] or "") != rules_hash:
            return 0
        return int(row["query_cursor"] or 0)
    finally:
        connection.close()


def _save_dice_cursor(cursor: int, rules_hash: str) -> None:
    connection = get_connection()
    try:
        _ensure_dice_state(connection)
        connection.execute(
            f"""
            INSERT INTO {DICE_STATE_TABLE}(source_name,query_cursor,targeting_rules_hash,updated_at)
            VALUES(?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(source_name) DO UPDATE SET
                query_cursor=excluded.query_cursor,
                targeting_rules_hash=excluded.targeting_rules_hash,
                updated_at=CURRENT_TIMESTAMP
            """,
            (SOURCE_NAME, int(cursor), rules_hash),
        )
        connection.commit()
    finally:
        connection.close()


def collect_direct_dice_jobs(
    results_limit: int,
    hours_old: int,
    *,
    max_search_pages: int | None = None,
    max_detail_pages: int | None = None,
    time_budget_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            "Playwright is not installed in the project environment."
        ) from error

    runtime = provider_runtime().get("dice") or {}
    required_runtime = {
        "max_search_pages",
        "max_detail_pages",
        "time_budget_seconds",
        "page_timeout_ms",
    }
    missing_runtime = sorted(required_runtime - set(runtime))
    if missing_runtime:
        raise RuntimeError(
            "Canonical provider_runtime.dice policy is incomplete: "
            + ", ".join(missing_runtime)
        )

    def env_int(name: str, default: Any, minimum: int, maximum: int) -> int:
        try:
            value = int(str(os.getenv(name) or default).strip())
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    def env_float(name: str, default: Any, minimum: float, maximum: float) -> float:
        try:
            value = float(str(os.getenv(name) or default).strip())
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    results_limit = max(1, min(int(results_limit), 100))

    rules = load_dashboard_targeting_rules()
    roles = list(rules.matching_roles)
    plans = [dict(value) for value in rules.location_plan]
    search_queries, rules_hash = build_dashboard_search_queries(terms_per_query=3, max_queries=40)
    if not roles or not search_queries:
        raise RuntimeError("Dice cannot run because dashboard Target tracks/roles are empty.")
    if not plans:
        raise RuntimeError("Dice cannot run because dashboard location rules are empty.")

    all_query_pairs: list[tuple[str, str, dict[str, Any]]] = []
    for search_query in search_queries:
        for plan in plans:
            location = str(plan.get("search_location") or "").strip()
            if bool(plan.get("remote_only")) and not location:
                location = "Remote"
            if not location:
                parts = [str(plan.get("city") or "").strip(), str(plan.get("state") or "").strip()]
                location = ", ".join(value for value in parts if value)
            all_query_pairs.append((search_query, location or "United States", dict(plan)))

    query_cursor_before = _load_dice_cursor(rules_hash) % len(all_query_pairs)
    query_pairs = all_query_pairs[query_cursor_before:] + all_query_pairs[:query_cursor_before]

    if max_search_pages is None:
        search_cap = env_int("DICE_MAX_SEARCH_PAGES", runtime["max_search_pages"], 1, 12)
    else:
        search_cap = max(1, min(int(max_search_pages), 20))

    if max_detail_pages is None:
        detail_cap = env_int(
            "DICE_MAX_DETAIL_PAGES",
            runtime["max_detail_pages"],
            1,
            40,
        )
    else:
        detail_cap = max(1, min(int(max_detail_pages), 40))

    if time_budget_seconds is None:
        budget = env_float(
            "DICE_TIME_BUDGET_SECONDS",
            runtime["time_budget_seconds"],
            30.0,
            300.0,
        )
    else:
        budget = max(15.0, min(float(time_budget_seconds), 900.0))

    page_timeout_ms = env_int(
        "DICE_PAGE_TIMEOUT_MS",
        runtime["page_timeout_ms"],
        5_000,
        45_000,
    )
    started = time.monotonic()

    def elapsed_seconds() -> float:
        return time.monotonic() - started

    def budget_exhausted() -> bool:
        return elapsed_seconds() >= budget

    def bounded_timeout_ms(default_ms: int) -> int:
        remaining_ms = int(max(1.0, budget - elapsed_seconds()) * 1000)
        return max(1_000, min(default_ms, remaining_ms))

    configured_browser = find_browser_executable()

    diagnostics: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "browser_executable": "",
        "configuration_source": "SQLite dashboard",
        "target_roles_hardcoded": False,
        "location_values_hardcoded": False,
        "target_role_count": len(roles),
        "active_location_rule_count": len(plans),
        "query_family_count": len(search_queries),
        "query_pair_count": len(all_query_pairs),
        "query_cursor_before": query_cursor_before,
        "targeting_rules_hash": rules_hash,
        "queries_attempted": [],
        "browser_mode": "",
        "search_pages_attempted": 0,
        "search_pages_loaded": 0,
        "detail_pages_attempted": 0,
        "detail_pages_loaded": 0,
        "jsonld_jobs_found": 0,
        "jsonld_jobs_accepted": 0,
        "canonical_evaluations": 0,
        "canonical_primary_counts": {},
        "rejected_role_not_relevant": 0,
        "rejected_location_not_relevant": 0,
        "rejected_older_than_hours_limit": 0,
        "candidate_samples": [],
        "candidate_links_found": 0,
        "blocked_pages": [],
        "page_errors": [],
        "detail_enrichments": 0,
        "max_search_pages": search_cap,
        "max_detail_pages": detail_cap,
        "time_budget_seconds": budget,
        "page_timeout_ms": page_timeout_ms,
        "budget_exhausted": False,
    }
    jobs_by_key: dict[str, dict[str, Any]] = {}
    candidate_links: list[str] = []
    candidate_seen: set[str] = set()

    with sync_playwright() as playwright:
        bundled_browser = Path(str(playwright.chromium.executable_path))
        if bundled_browser.exists():
            browser_path = bundled_browser
            diagnostics["browser_mode"] = "playwright_bundled_chromium"
        else:
            browser_path = configured_browser
            diagnostics["browser_mode"] = "installed_system_browser"

        if browser_path is None or not Path(browser_path).exists():
            raise RuntimeError(
                "No Playwright Chromium, Chrome, Microsoft Edge, or Chromium "
                "browser was found."
            )

        diagnostics["browser_executable"] = str(browser_path)

        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(browser_path),
            args=[
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-component-update",
                "--disable-background-networking",
            ],
        )
        context = browser.new_context()
        page = context.new_page()

        try:
            for term, location, query_plan in query_pairs:
                if len(jobs_by_key) >= results_limit:
                    break
                if diagnostics["search_pages_attempted"] >= search_cap:
                    break
                if budget_exhausted():
                    diagnostics["budget_exhausted"] = True
                    break

                diagnostics["search_pages_attempted"] += 1
                diagnostics["queries_attempted"].append(
                    {
                        "role": term,
                        "location": location,
                        "rule_id": query_plan.get("rule_id"),
                        "rule_name": query_plan.get("rule_name"),
                    }
                )
                search_url = (
                    "https://www.dice.com/jobs?"
                    f"q={quote_plus(term)}"
                    f"&location={quote_plus(location)}"
                    "&radius=50&radiusUnit=mi&page=1&pageSize=20"
                    "&language=en"
                )
                try:
                    page.goto(
                        search_url,
                        wait_until="domcontentloaded",
                        timeout=bounded_timeout_ms(page_timeout_ms),
                    )
                    if not budget_exhausted():
                        page.wait_for_timeout(700)
                    page_html = page.content()
                    diagnostics["search_pages_loaded"] += 1
                except Exception as error:
                    diagnostics["page_errors"].append(
                        f"{term} / {location}: {error}"
                    )
                    continue

                blocked_reason = _blocked(page_html)
                if blocked_reason:
                    diagnostics["blocked_pages"].append(
                        {
                            "url": page.url,
                            "reason": blocked_reason,
                        }
                    )
                    continue

                nodes = parse_jobposting_nodes(page_html, page.url)
                diagnostics["jsonld_jobs_found"] += len(nodes)
                for job in nodes:
                    node_url = str(job.get("job_url") or "").strip()
                    if node_url and node_url not in candidate_seen:
                        candidate_seen.add(node_url)
                        candidate_links.append(node_url)

                    rejection_reason, target_role, location_matches, category = (
                        _job_rejection_reason(
                            job,
                            hours_old,
                            roles,
                            plans,
                        )
                    )
                    category = _count_canonical_result(diagnostics, category, job)
                    if len(diagnostics["candidate_samples"]) < 12:
                        diagnostics["candidate_samples"].append(
                            _candidate_diagnostic(
                                job,
                                origin="search_jsonld",
                                rejection_reason=rejection_reason,
                                target_role=target_role,
                                location_matches=location_matches,
                            )
                        )
                    if rejection_reason:
                        _aadil_dice_rejection_key_v1_1_2 = f"rejected_{rejection_reason}"
                        diagnostics[_aadil_dice_rejection_key_v1_1_2] = int(diagnostics.get(_aadil_dice_rejection_key_v1_1_2, 0)) + 1
                        continue

                    diagnostics["jsonld_jobs_accepted"] += 1
                    job["target_track"] = target_role
                    job["dashboard_location_matches"] = location_matches
                    key = (
                        _canonical_url(job.get("job_url"))
                        or "|".join(
                            [
                                str(job.get("company") or "").lower(),
                                str(job.get("title") or "").lower(),
                                str(job.get("location") or "").lower(),
                            ]
                        )
                    )
                    if key:
                        jobs_by_key[key] = _prefer_richer_job(
                            jobs_by_key.get(key),
                            job,
                        )

                links = extract_dice_links(page_html, page.url, roles)
                for link in links:
                    if link not in candidate_seen:
                        candidate_seen.add(link)
                        candidate_links.append(link)
                diagnostics["candidate_links_found"] = len(candidate_links)

            for link in candidate_links:
                if len(jobs_by_key) >= results_limit:
                    break
                if diagnostics["detail_pages_attempted"] >= detail_cap:
                    break
                if budget_exhausted():
                    diagnostics["budget_exhausted"] = True
                    break

                existing_key = _canonical_url(link)
                existing_job = jobs_by_key.get(existing_key)
                if existing_job is not None and _has_meaningful_description(existing_job):
                    continue

                diagnostics["detail_pages_attempted"] += 1
                try:
                    page.goto(
                        link,
                        wait_until="domcontentloaded",
                        timeout=bounded_timeout_ms(page_timeout_ms),
                    )
                    if not budget_exhausted():
                        page.wait_for_timeout(500)
                    try:
                        page.wait_for_selector(
                            "script[type='application/ld+json'], "
                            "[data-cy='jobDescription'], "
                            "[data-testid='job-description'], "
                            "#jobDescription, .job-description",
                            timeout=bounded_timeout_ms(5_000),
                        )
                    except Exception:
                        pass
                    page_html = page.content()
                    diagnostics["detail_pages_loaded"] += 1
                except Exception as error:
                    diagnostics["page_errors"].append(f"{link}: {error}")
                    continue

                blocked_reason = _blocked(page_html)
                if blocked_reason:
                    diagnostics["blocked_pages"].append(
                        {"url": page.url, "reason": blocked_reason}
                    )
                    continue

                nodes = parse_jobposting_nodes(page_html, page.url)
                diagnostics["jsonld_jobs_found"] += len(nodes)
                if not nodes:
                    fallback = _fallback_dom_job(page_html, page.url)
                    nodes = [fallback] if fallback else []

                for job in nodes:
                    if not job:
                        continue
                    rejection_reason, target_role, location_matches, category = (
                        _job_rejection_reason(
                            job,
                            hours_old,
                            roles,
                            plans,
                        )
                    )
                    category = _count_canonical_result(diagnostics, category, job)
                    if len(diagnostics["candidate_samples"]) < 12:
                        diagnostics["candidate_samples"].append(
                            _candidate_diagnostic(
                                job,
                                origin="detail_page",
                                rejection_reason=rejection_reason,
                                target_role=target_role,
                                location_matches=location_matches,
                            )
                        )
                    if rejection_reason:
                        _aadil_dice_rejection_key_v1_1_1 = f"rejected_{rejection_reason}"
                        diagnostics[_aadil_dice_rejection_key_v1_1_1] = int(diagnostics.get(_aadil_dice_rejection_key_v1_1_1, 0)) + 1
                        continue
                    diagnostics["jsonld_jobs_accepted"] += 1
                    job["target_track"] = target_role
                    job["dashboard_location_matches"] = location_matches
                    key = (
                        _canonical_url(job.get("job_url"))
                        or "|".join(
                            [
                                str(job.get("company") or "").lower(),
                                str(job.get("title") or "").lower(),
                                str(job.get("location") or "").lower(),
                            ]
                        )
                    )
                    if key:
                        before_description = str(
                            (jobs_by_key.get(key) or {}).get("description") or ""
                        )
                        jobs_by_key[key] = _prefer_richer_job(
                            jobs_by_key.get(key), job
                        )
                        after_description = str(
                            jobs_by_key[key].get("description") or ""
                        )
                        if len(after_description) > len(before_description):
                            diagnostics["detail_enrichments"] += 1
        finally:
            try:
                context.close()
            finally:
                browser.close()

    diagnostics["elapsed_seconds"] = round(elapsed_seconds(), 3)
    diagnostics["budget_exhausted"] = (
        diagnostics["budget_exhausted"] or budget_exhausted()
    )

    cursor_advance = max(1, int(diagnostics["search_pages_attempted"] or 0))
    query_cursor_after = (query_cursor_before + cursor_advance) % len(all_query_pairs)
    _save_dice_cursor(query_cursor_after, rules_hash)
    diagnostics["query_cursor_after"] = query_cursor_after

    jobs = list(jobs_by_key.values())[:results_limit]
    counts = dict(diagnostics.get("canonical_primary_counts") or {})
    within_duplicates = int(counts.get("DUPLICATE") or 0)
    normalized_count = sum(int(value or 0) for value in counts.values())
    request_count = int(diagnostics["search_pages_attempted"]) + int(
        diagnostics["detail_pages_attempted"]
    )
    diagnostics.update({
        "raw_jobs_found": normalized_count,
        "raw_normalized": normalized_count,
        "unique_jobs_ready": len(jobs),
        "eligible": len(jobs),
        "duplicates_within_run": within_duplicates,
        "duplicate": within_duplicates,
        "reject_role": int(counts.get("REJECT_ROLE") or 0),
        "reject_location": int(counts.get("REJECT_LOCATION") or 0),
        "reject_hard_requirement": int(counts.get("REJECT_HARD_REQUIREMENT") or 0),
        "reject_company": int(counts.get("REJECT_COMPANY") or 0),
        "reject_other_targeting": int(counts.get("REJECT_OTHER_TARGETING") or 0),
        "primary_counts": counts,
        "accounting_delta": normalized_count - sum(int(value or 0) for value in counts.values()),
        "request_count": request_count,
        "query_requests": [{
            "query_name": "Configured Dice query rotation",
            "role_family": "",
            "requests": request_count,
            "raw": normalized_count,
            "errors": len(diagnostics.get("page_errors") or []),
            "duration_ms": round(elapsed_seconds() * 1000, 2),
            "selection_mode": "configured_cursor_rotation",
        }],
        "errors": list(diagnostics.get("page_errors") or []),
    })
    diagnostics.pop("_eligible_identities_seen", None)
    diagnostics["jobs_with_meaningful_description"] = sum(
        1 for job in jobs if _has_meaningful_description(job)
    )
    if (
        not jobs
        and diagnostics["blocked_pages"]
        and diagnostics["search_pages_loaded"] > 0
    ):
        raise RuntimeError(
            "Dice loaded a bot/challenge page instead of job results. "
            + json.dumps(diagnostics["blocked_pages"][:3])
        )
    if diagnostics["search_pages_loaded"] == 0:
        raise RuntimeError(
            "No Dice search page loaded successfully: "
            + json.dumps(diagnostics["page_errors"][:3])
        )
    return jobs, diagnostics

def _update_health(
    success: bool,
    jobs_found: int,
    error: str | None = None,
    elapsed_ms: int | None = None,
) -> None:
    connection = get_connection()
    try:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(source_health)"
            ).fetchall()
        }
        assignments = [
            "last_run_at = CURRENT_TIMESTAMP",
            "jobs_found_last_run = ?",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        values: list[Any] = [int(jobs_found)]

        if "average_response_ms" in columns and elapsed_ms is not None:
            assignments.append("average_response_ms = ?")
            values.append(int(elapsed_ms))

        if success:
            assignments.extend(
                [
                    "health_status = 'healthy'",
                    "last_success_at = CURRENT_TIMESTAMP",
                    "consecutive_failures = 0",
                    "last_error = NULL",
                ]
            )
            if "last_http_status" in columns:
                assignments.append("last_http_status = 200")
        else:
            assignments.extend(
                [
                    "health_status = 'failed'",
                    "last_failure_at = CURRENT_TIMESTAMP",
                    "consecutive_failures = consecutive_failures + 1",
                    "last_error = ?",
                ]
            )
            values.append(str(error or "unknown error")[:2000])

        values.append(SOURCE_NAME)
        connection.execute(
            f"""
            UPDATE source_health
            SET {', '.join(assignments)}
            WHERE lower(source_name) = lower(?)
            """,
            values,
        )
        connection.commit()
    finally:
        connection.close()


def self_test() -> dict[str, Any]:
    fixture = """
    <html><script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "People Analytics Intern",
      "hiringOrganization": {"@type": "Organization", "name": "Example"},
      "jobLocation": {"address": {
        "addressLocality": "New York",
        "addressRegion": "NY",
        "addressCountry": "US"
      }},
      "description": "<p>Analyze workforce data.</p>",
      "datePosted": "2026-07-05",
      "url": "https://www.dice.com/job-detail/example"
    }
    </script></html>
    """
    jobs = parse_jobposting_nodes(
        fixture,
        "https://www.dice.com/job-detail/example",
    )
    assert len(jobs) == 1
    assert jobs[0]["title"] == "People Analytics Intern"
    assert jobs[0]["company"] == "Example"
    return {
        "success": True,
        "network_request_made": False,
        "parsed_jobs": len(jobs),
        "browser_executable": (
            str(find_browser_executable())
            if find_browser_executable()
            else None
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Direct Dice.com discovery worker without Google scraping."
    )
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--results", type=int)
    parser.add_argument("--hours-old", type=int)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return

    source_state = get_source_runtime_state(SOURCE_NAME)
    if not source_state["enabled"]:

        output = {
            "success": True,
            "source": SOURCE_NAME,
            "worker_action": "skip",
            "skip_reason": "source_disabled",
            "source_state": source_state,
            "network_request_made": False,
            "jobs_inserted": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
        }
        emit_source_run_result(output)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    if not source_state["due"] and not args.run_now:
        output = {
            "success": True,
            "source": SOURCE_NAME,
            "worker_action": "skip",
            "skip_reason": "cadence_not_due",
            "source_state": source_state,
            "network_request_made": False,
            "jobs_inserted": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
        }
        emit_source_run_result(output)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    started = time.monotonic()
    try:
        dice_runtime = provider_runtime().get("dice") or {}
        try:
            results_limit = int(
                args.results if args.results is not None else dice_runtime["results_limit"]
            )
            hours_old = int(
                args.hours_old if args.hours_old is not None else dice_runtime["hours_old"]
            )
        except (KeyError, TypeError, ValueError):
            raise RuntimeError(
                "Canonical provider_runtime.dice results/hour policy is incomplete."
            ) from None
        run_started_at = utc_now()
        raw_jobs, diagnostics = collect_direct_dice_jobs(
            results_limit=max(1, min(results_limit, 100)),
            hours_old=max(1, min(hours_old, 720)),
        )
        provider_raw = int(diagnostics.get("raw_jobs_found") or len(raw_jobs))
        diagnostics["run_started_at"] = run_started_at
        connection = get_connection()
        stored: list[dict[str, Any]] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            for raw_job in raw_jobs:
                stored.append(
                    save_job(
                        connection,
                        raw_job,
                        actor="dice_direct_worker",
                    )
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        inserted = sum(bool(item.get("inserted")) for item in stored)
        duplicates = len(stored) - inserted
        telegram_result = dispatch_unsent_jobs(
            source_prefix=SOURCE_PREFIX,
            limit=telegram_batch_limit(),
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        diagnostics["duration_ms"] = elapsed_ms
        diagnostics["telegram_messages"] = int(
            telegram_result.get("telegram_messages_sent") or 0
        )
        rejected_count = sum(
            int(diagnostics.get(key) or 0)
            for key in (
                "reject_role",
                "reject_location",
                "reject_hard_requirement",
                "reject_company",
                "reject_other_targeting",
            )
        )
        record_source_metrics(
            SOURCE_NAME,
            raw_jobs=provider_raw,
            eligible_jobs=len(raw_jobs),
            inserted_jobs=inserted,
            duplicate_jobs=duplicates,
            rejected_jobs=rejected_count,
            provider_used="dice_direct_playwright",
            filter_summary=diagnostics,
        )
        _update_health(
            success=True,
            jobs_found=int(diagnostics.get("jsonld_jobs_found") or len(raw_jobs)),
            elapsed_ms=elapsed_ms,
        )
        output = {
            "success": True,
            "source": SOURCE_NAME,
            "adapter": "direct_dice_playwright_jsonld",
            "worker_action": "run",
            "run_reason": (
                "manual_run_now" if args.run_now else "scheduled_due"
            ),
            "network_request_made": True,
            "raw_jobs_found": provider_raw,
            "jobs_after_dashboard_filters": len(raw_jobs),
            "jobs_inserted": inserted,
            "database_duplicates": duplicates,
            "telegram_messages": int(
                telegram_result.get("telegram_messages_sent") or 0
            ),
            "dispatch": telegram_result,
            "diagnostics": diagnostics,
            "n8n_calls": 0,
            "errors": [],
            "completed_at": utc_now(),
        }
        emit_source_run_result(output)
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    except Exception as error:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _update_health(
            success=False,
            jobs_found=0,
            error=str(error),
            elapsed_ms=elapsed_ms,
        )
        output = {
            "success": False,
            "source": SOURCE_NAME,
            "adapter": "direct_dice_playwright_jsonld",
            "worker_action": "failed",
            "network_request_made": True,
            "raw_jobs_found": 0,
            "jobs_inserted": 0,
            "database_duplicates": 0,
            "telegram_messages": 0,
            "n8n_calls": 0,
            "errors": [str(error)],
            "completed_at": utc_now(),
        }
        emit_source_run_result(output)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        raise


if __name__ == "__main__":
    main()
