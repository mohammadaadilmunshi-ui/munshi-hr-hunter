"""Append-only, job-bound verified LinkedIn employer URL evidence.

V5 store is independent of jobs and application lifecycle tables; never
overwrites jobs.job_url or jobs.apply_url. initialize_schema() is an explicit
write operation intended for controlled migrations only. Read UI is fail-closed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.linkedin_employer_link_resolver_v1 import (
    LinkResolution,
    external_url,
)
from app.linkedin_url_contract import canonical_linkedin_job_url

_TABLE = "linkedin_employer_url_evidence_v5"
_ALLOWED_SOURCES = frozenset(
    {"verified_first_party_careers", "official_public_ats", "ats_company_registry"}
)
MAX_AGE = timedelta(days=7)
DDL = """
CREATE TABLE IF NOT EXISTS linkedin_employer_url_evidence_v5 (
    evidence_id TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL,
    job_identity_sha256 TEXT NOT NULL,
    linkedin_job_url TEXT NOT NULL,
    employer_job_url TEXT NOT NULL,
    employer_apply_url TEXT NOT NULL,
    ats_platform TEXT NOT NULL,
    resolution_source TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'VERIFIED'),
    submit_authorized INTEGER NOT NULL DEFAULT 0 CHECK(submit_authorized=0)
);
CREATE INDEX IF NOT EXISTS idx_linkedin_verified_url_evidence_v5_job
ON linkedin_employer_url_evidence_v5(job_id, created_at DESC);
"""


def _utc(ts: datetime) -> datetime:
    if not isinstance(ts, datetime) or ts.tzinfo is None:
        raise ValueError("Evidence timestamp must be timezone-aware")
    return ts.astimezone(timezone.utc)


def _fingerprint(job: Mapping[str, Any]) -> str:
    fields = (
        str(job.get("id") or "").strip(),
        str(job.get("company_name") or "").casefold().strip(),
        str(job.get("title") or "").casefold().strip(),
        str(job.get("job_url") or "").strip(),
    )
    if not all(fields):
        raise ValueError("Job identity missing")
    return hashlib.sha256(
        json.dumps(fields, separators=(",", ":")).encode()
    ).hexdigest()


def _posting(job: Mapping[str, Any]) -> str:
    if "linkedin" not in str(job.get("source") or "").lower():
        raise ValueError("This evidence contract requires a LinkedIn-sourced job")
    raw = canonical_linkedin_job_url(job.get("job_url"), job_id=job.get("ats_job_id"))
    if not raw:
        raise ValueError("Canonical LinkedIn posting URL missing")
    return raw


def validate_verified_evidence(
    job: Mapping[str, Any],
    result: LinkResolution,
    *,
    verified_at: datetime,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Produce immutable evidence; no DB access, no submit eligibility grant."""
    current = _utc(now or datetime.now(timezone.utc))
    at = _utc(verified_at)
    if at > current + timedelta(minutes=2) or current - at > MAX_AGE:
        raise ValueError(
            "Verified external job-link evidence is expired or future-dated"
        )
    posting = _posting(job)
    if (
        result.resolution_status != "VERIFIED"
        or result.resolution_source not in _ALLOWED_SOURCES
    ):
        raise ValueError("Only official-source VERIFIED evidence may be persisted")
    if result.linkedin_job_url != posting:
        raise ValueError("Employer evidence refers to a different LinkedIn listing")
    link = external_url(result.employer_apply_url)
    employer_job = external_url(result.employer_job_url)
    if not link or not employer_job:
        raise ValueError(
            "Employer application/job URLs are invalid or aggregator-hosted"
        )
    if not result.ats_platform:
        raise ValueError("The verified employer ATS is missing")
    evidence = dict(result.evidence)
    if (
        evidence.get("identity_evidence") != "strong"
        or evidence.get("needs_employer_confirmation") is not False
    ):
        raise ValueError("Employer job identity is not sufficiently corroborated")
    if evidence.get("submit_authorized") is True:
        raise ValueError("Link verification must not grant submission authority")
    if result.resolution_source == "verified_first_party_careers" and (
        not evidence.get("employer_approval_reference")
        or not evidence.get("ats_board_from_official_website")
        or evidence.get("signed_approval_validated") is not True
    ):
        raise ValueError("First-party source lacks verified signed approval provenance")
    if result.resolution_source == "official_public_ats" and not evidence.get(
        "matching_fields"
    ):
        raise ValueError("Official ATS source lacks explicit matching fields")
    if int(job.get("id") or 0) < 1:
        raise ValueError("Persisted job identity must be positive")
    content = {
        "job_id": int(job["id"]),
        "job_identity_sha256": _fingerprint(job),
        "linkedin_job_url": posting,
        "employer_job_url": employer_job,
        "employer_apply_url": link,
        "ats_platform": str(result.ats_platform),
        "resolution_source": result.resolution_source,
        "provenance_json": json.dumps(evidence, sort_keys=True, separators=(",", ":")),
        "verified_at": at.isoformat(timespec="seconds"),
        "status": "VERIFIED",
        "submit_authorized": 0,
    }
    evidence_id = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"evidence_id": evidence_id, **content}


def initialize_schema(
    connection: sqlite3.Connection, *, allow_migration: bool = False
) -> None:
    """Explicit migration gate. Never called by read-only UI / dry-run."""
    if not allow_migration:
        raise PermissionError("Evidence schema migration must be explicitly authorized")
    connection.executescript(DDL)


def _table_exists(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)
        ).fetchone()
        is not None
    )


def latest_verified_for_job(
    connection: sqlite3.Connection,
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Read-only, fail-closed projection; no schema creation or DB mutation."""
    if not _table_exists(connection):
        return None
    try:
        fingerprint = _fingerprint(job)
        posting = _posting(job)
    except (ValueError, TypeError):
        return None
    rows = connection.execute(
        f"SELECT * FROM {_TABLE} WHERE job_id=? ORDER BY created_at DESC, evidence_id DESC LIMIT 20",
        (int(job["id"]),),
    ).fetchall()
    now_utc = _utc(now or datetime.now(timezone.utc))
    for row in rows:
        data = (
            dict(row)
            if isinstance(row, sqlite3.Row)
            else dict(
                zip(
                    [
                        c[0]
                        for c in connection.execute(
                            f"SELECT * FROM {_TABLE} LIMIT 0"
                        ).description
                    ],
                    row,
                )
            )
        )
        if (
            data["job_identity_sha256"] != fingerprint
            or data["linkedin_job_url"] != posting
        ):
            continue
        try:
            verified_at = _utc(datetime.fromisoformat(data["verified_at"]))
        except (ValueError, TypeError):
            continue
        if now_utc - verified_at > MAX_AGE or verified_at > now_utc + timedelta(
            minutes=2
        ):
            continue
        if data["status"] != "VERIFIED" or data["submit_authorized"] != 0:
            continue
        if not external_url(data["employer_apply_url"]):
            continue
        return data
    return None


def persist_verified_evidence(
    connection: sqlite3.Connection,
    job: Mapping[str, Any],
    result: LinkResolution,
    *,
    verified_at: datetime,
    now: datetime | None = None,
    allow_write: bool = False,
) -> dict[str, Any]:
    """Only after controlled migration and explicit write approval.

    Append-only and idempotent. Reject competing currently verified links
    instead of silently replacing them.
    """
    if not allow_write:
        raise PermissionError(
            "Link evidence persistence requires explicit write approval"
        )
    if not _table_exists(connection):
        raise RuntimeError("Verified URL evidence migration is not installed")
    # Confirm the persisted job still matches the candidate presented by the
    # resolver. A caller cannot write evidence for a made-up/stale job ID.
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone():
        raise RuntimeError("Canonical jobs table is unavailable")
    current = connection.execute(
        "SELECT id, source, company_name, title, job_url, ats_job_id "
        "FROM jobs WHERE id=?",
        (int(job.get("id") or 0),),
    ).fetchone()
    if current is None:
        raise ValueError("Canonical job was not found")
    if isinstance(current, sqlite3.Row):
        actual = dict(current)
    else:
        actual = dict(
            zip(
                ("id", "source", "company_name", "title", "job_url", "ats_job_id"),
                current,
            )
        )
    if _fingerprint(actual) != _fingerprint(job) or _posting(actual) != _posting(job):
        raise ValueError("Persisted canonical job differs from proposed link evidence")
    content = validate_verified_evidence(job, result, verified_at=verified_at, now=now)
    existing = latest_verified_for_job(connection, job, now=now)
    if existing and existing["employer_apply_url"] != content["employer_apply_url"]:
        raise ValueError(
            "A different currently verified URL already exists; manual reconciliation required"
        )
    previous = connection.execute(
        f"SELECT evidence_id FROM {_TABLE} WHERE evidence_id=?",
        (content["evidence_id"],),
    ).fetchone()
    if previous:
        return {"evidence_id": content["evidence_id"], "replayed": True}
    row = dict(content)
    row["created_at"] = _utc(now or datetime.now(timezone.utc)).isoformat(
        timespec="seconds"
    )
    keys = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    connection.execute(
        f"INSERT INTO {_TABLE} ({keys}) VALUES ({placeholders})",
        tuple(row.values()),
    )
    return {"evidence_id": content["evidence_id"], "replayed": False}


def destination_buttons(
    job: Mapping[str, Any],
    verified: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """UI-facing projection; never invent or automatically submit a URL."""
    is_linkedin = "linkedin" in str(job.get("source") or "").lower()
    if not is_linkedin:
        return {"is_linkedin": False}
    try:
        original = _posting(job)
    except (ValueError, TypeError):
        original = None
    employer_url = None
    if verified:
        try:
            if (
                verified.get("job_identity_sha256") == _fingerprint(job)
                and verified.get("linkedin_job_url") == original
                and verified.get("status") == "VERIFIED"
                and verified.get("submit_authorized") == 0
                and external_url(verified.get("employer_apply_url"))
            ):
                employer_url = str(verified["employer_apply_url"])
        except (ValueError, TypeError, AttributeError):
            pass
    return {
        "is_linkedin": True,
        "linkedin_job_url": original,
        "employer_apply_url": employer_url,
        "employer_link_state": "VERIFIED" if employer_url else "UNAVAILABLE",
        "prepare_action": "INTERNAL_MUNSHI",
        "submit_authorized": False,
    }
