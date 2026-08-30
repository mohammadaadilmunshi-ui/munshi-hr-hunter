from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.job_store import save_job
from app.dedupe_policy import dedupe_keeper_decision

TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "source", "src", "ref", "refid", "ref_id", "tracking", "trk",
    "gh_src", "lever-source", "from", "fromage",
}
UNKNOWN_VALUES = {
    "", "unknown", "unknown company", "unknown position", "not specified",
    "none", "nan", "n/a",
}
LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "company", "co", "plc", "group", "holdings",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS job_cross_source_fingerprints (
    fingerprint_kind TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    job_id INTEGER,
    source TEXT,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (fingerprint_kind, fingerprint)
)
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(SCHEMA_SQL)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_job_cross_source_fingerprints_job
        ON job_cross_source_fingerprints(job_id)
        """
    )


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in UNKNOWN_VALUES else text


def _tokens(value: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", _clean(value).lower())


def normalize_company(value: Any) -> str:
    tokens = _tokens(value)
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalize_title(value: Any) -> str:
    return " ".join(_tokens(value))


def normalize_location(value: Any, remote_type: Any = None) -> str:
    combined = f"{_clean(value)} {_clean(remote_type)}".lower()
    if any(term in combined for term in ("remote", "work from home", "wfh")):
        return "remote"
    replacements = {
        "new jersey": "nj",
        "new york": "ny",
        "pennsylvania": "pa",
        "philadelphia": "philadelphia",
    }
    for old, new in replacements.items():
        combined = re.sub(rf"\b{re.escape(old)}\b", new, combined)
    return " ".join(re.findall(r"[a-z0-9]+", combined))


def canonical_url(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except Exception:
        return text
    if not parts.scheme or not parts.netloc:
        return text

    hostname = parts.netloc.lower().removeprefix("www.")
    query_pairs = parse_qsl(parts.query, keep_blank_values=False)
    kept: list[tuple[str, str]] = []
    for key, val in query_pairs:
        key_lower = key.lower()
        if key_lower in TRACKING_QUERY_KEYS or key_lower.startswith("utm_"):
            continue
        # Indeed job identity is carried by jk/vjk. Preserve it.
        if "indeed." in hostname:
            if key_lower in {"jk", "vjk"}:
                kept.append((key_lower, val))
            continue
        kept.append((key, val))

    path = re.sub(r"/+$", "", parts.path or "/")
    return urlunsplit((
        parts.scheme.lower(),
        hostname,
        path,
        urlencode(sorted(kept)),
        "",
    ))


def _hash(parts: list[str]) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def fingerprints(job: dict[str, Any]) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []

    company = normalize_company(job.get("company_name") or job.get("company"))

    url = canonical_url(
        job.get("apply_url") or job.get("job_url") or job.get("url")
    )
    if url:
        output.append(("url", _hash([url])))

    ats_id = _clean(job.get("ats_job_id"))
    if ats_id and len(ats_id) >= 5:
        # Requisition IDs are company-scoped, not provider-scoped.  Using the
        # provider here allowed the same employer requisition to bypass global
        # dedupe when syndicated to LinkedIn, Indeed, or another ATS surface.
        owner = company or _clean(job.get("source")).lower().split("/", 1)[0]
        output.append(("ats", _hash([owner, ats_id.lower()])))

    title = normalize_title(job.get("title") or job.get("job_title"))
    location = normalize_location(
        job.get("location_raw") or job.get("location"),
        job.get("remote_type"),
    )
    if company and title and location:
        output.append(("semantic", _hash([company, title, location])))

    # Stable order, no duplicates.
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for item in output:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _job_row(
    connection: sqlite3.Connection,
    job_id: int | None,
) -> dict[str, Any] | None:
    if job_id is None:
        return None
    try:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ? LIMIT 1",
            (int(job_id),),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    columns = [str(value[0]) for value in (connection.execute("SELECT * FROM jobs LIMIT 0").description or [])]
    return dict(zip(columns, row))


def find_existing_job_id(
    connection: sqlite3.Connection,
    job: dict[str, Any],
) -> tuple[int | None, str | None]:
    """Return only a *currently authoritative* duplicate keeper.

    Historical rejected/blacklisted rows remain preserved, but they are not
    allowed to suppress a newly corrected rediscovery.
    """
    ensure_schema(connection)
    for kind, fingerprint in fingerprints(job):
        row = connection.execute(
            """
            SELECT job_id
            FROM job_cross_source_fingerprints
            WHERE fingerprint_kind = ? AND fingerprint = ?
            """,
            (kind, fingerprint),
        ).fetchone()
        if row is None or row[0] is None:
            continue
        existing_id = int(row[0])
        decision = dedupe_keeper_decision(_job_row(connection, existing_id))
        if decision.get("allowed"):
            return existing_id, kind
    return None, None


def register_job(
    connection: sqlite3.Connection,
    job_id: int | None,
    job: dict[str, Any],
) -> int:
    """Register accepted jobs and lazily repair stale fingerprint ownership.

    A rejected save has no job_id and must never reserve a global fingerprint.
    If an existing fingerprint points to a historical non-keeper, repoint only
    the fingerprint mapping to the newly accepted keeper. The historical job
    row and all downstream evidence remain untouched.
    """
    ensure_schema(connection)
    if job_id is None:
        return 0
    job_id = int(job_id)
    count = 0
    source = _clean(job.get("source"))

    for kind, fingerprint in fingerprints(job):
        existing = connection.execute(
            """
            SELECT job_id
            FROM job_cross_source_fingerprints
            WHERE fingerprint_kind = ? AND fingerprint = ?
            """,
            (kind, fingerprint),
        ).fetchone()

        if existing is None:
            cursor = connection.execute(
                """
                INSERT INTO job_cross_source_fingerprints (
                    fingerprint_kind, fingerprint, job_id, source,
                    first_seen_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (kind, fingerprint, job_id, source),
            )
            count += int(cursor.rowcount or 0)
            continue

        existing_id = existing[0]
        if existing_id is None:
            allowed = False
        elif int(existing_id) == job_id:
            allowed = True
        else:
            allowed = bool(
                dedupe_keeper_decision(_job_row(connection, int(existing_id))).get("allowed")
            )

        if existing_id is None or (int(existing_id) != job_id and not allowed):
            cursor = connection.execute(
                """
                UPDATE job_cross_source_fingerprints
                SET job_id = ?,
                    source = ?,
                    last_seen_at = CURRENT_TIMESTAMP
                WHERE fingerprint_kind = ? AND fingerprint = ?
                """,
                (job_id, source, kind, fingerprint),
            )
            count += int(cursor.rowcount or 0)
        else:
            connection.execute(
                """
                UPDATE job_cross_source_fingerprints
                SET last_seen_at = CURRENT_TIMESTAMP
                WHERE fingerprint_kind = ? AND fingerprint = ?
                """,
                (kind, fingerprint),
            )
    return count


def _touch_existing_job(
    connection: sqlite3.Connection,
    job_id: int,
) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(jobs)")
    }
    assignments = []
    if "last_seen_at" in columns:
        assignments.append("last_seen_at = CURRENT_TIMESTAMP")
    if "updated_at" in columns:
        assignments.append("updated_at = CURRENT_TIMESTAMP")
    if assignments:
        connection.execute(
            f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?",
            (job_id,),
        )


def store_with_global_dedupe(
    connection: sqlite3.Connection,
    job: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    existing_id, matched_kind = find_existing_job_id(connection, job)
    if existing_id is not None:
        _touch_existing_job(connection, existing_id)
        register_job(connection, existing_id, job)
        return {
            "inserted": False,
            "job_id": existing_id,
            "duplicate_reason": f"cross_source_{matched_kind}_fingerprint",
            "global_fingerprint_duplicate": True,
        }

    result = save_job(connection, job, actor=actor)
    if not isinstance(result, dict):
        result = {"inserted": True, "result": result}

    job_id = result.get("job_id") or result.get("id")
    try:
        job_id_int = int(job_id) if job_id is not None else None
    except (TypeError, ValueError):
        job_id_int = None
    register_job(connection, job_id_int, job)
    result.setdefault("global_fingerprint_duplicate", False)
    return result


def backfill_fingerprints(connection: sqlite3.Connection) -> dict[str, int]:
    ensure_schema(connection)
    job_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(jobs)")
    }
    wanted = [
        "id", "source", "ats_job_id", "company_name", "company", "title",
        "job_title", "location_raw", "location", "remote_type",
        "apply_url", "job_url", "url",
    ]
    selected = [column for column in wanted if column in job_columns]
    if "id" not in selected:
        raise RuntimeError("jobs.id is required for fingerprint backfill.")

    rows = connection.execute(
        f"SELECT {', '.join(selected)} FROM jobs ORDER BY id ASC"
    ).fetchall()
    inserted = 0
    scanned = 0
    for row in rows:
        job = dict(zip(selected, row))
        scanned += 1
        inserted += register_job(connection, int(job["id"]), job)
    return {
        "jobs_scanned": scanned,
        "fingerprints_inserted": inserted,
        "fingerprints_total": int(
            connection.execute(
                "SELECT COUNT(*) FROM job_cross_source_fingerprints"
            ).fetchone()[0]
        ),
    }
