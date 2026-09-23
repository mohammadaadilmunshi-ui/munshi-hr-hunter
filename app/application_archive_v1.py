"""Durable, reversible Application Center visibility state.

Archiving never changes lifecycle truth or deletes jobs, artifacts, approvals,
receipts, or Prepare history.  Every explicit archive/restore action appends an
owner-scoped audit event.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable
from uuid import uuid4

from app.database import get_connection
from app.tenant_foundation import current_owner, current_owner_readonly


SCHEMA = """
CREATE TABLE IF NOT EXISTS application_visibility_v1 (
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
    archived_at TEXT,
    restored_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(tenant_id,user_id,application_id)
);
CREATE INDEX IF NOT EXISTS idx_application_visibility_v1_active
ON application_visibility_v1(tenant_id,user_id,archived,updated_at DESC);

CREATE TABLE IF NOT EXISTS application_visibility_events_v1 (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('ARCHIVED','RESTORED')),
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_application_visibility_events_v1_application
ON application_visibility_events_v1(tenant_id,user_id,application_id,occurred_at DESC);
"""


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        connection.executescript(SCHEMA)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _assert_application_binding(
    connection: sqlite3.Connection,
    *,
    tenant_id: str,
    user_id: str,
    application_id: str,
    job_id: int,
) -> None:
    found = False
    for table in ("prepare_operations_v1", "application_lifecycle_records"):
        if not _table_exists(connection, table):
            continue
        row = connection.execute(
            f"""SELECT 1 FROM {table}
                  WHERE tenant_id=? AND user_id=?
                    AND application_id=? AND job_id=? LIMIT 1""",
            (tenant_id, user_id, application_id, int(job_id)),
        ).fetchone()
        if row is not None:
            found = True
            break
    if not found:
        raise LookupError("The application identity is not bound to this job and owner.")


def _set_archived(*, application_id: str, job_id: int, archived: bool) -> dict[str, Any]:
    resolved_application_id = str(application_id or "").strip()
    if not resolved_application_id:
        raise ValueError("Application id is required.")
    if int(job_id) < 1:
        raise ValueError("Job id must be positive.")
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        _assert_application_binding(
            connection,
            tenant_id=owner.tenant_id,
            user_id=owner.user_id,
            application_id=resolved_application_id,
            job_id=int(job_id),
        )
        event_type = "ARCHIVED" if archived else "RESTORED"
        connection.execute(
            """INSERT INTO application_visibility_v1(
                   tenant_id,user_id,application_id,job_id,archived,
                   archived_at,restored_at,updated_at
               ) VALUES (?,?,?,?,?,
                   CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE NULL END,
                   CASE WHEN ?=0 THEN CURRENT_TIMESTAMP ELSE NULL END,
                   CURRENT_TIMESTAMP)
               ON CONFLICT(tenant_id,user_id,application_id) DO UPDATE SET
                   job_id=excluded.job_id,
                   archived=excluded.archived,
                   archived_at=CASE WHEN excluded.archived=1 THEN CURRENT_TIMESTAMP ELSE application_visibility_v1.archived_at END,
                   restored_at=CASE WHEN excluded.archived=0 THEN CURRENT_TIMESTAMP ELSE application_visibility_v1.restored_at END,
                   updated_at=CURRENT_TIMESTAMP""",
            (
                owner.tenant_id,
                owner.user_id,
                resolved_application_id,
                int(job_id),
                int(archived),
                int(archived),
                int(archived),
            ),
        )
        connection.execute(
            """INSERT INTO application_visibility_events_v1(
                   event_id,tenant_id,user_id,application_id,job_id,event_type
               ) VALUES (?,?,?,?,?,?)""",
            (
                f"application-visibility-{uuid4()}",
                owner.tenant_id,
                owner.user_id,
                resolved_application_id,
                int(job_id),
                event_type,
            ),
        )
        row = connection.execute(
            """SELECT * FROM application_visibility_v1
                WHERE tenant_id=? AND user_id=? AND application_id=?""",
            (owner.tenant_id, owner.user_id, resolved_application_id),
        ).fetchone()
        connection.commit()
        return dict(row)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def archive_application(*, application_id: str, job_id: int) -> dict[str, Any]:
    return _set_archived(application_id=application_id, job_id=job_id, archived=True)


def restore_application(*, application_id: str, job_id: int) -> dict[str, Any]:
    return _set_archived(application_id=application_id, job_id=job_id, archived=False)


def visibility_for(application_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    identifiers = tuple(dict.fromkeys(str(value).strip() for value in application_ids if str(value).strip()))
    if not identifiers:
        return {}
    connection = get_connection()
    try:
        if not _table_exists(connection, "application_visibility_v1"):
            return {}
        owner = current_owner_readonly(connection)
        placeholders = ",".join("?" for _ in identifiers)
        rows = connection.execute(
            f"""SELECT application_id,job_id,archived,archived_at,restored_at,updated_at
                  FROM application_visibility_v1
                 WHERE tenant_id=? AND user_id=?
                   AND application_id IN ({placeholders})""",
            (owner.tenant_id, owner.user_id, *identifiers),
        ).fetchall()
        return {str(row["application_id"]): dict(row) for row in rows}
    finally:
        connection.close()


def audit_events(application_id: str) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        if not _table_exists(connection, "application_visibility_events_v1"):
            return []
        owner = current_owner_readonly(connection)
        return [
            dict(row)
            for row in connection.execute(
                """SELECT event_id,application_id,job_id,event_type,occurred_at
                      FROM application_visibility_events_v1
                     WHERE tenant_id=? AND user_id=? AND application_id=?
                     ORDER BY occurred_at,rowid""",
                (owner.tenant_id, owner.user_id, str(application_id)),
            ).fetchall()
        ]
    finally:
        connection.close()
