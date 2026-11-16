"""Regression coverage: fair route rotation and single-flight status polling."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from app import database
from app import relay_application_dispatch_v1 as relay
from app.relay_receipt_poll_cursor_v1 import receipt_poll_window


def test_rotates_all_jobs_and_preserves_cursor_across_calls(tmp_path: Path):
    path = tmp_path / "hunter.sqlite"
    kwargs = {"tenant_id": "t", "user_id": "u", "db_path": path}
    jobs = list(range(1, 23))
    batches = []
    for _ in range(3):
        with receipt_poll_window(jobs=jobs, limit=10, **kwargs) as batch:
            batches.append(batch)
    assert batches == [
        list(range(1, 11)),
        list(range(11, 21)),
        [21, 22, 1, 2, 3, 4, 5, 6, 7, 8],
    ]
    assert set(batches[0] + batches[1] + batches[2]) == set(jobs)


def test_concurrent_poller_skips_instead_of_double_processing(tmp_path: Path):
    kwargs = {"tenant_id": "t", "user_id": "u", "db_path": tmp_path / "hunter.sqlite"}
    with receipt_poll_window(jobs=[1, 2, 3], limit=2, **kwargs) as first:
        assert first == [1, 2]
        with receipt_poll_window(jobs=[1, 2, 3], limit=2, **kwargs) as contender:
            assert contender is None
    with receipt_poll_window(jobs=[1, 2, 3], limit=2, **kwargs) as next_batch:
        assert next_batch == [3, 1]


def test_owner_cursor_is_separate_and_does_not_store_job_values(tmp_path: Path):
    path = tmp_path / "hunter.sqlite"
    with receipt_poll_window(
        tenant_id="one", user_id="same", db_path=path, jobs=[1001, 1002], limit=1
    ) as first:
        assert first == [1001]
    with receipt_poll_window(
        tenant_id="two", user_id="same", db_path=path, jobs=[2001, 2002], limit=1
    ) as second:
        assert second == [2001]
    cursors = list(tmp_path.glob(".relay-receipt-poll-*.cursor"))
    assert len(cursors) == 2
    for cursor in cursors:
        content = cursor.read_text()
        assert "1001" not in content and "2001" not in content
        assert os.stat(cursor).st_mode & 0o777 == 0o600


def test_dispatcher_rotates_and_avoids_repeated_first_ten(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "hunter.sqlite")
    viewed = []
    jobs = list(range(1, 23))

    class DB:
        def execute(self, statement, params=()):
            assert "SELECT DISTINCT r.job_id" in statement
            assert "LIMIT ?" not in statement
            return [{"job_id": item} for item in jobs]

        def close(self):
            pass

    monkeypatch.setattr(relay, "get_connection", DB)
    monkeypatch.setattr(
        relay, "current_owner",
        lambda _db: SimpleNamespace(tenant_id="t", user_id="u"),
    )

    def readback(job_id):
        viewed.append(job_id)
        return {"status": "PROVIDER_SUBMITTED", "retry_allowed": False}

    monkeypatch.setattr(relay, "reconcile_from_dashboard", readback)
    results = [relay.reconcile_owner_pending(limit=10) for _ in range(3)]
    assert [r["checked"] for r in results] == [10, 10, 10]
    assert all(r["applications_sent"] == 0 for r in results)
    assert viewed[:10] == list(range(1, 11))
    assert viewed[10:20] == list(range(11, 21))
    assert set(viewed) == set(jobs)
