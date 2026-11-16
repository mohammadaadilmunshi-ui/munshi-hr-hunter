"""Regression coverage: fair route rotation and single-flight status polling."""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
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
        # Store only a single internal checkpoint ID, not the job inventory,
        # external provider IDs, URLs, credentials or application answers.
        checkpoint = json.loads(cursor.read_text())
        assert set(checkpoint) == {"version", "last_job_id"}
        assert checkpoint["version"] == 2
        assert checkpoint["last_job_id"] in (1001, 2001)
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


def test_new_jobs_cannot_permanently_starve_older_jobs(tmp_path: Path):
    # Ten new jobs appear at the front of an updated-at-descending list
    # between every pair of polls. A positional cursor repeats 22..13 forever.
    checkpoint = tmp_path / "hunter.sqlite"
    visited = []
    for cycle in range(7):
        descending_jobs = list(range(22 + cycle * 10, 0, -1))
        with receipt_poll_window(
            tenant_id="t", user_id="u", db_path=checkpoint,
            jobs=descending_jobs, limit=10,
        ) as batch:
            visited.extend(batch)
    assert set(range(1, 23)).issubset(visited)


def test_removed_job_cursor_wraps_to_remaining_jobs(tmp_path: Path):
    kwargs = dict(
        tenant_id="t", user_id="u", db_path=tmp_path / "hunter.sqlite",
        limit=2,
    )
    with receipt_poll_window(jobs=[1, 2, 3, 4], **kwargs) as first:
        assert first == [1, 2]
    # Previous cursor anchor has disappeared; continue from the next ID.
    with receipt_poll_window(jobs=[1, 3, 4], **kwargs) as second:
        assert second == [3, 4]
    with receipt_poll_window(jobs=[1, 3, 4], **kwargs) as third:
        assert third == [1, 3]


def test_crashed_page_can_be_revisited_after_cursor_cycle(tmp_path: Path):
    kwargs = dict(
        tenant_id="t", user_id="u", db_path=tmp_path / "hunter.sqlite",
        jobs=[1, 2, 3, 4], limit=2,
    )
    try:
        with receipt_poll_window(**kwargs) as first:
            assert first == [1, 2]
            raise RuntimeError("synthetic process failure before readback")
    except RuntimeError:
        pass
    with receipt_poll_window(**kwargs) as next_page:
        assert next_page == [3, 4]
    with receipt_poll_window(**kwargs) as retry_page:
        assert retry_page == [1, 2]


def test_second_process_does_not_poll_same_owner_simultaneously(tmp_path: Path):
    # The host cron and Hunter supervisor are distinct operating-system
    # processes; verify the actual flock boundary rather than one-process
    # nested contexts alone.
    db_path = tmp_path / "hunter.sqlite"
    child_code = '''
import sys
from pathlib import Path
from app.relay_receipt_poll_cursor_v1 import receipt_poll_window
with receipt_poll_window(
    tenant_id="t", user_id="u", db_path=Path(sys.argv[1]),
    jobs=[1, 2, 3], limit=2,
) as batch:
    print("LOCK_HELD", flush=True)
    sys.stdin.readline()
'''
    proc = subprocess.Popen(
        [sys.executable, "-c", child_code, str(db_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        ready, _, _ = select.select([proc.stdout], [], [], 8)
        assert ready, "Child never acquired the synthetic receipt lease"
        assert proc.stdout.readline().strip() == "LOCK_HELD"
        with receipt_poll_window(
            tenant_id="t", user_id="u", db_path=db_path,
            jobs=[1, 2, 3], limit=2,
        ) as overlap:
            assert overlap is None
    finally:
        assert proc.stdin is not None
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.communicate(timeout=8)
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0
    with receipt_poll_window(
        tenant_id="t", user_id="u", db_path=db_path,
        jobs=[1, 2, 3], limit=2,
    ) as next_batch:
        assert next_batch == [3, 1]
