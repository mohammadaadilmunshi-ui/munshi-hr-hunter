from __future__ import annotations

from app import unified_hourly_coordinator as coordinator


def test_unified_coordinator_cannot_launch_source_workers(monkeypatch, hunter_db) -> None:
    monkeypatch.setattr(coordinator, "load_enabled_sources", lambda: ["Ashby"])
    monkeypatch.setattr(
        coordinator,
        "queue_candidates",
        lambda **_kwargs: {"success": True, "queued": 0},
    )
    monkeypatch.setattr(
        coordinator,
        "dispatch_pending",
        lambda **_kwargs: {"success": True, "dispatched": [], "errors": [], "n8n_calls": 0},
    )

    result = coordinator.run_coordinator(
        skip_workers=False,
        force_workers=True,
        dry_run=True,
        webhook_mode="test",
        timeout_seconds=60,
    )

    assert result["success"] is True
    assert result["worker_results"] == []
    assert result["queue_only_invariant"] is True
    assert result["skip_workers"] is True
    assert result["force_workers"] is False
    assert result["source_worker_execution_requested_but_blocked"] is True
    assert not hasattr(coordinator, "run_source_worker")
    assert not hasattr(coordinator, "CURRENT_WORKERS")
