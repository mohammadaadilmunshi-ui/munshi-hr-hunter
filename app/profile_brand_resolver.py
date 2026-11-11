"""Public organization-brand resolution for the MUNSHI Profile workspace.

Only public organization/institution/issuer names are sent to Wikidata. Candidate
contact data, resume bullets, self-ID, work authorization, and other private
profile fields are never transmitted by this resolver.

Resolved public logo URLs are cached in SQLite so the Profile page does not make
network requests on every render. Wikimedia Commons is preferred when Wikidata
exposes an official logo (P154); an official-site favicon is a fallback.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable
from urllib.parse import quote, urlparse

import httpx

from app.database import get_connection

SCHEMA_VERSION = "profile-brand-resolver-v1"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
_USER_AGENT = "MUNSHI-Profile/1.0 (public brand metadata resolver)"
_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")

BRAND_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_brand_assets(
    normalized_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    entity_id TEXT,
    logo_url TEXT,
    website_url TEXT,
    resolver TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('resolved','fallback','miss')),
    resolved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(normalized_name, kind)
);
CREATE INDEX IF NOT EXISTS idx_profile_brand_assets_resolved
ON profile_brand_assets(status, resolved_at DESC);
"""


def ensure_schema(connection=None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        connection.executescript(BRAND_CACHE_SCHEMA)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _clean_name(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").strip())[:240]


def _normalized_name(value: Any) -> str:
    return _NON_WORD_RE.sub(" ", _clean_name(value).casefold()).strip()


def initials(value: Any) -> str:
    words = [part for part in _clean_name(value).replace("&", " ").split() if part]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def _cache_get(name: str, kind: str) -> dict[str, Any] | None:
    normalized = _normalized_name(name)
    if not normalized:
        return None
    connection = get_connection()
    try:
        ensure_schema(connection)
        row = connection.execute(
            """SELECT * FROM profile_brand_assets
               WHERE normalized_name=? AND kind=?
                 AND resolved_at >= datetime('now', CASE WHEN status='miss' THEN '-7 days' ELSE '-30 days' END)
               LIMIT 1""",
            (normalized, str(kind or "organization")),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def _cache_put(name: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalized_name(name)
    display = _clean_name(name)
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute(
            """INSERT INTO profile_brand_assets(
                    normalized_name,kind,display_name,entity_id,logo_url,website_url,resolver,status,resolved_at
                ) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(normalized_name,kind) DO UPDATE SET
                    display_name=excluded.display_name,
                    entity_id=excluded.entity_id,
                    logo_url=excluded.logo_url,
                    website_url=excluded.website_url,
                    resolver=excluded.resolver,
                    status=excluded.status,
                    resolved_at=CURRENT_TIMESTAMP""",
            (
                normalized,
                str(kind or "organization"),
                display,
                str(payload.get("entity_id") or "") or None,
                str(payload.get("logo_url") or "") or None,
                str(payload.get("website_url") or "") or None,
                str(payload.get("resolver") or "wikidata"),
                str(payload.get("status") or "miss"),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "normalized_name": normalized,
        "kind": str(kind or "organization"),
        "display_name": display,
        **payload,
    }


def _claim_value(entity: dict[str, Any], property_id: str) -> Any:
    claims = ((entity.get("claims") or {}).get(property_id) or [])
    for claim in claims:
        value = (((claim or {}).get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if value not in (None, ""):
            return value
    return None


def _candidate_score(name: str, kind: str, result: dict[str, Any]) -> int:
    wanted = _normalized_name(name)
    label = _normalized_name(result.get("label"))
    aliases = [_normalized_name(value) for value in result.get("aliases") or []]
    description = str(result.get("description") or "").casefold()
    score = 0
    if label == wanted:
        score += 100
    elif wanted and (wanted in label or label in wanted):
        score += 45
    if wanted in aliases:
        score += 35
    kind = str(kind or "organization").casefold()
    kind_tokens = {
        "education": ("university", "college", "school", "educational", "academy"),
        "employer": ("company", "business", "manufacturer", "corporation", "organization", "firm"),
        "certification": ("company", "organization", "education", "university", "academy", "bank"),
        "organization": ("organization", "company", "institution", "business"),
    }.get(kind, ("organization", "company", "institution"))
    if any(token in description for token in kind_tokens):
        score += 12
    return score


def _commons_logo(filename: str) -> str:
    return "https://commons.wikimedia.org/wiki/Special:Redirect/file/" + quote(filename, safe="") + "?width=160"


def _favicon_for_website(website_url: str) -> str:
    parsed = urlparse(str(website_url or ""))
    host = parsed.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    return "https://icons.duckduckgo.com/ip3/" + quote(host, safe=".") + ".ico"


def _resolve_remote(name: str, kind: str) -> dict[str, Any]:
    display = _clean_name(name)
    if not display:
        return {"status": "miss", "resolver": "wikidata"}
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=3.5, follow_redirects=True, headers=headers) as client:
        search = client.get(
            _WIKIDATA_API,
            params={
                "action": "wbsearchentities",
                "search": display,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "limit": 6,
                "format": "json",
            },
        )
        search.raise_for_status()
        candidates = list((search.json() or {}).get("search") or [])
        if not candidates:
            return {"status": "miss", "resolver": "wikidata"}
        ranked = sorted(candidates, key=lambda item: _candidate_score(display, kind, item), reverse=True)
        best = ranked[0]
        entity_id = str(best.get("id") or "")
        if not entity_id:
            return {"status": "miss", "resolver": "wikidata"}
        entity_response = client.get(_WIKIDATA_ENTITY.format(entity_id=quote(entity_id, safe="")))
        entity_response.raise_for_status()
        entity = (((entity_response.json() or {}).get("entities") or {}).get(entity_id) or {})
        logo_filename = _claim_value(entity, "P154")
        website_url = str(_claim_value(entity, "P856") or "")
        logo_url = _commons_logo(str(logo_filename)) if logo_filename else _favicon_for_website(website_url)
        status = "resolved" if logo_filename else "fallback" if logo_url else "miss"
        return {
            "entity_id": entity_id,
            "logo_url": logo_url,
            "website_url": website_url,
            "resolver": "wikidata",
            "status": status,
        }


def resolve_brand_asset(name: Any, *, kind: str = "organization", refresh: bool = False) -> dict[str, Any]:
    display = _clean_name(name)
    normalized_kind = str(kind or "organization").strip().casefold()[:40] or "organization"
    if not display:
        return {
            "display_name": "",
            "kind": normalized_kind,
            "logo_url": "",
            "website_url": "",
            "status": "miss",
            "initials": "?",
        }
    cached = None if refresh else _cache_get(display, normalized_kind)
    if cached:
        cached["initials"] = initials(display)
        return cached
    try:
        payload = _resolve_remote(display, normalized_kind)
    except (httpx.HTTPError, ValueError, TypeError):
        payload = {"status": "miss", "resolver": "wikidata"}
    stored = _cache_put(display, normalized_kind, payload)
    stored["initials"] = initials(display)
    return stored


def resolve_brand_assets(
    requests: Iterable[tuple[Any, str]], *, refresh: bool = False, max_workers: int = 5
) -> dict[tuple[str, str], dict[str, Any]]:
    """Resolve a small batch concurrently while preserving a persistent cache."""
    unique: dict[tuple[str, str], tuple[str, str]] = {}
    for raw_name, raw_kind in requests:
        name = _clean_name(raw_name)
        kind = str(raw_kind or "organization").strip().casefold() or "organization"
        if name:
            unique[(_normalized_name(name), kind)] = (name, kind)
    if not unique:
        return {}
    resolved: dict[tuple[str, str], dict[str, Any]] = {}
    workers = max(1, min(int(max_workers), 6, len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(resolve_brand_asset, name, kind=kind, refresh=refresh): key
            for key, (name, kind) in unique.items()
        }
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                resolved[key] = future.result()
            except Exception:
                name, kind = unique[key]
                resolved[key] = {
                    "display_name": name,
                    "kind": kind,
                    "logo_url": "",
                    "website_url": "",
                    "status": "miss",
                    "initials": initials(name),
                }
    return resolved
