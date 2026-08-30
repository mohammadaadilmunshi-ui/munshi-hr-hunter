from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app import randomized_source_runner, runtime_recovery
from app.runtime_recovery import RecoverySafetyError, RuntimeRecovery, SERVICES


def _status(**overrides: Any) -> dict[str, Any]:
    value = {
        "healthy": False,
        "launchd_loaded": False,
        "pids": [],
        "state": "Stopped",
        "kind": "core",
    }
    value.update(overrides)
    return value


class ComponentHarness(RuntimeRecovery):
    def __init__(self, status: dict[str, Any], *, becomes_healthy: bool = True) -> None:
        super().__init__(command_runner=lambda args, timeout: subprocess.CompletedProcess(args, 0, "", ""))
        self.current_status = status
        self.becomes_healthy = becomes_healthy
        self.bootstrap_calls = 0
        self.kickstart_calls = 0
        self.terminate_calls = 0

    def component_status(self, spec):  # type: ignore[no-untyped-def]
        return dict(self.current_status)

    def port_owner(self, port: int) -> int | None:
        return self.current_status["pids"][0] if self.current_status["pids"] else None

    def _bootstrap(self, spec):  # type: ignore[no-untyped-def]
        self.bootstrap_calls += 1
        return True

    def _kickstart(self, spec):  # type: ignore[no-untyped-def]
        self.kickstart_calls += 1
        return True

    def _wait_healthy(self, spec):  # type: ignore[no-untyped-def]
        return self.becomes_healthy

    def _terminate_owned_unmanaged(self, spec, timeout_seconds=30):  # type: ignore[no-untyped-def]
        self.terminate_calls += 1


def test_healthy_owned_service_is_not_restarted_and_recover_is_idempotent() -> None:
    harness = ComponentHarness(_status(healthy=True, launchd_loaded=True, pids=[91], state="Healthy"))
    first = harness._recover_component(SERVICES["fastapi"], repair_unhealthy=True)
    second = harness._recover_component(SERVICES["fastapi"], repair_unhealthy=True)
    assert first["action"] == second["action"] == "already healthy"
    assert harness.bootstrap_calls == harness.kickstart_calls == harness.terminate_calls == 0


def test_missing_service_is_started_once_and_unmanaged_healthy_service_is_adopted() -> None:
    missing = ComponentHarness(_status())
    result = missing._recover_component(SERVICES["fastapi"], repair_unhealthy=True)
    assert result["result"] == "Healthy"
    assert missing.bootstrap_calls == 1

    unmanaged = ComponentHarness(_status(healthy=True, launchd_loaded=False, pids=[101], state="Degraded"))
    result = unmanaged._recover_component(SERVICES["n8n"], repair_unhealthy=True)
    assert result["action"] == "adopted healthy process into canonical LaunchAgent"
    assert unmanaged.terminate_calls == 1
    assert unmanaged.bootstrap_calls == 1


def test_duplicate_singleton_processes_stop_automatic_recovery() -> None:
    harness = ComponentHarness(_status(pids=[101, 102], state="Failed"))
    result = harness._recover_component(SERVICES["streamlit"], repair_unhealthy=True)
    assert result["action"] == "none"
    assert "Manual intervention required" in result["result"]
    assert harness.bootstrap_calls == harness.kickstart_calls == 0


def test_failed_start_enters_bounded_backoff_without_restart_storm(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_recovery, "BACKOFF_PATH", tmp_path / "backoff.json")
    harness = ComponentHarness(_status(), becomes_healthy=False)
    first = harness._recover_component(SERVICES["fastapi"], repair_unhealthy=True)
    second = harness._recover_component(SERVICES["fastapi"], repair_unhealthy=True)
    assert first["result"] == "Failed to become healthy"
    assert second["action"] == "backoff"
    assert harness.bootstrap_calls == 1


def test_source_runner_lock_requires_process_identity_and_only_confirmed_stale_is_removed(
    tmp_path: Path, monkeypatch
) -> None:
    lock = tmp_path / "runner.lock"
    monkeypatch.setattr(randomized_source_runner, "LOCK_PATH", lock)
    lock.write_text(json.dumps({"pid": 88, "process_started_at": "same", "mode": "scheduled"}))
    monkeypatch.setattr(
        randomized_source_runner,
        "_process_identity",
        lambda pid: {"started": "same", "command": "python -m app.randomized_source_runner"},
    )
    assert randomized_source_runner.inspect_source_runner_lock()["state"] == "active"
    assert randomized_source_runner.recover_stale_source_runner_lock()["recovered"] is False
    assert lock.exists()

    monkeypatch.setattr(randomized_source_runner, "_process_identity", lambda pid: None)
    result = randomized_source_runner.recover_stale_source_runner_lock()
    assert result["recovered"] is True
    assert not lock.exists()


def _create_preservation_databases(hunter: Path, n8n: Path) -> None:
    connection = sqlite3.connect(hunter)
    connection.executescript(
        """
        CREATE TABLE jobs(id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE source_runs(id INTEGER PRIMARY KEY, run_status TEXT);
        CREATE TABLE targeting_decisions(id INTEGER PRIMARY KEY, primary_category TEXT);
        CREATE TABLE telegram_delivery_claims(id INTEGER PRIMARY KEY, delivery_state TEXT);
        CREATE TABLE telegram_operational_outbox(logical_id TEXT PRIMARY KEY,notification_kind TEXT,source_name TEXT,run_id TEXT,incident_id TEXT,delivery_state TEXT);
        CREATE TABLE n8n_dispatch_queue(id INTEGER PRIMARY KEY, queue_status TEXT);
        CREATE TABLE source_health(source_name TEXT PRIMARY KEY, enabled INTEGER, cadence_minutes INTEGER, cost_mode TEXT, consecutive_failures INTEGER, health_status TEXT);
        CREATE TABLE source_random_schedule(source_name TEXT PRIMARY KEY, next_run_at TEXT, schedule_state TEXT, consecutive_scheduler_failures INTEGER);
        CREATE TABLE settings(setting_key TEXT PRIMARY KEY, value_json TEXT);
        INSERT INTO jobs VALUES(1,'HR Generalist');
        INSERT INTO source_runs VALUES(1,'completed');
        INSERT INTO targeting_decisions VALUES(1,'ELIGIBLE');
        INSERT INTO telegram_delivery_claims VALUES(1,'sent');
        INSERT INTO telegram_operational_outbox VALUES('adapter_run_summary:run-1','adapter_run_summary','Ashby','run-1',NULL,'sent');
        INSERT INTO n8n_dispatch_queue VALUES(1,'completed');
        INSERT INTO source_health VALUES('Ashby',1,360,'free',2,'degraded');
        INSERT INTO source_random_schedule VALUES('Ashby','2026-08-25 06:00:00','failure_backoff',2);
        INSERT INTO settings VALUES('targeting','{"mode":"OPT"}');
        """
    )
    connection.commit()
    connection.close()
    connection = sqlite3.connect(n8n)
    connection.execute(
        "CREATE TABLE execution_entity(id INTEGER PRIMARY KEY,status TEXT,stoppedAt TEXT)"
    )
    connection.execute("INSERT INTO execution_entity VALUES(1,'success','2026-08-25 01:00:00')")
    connection.commit()
    connection.close()


def test_preservation_snapshot_covers_history_policy_schedules_backoff_queues_and_claims(
    tmp_path: Path, monkeypatch
) -> None:
    hunter = tmp_path / "hunter.db"
    n8n = tmp_path / "n8n.sqlite"
    _create_preservation_databases(hunter, n8n)
    project_env = tmp_path / ".env"
    runtime_env = tmp_path / "n8n.env"
    project_env.write_text("TOKEN=preserved\n")
    runtime_env.write_text("SECRET=preserved\n")
    monkeypatch.setattr(runtime_recovery, "HUNTER_DB", hunter)
    monkeypatch.setattr(runtime_recovery, "N8N_DB", n8n)
    monkeypatch.setattr(runtime_recovery, "ROOT", tmp_path)
    snapshot = RuntimeRecovery().durable_state_snapshot()
    again = RuntimeRecovery().durable_state_snapshot()
    assert snapshot == again
    assert snapshot["jobs"] == snapshot["source_runs"] == snapshot["targeting_decisions"] == 1
    assert snapshot["telegram_claims"] == snapshot["n8n_queue"] == 1
    assert snapshot["telegram_operational_cards"] == 1
    assert snapshot["telegram_operational_identity_hash"] == again["telegram_operational_identity_hash"]
    assert snapshot["source_policy_hash"] == again["source_policy_hash"]
    assert snapshot["source_schedule_hash"] == again["source_schedule_hash"]
    assert snapshot["targeting_hash"] == again["targeting_hash"]


def test_active_source_worker_blocks_controlled_stop_without_overlap() -> None:
    recovery = RuntimeRecovery(sleep=lambda seconds: None)
    recovery.source_lock = lambda: {"state": "active", "pid": 4242}  # type: ignore[method-assign]
    with pytest.raises(RecoverySafetyError, match="source worker PID 4242 is active"):
        recovery._wait_for_source_worker(0)


def test_public_entrypoint_exposes_required_modes_and_never_contains_broad_kill() -> None:
    root = Path(__file__).resolve().parent.parent
    wrapper = root / "bin" / "munshi-safe-restart"
    source = wrapper.read_text(encoding="utf-8")
    module = (root / "app" / "runtime_recovery.py").read_text(encoding="utf-8")
    assert wrapper.stat().st_mode & 0o111
    assert all(mode in module for mode in ("status", "start", "recover", "restart", "stop", "verify"))
    assert "pkill" not in source + module
    assert "killall" not in source + module
    assert "SIGKILL" not in source
    assert "-WAL" not in source + module
    assert "-SHM" not in source + module
