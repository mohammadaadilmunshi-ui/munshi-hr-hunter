"""Repair recent stored LinkedIn posting URLs without inventing identifiers.

The repair is intentionally narrow:
- only rows whose source indicates LinkedIn;
- only rows first seen within the requested lookback (48 hours by default);
- only trustworthy identifiers already stored by MUNSHI;
- no network calls and no employer interaction.

A posting URL can be recovered from:
1. an existing LinkedIn /jobs/view URL;
2. the stored ats_job_id (JobSpy persists LinkedIn's raw id there);
3. LinkedIn-sourced provenance entries whose external_id or url is recoverable.

Unrecoverable rows remain preserved as evidence and are quarantined from Browse Jobs.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from app.job_store import PROTECTED_STATUSES, create_url_fingerprint
from app.linkedin_url_contract import (
    LINKEDIN_QUARANTINE_REASON,
    canonical_linkedin_job_url,
    direct_application_url,
    is_linkedin_source,
)


REPAIR_EVENT_TYPE = "linkedin_recent_url_backfill_v1"
DEFAULT_LOOKBACK_HOURS = 48


def _text(value: Any) -> str:
    return str(value or "").strip()


def _provenance(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        raw = value
    else:
        try:
            raw = json.loads(_text(value) or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def trusted_linkedin_job_id(row: Mapping[str, Any]) -> str | None:
    """Return only a LinkedIn job id supported by stored source evidence."""
    for candidate in (row.get("job_url"), row.get("ats_job_id")):
        posting = canonical_linkedin_job_url(candidate)
        if posting:
            return posting.rsplit("/", 1)[-1]

    for item in _provenance(row.get("source_provenance_json")):
        if not is_linkedin_source(item.get("source")):
            continue
        for candidate in (
            item.get("url"),
            item.get("external_id"),
            item.get("linkedin_job_id"),
            item.get("ats_job_id"),
        ):
            posting = canonical_linkedin_job_url(candidate)
            if posting:
                return posting.rsplit("/", 1)[-1]
    return None


def _canonical_posting(row: Mapping[str, Any]) -> str | None:
    posting = canonical_linkedin_job_url(row.get("job_url"))
    if posting:
        return posting
    job_id = trusted_linkedin_job_id(row)
    return canonical_linkedin_job_url(job_id=job_id) if job_id else None


def _planned_change(row: Mapping[str, Any]) -> dict[str, Any]:
    posting = _canonical_posting(row)
    direct = direct_application_url(row.get("apply_url"), linkedin_url=posting)
    current_status = _text(row.get("status")).casefold()
    current_reason = _text(row.get("hard_rejection_reason"))

    if posting:
        new_status = _text(row.get("status")) or "found"
        new_reason = row.get("hard_rejection_reason")
        if (
            current_status.startswith("quarantined")
            and current_reason == LINKEDIN_QUARANTINE_REASON
        ):
            new_status = "found"
        if current_reason == LINKEDIN_QUARANTINE_REASON:
            new_reason = None

        fingerprint = create_url_fingerprint(
            {"job_url": posting, "apply_url": direct}
        )
        return {
            "action": "repair",
            "job_url": posting,
            "apply_url": direct,
            "url_fingerprint": fingerprint,
            "status": new_status,
            "hard_rejection_reason": new_reason,
            "linkedin_job_id": posting.rsplit("/", 1)[-1],
        }

    protected = current_status in PROTECTED_STATUSES
    new_status = row.get("status") if protected else "quarantined"
    new_reason = (
        row.get("hard_rejection_reason")
        if current_reason and current_reason != LINKEDIN_QUARANTINE_REASON
        else LINKEDIN_QUARANTINE_REASON
    )
    return {
        "action": "quarantine",
        "job_url": None,
        "apply_url": direct,
        "url_fingerprint": create_url_fingerprint(
            {"job_url": None, "apply_url": direct}
        ),
        "status": new_status,
        "hard_rejection_reason": new_reason,
        "linkedin_job_id": None,
        "protected_status_preserved": protected,
    }


def _different(row: Mapping[str, Any], planned: Mapping[str, Any]) -> bool:
    for key in (
        "job_url",
        "apply_url",
        "url_fingerprint",
        "status",
        "hard_rejection_reason",
    ):
        left = row.get(key)
        right = planned.get(key)
        if (None if left in (None, "") else str(left)) != (
            None if right in (None, "") else str(right)
        ):
            return True
    return False


def recent_linkedin_rows(
    connection: sqlite3.Connection,
    *,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
) -> list[dict[str, Any]]:
    hours = int(lookback_hours)
    if hours < 1 or hours > 168:
        raise ValueError("LinkedIn backfill lookback must be between 1 and 168 hours.")
    rows = connection.execute(
        """
        SELECT
            id, source, ats_job_id, job_url, apply_url, url_fingerprint,
            status, hard_rejection_reason, first_seen_at, last_seen_at,
            source_provenance_json
        FROM jobs
        WHERE lower(COALESCE(source,'')) LIKE '%linkedin%'
          AND datetime(first_seen_at) >= datetime('now', ?)
        ORDER BY first_seen_at ASC, id ASC
        """,
        (f"-{hours} hours",),
    ).fetchall()
    return [dict(row) for row in rows]


def plan_recent_linkedin_backfill(
    connection: sqlite3.Connection,
    *,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
) -> dict[str, Any]:
    rows = recent_linkedin_rows(connection, lookback_hours=lookback_hours)
    planned: list[dict[str, Any]] = []
    counts = {
        "scanned": len(rows),
        "repaired": 0,
        "quarantined": 0,
        "already_valid": 0,
        "unchanged_unrecoverable": 0,
        "protected_status_preserved": 0,
        "changed": 0,
    }

    for row in rows:
        change = _planned_change(row)
        changed = _different(row, change)
        if change["action"] == "repair":
            canonical_now = canonical_linkedin_job_url(row.get("job_url"))
            if not changed and canonical_now:
                counts["already_valid"] += 1
            elif changed:
                counts["repaired"] += 1
        else:
            if changed:
                counts["quarantined"] += 1
            else:
                counts["unchanged_unrecoverable"] += 1
            if change.get("protected_status_preserved"):
                counts["protected_status_preserved"] += 1

        if changed:
            counts["changed"] += 1
        planned.append(
            {
                "job_id": int(row["id"]),
                "first_seen_at": row.get("first_seen_at"),
                "source": row.get("source"),
                "changed": changed,
                **change,
            }
        )

    return {
        "version": REPAIR_EVENT_TYPE,
        "lookback_hours": int(lookback_hours),
        "counts": counts,
        "rows": planned,
    }


def apply_recent_linkedin_backfill(
    connection: sqlite3.Connection,
    *,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    actor: str = "linkedin-recent-backfill",
) -> dict[str, Any]:
    """Apply the planned 48h repair in one SQLite write transaction."""
    plan = plan_recent_linkedin_backfill(
        connection,
        lookback_hours=lookback_hours,
    )
    changed_rows = [row for row in plan["rows"] if row["changed"]]
    if not changed_rows:
        return plan

    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        for row in changed_rows:
            job_id = int(row["job_id"])
            cursor = connection.execute(
                """
                UPDATE jobs
                SET job_url=?,
                    apply_url=?,
                    url_fingerprint=?,
                    status=?,
                    hard_rejection_reason=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                  AND lower(COALESCE(source,'')) LIKE '%linkedin%'
                  AND datetime(first_seen_at) >= datetime('now', ?)
                """,
                (
                    row.get("job_url"),
                    row.get("apply_url"),
                    row.get("url_fingerprint"),
                    row.get("status"),
                    row.get("hard_rejection_reason"),
                    job_id,
                    f"-{int(lookback_hours)} hours",
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RuntimeError(
                    f"LinkedIn 48h backfill lost its scoped row before update: {job_id}"
                )
            stored = connection.execute(
                """
                SELECT job_url,apply_url,url_fingerprint,status,hard_rejection_reason
                FROM jobs WHERE id=?
                """,
                (job_id,),
            ).fetchone()
            if stored is None:
                raise RuntimeError(
                    f"LinkedIn 48h backfill lost its row after update: {job_id}"
                )
            stored_map = dict(stored)
            for field in (
                "job_url",
                "apply_url",
                "url_fingerprint",
                "status",
                "hard_rejection_reason",
            ):
                actual = stored_map.get(field)
                expected = row.get(field)
                if (None if actual in (None, "") else str(actual)) != (
                    None if expected in (None, "") else str(expected)
                ):
                    raise RuntimeError(
                        f"LinkedIn 48h backfill verification failed for "
                        f"job {job_id} field {field}."
                    )

            connection.execute(
                """
                INSERT INTO events(
                    job_id,event_type,actor,event_status,payload_json
                ) VALUES (?,?,?,?,?)
                """,
                (
                    job_id,
                    REPAIR_EVENT_TYPE,
                    str(actor or "linkedin-recent-backfill")[:120],
                    "recorded",
                    json.dumps(
                        {
                            "lookback_hours": int(lookback_hours),
                            "action": row.get("action"),
                            "linkedin_job_id": row.get("linkedin_job_id"),
                            "job_url": row.get("job_url"),
                            "apply_url_present": bool(row.get("apply_url")),
                            "protected_status_preserved": bool(
                                row.get("protected_status_preserved")
                            ),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
        if owns_transaction and connection.in_transaction:
            connection.commit()
    except Exception:
        if owns_transaction and connection.in_transaction:
            connection.rollback()
        raise
    return plan
