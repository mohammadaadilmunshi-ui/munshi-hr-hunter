"""Explicit, bounded, durable, nonblocking read-only provider synchronization.

Streamlit only records the user's intent in a small, permission-restricted JSON
control file on the existing Hunter data volume. A separate, supervised Python
process performs exact MUNSHI Relay reads, in bounded batches, while the UI
remains responsive. This module never sends/retries applications or mail,
and does not create/alter the schema of hunter.db.

This first-party production dashboard is a singleton. Fail closed if a future
multi-tenant owner tries to share the same control-file queue.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.platform_config import database_path
from app.tenant_foundation import (
    DEFAULT_TENANT_ID, DEFAULT_USER_ID, current_owner_readonly,
)

MODES = frozenset({"history", "receipts"})
ACTIVE_STATES = frozenset({"QUEUED", "RUNNING"})
_HISTORY_PAGE_LIMIT = 500  # At most 5,000 acknowledged records per manual request.
_HISTORY_BATCH_PAGES = 6   # At most 60 authorized provider rows in a worker tick.
_MAX_STATUS_BYTES = 8192
_VERSION = 1


def _owner_digest() -> str:
    owner = current_owner_readonly()
    if (owner.tenant_id, owner.user_id) != (DEFAULT_TENANT_ID, DEFAULT_USER_ID):
        raise PermissionError(
            "The singleton tracker worker cannot process a different user."
        )
    return hashlib.sha256(
        f"{owner.tenant_id}:{owner.user_id}".encode("utf-8")
    ).hexdigest()


def _directory() -> Path:
    # The existing persistent Hunter volume is writable only by the Hunter UID.
    # No modifications to the main SQLite schema or database settings.
    root = database_path().parent
    if not root.exists() or not root.is_dir() or root.is_symlink():
        raise RuntimeError("The Hunter data volume is not available.")
    return root


def _paths(mode: str) -> tuple[Path, Path, Path]:
    if mode not in MODES:
        raise ValueError("Only bounded application readback modes are available.")
    base = _directory() / f"external_sync_{mode}_{_owner_digest()[:16]}_v1"
    return base.with_suffix(".json"), base.with_suffix(".lock"), base.with_suffix(".runlock")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _locked(path: Path, *, nonblocking: bool = False) -> Iterator[bool]:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        op = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        try:
            fcntl.flock(fd, op)
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _load(path: Path, mode: str) -> dict[str, Any]:
    idle = {"version": _VERSION, "mode": mode, "state": "IDLE", "checked": 0}
    try:
        if path.is_symlink():
            raise PermissionError("The sync status path cannot be a symlink.")
        if not path.exists():
            return idle
        with open(path, "rb") as stream:
            data = stream.read(_MAX_STATUS_BYTES + 1)
        if len(data) > _MAX_STATUS_BYTES:
            raise ValueError("The sync status exceeded its safe size.")
        status = json.loads(data)
        if (
            not isinstance(status, dict)
            or status.get("version") != _VERSION
            or status.get("mode") != mode
            or status.get("owner") != _owner_digest()
        ):
            raise ValueError("The sync status identity does not match its owner.")
        return status
    except FileNotFoundError:
        return idle


def _store(path: Path, value: Mapping[str, Any]) -> None:
    raw = json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > _MAX_STATUS_BYTES:
        raise ValueError("The sync status exceeded its safe size.")
    fd, temporary = tempfile.mkstemp(prefix=".external-sync-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_status(mode: str) -> dict[str, Any]:
    path, lock, _ = _paths(mode)
    with _locked(lock) as acquired:
        if not acquired:
            raise RuntimeError("Could not acquire the tracker status lock.")
        return dict(_load(path, mode))


def request_sync(mode: str) -> dict[str, Any]:
    """User-clicked action: writes a safe request only; never contacts a provider."""
    path, lock, _ = _paths(mode)
    with _locked(lock) as acquired:
        if not acquired:
            raise RuntimeError("Could not acquire the tracker status lock.")
        current = _load(path, mode)
        if current.get("state") in ACTIVE_STATES:
            return dict(current)
        # New explicit request restarts from page one. Already-saved rows are
        # deduplicated by exact provider keys; no external job is resubmitted.
        state = {
            "version": _VERSION, "mode": mode, "owner": _owner_digest(),
            "state": "QUEUED", "request_id": secrets.token_hex(16),
            "requested_at": _now(), "updated_at": _now(),
            "checked": 0, "page_next": 1, "provider_total": None,
            "pages_complete": 0, "inserted": 0, "updated": 0, "skipped": 0,
            "error_code": None, "last_page_digest": None,
        }
        _store(path, state)
        return dict(state)


def _patch(mode: str, *, expected_request: str, **fields: Any) -> dict[str, Any]:
    path, lock, _ = _paths(mode)
    with _locked(lock) as acquired:
        if not acquired:
            raise RuntimeError("Could not acquire the tracker status lock.")
        state = _load(path, mode)
        if state.get("request_id") != expected_request:
            raise RuntimeError("The tracker request changed during a worker read.")
        state.update(fields)
        state["updated_at"] = _now()
        _store(path, state)
        return dict(state)


def _error_code(error: Exception) -> str:
    from app.tsenta_oauth_v1 import TsentaOAuthError
    if isinstance(error, TsentaOAuthError):
        return "AUTHORIZATION_UNAVAILABLE"
    if isinstance(error, (TimeoutError, ConnectionError)):
        return "PROVIDER_TIMEOUT"
    if isinstance(error, sqlite3.OperationalError):
        return "STORAGE_BUSY"
    if isinstance(error, (ValueError, TypeError)):
        return "PROVIDER_RESPONSE_UNSUPPORTED"
    return "EXTERNAL_READ_FAILED"


def _run_history(state: dict[str, Any]) -> dict[str, Any]:
    from app.external_application_tracker_v1 import (
        HISTORY_START_DATE, PAGE_SIZE, _rows, persist_tracked_rows,
    )
    from app.tsenta_mcp_adapter_v1 import _unwrap
    from app.tsenta_mcp_http_v1 import TsentaMcpReadClient
    from app.tsenta_oauth_v1 import _token_for_current_owner

    request = str(state["request_id"])
    current = _patch("history", expected_request=request, state="RUNNING")
    with TsentaMcpReadClient(_token_for_current_owner()) as mcp:
        mcp.initialize()
        for _ in range(_HISTORY_BATCH_PAGES):
            page = int(current["page_next"])
            if page > _HISTORY_PAGE_LIMIT:
                return _patch("history", expected_request=request, state="PARTIAL")
            payload = _unwrap(mcp.call_read("list_tracked_applications", {
                "page": page, "limit": PAGE_SIZE, "startDate": HISTORY_START_DATE,
            }), tool_name="list_tracked_applications")
            if not isinstance(payload, Mapping):
                raise TypeError("Unsupported provider metadata.")
            records = _rows(payload)
            pagination = payload.get("pagination")
            if not isinstance(pagination, Mapping):
                pagination = {}
            provider_total = pagination.get("total")
            if not isinstance(provider_total, int) or isinstance(provider_total, bool) or not 0 <= provider_total <= 1_000_000:
                provider_total = current.get("provider_total")
            page_digest = hashlib.sha256(
                json.dumps([row.get("id") for row in records], separators=(",", ":")).encode()
            ).hexdigest()
            if page > 1 and page_digest == current.get("last_page_digest"):
                return _patch("history", expected_request=request, state="PARTIAL", error_code="PROVIDER_PAGINATION_REPEATED")
            result = persist_tracked_rows(records)
            current = _patch(
                "history", expected_request=request, checked=int(current["checked"]) + len(records),
                inserted=int(current["inserted"]) + int(result["inserted"]),
                updated=int(current["updated"]) + int(result["updated"]),
                skipped=int(current["skipped"]) + int(result["skipped"]),
                pages_complete=int(current["pages_complete"]) + 1,
                page_next=page + 1, provider_total=provider_total,
                last_page_digest=page_digest,
            )
            if len(records) < PAGE_SIZE or pagination.get("hasMore") is False:
                return _patch("history", expected_request=request, state="COMPLETE")
        if int(current["page_next"]) > _HISTORY_PAGE_LIMIT:
            return _patch("history", expected_request=request, state="PARTIAL")
    return current


def _run_receipts(state: dict[str, Any]) -> dict[str, Any]:
    from app.relay_application_dispatch_v1 import reconcile_owner_pending
    request = str(state["request_id"])
    _patch("receipts", expected_request=request, state="RUNNING")
    # Existing reader only; it cannot request, reserve, retry or send any job.
    result = reconcile_owner_pending(limit=10)
    return _patch(
        "receipts", expected_request=request, state="COMPLETE",
        checked=int(result.get("checked") or 0),
        employer_confirmed=int(result.get("employer_confirmed") or 0),
        provider_errors=int(result.get("errors") or 0),
    )


def worker_once() -> dict[str, Any]:
    """One bounded worker iteration; called by the existing Hunter supervisor."""
    for mode in ("receipts", "history"):
        path, _, runlock = _paths(mode)
        with _locked(runlock, nonblocking=True) as acquired:
            if not acquired:
                continue
            state = read_status(mode)
            if state.get("state") not in ACTIVE_STATES:
                continue
            request = str(state.get("request_id") or "")
            if not request:
                continue
            try:
                if mode == "receipts":
                    return _run_receipts(state)
                return _run_history(state)
            except Exception as error:
                # Do not log message bodies, OAuth tokens, provider payloads or
                # raw user-supplied company/role strings.
                return _patch(mode, expected_request=request,
                              state="ERROR", error_code=_error_code(error))
    return {"state": "IDLE"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args(argv)
    if not args.worker:
        parser.error("This module is a supervised read-only worker.")
    result = worker_once()
    if result.get("state") != "IDLE":
        print("external tracker sync:", result.get("state"),
              "checked:", int(result.get("checked") or 0), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
