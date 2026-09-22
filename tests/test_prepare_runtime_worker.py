from __future__ import annotations

import pytest

from app import autonomous_prepare_v1, prepare_runtime_worker


@pytest.fixture(autouse=True)
def isolate_runtime_health(monkeypatch):
    monkeypatch.setattr(
        prepare_runtime_worker.prepare_orchestrator_v1,
        "recover_expired_leases",
        lambda **_kwargs: {"expired": 0, "requeued": 0, "failed": 0},
    )
    monkeypatch.setattr(prepare_runtime_worker, "_write_heartbeat", lambda _result: None)


def test_run_once_delegates_only_to_open_prepare_runtime(monkeypatch) -> None:
    calls = []

    def drive_open_operations(
        *, limit: int, newest_first: bool, min_created_at: str | None
    ):
        calls.append((limit, newest_first, min_created_at))
        return {
            "success": True,
            "operation_count": 2,
            "results": [],
            "errors": [],
            "submission_authority": False,
        }

    monkeypatch.setattr(
        prepare_runtime_worker.autonomous_prepare_v1,
        "drive_open_operations",
        drive_open_operations,
    )

    result = prepare_runtime_worker.run_once(limit=7)

    assert calls == [(5, True, None)]
    assert result["success"] is True
    assert result["submission_authority"] is False


def test_run_once_bounds_operation_batch(monkeypatch) -> None:
    limits = []

    monkeypatch.setattr(
        prepare_runtime_worker.autonomous_prepare_v1,
        "drive_open_operations",
        lambda *, limit, newest_first, min_created_at: limits.append(
            (limit, newest_first, min_created_at)
        ) or {
            "success": True,
            "operation_count": 0,
            "results": [],
            "errors": [],
            "submission_authority": False,
        },
    )

    prepare_runtime_worker.run_once(limit=0)
    prepare_runtime_worker.run_once(limit=999)

    assert limits == [(1, True, None), (5, True, None)]


def test_run_once_passes_activation_watermark(monkeypatch) -> None:
    calls = []
    monkeypatch.setenv(
        "MUNSHI_PREPARE_RUNTIME_MIN_CREATED_AT",
        "2026-09-18T19:45:00+00:00",
    )
    monkeypatch.setattr(
        prepare_runtime_worker.autonomous_prepare_v1,
        "drive_open_operations",
        lambda **kwargs: calls.append(kwargs) or {
            "success": True,
            "operation_count": 0,
            "results": [],
            "errors": [],
            "submission_authority": False,
        },
    )

    prepare_runtime_worker.run_once()

    assert calls == [{
        "limit": 5,
        "newest_first": True,
        "min_created_at": "2026-09-18T19:45:00+00:00",
    }]


def test_run_once_does_not_serialize_prepare_behind_source_discovery(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        prepare_runtime_worker,
        "_source_runner_status",
        lambda: {"state": "active", "pid": 4321},
    )
    monkeypatch.setattr(
        prepare_runtime_worker.autonomous_prepare_v1,
        "drive_open_operations",
        lambda **kwargs: calls.append(kwargs) or {
            "success": True,
            "operation_count": 2,
            "results": [],
            "errors": [],
            "submission_authority": False,
        },
    )

    result = prepare_runtime_worker.run_once(limit=3)

    assert calls == [{"limit": 3, "newest_first": True, "min_created_at": None}]
    assert result["success"] is True
    assert result["operation_count"] == 2
    assert result["source_runner"] == {"state": "active", "pid": 4321}


def test_one_bad_operation_does_not_prevent_next_operation(monkeypatch) -> None:
    class Connection:
        def execute(self, _sql, _params):
            return self

        def fetchall(self):
            return [("prepare-a",), ("prepare-b",)]

        def close(self):
            return None

    monkeypatch.setattr(autonomous_prepare_v1, "get_connection", lambda: Connection())
    monkeypatch.setattr(
        autonomous_prepare_v1,
        "current_owner",
        lambda _connection: type("Owner", (), {"tenant_id": "t", "user_id": "u"})(),
    )
    monkeypatch.setattr(autonomous_prepare_v1.orchestrator, "ensure_schema", lambda: None)

    def drive(operation_id):
        if operation_id == "prepare-a":
            raise RuntimeError("malformed fixture")
        return {"operation_id": operation_id, "status": "RUNNING"}

    monkeypatch.setattr(autonomous_prepare_v1, "drive_operation_once", drive)

    result = autonomous_prepare_v1.drive_open_operations(limit=5)

    assert [item["operation_id"] for item in result["results"]] == ["prepare-b"]
    assert result["errors"][0]["operation_id"] == "prepare-a"
