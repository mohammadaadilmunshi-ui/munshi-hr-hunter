from __future__ import annotations

from app import prepare_runtime_worker


def test_run_once_delegates_only_to_open_prepare_runtime(monkeypatch) -> None:
    calls = []

    def drive_open_operations(*, limit: int, newest_first: bool):
        calls.append((limit, newest_first))
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

    assert calls == [(5, True)]
    assert result["success"] is True
    assert result["submission_authority"] is False


def test_run_once_bounds_operation_batch(monkeypatch) -> None:
    limits = []

    monkeypatch.setattr(
        prepare_runtime_worker.autonomous_prepare_v1,
        "drive_open_operations",
        lambda *, limit, newest_first: limits.append((limit, newest_first)) or {
            "success": True,
            "operation_count": 0,
            "results": [],
            "errors": [],
            "submission_authority": False,
        },
    )

    prepare_runtime_worker.run_once(limit=0)
    prepare_runtime_worker.run_once(limit=999)

    assert limits == [(1, True), (5, True)]
