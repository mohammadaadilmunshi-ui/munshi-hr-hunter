from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from app import stale_worker_reconciler_v1 as reconciler


class _Cursor:
    def __init__(self, *, rows=(), row=None):
        self._rows = list(rows)
        self._row = row

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, *, busy: bool):
        self.busy = busy
        self.row_factory = None
        self.events: list[str] = []

    def execute(self, statement: str, _parameters=()):
        normalized = " ".join(statement.split()).upper()
        if normalized.startswith("SELECT H.SOURCE_NAME"):
            return _Cursor(rows=[])
        if normalized == "BEGIN IMMEDIATE":
            self.events.append("begin")
            if self.busy:
                raise sqlite3.OperationalError("database is locked")
            return _Cursor()
        if normalized == "PRAGMA QUICK_CHECK":
            self.events.append("quick_check")
            return _Cursor(row=("ok",))
        return _Cursor()

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")

    def close(self):
        self.events.append("close")


def _install_runtime_stubs(monkeypatch, tmp_path, connection: _Connection) -> None:
    monkeypatch.setattr(reconciler, "DB", tmp_path / "hunter.db")
    monkeypatch.setattr(reconciler, "LOCK_PATH", tmp_path / "reconciler.lock")
    monkeypatch.setattr(
        reconciler.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=""),
    )
    monkeypatch.setattr(reconciler.sqlite3, "connect", lambda *_args, **_kwargs: connection)


def test_writer_contention_defers_without_crashing_discovery(monkeypatch, tmp_path) -> None:
    connection = _Connection(busy=True)
    _install_runtime_stubs(monkeypatch, tmp_path, connection)

    result = reconciler.reconcile_dashboard_stale_workers()

    assert result["success"] is True
    assert result["deferred"] is True
    assert result["reason"] == "sqlite_writer_busy"
    assert result["database_integrity"] == "deferred"
    assert connection.events == ["begin", "rollback", "close"]


def test_database_check_runs_after_short_writer_transaction(monkeypatch, tmp_path) -> None:
    connection = _Connection(busy=False)
    _install_runtime_stubs(monkeypatch, tmp_path, connection)

    result = reconciler.reconcile_dashboard_stale_workers()

    assert result["success"] is True
    assert result["database_integrity"] == "ok"
    assert connection.events.index("commit") < connection.events.index("quick_check")
    assert connection.events[-1] == "close"
