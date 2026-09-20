from __future__ import annotations

import json
import sqlite3

from app import linkedin_recent_backfill as backfill
from app.linkedin_url_contract import LINKEDIN_QUARANTINE_REASON


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            ats_job_id TEXT,
            job_url TEXT,
            apply_url TEXT,
            url_fingerprint TEXT,
            status TEXT NOT NULL DEFAULT 'found',
            hard_rejection_reason TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT,
            source_provenance_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            event_status TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    return connection


def _insert(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    source: str = "JobSpy/linkedin",
    ats_job_id: str | None = None,
    job_url: str | None = None,
    apply_url: str | None = None,
    status: str = "found",
    reason: str | None = None,
    age: str = "-1 hour",
    provenance: object = (),
) -> None:
    connection.execute(
        """
        INSERT INTO jobs(
            id,source,ats_job_id,job_url,apply_url,url_fingerprint,status,
            hard_rejection_reason,first_seen_at,last_seen_at,source_provenance_json
        )
        VALUES (?,?,?,?,?,?,?, ?,datetime('now',?),CURRENT_TIMESTAMP,?)
        """,
        (
            job_id,
            source,
            ats_job_id,
            job_url,
            apply_url,
            None,
            status,
            reason,
            age,
            json.dumps(provenance),
        ),
    )
    connection.commit()


def test_repairs_recent_job_from_jobspy_ats_id() -> None:
    connection = _db()
    _insert(
        connection,
        job_id=1,
        ats_job_id="4466640197",
        apply_url="https://boards.greenhouse.io/example/jobs/123",
    )

    result = backfill.apply_recent_linkedin_backfill(connection)
    row = dict(connection.execute("SELECT * FROM jobs WHERE id=1").fetchone())

    assert result["counts"]["repaired"] == 1
    assert row["job_url"] == "https://www.linkedin.com/jobs/view/4466640197"
    assert row["apply_url"] == "https://boards.greenhouse.io/example/jobs/123"
    assert row["url_fingerprint"]
    assert connection.execute(
        "SELECT COUNT(*) FROM events WHERE job_id=1 AND event_type=?",
        (backfill.REPAIR_EVENT_TYPE,),
    ).fetchone()[0] == 1


def test_uses_only_linkedin_sourced_provenance_external_id() -> None:
    connection = _db()
    _insert(
        connection,
        job_id=1,
        provenance=[
            {
                "source": "JobSpy/linkedin",
                "external_id": "4466640198",
                "url": "",
            }
        ],
    )
    _insert(
        connection,
        job_id=2,
        provenance=[
            {
                "source": "USAJobs",
                "external_id": "4466640199",
                "url": "",
            }
        ],
    )

    plan = backfill.plan_recent_linkedin_backfill(connection)
    rows = {row["job_id"]: row for row in plan["rows"]}

    assert rows[1]["job_url"] == "https://www.linkedin.com/jobs/view/4466640198"
    assert rows[1]["action"] == "repair"
    assert rows[2]["job_url"] is None
    assert rows[2]["action"] == "quarantine"


def test_does_not_touch_non_linkedin_or_jobs_older_than_48_hours() -> None:
    connection = _db()
    _insert(
        connection,
        job_id=1,
        source="JobSpy/indeed",
        ats_job_id="4466640200",
    )
    _insert(
        connection,
        job_id=2,
        ats_job_id="4466640201",
        age="-49 hours",
    )

    plan = backfill.plan_recent_linkedin_backfill(connection)

    assert plan["counts"]["scanned"] == 0
    assert plan["rows"] == []


def test_unrecoverable_recent_linkedin_job_is_quarantined() -> None:
    connection = _db()
    _insert(
        connection,
        job_id=1,
        ats_job_id="linkedin-unknown",
        apply_url="https://jobs.example.com/apply/abc",
    )

    result = backfill.apply_recent_linkedin_backfill(connection)
    row = dict(connection.execute("SELECT * FROM jobs WHERE id=1").fetchone())

    assert result["counts"]["quarantined"] == 1
    assert row["job_url"] is None
    assert row["apply_url"] == "https://jobs.example.com/apply/abc"
    assert row["status"] == "quarantined"
    assert row["hard_rejection_reason"] == LINKEDIN_QUARANTINE_REASON


def test_repair_unquarantines_only_linkedin_link_quarantine() -> None:
    connection = _db()
    _insert(
        connection,
        job_id=1,
        ats_job_id="4466640202",
        status="quarantined",
        reason=LINKEDIN_QUARANTINE_REASON,
    )
    _insert(
        connection,
        job_id=2,
        ats_job_id="4466640203",
        status="quarantined",
        reason="other_rejection",
    )

    backfill.apply_recent_linkedin_backfill(connection)
    first = dict(connection.execute("SELECT * FROM jobs WHERE id=1").fetchone())
    second = dict(connection.execute("SELECT * FROM jobs WHERE id=2").fetchone())

    assert first["status"] == "found"
    assert first["hard_rejection_reason"] is None
    assert second["status"] == "quarantined"
    assert second["hard_rejection_reason"] == "other_rejection"


def test_protected_status_is_not_overwritten_when_unrecoverable() -> None:
    connection = _db()
    _insert(
        connection,
        job_id=1,
        ats_job_id="unknown",
        status="already_applied",
    )

    backfill.apply_recent_linkedin_backfill(connection)
    row = dict(connection.execute("SELECT * FROM jobs WHERE id=1").fetchone())

    assert row["status"] == "already_applied"
    assert row["hard_rejection_reason"] == LINKEDIN_QUARANTINE_REASON


def test_existing_linkedin_listing_is_removed_from_apply_url() -> None:
    connection = _db()
    _insert(
        connection,
        job_id=1,
        ats_job_id="4466640204",
        job_url="https://www.linkedin.com/jobs/view/4466640204?trk=test",
        apply_url="https://www.linkedin.com/jobs/view/4466640204",
    )

    backfill.apply_recent_linkedin_backfill(connection)
    row = dict(connection.execute("SELECT * FROM jobs WHERE id=1").fetchone())

    assert row["job_url"] == "https://www.linkedin.com/jobs/view/4466640204"
    assert row["apply_url"] is None


def test_dry_run_plan_never_mutates_database() -> None:
    connection = _db()
    _insert(connection, job_id=1, ats_job_id="4466640205")

    plan = backfill.plan_recent_linkedin_backfill(connection)
    row = dict(connection.execute("SELECT * FROM jobs WHERE id=1").fetchone())

    assert plan["counts"]["repaired"] == 1
    assert row["job_url"] is None
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
