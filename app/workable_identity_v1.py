"""Read-only canonical identity discovery for public Workable job URLs.

A Workable /j/<opaque> URL can advertise a DIFFERENT canonical /j/ code
and a numeric public job ID. Never trust raw URL equality as a complete
duplicate check. This resolver only visits HTTPS workable.com hosts, checks
each redirect destination before following it, and never submits a form.
"""
from __future__ import annotations

import re
from functools import lru_cache
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx


_CODE = re.compile(r"/j/([0-9a-fA-F]{8,16})(?:/|$)")
_JOB = re.compile(r"/jobs/([0-9]{4,14})(?:/|$)")


def _workable_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = str(parsed.hostname or "").casefold().rstrip(".")
    return parsed.scheme == "https" and (
        host == "workable.com" or host.endswith(".workable.com")
    ) and not parsed.username and not parsed.password


def url_identities(url: str) -> frozenset[str]:
    """Offline identities from known Workable URLs; no network or mutations."""
    if not _workable_url(url):
        return frozenset({"exact:" + str(url).strip()})
    parts = urlsplit(url)
    host = str(parts.hostname or "").casefold()
    pieces = [part for part in parts.path.split("/") if part]
    org = ""
    if host == "apply.workable.com" and len(pieces) > 2 and pieces[1] in {"j", "jobs"}:
        org = pieces[0].casefold()
    elif host not in {"apply.workable.com", "workable.com"}:
        org = host.split(".")[0]
    identities: set[str] = set()
    code = _CODE.search(parts.path)
    job = _JOB.search(parts.path)
    if code:
        identities.add("workable:code:" + code.group(1).casefold())
    if job and org:
        identities.add(f"workable:{org}:job:{job.group(1)}")
    return frozenset(identities)


class _WorkableMeta(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.job_id: str | None = None
        self.related_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        props = dict(attrs)
        if tag == "meta" and props.get("name") == "job":
            value = str(props.get("content") or "")
            if re.fullmatch(r"[0-9]{4,14}", value):
                self.job_id = value
        if tag == "link" and props.get("rel") in {"canonical", "alternate"}:
            href = str(props.get("href") or "")
            if _workable_url(href):
                self.related_urls.append(href)


@lru_cache(maxsize=256)
def resolved_workable_identities(url: str) -> frozenset[str]:
    """Resolve canonical aliases, failing closed if public Workable evidence fails."""
    if not _workable_url(url):
        return url_identities(url)
    current = url
    identities = set(url_identities(current))
    with httpx.Client(timeout=6.0, follow_redirects=False) as client:
        for _ in range(5):
            if not _workable_url(current):
                raise RuntimeError("Workable redirect left the verified ATS domain.")
            result = client.get(current, headers={"Accept": "text/html"})
            identities.update(url_identities(current))
            if result.status_code in {301, 302, 303, 307, 308}:
                target = urljoin(current, str(result.headers.get("location") or ""))
                if not _workable_url(target):
                    raise RuntimeError("Workable returned an untrusted redirect.")
                current = target
                continue
            if result.status_code != 200 or len(result.content) > 300_000:
                raise RuntimeError("Workable canonical job identity unavailable.")
            parser = _WorkableMeta()
            parser.feed(result.text)
            for link in parser.related_urls:
                identities.update(url_identities(link))
            if parser.job_id:
                pieces = [x for x in urlsplit(current).path.split("/") if x]
                host = str(urlsplit(current).hostname or "").casefold()
                org = (pieces[0] if host == "apply.workable.com" and len(pieces) > 2
                       else host.split(".")[0] if host != "apply.workable.com" else "")
                if org:
                    identities.add(f"workable:{org.casefold()}:job:{parser.job_id}")
            if not parser.job_id or len(identities) < 2:
                raise RuntimeError("Workable alias evidence is incomplete.")
            return frozenset(identities)
    raise RuntimeError("Workable canonical redirect chain exceeded its limit.")


def lookup_terms(url: str, identities: frozenset[str]) -> tuple[str, ...]:
    """Bounded provider search terms for the same exact ATS opening."""
    original = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    related = sorted(
        (item.rsplit(":", 1)[-1] for item in identities
         if item.startswith("workable:") and ":" in item),
        key=lambda value: (not value.isdigit(), value != original, value),
    )
    return tuple(dict.fromkeys([original, *related]))[:5]
