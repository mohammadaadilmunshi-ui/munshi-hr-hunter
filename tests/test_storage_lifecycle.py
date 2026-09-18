from __future__ import annotations

from app.storage_lifecycle import RetentionPolicy, run_storage_lifecycle


def _job(connection, fingerprint: str, *, saved: bool = False, applied: bool = False) -> int:
    cursor = connection.execute(
        """
        INSERT INTO jobs(
          job_fingerprint,source,company_name,title,already_applied,
          first_seen_at,last_seen_at,created_at,updated_at
        ) VALUES(?,?,?,?,?,'2000-01-01T00:00:00+00:00','2000-01-01T00:00:00+00:00',
                 '2000-01-01T00:00:00+00:00','2000-01-01T00:00:00+00:00')
        """,
        (fingerprint, "test", "Example", "Role", 1 if applied else 0),
    )
    job_id = int(cursor.lastrowid)
    if saved:
        connection.execute(
            "INSERT INTO product_job_state(job_id,saved) VALUES(?,1)",
            (job_id,),
        )
    return job_id


def test_retention_defaults_are_conservative():
    policy = RetentionPolicy()
    assert policy.stale_job_days == 30
    assert policy.tombstone_grace_days == 7
    assert policy.batch_limit == 500


def test_dry_run_never_mutates_jobs_or_tombstones(hunter_db):
    from app.database import get_connection
    from app.product_state import ensure_schema as ensure_product_schema

    connection = get_connection()
    try:
        ensure_product_schema(connection)
        job_id = _job(connection, "retention-dry-run")
        connection.commit()
    finally:
        connection.close()

    result = run_storage_lifecycle(dry_run=True)
    assert result["eligible_count"] >= 1
    assert result["marked_count"] == 0
    assert result["purged_count"] == 0

    connection = get_connection()
    try:
        assert connection.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert not connection.execute(
            "SELECT 1 FROM storage_retention_tombstones WHERE job_id=?", (job_id,)
        ).fetchone()
    finally:
        connection.close()


def test_saved_applied_and_application_evidence_jobs_are_protected(hunter_db):
    from app.database import get_connection
    from app.product_state import ensure_schema as ensure_product_schema

    connection = get_connection()
    try:
        ensure_product_schema(connection)
        saved_id = _job(connection, "retention-saved", saved=True)
        applied_id = _job(connection, "retention-applied", applied=True)
        evidence_id = _job(connection, "retention-evidence")
        connection.execute(
            "CREATE TABLE application_test_evidence(id INTEGER PRIMARY KEY,job_id INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO application_test_evidence(job_id) VALUES(?)", (evidence_id,)
        )
        connection.commit()
    finally:
        connection.close()

    result = run_storage_lifecycle(dry_run=False)
    assert result["protected"].get("user_saved", 0) >= 1
    assert result["protected"].get("already_applied", 0) >= 1
    assert result["protected"].get("durable_product_evidence", 0) >= 1

    connection = get_connection()
    try:
        marked = {
            int(row[0])
            for row in connection.execute(
                "SELECT job_id FROM storage_retention_tombstones WHERE state='MARKED'"
            ).fetchall()
        }
        assert saved_id not in marked
        assert applied_id not in marked
        assert evidence_id not in marked
    finally:
        connection.close()


def test_discovery_only_job_is_tombstoned_then_purged_after_grace(hunter_db):
    from app.database import get_connection
    from app.product_state import ensure_schema as ensure_product_schema

    connection = get_connection()
    try:
        ensure_product_schema(connection)
        job_id = _job(connection, "retention-purge")
        connection.commit()
    finally:
        connection.close()

    first = run_storage_lifecycle(
        policy=RetentionPolicy(stale_job_days=30, tombstone_grace_days=7, batch_limit=50),
        dry_run=False,
    )
    assert first["marked_count"] >= 1

    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT state,purge_after FROM storage_retention_tombstones WHERE job_id=?",
            (job_id,),
        ).fetchone()
        assert row is not None and row["state"] == "MARKED"
        assert connection.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
        connection.execute(
            "UPDATE storage_retention_tombstones SET purge_after='2000-01-02T00:00:00+00:00' WHERE job_id=?",
            (job_id,),
        )
        connection.commit()
    finally:
        connection.close()

    second = run_storage_lifecycle(
        policy=RetentionPolicy(stale_job_days=30, tombstone_grace_days=7, batch_limit=50),
        dry_run=False,
    )
    assert second["purged_count"] >= 1

    connection = get_connection()
    try:
        assert not connection.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
        tombstone = connection.execute(
            "SELECT state,purged_at FROM storage_retention_tombstones WHERE job_id=?",
            (job_id,),
        ).fetchone()
        assert tombstone["state"] == "PURGED"
        assert tombstone["purged_at"]
    finally:
        connection.close()


def test_job_becoming_saved_during_grace_is_restored_not_deleted(hunter_db):
    from app.database import get_connection
    from app.product_state import ensure_schema as ensure_product_schema

    connection = get_connection()
    try:
        ensure_product_schema(connection)
        job_id = _job(connection, "retention-restore")
        connection.commit()
    finally:
        connection.close()

    run_storage_lifecycle(dry_run=False)
    connection = get_connection()
    try:
        connection.execute(
            "INSERT INTO product_job_state(job_id,saved) VALUES(?,1)", (job_id,)
        )
        connection.execute(
            "UPDATE storage_retention_tombstones SET purge_after='2000-01-02T00:00:00+00:00' WHERE job_id=?",
            (job_id,),
        )
        connection.commit()
    finally:
        connection.close()

    result = run_storage_lifecycle(dry_run=False)
    assert result["restored_count"] >= 1

    connection = get_connection()
    try:
        assert connection.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
        state = connection.execute(
            "SELECT state FROM storage_retention_tombstones WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        assert state == "RESTORED"
    finally:
        connection.close()
