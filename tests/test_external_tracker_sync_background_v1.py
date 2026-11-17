"""Durable, safe, non-blocking Relay readback tests; no real network or submissions."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import external_tracker_sync_background_v1 as queue
from app import external_application_tracker_v1 as tracker


@pytest.fixture
def isolated_sync(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(queue, "database_path", lambda: tmp_path / "hunter.db")
    monkeypatch.setattr(
        queue, "current_owner_readonly",
        lambda: SimpleNamespace(tenant_id="default", user_id="local-owner"),
    )
    return tmp_path


def test_user_click_only_enqueues_safe_job_and_deduplicates(isolated_sync):
    initial = queue.read_status("history")
    assert initial["state"] == "IDLE"
    request = queue.request_sync("history")
    assert request["state"] == "QUEUED"
    assert request["checked"] == 0
    assert request["page_next"] == 1
    assert queue.request_sync("history") == request
    assert queue.request_sync("receipts")["state"] == "QUEUED"
    assert queue.read_status("history")["mode"] == "history"
    path, _, _ = queue._paths("history")
    assert os.stat(path).st_mode & 0o777 == 0o600
    data = path.read_text()
    assert "access_token" not in data
    assert "companyName" not in data
    assert "positionTitle" not in data
    assert "password" not in data
    assert "applicationId" not in data


def test_repeated_clicks_do_not_restart_running_or_overwrite_progress(isolated_sync):
    state = queue.request_sync("history")
    advanced = queue._patch(
        "history", expected_request=state["request_id"],
        state="RUNNING", checked=70, page_next=8,
    )
    assert queue.request_sync("history") == advanced
    assert queue.read_status("history")["checked"] == 70


def test_completed_job_can_be_explicitly_requested_again(isolated_sync):
    state = queue.request_sync("history")
    queue._patch("history", expected_request=state["request_id"], state="COMPLETE")
    new = queue.request_sync("history")
    assert new["state"] == "QUEUED"
    assert new["checked"] == 0
    assert new["page_next"] == 1
    assert new["request_id"] != state["request_id"]


def test_status_never_crosses_owner_boundary(isolated_sync, monkeypatch):
    queue.request_sync("history")
    monkeypatch.setattr(
        queue, "current_owner_readonly",
        lambda: SimpleNamespace(tenant_id="another", user_id="user-two"),
    )
    with pytest.raises(PermissionError):
        queue.read_status("history")
    with pytest.raises(PermissionError):
        queue.request_sync("history")


def test_invalid_readback_modes_are_rejected_before_filesystem_use(isolated_sync):
    for name in ("submit", "../history", "apply_to_job", "mail", ""):
        with pytest.raises(ValueError):
            queue.request_sync(name)


def test_worker_is_idle_without_explicit_request(isolated_sync, monkeypatch):
    monkeypatch.setattr(
        queue, "_run_history",
        lambda *_: pytest.fail("worker must not read without authorization"),
    )
    assert queue.worker_once()["state"] == "IDLE"


def test_worker_reads_exact_provider_stages_in_bounded_batches(
    isolated_sync, monkeypatch,
):
    from app import tsenta_mcp_http_v1 as http
    from app import tsenta_oauth_v1 as oauth

    calls = []
    imported = []

    class FakeClient:
        def __init__(self, token):
            assert token == "injected-nonproduction-token"
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def initialize(self):
            return None
        def call_read(self, name, arguments):
            calls.append((name, dict(arguments)))
            assert name == "list_tracked_applications"
            page = arguments["page"]
            assert page in (1, 2)
            size = 10 if page == 1 else 2
            return {
                "applications": [
                    {"id": f"synthetic-{page}-{i}", "status": "APPLIED"}
                    for i in range(size)
                ],
                "pagination": {"total": 12, "hasMore": page == 1},
            }

    def fake_persist(rows):
        imported.extend(rows)
        return {"inserted": len(rows), "updated": 0, "skipped": 0}

    monkeypatch.setattr(http, "TsentaMcpReadClient", FakeClient)
    monkeypatch.setattr(oauth, "_token_for_current_owner", lambda: "injected-nonproduction-token")
    monkeypatch.setattr(tracker, "persist_tracked_rows", fake_persist)
    queue.request_sync("history")
    result = queue.worker_once()
    assert result["state"] == "COMPLETE"
    assert result["checked"] == 12
    assert result["inserted"] == 12
    assert result["pages_complete"] == 2
    assert result["page_next"] == 3
    assert len(calls) == 2
    assert all(args["limit"] == 10 for _, args in calls)
    assert all(args["startDate"] == "2020-01-01" for _, args in calls)
    persisted = queue.read_status("history")
    assert persisted["state"] == "COMPLETE"
    assert "synthetic-" not in json.dumps(persisted)
    assert len(imported) == 12


def test_provider_receipt_worker_never_submits_or_retries(
    isolated_sync, monkeypatch,
):
    from app import relay_application_dispatch_v1 as dispatch
    seen = []
    def readback_only(*, limit):
        seen.append(limit)
        return {"checked": 4, "employer_confirmed": 1, "errors": 0}
    monkeypatch.setattr(dispatch, "reconcile_owner_pending", readback_only)
    queue.request_sync("receipts")
    result = queue.worker_once()
    assert seen == [10]
    assert result["state"] == "COMPLETE"
    assert result["checked"] == 4
    assert result["employer_confirmed"] == 1


def test_database_busy_cannot_turn_into_a_false_success(isolated_sync, monkeypatch):
    def storage_busy(_):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(queue, "_run_history", storage_busy)
    queue.request_sync("history")
    result = queue.worker_once()
    assert result["state"] == "ERROR"
    assert result["error_code"] == "STORAGE_BUSY"
    assert queue.read_status("history")["state"] == "ERROR"


def test_worker_request_is_bounded_to_current_owner_and_cannot_be_replaced(
    isolated_sync,
):
    one = queue.request_sync("history")
    with pytest.raises(RuntimeError):
        queue._patch("history", expected_request="forged", state="COMPLETE")
    assert queue.read_status("history") == one


def test_wrong_identity_or_oversized_status_is_not_trusted(isolated_sync):
    queue.request_sync("history")
    path, _, _ = queue._paths("history")
    original = json.loads(path.read_text())
    original["owner"] = "other-tenant"
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError):
        queue.read_status("history")
    path.write_bytes(b" " * 8300)
    with pytest.raises(ValueError):
        queue.read_status("history")
