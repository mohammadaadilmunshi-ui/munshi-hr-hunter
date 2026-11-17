"""Prevent live notification worker's repeated DDL from locking active dashboard."""
from __future__ import annotations

import sqlite3

from app import application_notifications_runtime_v3 as notifications


def test_complete_schema_does_not_rerun_legacy_ddl_during_worker_cycle(
    tmp_path, monkeypatch,
):
    db = sqlite3.connect(tmp_path / "ready-notifications.sqlite", timeout=0.1)
    db.row_factory = sqlite3.Row
    try:
        notifications.ensure_schema(db)
        db.commit()
        assert notifications._runtime_schema_ready(db)

        def ddl_should_not_run(*_args, **_kwargs):
            raise AssertionError("hot notification reader must not execute schema setup")

        monkeypatch.setattr(notifications, "ensure_schema", ddl_should_not_run)
        notifications._ensure_runtime_schema_if_needed(db)
        assert notifications._runtime_schema_ready(db)
    finally:
        db.close()


def test_missing_required_index_uses_existing_schema_migration_path(
    tmp_path, monkeypatch,
):
    db = sqlite3.connect(tmp_path / "incomplete-notifications.sqlite")
    db.row_factory = sqlite3.Row
    try:
        notifications.ensure_schema(db)
        db.execute("DROP INDEX idx_application_notification_outbox_owner_v1")
        db.commit()
        assert not notifications._runtime_schema_ready(db)
        checked = []

        def checked_migration(conn):
            assert conn is db
            checked.append(True)

        monkeypatch.setattr(notifications, "ensure_schema", checked_migration)
        notifications._ensure_runtime_schema_if_needed(db)
        assert checked == [True]
    finally:
        db.close()
