from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from app.platform_config import (
    endpoint_url,
    is_macos,
    n8n_database_path as configured_n8n_database_path,
)


ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
CONFIG_DIR = ROOT / "config" / "launchagents"
LAUNCH_DIR = HOME / "Library" / "LaunchAgents"
HUNTER_DB = DATA_DIR / "hunter.db"
N8N_DB = configured_n8n_database_path()
LOCK_PATH = DATA_DIR / "munshi_runtime_recovery.lock"
STATE_PATH = DATA_DIR / "munshi_runtime_recovery_status.json"
BACKOFF_PATH = DATA_DIR / "munshi_runtime_recovery_backoff.json"
EVENT_LOG = LOG_DIR / "runtime_recovery_events.jsonl"
TELEGRAM_HEARTBEAT = DATA_DIR / "telegram_listener_heartbeat.json"


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    label: str
    kind: str
    pattern: str
    health_url: str | None = None
    port: int | None = None
    grace_seconds: int = 45

    @property
    def installed_plist(self) -> Path:
        return LAUNCH_DIR / f"{self.label}.plist"

    @property
    def canonical_plist(self) -> Path:
        return CONFIG_DIR / f"{self.label}.plist"


SERVICES = {
    "n8n": ServiceSpec(
        "n8n", "com.aadil.hr-hunter.n8n", "core", r"(^|/)n8n( |$).*start|node .*/n8n start",
        endpoint_url("n8n", "/healthz"), 5678, 75,
    ),
    "fastapi": ServiceSpec(
        "fastapi", "com.aadil.hr-hunter.fastapi", "core", r"uvicorn .*app\.api:app",
        endpoint_url("fastapi", "/health"), 8000, 45,
    ),
    "telegram": ServiceSpec(
        "telegram", "com.aadil.hr-hunter.telegram", "core", r"app\.telegram_listener",
        None, None, 35,
    ),
    "randomized_scheduler": ServiceSpec(
        "randomized_scheduler", "com.aadil.hr-hunter.randomized-sources", "timer",
        r"app\.randomized_source_runner", None, None, 30,
    ),
    "hourly_coordinator": ServiceSpec(
        "hourly_coordinator", "com.aadil.hr-hunter.unified-hourly", "timer",
        r"app\.unified_hourly_coordinator", None, None, 30,
    ),
    "streamlit": ServiceSpec(
        "streamlit", "com.aadil.hr-hunter.streamlit", "core", r"streamlit run .*/app/dashboard\.py",
        endpoint_url("streamlit", "/_stcore/health"), 8501, 60,
    ),
}

START_ORDER = (
    "n8n", "fastapi", "telegram", "randomized_scheduler", "hourly_coordinator", "streamlit"
)
STOP_ORDER = (
    "randomized_scheduler", "hourly_coordinator", "streamlit", "telegram", "fastapi", "n8n"
)
BACKOFF_SECONDS = (5, 15, 30, 60, 300)


class RecoverySafetyError(RuntimeError):
    pass


def _run_command(args: Sequence[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), text=True, capture_output=True, timeout=timeout, check=False
    )


def _http_probe(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 400
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return fallback


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _rows_hash(rows: list[tuple[Any, ...]]) -> str:
    return _sha256_bytes(json.dumps(rows, default=str, separators=(",", ":")).encode("utf-8"))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


class RuntimeRecovery:
    def __init__(
        self,
        *,
        command_runner: Callable[[Sequence[str], int], subprocess.CompletedProcess[str]] | None = None,
        http_probe: Callable[[str, float], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.command_runner = command_runner or _run_command
        self.http_probe = http_probe or _http_probe
        self.sleep = sleep
        self.uid = os.getuid()
        self._lock_handle: Any = None

    def acquire(self) -> None:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        handle = LOCK_PATH.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise RecoverySafetyError("Another MUNSHI recovery command is already running.") from error
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "started_at": _iso_now()}))
        handle.flush()
        self._lock_handle = handle

    def release(self) -> None:
        if self._lock_handle is None:
            return
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock_handle.close()
            self._lock_handle = None

    def command(self, args: Sequence[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
        if args and args[0] in {"launchctl", "plutil"} and not is_macos():
            return subprocess.CompletedProcess(args, 1, "", "macOS command unavailable in portable mode")
        return self.command_runner(args, timeout)

    def launch_details(self, spec: ServiceSpec) -> dict[str, Any]:
        if not is_macos():
            return {"loaded": False, "state": "external scheduler", "pid": None, "runs": 0, "last_exit": None}
        result = self.command(
            ["launchctl", "print", f"gui/{self.uid}/{spec.label}"], timeout=10
        )
        if result.returncode != 0:
            return {"loaded": False, "state": "not loaded", "pid": None, "runs": 0, "last_exit": None}
        output = result.stdout

        def field(name: str) -> str | None:
            for line in output.splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{name} ="):
                    return stripped.split("=", 1)[1].strip()
            return None

        pid_text = field("pid")
        runs_text = field("runs")
        return {
            "loaded": True,
            "state": field("state") or "loaded",
            "pid": int(pid_text) if pid_text and pid_text.isdigit() else None,
            "runs": int(runs_text) if runs_text and runs_text.isdigit() else 0,
            "last_exit": field("last exit code"),
        }

    def process_command(self, pid: int) -> str:
        result = self.command(["ps", "-p", str(pid), "-o", "command="], timeout=8)
        return result.stdout.strip() if result.returncode == 0 else ""

    def process_cwd(self, pid: int) -> str:
        result = self.command(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], timeout=8)
        if result.returncode not in {0, 1}:
            return ""
        return next((line[1:] for line in result.stdout.splitlines() if line.startswith("n")), "")

    def matching_pids(self, spec: ServiceSpec) -> list[int]:
        candidates: set[int] = set()
        launch_pid = self.launch_details(spec).get("pid")
        if launch_pid:
            candidates.add(int(launch_pid))
        if spec.port:
            port_pid = self.port_owner(spec.port)
            if port_pid:
                candidates.add(port_pid)
        result = self.command(["ps", "-axo", "pid=,command="], timeout=8)
        if result.returncode == 0:
            import re

            matcher = re.compile(spec.pattern)
            for line in result.stdout.splitlines():
                fields = line.strip().split(None, 1)
                if len(fields) == 2 and fields[0].isdigit() and matcher.search(fields[1]):
                    candidates.add(int(fields[0]))
        pids: list[int] = []
        for pid in candidates:
            command = self.process_command(pid)
            cwd = self.process_cwd(pid)
            if command and cwd == str(ROOT):
                pids.append(pid)
        return sorted(set(pids))

    def port_owner(self, port: int) -> int | None:
        result = self.command(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], timeout=8
        )
        for text in result.stdout.split():
            if text.isdigit():
                return int(text)
        return None

    def telegram_healthy(self) -> bool:
        payload = _read_json(TELEGRAM_HEARTBEAT, {})
        try:
            updated = datetime.fromisoformat(str(payload.get("updated_at") or "").replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = (_utc_now() - updated.astimezone(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            return False
        return str(payload.get("state") or "").casefold() == "online" and age < 180

    def service_healthy(self, spec: ServiceSpec) -> bool:
        if spec.name == "telegram":
            return self.telegram_healthy() and len(self.matching_pids(spec)) == 1
        if spec.health_url:
            return bool(self.http_probe(spec.health_url, 3.0))
        return False

    def network_available(self) -> bool:
        try:
            socket.getaddrinfo("api.telegram.org", 443, type=socket.SOCK_STREAM)
            return True
        except OSError:
            return False

    def source_lock(self) -> dict[str, Any]:
        from app.randomized_source_runner import inspect_source_runner_lock

        return inspect_source_runner_lock()

    def recover_source_lock(self) -> dict[str, Any]:
        from app.randomized_source_runner import recover_stale_source_runner_lock

        return recover_stale_source_runner_lock()

    def component_status(self, spec: ServiceSpec) -> dict[str, Any]:
        launch = self.launch_details(spec)
        pids = self.matching_pids(spec)
        healthy = self.service_healthy(spec) if spec.kind == "core" else bool(launch["loaded"])
        if len(pids) > 1:
            state = "Failed"
            message = f"Duplicate singleton processes detected ({len(pids)})"
        elif spec.kind == "timer" and launch["loaded"]:
            state = "Running" if pids else "Waiting"
            message = "Canonical launchd timer is loaded"
        elif healthy and launch["loaded"]:
            state = "Healthy"
            message = "Healthy under canonical launchd ownership"
        elif healthy:
            state = "Degraded"
            message = "Healthy process is not yet owned by the canonical LaunchAgent"
        elif launch["loaded"] and pids:
            state = "Starting"
            message = "Owned process is starting or health has not become ready"
        elif launch["loaded"]:
            state = "Degraded"
            message = "LaunchAgent is loaded but the component is not healthy"
        else:
            state = "Stopped"
            message = "Canonical LaunchAgent is not loaded"
        return {
            "name": spec.name,
            "label": spec.label,
            "kind": spec.kind,
            "state": state,
            "message": message,
            "healthy": healthy,
            "launchd_loaded": bool(launch["loaded"]),
            "launchd_state": launch["state"],
            "launchd_runs": launch["runs"],
            "last_exit": launch["last_exit"],
            "pids": pids,
            "port": spec.port,
        }

    def database_quick_check(self, path: Path) -> str:
        if not path.exists():
            return "missing"
        for attempt in range(4):
            try:
                connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
                connection.execute("PRAGMA busy_timeout=5000")
                row = connection.execute("PRAGMA quick_check").fetchone()
                connection.close()
                return str(row[0]) if row else "unknown"
            except sqlite3.OperationalError as error:
                if "locked" in str(error).casefold() or "busy" in str(error).casefold():
                    if attempt < 3:
                        self.sleep(1)
                        continue
                    return "busy (active writer)"
                return f"error: {type(error).__name__}"
            except sqlite3.DatabaseError as error:
                return f"error: {type(error).__name__}"
        return "busy (active writer)"

    def active_n8n_executions(self) -> int:
        if not N8N_DB.exists():
            return 0
        connection = sqlite3.connect(f"file:{N8N_DB}?mode=ro", uri=True, timeout=5)
        try:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM execution_entity
                    WHERE lower(COALESCE(status,'')) IN ('new','running','waiting')
                      AND stoppedAt IS NULL
                    """
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def durable_state_snapshot(self) -> dict[str, Any]:
        connection = sqlite3.connect(f"file:{HUNTER_DB}?mode=ro", uri=True, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")

        def count(table: str) -> int:
            return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

        def grouped(table: str, column: str) -> list[tuple[Any, ...]]:
            return [
                tuple(row)
                for row in connection.execute(
                    f'SELECT "{column}",COUNT(*) FROM "{table}" GROUP BY "{column}" ORDER BY "{column}"'
                )
            ]

        source_policy = [
            tuple(row) for row in connection.execute("SELECT * FROM source_health ORDER BY source_name")
        ]
        schedules = [
            tuple(row) for row in connection.execute("SELECT * FROM source_random_schedule ORDER BY source_name")
        ]
        telegram_rows = [
            tuple(row) for row in connection.execute("SELECT * FROM telegram_delivery_claims ORDER BY id")
        ]
        queue_rows = [
            tuple(row) for row in connection.execute("SELECT * FROM n8n_dispatch_queue ORDER BY id")
        ]
        targeting = connection.execute(
            "SELECT value_json FROM settings WHERE setting_key='targeting'"
        ).fetchone()
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        operational_ids = (
            [
                tuple(row)
                for row in connection.execute(
                    "SELECT logical_id,notification_kind,source_name,run_id,incident_id FROM telegram_operational_outbox ORDER BY logical_id"
                )
            ]
            if "telegram_operational_outbox" in tables
            else []
        )
        snapshot = {
            "jobs": count("jobs"),
            "source_runs": count("source_runs"),
            "targeting_decisions": count("targeting_decisions"),
            "telegram_claims": count("telegram_delivery_claims"),
            "telegram_claim_states_hash": _rows_hash(grouped("telegram_delivery_claims", "delivery_state")),
            "telegram_claim_records_hash": _rows_hash(telegram_rows),
            "n8n_queue": count("n8n_dispatch_queue"),
            "n8n_queue_states_hash": _rows_hash(grouped("n8n_dispatch_queue", "queue_status")),
            "n8n_queue_records_hash": _rows_hash(queue_rows),
            "source_policy_hash": _rows_hash(source_policy),
            "source_schedule_hash": _rows_hash(schedules),
            "targeting_hash": _sha256_bytes(str(targeting[0] if targeting else "").encode("utf-8")),
            "telegram_operational_cards": len(operational_ids),
            "telegram_operational_identity_hash": _rows_hash(operational_ids),
        }
        connection.close()
        credential_files = {
            "project_environment": ROOT / ".env",
            "n8n_runtime_environment": ROOT / ".runtime" / "n8n_runtime.env",
        }
        snapshot["credential_file_hashes"] = {
            label: _sha256_bytes(path.read_bytes()) if path.exists() else "missing"
            for label, path in credential_files.items()
        }
        return snapshot

    def status_snapshot(self) -> dict[str, Any]:
        components = {name: self.component_status(spec) for name, spec in SERVICES.items()}
        lock = self.source_lock()
        if lock.get("state") == "active":
            worker_state = "Running"
        elif lock.get("state") == "stale":
            worker_state = "Stale"
        else:
            worker_state = "Waiting"
        operational = self.telegram_operational_status()
        return {
            "checked_at": _iso_now(),
            "network": "Available" if self.network_available() else "Network unavailable",
            "hunter_database": self.database_quick_check(HUNTER_DB),
            "n8n_database": self.database_quick_check(N8N_DB),
            "components": components,
            "source_worker": {**lock, "display_state": worker_state},
            "active_n8n_executions": self.active_n8n_executions(),
            "telegram_operational_outbox": operational,
            "last_recovery": _read_json(STATE_PATH, {}),
        }

    def telegram_operational_status(self) -> dict[str, Any]:
        if not HUNTER_DB.exists():
            return {"available": False, "reason": "Hunter database missing"}
        connection = sqlite3.connect(f"file:{HUNTER_DB}?mode=ro", uri=True, timeout=5)
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_operational_outbox'"
            ).fetchone()
            if not exists:
                return {"available": False, "reason": "Operational outbox schema missing"}
            counts = {
                str(state): int(count)
                for state, count in connection.execute(
                    "SELECT delivery_state,COUNT(*) FROM telegram_operational_outbox GROUP BY delivery_state"
                )
            }
            return {
                "available": True,
                "counts": counts,
                "pending_or_retrying": counts.get("pending", 0) + counts.get("retry", 0),
                "uncertain": counts.get("uncertain", 0),
            }
        finally:
            connection.close()

    def record_event(self, event_type: str, **fields: Any) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"event_type": event_type, "created_at": _iso_now(), **fields}
        with EVENT_LOG.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _plist_backup(self, target: Path) -> Path:
        backup_dir = ROOT / "patch_backups" / "runtime_recovery_launchagents" / datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / target.name
        shutil.copy2(target, backup)
        return backup

    def ensure_launchagent_files(self) -> list[dict[str, str]]:
        if not is_macos():
            raise RecoverySafetyError("LaunchAgent recovery is macOS-only; use an external process manager.")
        LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
        changes: list[dict[str, str]] = []
        for spec in SERVICES.values():
            source = spec.canonical_plist
            target = spec.installed_plist
            if not source.exists():
                raise RecoverySafetyError(f"Canonical LaunchAgent is missing: {source}")
            if target.exists() and target.read_bytes() == source.read_bytes():
                continue
            backup = self._plist_backup(target) if target.exists() else None
            temporary = target.with_suffix(".plist.tmp")
            rendered = source.read_text(encoding="utf-8")
            rendered = rendered.replace("__PROJECT_ROOT__", str(ROOT))
            rendered = rendered.replace("__HOME__", str(HOME))
            rendered = rendered.replace("__PATH__", os.environ.get("PATH", "/usr/bin:/bin"))
            temporary.write_text(rendered, encoding="utf-8")
            os.chmod(temporary, 0o644)
            validation = self.command(["plutil", "-lint", str(temporary)], timeout=10)
            if validation.returncode != 0:
                temporary.unlink(missing_ok=True)
                raise RecoverySafetyError(f"LaunchAgent validation failed: {source.name}")
            os.replace(temporary, target)
            changes.append({
                "service": spec.name,
                "installed": str(target),
                "backup": str(backup) if backup else "new file",
            })
        return changes

    def _backoff_state(self) -> dict[str, Any]:
        return dict(_read_json(BACKOFF_PATH, {}) or {})

    def _attempt_allowed(self, service: str) -> bool:
        state = self._backoff_state().get(service) or {}
        try:
            next_allowed = datetime.fromisoformat(str(state.get("next_allowed_at") or ""))
            if next_allowed.tzinfo is None:
                next_allowed = next_allowed.replace(tzinfo=timezone.utc)
            return _utc_now() >= next_allowed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return True

    def _record_attempt(self, service: str, success: bool) -> None:
        state = self._backoff_state()
        if success:
            state.pop(service, None)
        else:
            previous = dict(state.get(service) or {})
            failures = int(previous.get("failures") or 0) + 1
            delay = BACKOFF_SECONDS[min(failures - 1, len(BACKOFF_SECONDS) - 1)]
            state[service] = {
                "failures": failures,
                "last_failure_at": _iso_now(),
                "next_allowed_at": datetime.fromtimestamp(_utc_now().timestamp() + delay, timezone.utc).isoformat(),
                "delay_seconds": delay,
            }
        _atomic_json(BACKOFF_PATH, state)

    def _bootstrap(self, spec: ServiceSpec) -> bool:
        result = self.command(
            ["launchctl", "bootstrap", f"gui/{self.uid}", str(spec.installed_plist)], timeout=20
        )
        return result.returncode == 0 or self.launch_details(spec)["loaded"]

    def _kickstart(self, spec: ServiceSpec) -> bool:
        result = self.command(
            ["launchctl", "kickstart", "-k", f"gui/{self.uid}/{spec.label}"], timeout=20
        )
        return result.returncode == 0

    def _bootout(self, spec: ServiceSpec) -> bool:
        if not self.launch_details(spec)["loaded"]:
            return True
        result = self.command(
            ["launchctl", "bootout", f"gui/{self.uid}/{spec.label}"], timeout=30
        )
        return result.returncode == 0 or not self.launch_details(spec)["loaded"]

    def _wait_healthy(self, spec: ServiceSpec) -> bool:
        if spec.kind == "timer":
            return bool(self.launch_details(spec)["loaded"])
        deadline = time.monotonic() + spec.grace_seconds
        while time.monotonic() < deadline:
            if self.service_healthy(spec):
                return True
            self.sleep(2)
        return self.service_healthy(spec)

    def _recover_component(self, spec: ServiceSpec, *, repair_unhealthy: bool) -> dict[str, Any]:
        before = self.component_status(spec)
        if before["healthy"] and before["launchd_loaded"] and len(before["pids"]) <= 1:
            return {"service": spec.name, "action": "already healthy", "result": before["state"]}
        if spec.kind == "timer" and before["launchd_loaded"]:
            return {"service": spec.name, "action": "already loaded", "result": before["state"]}
        if not self._attempt_allowed(spec.name):
            return {"service": spec.name, "action": "backoff", "result": "Crash-loop protection active"}
        if len(before["pids"]) > 1:
            return {"service": spec.name, "action": "none", "result": "Manual intervention required: duplicate processes"}
        if spec.port:
            owner = self.port_owner(spec.port)
            if owner and owner not in before["pids"]:
                return {"service": spec.name, "action": "none", "result": f"Manual intervention required: port {spec.port} has an unrecognized owner"}
        if not before["launchd_loaded"]:
            if before["healthy"] and before["pids"]:
                self._terminate_owned_unmanaged(spec)
            started = self._bootstrap(spec)
            action = (
                "adopted healthy process into canonical LaunchAgent"
                if before["healthy"]
                else "loaded canonical LaunchAgent"
            )
        elif repair_unhealthy:
            started = self._kickstart(spec)
            action = "recovered unhealthy LaunchAgent"
        else:
            return {"service": spec.name, "action": "none", "result": "Degraded; recover mode required"}
        success = bool(started and self._wait_healthy(spec))
        self._record_attempt(spec.name, success)
        return {"service": spec.name, "action": action, "result": "Healthy" if success else "Failed to become healthy"}

    def recover(
        self,
        *,
        trigger: str = "manual",
        repair_unhealthy: bool = True,
        _lock_owned: bool = False,
    ) -> dict[str, Any]:
        if not _lock_owned:
            self.acquire()
        started_at = _iso_now()
        self.record_event("runtime_recovery_started", trigger=trigger)
        try:
            hunter_check = self.database_quick_check(HUNTER_DB)
            n8n_check = self.database_quick_check(N8N_DB)
            if hunter_check not in {"ok", "busy (active writer)"} or n8n_check not in {"ok", "busy (active writer)"}:
                raise RecoverySafetyError("Database verification requires manual intervention; runtime mutation was stopped.")
            installed = self.ensure_launchagent_files()
            lock_result = self.recover_source_lock()
            actions = [
                self._recover_component(SERVICES[name], repair_unhealthy=repair_unhealthy)
                for name in START_ORDER
            ]
            final = self.status_snapshot()
            completed = {
                "started_at": started_at,
                "completed_at": _iso_now(),
                "trigger": trigger,
                "result": "Healthy" if self._verification_errors(final) == [] else "Degraded",
                "installed_launchagents": installed,
                "actions": actions,
                "source_lock": lock_result,
                "network": final["network"],
            }
            _atomic_json(STATE_PATH, completed)
            self.record_event(
                "runtime_recovery_completed",
                trigger=trigger,
                result=completed["result"],
                recovered=[item["service"] for item in actions if item["action"] not in {"already healthy", "already loaded", "none", "backoff"}],
                already_healthy=[item["service"] for item in actions if item["action"] in {"already healthy", "already loaded"}],
            )
            return completed
        except Exception as error:
            self.record_event("runtime_recovery_failed", trigger=trigger, error_type=type(error).__name__)
            raise
        finally:
            if not _lock_owned:
                self.release()

    def _wait_for_source_worker(self, timeout_seconds: int) -> None:
        deadline = time.monotonic() + max(0, timeout_seconds)
        while True:
            status = self.source_lock()
            if status.get("state") != "active":
                self.recover_source_lock()
                return
            if time.monotonic() >= deadline:
                raise RecoverySafetyError(
                    f"Refusing controlled restart while source worker PID {status.get('pid')} is active."
                )
            self.sleep(2)

    def _terminate_owned_unmanaged(self, spec: ServiceSpec, timeout_seconds: int = 30) -> None:
        pids = self.matching_pids(spec)
        for pid in pids:
            command = self.process_command(pid)
            if not command or self.process_cwd(pid) != str(ROOT):
                raise RecoverySafetyError(f"Refusing to stop unrecognized {spec.name} process {pid}.")
            result = self.command(["kill", "-TERM", str(pid)], timeout=8)
            if result.returncode not in {0, 1}:
                raise RecoverySafetyError(f"Could not request graceful stop for {spec.name} PID {pid}.")
        deadline = time.monotonic() + timeout_seconds
        while pids and time.monotonic() < deadline:
            if not self.matching_pids(spec):
                return
            self.sleep(1)
        if self.matching_pids(spec):
            raise RecoverySafetyError(f"{spec.name} did not stop after SIGTERM; no SIGKILL was sent.")

    def stop(self, *, source_wait_seconds: int = 180, _lock_owned: bool = False) -> dict[str, Any]:
        if not _lock_owned:
            self.acquire()
        self.record_event("runtime_recovery_started", trigger="controlled_stop")
        try:
            self._wait_for_source_worker(source_wait_seconds)
            active_n8n = self.active_n8n_executions()
            if active_n8n:
                raise RecoverySafetyError(
                    f"Refusing controlled stop while {active_n8n} n8n execution(s) are active."
                )
            actions: list[dict[str, str]] = []
            for name in STOP_ORDER:
                spec = SERVICES[name]
                if self.launch_details(spec)["loaded"]:
                    success = self._bootout(spec)
                    actions.append({"service": name, "action": "unloaded LaunchAgent", "result": "Stopped" if success else "Failed"})
                    if not success:
                        raise RecoverySafetyError(f"Could not unload {spec.label}.")
                elif self.matching_pids(spec):
                    self._terminate_owned_unmanaged(spec)
                    actions.append({"service": name, "action": "gracefully stopped owned process", "result": "Stopped"})
                else:
                    actions.append({"service": name, "action": "already stopped", "result": "Stopped"})
            result = {"completed_at": _iso_now(), "trigger": "controlled_stop", "result": "Stopped", "actions": actions}
            _atomic_json(STATE_PATH, result)
            self.record_event("runtime_recovery_completed", trigger="controlled_stop", result="Stopped")
            return result
        finally:
            if not _lock_owned:
                self.release()

    def restart(self, *, source_wait_seconds: int = 180) -> dict[str, Any]:
        self.acquire()
        try:
            before = self.durable_state_snapshot()
            stopped = self.stop(source_wait_seconds=source_wait_seconds, _lock_owned=True)
            recovered = self.recover(
                trigger="controlled_restart", repair_unhealthy=True, _lock_owned=True
            )
            after = self.durable_state_snapshot()
            preservation = {key: before[key] == after[key] for key in before}
            recovered["controlled_stop"] = stopped
            recovered["durable_state_preserved"] = preservation
            if not all(preservation.values()):
                raise RecoverySafetyError("Controlled restart changed durable operational state unexpectedly.")
            return recovered
        finally:
            self.release()

    def _verification_errors(self, snapshot: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for name, component in snapshot["components"].items():
            if not component["launchd_loaded"]:
                errors.append(f"{name}: LaunchAgent not loaded")
            if component["kind"] == "core" and not component["healthy"]:
                errors.append(f"{name}: not healthy")
            if len(component["pids"]) > 1:
                errors.append(f"{name}: duplicate processes")
        if snapshot["source_worker"].get("state") == "stale":
            errors.append("source worker: stale lock")
        if snapshot["hunter_database"] not in {"ok", "busy (active writer)"}:
            errors.append("Hunter database requires attention")
        if snapshot["n8n_database"] not in {"ok", "busy (active writer)"}:
            errors.append("n8n database requires attention")
        if not snapshot.get("telegram_operational_outbox", {}).get("available"):
            errors.append("Telegram operational outbox requires attention")
        return errors

    def verify(self) -> dict[str, Any]:
        snapshot = self.status_snapshot()
        snapshot["verification_errors"] = self._verification_errors(snapshot)
        snapshot["verified"] = not snapshot["verification_errors"]
        snapshot["durable_state"] = self.durable_state_snapshot()
        return snapshot


def _human_status(snapshot: dict[str, Any]) -> str:
    lines = ["MUNSHI Apply — Safe Runtime Status", ""]
    for component in snapshot["components"].values():
        pid_text = ",".join(str(pid) for pid in component["pids"]) or "idle"
        lines.append(
            f"{component['name']:<24} {component['state']:<12} launchd={'loaded' if component['launchd_loaded'] else 'not loaded'} · pid={pid_text}"
        )
    worker = snapshot["source_worker"]
    lines.extend(
        [
            "",
            f"Source worker             {worker['display_state']}" + (f" · PID {worker.get('pid')}" if worker.get("pid") else ""),
            f"Network                   {snapshot['network']}",
            f"Hunter DB                 {snapshot['hunter_database']}",
            f"n8n DB                    {snapshot['n8n_database']}",
            f"Active n8n executions     {snapshot['active_n8n_executions']}",
            (
                "Telegram run summaries    "
                + (
                    f"ready · pending/retrying={snapshot['telegram_operational_outbox'].get('pending_or_retrying', 0)}"
                    if snapshot.get("telegram_operational_outbox", {}).get("available")
                    else "requires attention"
                )
            ),
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="munshi-safe-restart")
    parser.add_argument("command", choices=("status", "start", "recover", "restart", "stop", "verify"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--source-wait-seconds", type=int, default=180)
    args = parser.parse_args(argv)
    recovery = RuntimeRecovery()
    try:
        if args.command == "status":
            result = recovery.status_snapshot()
        elif args.command == "start":
            result = recovery.recover(trigger="manual_start", repair_unhealthy=False)
        elif args.command == "recover":
            result = recovery.recover(trigger="manual_recover", repair_unhealthy=True)
        elif args.command == "restart":
            result = recovery.restart(source_wait_seconds=args.source_wait_seconds)
        elif args.command == "stop":
            result = recovery.stop(source_wait_seconds=args.source_wait_seconds)
        else:
            result = recovery.verify()
        if args.as_json:
            print(json.dumps(result, indent=2, default=str))
        elif args.command in {"status", "verify"}:
            print(_human_status(result))
            if args.command == "verify" and result.get("verification_errors"):
                print("\nVerification issues:")
                for issue in result["verification_errors"]:
                    print(f"- {issue}")
        else:
            print(f"MUNSHI Apply runtime {args.command}: {result.get('result', 'complete')}")
            for action in result.get("actions", []):
                print(f"- {action['service']}: {action['action']} · {action['result']}")
        return 0 if not result.get("verification_errors") else 1
    except RecoverySafetyError as error:
        print(f"MUNSHI Apply recovery stopped safely: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
