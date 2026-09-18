from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import n8n_dispatch
from app import resume_engine_selector
from app import stored_job_n8n_worker


def test_n8n_engine_choice_calls_proven_stored_job_runner(monkeypatch):
    calls = []

    def fake_start(job_id: int, actor: str = "telegram"):
        calls.append((job_id, actor))
        return {
            "success": True,
            "started": True,
            "message": "n8n production run started.",
        }

    monkeypatch.setattr(
        stored_job_n8n_worker,
        "start_stored_job_run",
        fake_start,
    )

    result = resume_engine_selector._run_n8n(2944)

    assert calls == [(2944, "dashboard")]
    assert result["success"] is True
    assert result["started"] is True


def test_n8n_engine_choice_surfaces_guarded_worker_block(monkeypatch):
    monkeypatch.setattr(
        stored_job_n8n_worker,
        "start_stored_job_run",
        lambda *_args, **_kwargs: {
            "success": False,
            "started": False,
            "message": "The production n8n workflow is already running.",
        },
    )

    with pytest.raises(RuntimeError, match="already running"):
        resume_engine_selector._run_n8n(2944)


def test_stored_job_queue_creation_survives_implicit_setup_transaction(
    monkeypatch,
    tmp_path,
):
    db_path = tmp_path / "worker.sqlite"

    def connection_factory():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def fake_ensure_schema(connection):
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS n8n_dispatch_queue(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                queue_status TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS setup_touch(value INTEGER)"
        )
        connection.execute("INSERT INTO setup_touch(value) VALUES (1)")
        assert connection.in_transaction is True

    def fake_insert_queue_item(
        connection,
        job,
        dispatch_mode,
        webhook_mode,
    ):
        assert dispatch_mode == "telegram_manual"
        assert webhook_mode == "production"
        cursor = connection.execute(
            """
            INSERT INTO n8n_dispatch_queue(job_id,queue_status)
            VALUES (?,?)
            """,
            (int(job["id"]), "pending"),
        )
        row = connection.execute(
            "SELECT * FROM n8n_dispatch_queue WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
        return dict(row)

    monkeypatch.setattr(
        stored_job_n8n_worker,
        "get_connection",
        connection_factory,
    )
    monkeypatch.setattr(
        n8n_dispatch,
        "ensure_schema",
        fake_ensure_schema,
    )
    monkeypatch.setattr(
        n8n_dispatch,
        "insert_queue_item",
        fake_insert_queue_item,
    )

    queue_id, mode = stored_job_n8n_worker._create_or_reuse_queue(
        {"id": 2944}
    )

    assert queue_id == 1
    assert mode == "created"

    connection = connection_factory()
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM setup_touch"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM n8n_dispatch_queue"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_routing_source_no_longer_uses_native_prepare_as_n8n_runner():
    selector = Path("app/resume_engine_selector.py").read_text(
        encoding="utf-8"
    )
    worker = Path("app/stored_job_n8n_worker.py").read_text(
        encoding="utf-8"
    )

    run_start = selector.index("def _run_n8n")
    run_end = selector.index("\ndef _native_readiness", run_start)
    run_body = selector[run_start:run_end]

    assert "start_stored_job_run" in run_body
    assert "_resume_engine_original_n8n_prepare" not in run_body
    assert 'actor="dashboard"' in run_body

    create_start = worker.index("def _create_or_reuse_queue")
    create_end = worker.index("\ndef _dispatch_one", create_start)
    create_body = worker[create_start:create_end]

    assert "_begin_dispatch_transaction(connection)" in create_body
    assert 'connection.execute("BEGIN IMMEDIATE")' not in create_body
