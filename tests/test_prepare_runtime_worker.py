from __future__ import annotations

from app import prepare_runtime_worker


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
        "limit": 1,
        "newest_first": True,
        "min_created_at": "2026-09-18T19:45:00+00:00",
    }]


def test_run_once_defers_cleanly_while_discovery_owns_source_lock(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        prepare_runtime_worker,
        "_source_runner_status",
        lambda: {"state": "active", "pid": 4321},
    )
    monkeypatch.setattr(
        prepare_runtime_worker.autonomous_prepare_v1,
        "drive_open_operations",
        lambda **kwargs: calls.append(kwargs),
    )

    result = prepare_runtime_worker.run_once(limit=3)

    assert calls == []
    assert result == {
        "success": True,
        "status": "deferred",
        "reason": "source_discovery_active",
        "source_runner_pid": 4321,
        "operation_count": 0,
        "results": [],
        "errors": [],
        "submission_authority": False,
    }
