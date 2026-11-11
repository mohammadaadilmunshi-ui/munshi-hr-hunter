"""Progressive, evidence-safe storage lifecycle for MUNSHI Hunter.

The normal retention path is intentionally conservative:

* old discovery-only jobs may be tombstoned after the configured stale window;
* saved, applied, prepared, resume-bound, application-bound and otherwise
  evidence-linked jobs are protected from age-based deletion;
* tombstones have a grace period before hard purge;
* every run is audited and supports dry-run mode and bounded batches;
* database maintenance uses PRAGMA optimize and incremental vacuum only when the
  database is already configured for incremental auto-vacuum. Routine cleanup
  never performs a blocking full VACUUM.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RetentionPolicy:
    stale_job_days: int = 30
    tombstone_grace_days: int = 7
    batch_limit: int = 500
    incremental_vacuum_pages: int = 200

    def __post_init__(self) -> None:
        if not 25 <= self.stale_job_days <= 3650:
            raise ValueError("stale_job_days must be between 25 and 3650")
        if not 1 <= self.tombstone_grace_days <= 90:
            raise ValueError("tombstone_grace_days must be between 1 and 90")
        if not 1 <= self.batch_limit <= 5000:
            raise ValueError("batch_limit must be between 1 and 5000")
        if not 0 <= self.incremental_vacuum_pages <= 5000:
            raise ValueError("incremental_vacuum_pages must be between 0 and 5000")


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS storage_retention_tombstones(
          job_id INTEGER PRIMARY KEY,
          reason TEXT NOT NULL,
          marked_at TEXT NOT NULL,
          purge_after TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('MARKED','RESTORED','PURGED','BLOCKED')),
          last_run_id TEXT NOT NULL,
          last_error TEXT,
          purged_at TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_storage_tombstones_due
        ON storage_retention_tombstones(state,purge_after);

        CREATE TABLE IF NOT EXISTS storage_retention_runs(
          run_id TEXT PRIMARY KEY,
          dry_run INTEGER NOT NULL CHECK(dry_run IN (0,1)),
          stale_job_days INTEGER NOT NULL,
          grace_days INTEGER NOT NULL,
          batch_limit INTEGER NOT NULL,
          candidate_count INTEGER NOT NULL DEFAULT 0,
          marked_count INTEGER NOT NULL DEFAULT 0,
          restored_count INTEGER NOT NULL DEFAULT 0,
          purged_count INTEGER NOT NULL DEFAULT 0,
          blocked_count INTEGER NOT NULL DEFAULT 0,
          database_bytes_before INTEGER NOT NULL DEFAULT 0,
          database_bytes_after INTEGER NOT NULL DEFAULT 0,
          reclaimable_bytes_before INTEGER NOT NULL DEFAULT 0,
          reclaimable_bytes_after INTEGER NOT NULL DEFAULT 0,
          details_json TEXT NOT NULL DEFAULT '{}',
          started_at TEXT NOT NULL,
          completed_at TEXT
        );
        """
    )


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _storage_stats(connection: sqlite3.Connection) -> dict[str, int]:
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    freelist = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
    return {
        "page_count": page_count,
        "page_size": page_size,
        "freelist_count": freelist,
        "database_bytes": page_count * page_size,
        "reclaimable_bytes": freelist * page_size,
    }


def _protected_reference_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Find durable product-evidence tables that explicitly carry job_id."""
    protected_markers = (
        "application",
        "resume",
        "receipt",
        "plan",
        "preparation",
        "review",
    )
    explicit = {"n8n_results"}
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    result: list[str] = []
    for row in rows:
        table = str(row[0])
        if table.startswith("storage_retention_"):
            continue
        if "job_id" not in _columns(connection, table):
            continue
        lowered = table.lower()
        if table in explicit or any(marker in lowered for marker in protected_markers):
            result.append(table)
    return tuple(sorted(set(result)))


def _job_has_protected_reference(
    connection: sqlite3.Connection,
    job_id: int,
    reference_tables: tuple[str, ...],
) -> bool:
    for table in reference_tables:
        if connection.execute(
            f'SELECT 1 FROM "{table}" WHERE job_id=? LIMIT 1',
            (job_id,),
        ).fetchone():
            return True
    return False


def _job_is_user_saved(connection: sqlite3.Connection, job_id: int) -> bool:
    if not _table_exists(connection, "product_job_state"):
        return False
    row = connection.execute(
        "SELECT saved FROM product_job_state WHERE job_id=?",
        (job_id,),
    ).fetchone()
    return bool(row and row[0])


def _eligible(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    reference_tables: tuple[str, ...],
) -> tuple[bool, str]:
    job_id = int(row["id"])
    if bool(row["already_applied"]):
        return False, "already_applied"
    if _job_is_user_saved(connection, job_id):
        return False, "user_saved"
    if _job_has_protected_reference(connection, job_id, reference_tables):
        return False, "durable_product_evidence"
    return True, "stale_discovery_only"


def _stale_rows(
    connection: sqlite3.Connection,
    *,
    policy: RetentionPolicy,
) -> list[sqlite3.Row]:
    modifier = f"-{policy.stale_job_days} days"
    return connection.execute(
        """
        SELECT id, already_applied, status, last_seen_at, updated_at, created_at
        FROM jobs
        WHERE datetime(COALESCE(last_seen_at,updated_at,created_at)) < datetime('now', ?)
        ORDER BY datetime(COALESCE(last_seen_at,updated_at,created_at)) ASC, id ASC
        LIMIT ?
        """,
        (modifier, policy.batch_limit),
    ).fetchall()


def _due_tombstones(connection: sqlite3.Connection, *, limit: int) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT t.*, j.id AS live_job_id, j.already_applied, j.status,
               j.last_seen_at, j.updated_at, j.created_at
        FROM storage_retention_tombstones t
        LEFT JOIN jobs j ON j.id=t.job_id
        WHERE t.state='MARKED' AND datetime(t.purge_after) <= datetime('now')
        ORDER BY datetime(t.purge_after), t.job_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def _run_maintenance(connection: sqlite3.Connection, policy: RetentionPolicy) -> None:
    connection.execute("PRAGMA optimize")
    if policy.incremental_vacuum_pages <= 0:
        return
    auto_vacuum = int(connection.execute("PRAGMA auto_vacuum").fetchone()[0])
    if auto_vacuum == 2:  # incremental
        connection.execute(f"PRAGMA incremental_vacuum({policy.incremental_vacuum_pages})")


def run_storage_lifecycle(
    *,
    policy: RetentionPolicy | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark and, after grace, purge stale discovery-only jobs.

    The default is deliberately dry-run. Hard deletion only occurs when dry_run
    is False and the job remains eligible after its grace period.
    """
    from app.database import get_connection

    policy = policy or RetentionPolicy()
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    run_id = str(uuid4())
    connection = get_connection()
    try:
        ensure_schema(connection)
        before = _storage_stats(connection)
        reference_tables = _protected_reference_tables(connection)
        candidates = _stale_rows(connection, policy=policy)
        eligible_rows: list[sqlite3.Row] = []
        protected: dict[str, int] = {}
        for row in candidates:
            eligible, reason = _eligible(connection, row, reference_tables)
            if eligible:
                eligible_rows.append(row)
            else:
                protected[reason] = protected.get(reason, 0) + 1

        marked = restored = purged = blocked = 0
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO storage_retention_runs(
              run_id,dry_run,stale_job_days,grace_days,batch_limit,candidate_count,
              database_bytes_before,reclaimable_bytes_before,details_json,started_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                1 if dry_run else 0,
                policy.stale_job_days,
                policy.tombstone_grace_days,
                policy.batch_limit,
                len(candidates),
                before["database_bytes"],
                before["reclaimable_bytes"],
                json.dumps(
                    {
                        "protected": protected,
                        "reference_tables": list(reference_tables),
                        "eligible_job_ids": [int(row["id"]) for row in eligible_rows],
                    },
                    sort_keys=True,
                ),
                timestamp.isoformat(),
            ),
        )

        if not dry_run:
            eligible_ids = {int(row["id"]) for row in eligible_rows}
            purge_after = (timestamp + timedelta(days=policy.tombstone_grace_days)).isoformat()
            for row in eligible_rows:
                job_id = int(row["id"])
                connection.execute(
                    """
                    INSERT INTO storage_retention_tombstones(
                      job_id,reason,marked_at,purge_after,state,last_run_id,last_error,purged_at
                    ) VALUES(?,?,?,?, 'MARKED', ?,NULL,NULL)
                    ON CONFLICT(job_id) DO UPDATE SET
                      reason=excluded.reason,
                      state=CASE
                        WHEN storage_retention_tombstones.state='PURGED' THEN 'PURGED'
                        ELSE 'MARKED'
                      END,
                      last_run_id=excluded.last_run_id,
                      updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        job_id,
                        "stale_discovery_only",
                        timestamp.isoformat(),
                        purge_after,
                        run_id,
                    ),
                )
                marked += 1

            marked_rows = connection.execute(
                "SELECT job_id FROM storage_retention_tombstones WHERE state='MARKED'"
            ).fetchall()
            for marked_row in marked_rows:
                job_id = int(marked_row["job_id"])
                live = connection.execute(
                    "SELECT id,already_applied,status,last_seen_at,updated_at,created_at FROM jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                if live is None:
                    continue
                eligible_now, _ = _eligible(connection, live, reference_tables)
                stale_now = connection.execute(
                    """
                    SELECT datetime(COALESCE(last_seen_at,updated_at,created_at)) < datetime('now', ?)
                    FROM jobs WHERE id=?
                    """,
                    (f"-{policy.stale_job_days} days", job_id),
                ).fetchone()
                if not eligible_now or not bool(stale_now and stale_now[0]):
                    connection.execute(
                        """
                        UPDATE storage_retention_tombstones
                        SET state='RESTORED',last_run_id=?,updated_at=CURRENT_TIMESTAMP
                        WHERE job_id=? AND state='MARKED'
                        """,
                        (run_id, job_id),
                    )
                    restored += 1

            for tombstone in _due_tombstones(connection, limit=policy.batch_limit):
                job_id = int(tombstone["job_id"])
                if tombstone["live_job_id"] is None:
                    connection.execute(
                        """
                        UPDATE storage_retention_tombstones
                        SET state='PURGED',purged_at=COALESCE(purged_at,?),last_run_id=?,updated_at=CURRENT_TIMESTAMP
                        WHERE job_id=?
                        """,
                        (timestamp.isoformat(), run_id, job_id),
                    )
                    continue
                live = connection.execute(
                    "SELECT id,already_applied,status,last_seen_at,updated_at,created_at FROM jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                if live is None:
                    continue
                eligible_now, reason = _eligible(connection, live, reference_tables)
                if not eligible_now:
                    connection.execute(
                        """
                        UPDATE storage_retention_tombstones
                        SET state='RESTORED',last_run_id=?,last_error=?,updated_at=CURRENT_TIMESTAMP
                        WHERE job_id=?
                        """,
                        (run_id, f"protected:{reason}", job_id),
                    )
                    restored += 1
                    continue
                try:
                    result = connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
                    if result.rowcount == 1:
                        connection.execute(
                            """
                            UPDATE storage_retention_tombstones
                            SET state='PURGED',purged_at=?,last_run_id=?,last_error=NULL,updated_at=CURRENT_TIMESTAMP
                            WHERE job_id=?
                            """,
                            (timestamp.isoformat(), run_id, job_id),
                        )
                        purged += 1
                except sqlite3.IntegrityError:
                    connection.execute(
                        """
                        UPDATE storage_retention_tombstones
                        SET state='BLOCKED',last_run_id=?,last_error='foreign_key_protected',updated_at=CURRENT_TIMESTAMP
                        WHERE job_id=?
                        """,
                        (run_id, job_id),
                    )
                    blocked += 1

        connection.commit()
        if not dry_run and (marked or restored or purged or blocked):
            _run_maintenance(connection, policy)
        after = _storage_stats(connection)
        connection.execute(
            """
            UPDATE storage_retention_runs
            SET marked_count=?,restored_count=?,purged_count=?,blocked_count=?,
                database_bytes_after=?,reclaimable_bytes_after=?,completed_at=?
            WHERE run_id=?
            """,
            (
                marked,
                restored,
                purged,
                blocked,
                after["database_bytes"],
                after["reclaimable_bytes"],
                datetime.now(UTC).isoformat(),
                run_id,
            ),
        )
        connection.commit()
        return {
            "run_id": run_id,
            "dry_run": dry_run,
            "policy": {
                "stale_job_days": policy.stale_job_days,
                "tombstone_grace_days": policy.tombstone_grace_days,
                "batch_limit": policy.batch_limit,
            },
            "candidate_count": len(candidates),
            "eligible_count": len(eligible_rows),
            "protected": protected,
            "marked_count": marked,
            "restored_count": restored,
            "purged_count": purged,
            "blocked_count": blocked,
            "database_bytes": after["database_bytes"],
            "reclaimable_bytes": after["reclaimable_bytes"],
            "protected_reference_tables": list(reference_tables),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
