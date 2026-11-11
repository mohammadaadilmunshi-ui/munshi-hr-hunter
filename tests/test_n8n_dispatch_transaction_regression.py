from __future__ import annotations

import sqlite3

from app import n8n_dispatch


def test_dispatch_transaction_commits_setup_before_begin_immediate() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE sample(value INTEGER)")
        connection.execute("INSERT INTO sample(value) VALUES (1)")
        assert connection.in_transaction is True

        n8n_dispatch._begin_dispatch_transaction(connection)

        # The helper must leave the caller inside the deliberate dispatch
        # transaction, while the setup write has already been committed.
        assert connection.in_transaction is True
        connection.rollback()
        assert connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 1
    finally:
        connection.close()


def test_queue_candidates_tolerates_setup_transaction(hunter_db, monkeypatch) -> None:
    def _plan_with_setup_write(connection: sqlite3.Connection) -> dict:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS dispatch_setup_probe(value INTEGER)"
        )
        connection.execute("INSERT INTO dispatch_setup_probe(value) VALUES (1)")
        assert connection.in_transaction is True
        return {
            "threshold": 95.0,
            "auto_limit": 0,
            "product_volume_mode": "paused",
            "enabled_lane_count": 0,
            "manual_limit": 0,
            "auto_used_today": 0,
            "manual_used_today": 0,
            "manual_candidates": [],
            "auto_candidates": [],
        }

    monkeypatch.setattr(n8n_dispatch, "plan_candidates", _plan_with_setup_write)

    result = n8n_dispatch.queue_candidates(webhook_mode="test", dry_run=False)

    assert result["success"] is True
    assert result["selected_count"] == 0
    assert result["queued"] == []


def test_stored_job_worker_dispatch_helper_import_contract() -> None:
    # The stored-job worker imports this symbol lazily during Prepare.  This
    # assertion guards the exact production failure: ImportError on Prepare.
    helper = getattr(n8n_dispatch, "_begin_dispatch_transaction", None)
    assert callable(helper)
