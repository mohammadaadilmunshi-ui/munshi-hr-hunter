"""Bounded, read-only batch LinkedIn employer-link resolution.

Operates on already-discovered jobs and enrolled healthy ATS boards. Nothing
here schedules itself, updates Hunter SQLite or signs verified URL evidence.
Run from an authorized scheduler after discovery once deployment gates pass.
"""

from __future__ import annotations

import io
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from app.linkedin_employer_careers_v2 import (
    board_binding_from_ats_company_registry,
    resolve_from_known_company_boards,
)
from app.linkedin_employer_link_resolver_v1 import normalize
from app.linkedin_public_ats_feed_v1 import (
    ASHBY_MAX_BYTES,
    MAX_BYTES,
    NoRedirects,
)

_JOB_COLUMNS = (
    "id",
    "source",
    "company_name",
    "title",
    "location_raw",
    "description_raw",
    "job_url",
    "apply_url",
    "ats_job_id",
)
_BOARD_COLUMNS = (
    "company_name",
    "ats_type",
    "board_token",
    "careers_url",
    "enabled",
    "health_status",
)
_ALLOWED_FEED_HOSTS = {
    "boards-api.greenhouse.io",
    "api.ashbyhq.com",
    "api.lever.co",
    "api.smartrecruiters.com",
}
_MAX_MEMORY = 24_000_000


@dataclass
class _CachedHTTPResponse:
    body: bytes
    status: int

    def __post_init__(self) -> None:
        self.headers = {"Content-Length": str(len(self.body))}
        self._content = io.BytesIO(self.body)

    def read(self, size: int = -1) -> bytes:
        return self._content.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self._content.close()


class ATSReadCache:
    """Caches documented public GET feeds per run, never POST or credentials."""

    def __init__(self, *, opener: Any = None) -> None:
        self.opener = opener if opener is not None else build_opener(NoRedirects())
        self.items: dict[str, bytes] = {}
        self.total_bytes = 0
        self.network_requests = 0
        self.cache_hits = 0

    def open(self, request: Request, *, timeout: int):
        url = request.full_url
        parsed = urlsplit(url)
        if (
            request.get_method() != "GET"
            or parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_FEED_HOSTS
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or timeout > 7
        ):
            raise ValueError(
                "Only bounded official ATS public GET endpoints are allowed"
            )
        if url in self.items:
            self.cache_hits += 1
            return _CachedHTTPResponse(self.items[url], 200)
        if self.network_requests >= 60:
            raise ValueError("Public ATS request budget exhausted")
        response_cap = (
            ASHBY_MAX_BYTES if parsed.hostname == "api.ashbyhq.com" else MAX_BYTES
        )
        self.network_requests += 1
        with self.opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise ValueError("Official ATS read did not return HTTP 200")
            if int(response.headers.get("Content-Length", "0")) > response_cap:
                raise ValueError("Official ATS feed exceeded response cap")
            body = response.read(response_cap + 1)
        if len(body) > response_cap:
            raise ValueError("Official ATS feed exceeded response cap")
        if self.total_bytes + len(body) <= _MAX_MEMORY:
            self.items[url] = body
            self.total_bytes += len(body)
        return _CachedHTTPResponse(body, 200)


def preview_link_enrichment(
    connection: sqlite3.Connection,
    *,
    max_jobs: int = 50,
    max_boards: int = 12,
    allow_public_get: bool = False,
    opener: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only pass, designed for cron/queue execution AFTER live readiness.

    VERIFIED results are unsigned proposals, not trusted DB entries. Never
    present them as production links without approved independent signing.
    """
    if not 1 <= max_jobs <= 100 or not 1 <= max_boards <= 20:
        raise ValueError("Batch job/board budget outside permitted range")
    issued_at = now or datetime.now(timezone.utc)
    if issued_at.tzinfo is None:
        raise ValueError("Batch timestamp must have a timezone")
    rows = connection.execute(
        f"SELECT {','.join(_JOB_COLUMNS)} FROM jobs "
        "WHERE lower(coalesce(source,'')) LIKE '%linkedin%' "
        "ORDER BY id DESC LIMIT ?",
        (max_jobs,),
    ).fetchall()
    board_rows = connection.execute(
        f"SELECT {','.join(_BOARD_COLUMNS)} FROM ats_company_registry "
        "WHERE enabled=1 AND health_status='healthy'"
    ).fetchall()
    all_boards = [dict(zip(_BOARD_COLUMNS, tuple(row))) for row in board_rows]
    approved: dict[str, list[dict[str, Any]]] = {}
    for board in all_boards:
        if board_binding_from_ats_company_registry(board):
            approved.setdefault(normalize(board["company_name"]), []).append(board)

    cache = ATSReadCache(opener=opener) if allow_public_get else None
    seen_board_groups: set[str] = set()
    counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for raw in rows:
        job = dict(zip(_JOB_COLUMNS, tuple(raw)))
        company_key = normalize(job.get("company_name"))
        choices = approved.get(company_key, [])
        if not choices:
            status = "NO_VERIFIED_ATS_BOARD"
            resolution = None
        elif len(choices) != 1:
            status = "MULTIPLE_BOARDS_REVIEW"
            resolution = None
        elif (
            company_key not in seen_board_groups
            and len(seen_board_groups) >= max_boards
        ):
            status = "BOARD_BUDGET_EXHAUSTED"
            resolution = None
        elif not allow_public_get:
            status = "READY_FOR_OFFICIAL_ATS_GET"
            resolution = None
        else:
            seen_board_groups.add(company_key)
            resolution = resolve_from_known_company_boards(
                job,
                choices,
                allow_network=True,
                provider_opener=cache,
            )
            status = resolution.resolution_status
        counts[status] += 1
        if len(samples) < 20:
            samples.append({"job_id": int(job["id"]), "status": status})
        if resolution is not None and status == "VERIFIED":
            proposals.append(
                {
                    "job_id": int(job["id"]),
                    "verified_at": issued_at.astimezone(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "resolution": resolution.to_dict(),
                    "proof_status": "UNSIGNED_PROPOSAL_ONLY",
                }
            )
    return {
        "contract": "munshi-linkedin-enrichment-preview-v6",
        "mode": "PUBLIC_GET_READ_ONLY" if allow_public_get else "OFFLINE_READ_ONLY",
        "jobs_scanned": len(rows),
        "distinct_known_boards_queried": len(seen_board_groups),
        "status_counts": dict(sorted(counts.items())),
        "sample": samples,
        "unsigned_verified_proposals": proposals,
        "network_requests": cache.network_requests if cache else 0,
        "public_api_cache_hits": cache.cache_hits if cache else 0,
        "database_writes": 0,
        "applications_submitted": 0,
        "autorun_or_scheduler_installed": False,
        "requires_signer_before_dashboard": True,
    }
