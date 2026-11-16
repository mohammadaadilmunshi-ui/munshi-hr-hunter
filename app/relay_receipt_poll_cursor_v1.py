"""Fair cross-process status polling for already-claimed MUNSHI Relay jobs.

This file-based cursor is independent of SQLite writer transactions. Both the
host's cron-invoked process and Hunter's internal supervisor share the same
persistent data volume and therefore the same advisory lock and cursor.

Only existing job IDs are selected. No ATS account, employer, application
submission, provider credential or user answer is accessed here.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from app import database


def _cursor_path(*, tenant_id: str, user_id: str, db_path: Path) -> Path:
    owner_digest = hashlib.sha256(
        f"{tenant_id}\0{user_id}".encode()
    ).hexdigest()[:24]
    return db_path.parent / f".relay-receipt-poll-{owner_digest}.cursor"


@contextmanager
def receipt_poll_window(
    *,
    tenant_id: str,
    user_id: str,
    jobs: Sequence[int],
    limit: int,
    db_path: Path | None = None,
) -> Iterator[list[int] | None]:
    """Yield the next bounded page, or None if another poll is still running.

    The lock remains held while the caller reads back statuses. The cursor is
    persisted *before* network activity, so a failed page never starves other
    jobs. A restarted process resumes from the persisted position.
    """
    if not 1 <= limit <= 20:
        raise ValueError("Receipt polling limit must be between 1 and 20.")
    if not jobs:
        yield []
        return

    path = _cursor_path(
        tenant_id=tenant_id,
        user_id=user_id,
        db_path=Path(db_path) if db_path is not None else database.DB_PATH,
    )
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        acquired = True

        os.lseek(descriptor, 0, os.SEEK_SET)
        prior = os.read(descriptor, 256)
        try:
            stored = json.loads(prior) if prior else {}
            offset = int(stored.get("next_offset", 0))
        except (ValueError, TypeError, AttributeError):
            offset = 0

        total = len(jobs)
        offset %= total
        selected = [
            int(jobs[(offset + index) % total])
            for index in range(min(limit, total))
        ]
        updated = json.dumps({
            "version": 1,
            "next_offset": (offset + len(selected)) % total,
        }, separators=(",", ":")).encode("ascii")
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, updated)
        os.ftruncate(descriptor, len(updated))
        os.fsync(descriptor)

        yield selected
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
