from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from app import application_archive_v1 as archive


OWNER = SimpleNamespace(tenant_id="tenant-fixture", user_id="user-fixture")


def _connection_factory(path: Path):
    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    return connect


def test_archive_refresh_history_and_restore_preserve_underlying_records(tmp_path, monkeypatch) -> None:
    path = tmp_path / "archive.sqlite"
    connect = _connection_factory(path)
    monkeypatch.setattr(archive, "get_connection", connect)
    monkeypatch.setattr(archive, "current_owner", lambda _connection: OWNER)
    monkeypatch.setattr(archive, "current_owner_readonly", lambda _connection: OWNER)

    connection = connect()
    connection.executescript(
        """
        CREATE TABLE jobs(id INTEGER PRIMARY KEY, title TEXT NOT NULL);
        CREATE TABLE prepare_operations_v1(
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE resume_evidence(
            application_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL
        );
        """
    )
    connection.execute("INSERT INTO jobs VALUES (101,'People Analyst')")
    connection.execute(
        "INSERT INTO prepare_operations_v1 VALUES (?,?,?,?,?,?)",
        (OWNER.tenant_id, OWNER.user_id, "application-101", "prepare-101", 101, "RUNNING"),
    )
    connection.execute("INSERT INTO resume_evidence VALUES (?,?)", ("application-101", "resume-101"))
    connection.commit()
    connection.close()

    archived = archive.archive_application(application_id="application-101", job_id=101)
    assert archived["archived"] == 1
    assert archive.visibility_for(("application-101",))["application-101"]["archived"] == 1

    # A fresh connection models refresh/navigation/restart persistence.
    connection = connect()
    assert connection.execute("SELECT COUNT(*) FROM jobs WHERE id=101").fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM prepare_operations_v1 WHERE operation_id='prepare-101'"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM resume_evidence WHERE artifact_id='resume-101'"
    ).fetchone()[0] == 1
    connection.close()

    restored = archive.restore_application(application_id="application-101", job_id=101)
    assert restored["archived"] == 0
    assert [event["event_type"] for event in archive.audit_events("application-101")] == [
        "ARCHIVED",
        "RESTORED",
    ]


def test_archive_rejects_mismatched_application_job_binding(tmp_path, monkeypatch) -> None:
    path = tmp_path / "archive-mismatch.sqlite"
    connect = _connection_factory(path)
    monkeypatch.setattr(archive, "get_connection", connect)
    monkeypatch.setattr(archive, "current_owner", lambda _connection: OWNER)

    connection = connect()
    connection.execute(
        """CREATE TABLE prepare_operations_v1(
            tenant_id TEXT,user_id TEXT,application_id TEXT,job_id INTEGER
        )"""
    )
    connection.execute(
        "INSERT INTO prepare_operations_v1 VALUES (?,?,?,?)",
        (OWNER.tenant_id, OWNER.user_id, "application-a", 201),
    )
    connection.commit()
    connection.close()

    try:
        archive.archive_application(application_id="application-a", job_id=202)
    except LookupError as error:
        assert "not bound" in str(error)
    else:
        raise AssertionError("mismatched application/job identity must be rejected")
