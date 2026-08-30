from __future__ import annotations

import argparse
import json
import os
import re
import uuid
import time
import signal
import secrets
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.database import ROOT_DIR, get_connection, get_setting
from app.source_cooldown import (
    get_adapter_timer,
    mark_source_completed,
    mark_source_started,
)

EASTERN = ZoneInfo("America/New_York")
LOCK_PATH = ROOT_DIR / "data/randomized_sources_runner.lock"
BLOCK_TERMS = (
    "429", "403", "captcha", "rate limit",
    "too many requests", "/sorry/", "unusual traffic",
    "verify you are human", "access denied",
    "temporarily blocked",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--quiet-start", action="store_true")
    parser.add_argument("--max-sources", type=int, default=0)
    return parser.parse_args()


def normalize_source_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def load_enabled_sources() -> list[dict[str, Any]]:
    registry = get_setting("source_worker_registry", {}) or {}
    retired_keys = {
        normalize_source_name(value)
        for value in (registry.get("retired_source_keys") or [])
    }
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT * FROM source_health
            WHERE enabled = 1
            ORDER BY source_tier, source_name
            """
        ).fetchall()
        return [
            dict(row)
            for row in rows
            if normalize_source_name(row["source_name"])
            not in retired_keys
        ]
    finally:
        connection.close()


def discover_worker(source_name: str) -> str | None:
    registry = get_setting("source_worker_registry", {}) or {}
    for configured_name, module in dict(registry.get("workers") or {}).items():
        if normalize_source_name(configured_name) == normalize_source_name(source_name):
            return str(module or "").strip() or None
    slug = re.sub(
        r"[^a-z0-9]+", "_", source_name.lower()
    ).strip("_")
    candidate = ROOT_DIR / "app" / f"{slug}_worker.py"
    if candidate.exists():
        return f"app.{slug}_worker"
    return None


# AADIL_AUTOMATIC_ADAPTER_LIFECYCLE_JOBSPY_REPAIR_V2
_LOCK_TOKEN: str | None = None


def _process_identity(pid: int) -> dict[str, str] | None:
    if int(pid or 0) <= 0:
        return None
    try:
        started = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        ).stdout.strip()
        command = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except Exception:
        return None
    if not command:
        return None
    return {"started": started, "command": command}


def _lock_owner_is_current_runner(payload: dict[str, Any]) -> bool:
    pid = int(payload.get("pid") or 0)
    identity = _process_identity(pid)
    if identity is None:
        return False
    command = identity["command"]
    if "app.randomized_source_runner" not in command:
        return False
    stored_started = str(payload.get("process_started_at") or "").strip()
    if stored_started and stored_started != identity["started"]:
        return False
    return True


def inspect_source_runner_lock() -> dict[str, Any]:
    """Return ownership-aware lock state without changing it."""
    if not LOCK_PATH.exists():
        return {"state": "idle", "path": str(LOCK_PATH), "pid": None}
    try:
        payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"state": "stale", "path": str(LOCK_PATH), "pid": None, "reason": "invalid_lock_payload"}
    pid = int(payload.get("pid") or 0)
    if payload and _lock_owner_is_current_runner(payload):
        return {
            "state": "active",
            "path": str(LOCK_PATH),
            "pid": pid,
            "mode": str(payload.get("mode") or "source worker"),
            "started_at": str(payload.get("started_at") or ""),
        }
    return {
        "state": "stale",
        "path": str(LOCK_PATH),
        "pid": pid or None,
        "reason": "owner_process_missing_or_identity_mismatch",
    }


def recover_stale_source_runner_lock() -> dict[str, Any]:
    """Use the runner's canonical identity check before removing a stale lock."""
    status = inspect_source_runner_lock()
    if status["state"] != "stale":
        return {**status, "recovered": False}
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError as error:
        return {**status, "recovered": False, "error": type(error).__name__}
    return {**status, "state": "idle", "recovered": True}


def _source_timeout_seconds(source_name: str) -> int:
    runtime = get_setting("provider_runtime", {}) or {}
    try:
        minimum = int(runtime["minimum_worker_timeout_seconds"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(
            "minimum_worker_timeout_seconds is missing from canonical provider runtime policy."
        ) from None
    override = str(os.getenv("AADIL_SOURCE_TIMEOUT_SECONDS") or "").strip()
    if override:
        try:
            return max(minimum, int(override))
        except ValueError:
            pass

    try:
        default = max(minimum, int(runtime["default_worker_timeout_seconds"]))
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(
            "default_worker_timeout_seconds is missing from canonical provider runtime policy."
        ) from None
    for configured_name, seconds in dict(runtime.get("worker_timeout_overrides") or {}).items():
        if normalize_source_name(configured_name) == normalize_source_name(source_name):
            return max(minimum, int(seconds))
    return default


def _run_worker_command(
    command: list[str],
    *,
    timeout_seconds: int,
) -> tuple[subprocess.CompletedProcess[str], bool, str | None]:
    process = subprocess.Popen(
        command,
        cwd=ROOT_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    timeout_error: str | None = None
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        timeout_error = (
            f"worker exceeded hard timeout of {timeout_seconds} seconds"
        )
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        returncode = 124
    else:
        returncode = int(process.returncode or 0)

    return (
        subprocess.CompletedProcess(
            command,
            returncode,
            stdout or "",
            stderr or "",
        ),
        timed_out,
        timeout_error,
    )


def _due_sort_key(
    source: dict[str, Any],
    timers: dict[str, dict[str, Any]],
) -> tuple[datetime, str]:
    name = str(source.get("source_name") or "")
    timer = timers.get(name) or {}
    due_text = str(timer.get("next_allowed_at") or "").strip()
    try:
        due_at = datetime.fromisoformat(due_text.replace("Z", "+00:00"))
    except ValueError:
        due_at = datetime.max.replace(tzinfo=timezone.utc)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    return due_at.astimezone(timezone.utc), name.casefold()

def acquire_lock(mode: str) -> None:
    global _LOCK_TOKEN

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_status = recover_stale_source_runner_lock()
    if lock_status["state"] == "active":
        raise RuntimeError(
            "Another randomized source runner is active: "
            f"PID {int(lock_status.get('pid') or 0)}"
        )

    identity = _process_identity(os.getpid()) or {
        "started": "",
        "command": "app.randomized_source_runner",
    }
    _LOCK_TOKEN = uuid.uuid4().hex
    fd = os.open(
        LOCK_PATH,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(fd, "w") as handle:
        json.dump(
            {
                "pid": os.getpid(),
                "mode": mode,
                "started_at": datetime.now().isoformat(),
                "process_started_at": identity["started"],
                "command": identity["command"],
                "token": _LOCK_TOKEN,
            },
            handle,
        )



def release_lock() -> None:
    global _LOCK_TOKEN

    try:
        payload = json.loads(
            LOCK_PATH.read_text(encoding="utf-8")
        )
        owner_pid = int(payload.get("pid") or 0)
        owner_token = str(payload.get("token") or "")
        token_matches = (
            not owner_token
            or not _LOCK_TOKEN
            or owner_token == _LOCK_TOKEN
        )
        if owner_pid == os.getpid() and token_matches:
            LOCK_PATH.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    except Exception:
        # Never delete a lock that may belong to another live runner.
        pass
    finally:
        _LOCK_TOKEN = None



def active_work_reason() -> str:
    try:
        process_text = subprocess.run(
            ["ps", "-axo", "command="],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        ).stdout.lower()
        for marker in (
            "app.manual_input_worker",
            "app.stored_job_n8n_worker",
            "manual_input_worker.py",
            "stored_job_n8n_worker.py",
        ):
            if marker in process_text:
                return f"active process detected: {marker}"
    except Exception:
        pass

    connection = get_connection()
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in (
            "n8n_dispatch_queue", "dispatch_queue", "n8n_queue"
        ):
            if table not in tables:
                continue
            columns = {
                str(row[1])
                for row in connection.execute(
                    f"PRAGMA table_info({table})"
                )
            }
            status_column = (
                "queue_status"
                if "queue_status" in columns
                else "status"
                if "status" in columns
                else None
            )
            if status_column:
                count = connection.execute(
                    f"""
                    SELECT COUNT(*) FROM {table}
                    WHERE lower(COALESCE({status_column}, '')) IN (
                      'pending','queued','accepted','dispatching',
                      'dispatched','running','waiting','processing'
                    )
                    """
                ).fetchone()[0]
                if count:
                    return f"{count} open item(s) in {table}"
    finally:
        connection.close()

    integration = get_setting("integration_health", {}) or {}
    n8n_database = Path(str(integration.get("n8n_database_path") or ""))
    if n8n_database.is_file():
        connection = sqlite3.connect(
            f"file:{n8n_database}?mode=ro", uri=True, timeout=10
        )
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "execution_entity" in tables:
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(execution_entity)"
                    )
                }
                status_column = (
                    "status" if "status" in columns else None
                )
                workflow_column = (
                    "workflowId"
                    if "workflowId" in columns
                    else "workflow_id"
                    if "workflow_id" in columns
                    else None
                )
                if status_column:
                    query = (
                        "SELECT COUNT(*) FROM execution_entity "
                        f"WHERE lower(COALESCE({status_column}, '')) "
                        "IN ('new','running','waiting')"
                    )
                    params: tuple[Any, ...] = ()
                    workflow_id = str(
                        (integration.get("n8n_read_only_snapshot") or {}).get("workflow_id") or ""
                    )
                    if workflow_column and workflow_id:
                        query += f" AND {workflow_column} = ?"
                        params = (workflow_id,)
                    count = connection.execute(
                        query, params
                    ).fetchone()[0]
                    if count:
                        return (
                            f"{count} active production n8n execution(s)"
                        )
        finally:
            connection.close()
    return ""


def _last_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    values: list[tuple[int, int, dict[str, Any]]] = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, relative_end = decoder.raw_decode(text[index:])
        except Exception:
            continue
        if isinstance(value, dict):
            values.append((index + relative_end, index, value))
    if not values:
        return {}
    # Prefer the object that ends latest in stdout. If nested objects share
    # that endpoint, prefer the earliest start so the complete worker result
    # wins over one of its nested diagnostic dictionaries.
    _end, _start, payload = max(values, key=lambda item: (item[0], -item[1]))
    return payload


def _is_blocked(text: str, payload: dict[str, Any]) -> bool:
    if payload.get("blocked") is True or payload.get("rate_limited") is True:
        return True

    errors = payload.get("errors") or []
    # A complete structured success is authoritative. Provider libraries can
    # print incidental HTTP status text from optional probes even when the
    # bounded source request succeeds cleanly; that noise must not turn a
    # truthful healthy run into a synthetic scheduler failure.
    if (
        payload
        and payload.get("success") is True
        and not payload.get("partial_success")
        and not errors
    ):
        return False

    structured = {
        "errors": errors,
        "error": payload.get("error"),
        "http_status": payload.get("http_status"),
        "status": payload.get("status"),
        "skip_reason": payload.get("skip_reason"),
    }
    combined = json.dumps(structured, default=str).lower()
    if not payload:
        combined = text.lower()
    return any(term in combined for term in BLOCK_TERMS)


def _is_deferred(text: str, payload: dict[str, Any]) -> bool:
    reason = str(payload.get("skip_reason") or "").lower()
    if payload.get("worker_action") == "skip":
        return any(
            term in reason
            for term in (
                "active", "queue", "running",
                "locked", "manual", "production",
                "backoff", "cadence", "cooldown", "not_due",
            )
        )
    lowered = text.lower()
    return (
        "active production" in lowered
        or "open item" in lowered
    )


def _deferred_incident_reason(payload: dict[str, Any]) -> tuple[str, str]:
    reason = str(payload.get("skip_reason") or "").casefold()
    if "backoff" in reason:
        return "failure_backoff", "Provider failure backoff prevented this due attempt from starting"
    if "cadence" in reason or "cooldown" in reason or "not_due" in reason:
        return (
            "provider_cadence_deferred",
            "Provider-specific cadence deferred this scheduler-selected attempt",
        )
    if "lock" in reason or "running" in reason:
        return "worker_lock_active", "A canonical worker lock prevented this due attempt from starting"
    if "active" in reason or "queue" in reason or "production" in reason:
        return "serialized_queue_wait", "The single-worker lane was serving other active work"
    return "adapter_start_deferred", "The adapter deferred before a provider run began"


def _record_runner_failure(
    source_name: str,
    *,
    status: str,
    returncode: int,
    error: str,
    record_health: bool = True,
) -> str:
    """Keep dashboard source health aligned with scheduler-level failures.

    A killed/timed-out child cannot write its own final health record. The
    supervisor therefore owns this narrow failure transition; it never creates
    an unregistered source or changes source policy.
    """
    run_id = f"scheduler-{uuid.uuid4()}"
    failure_detail = json.dumps(
        {
            "worker_status": status,
            "worker_returncode": int(returncode),
            "error": str(error)[:1000],
        },
        ensure_ascii=False,
    )
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if record_health:
            cursor = connection.execute(
                """
                UPDATE source_health
                SET last_run_at=CURRENT_TIMESTAMP,
                    last_failure_at=CURRENT_TIMESTAMP,
                    consecutive_failures=COALESCE(consecutive_failures,0)+1,
                    health_status='failed',
                    last_error=?,
                    jobs_found_last_run=0,
                    raw_jobs_last_run=0,
                    normalized_jobs_last_run=0,
                    duplicate_jobs_last_run=0,
                    eligible_jobs_last_run=0,
                    inserted_jobs_last_run=0,
                    rejected_jobs_last_run=0,
                    reject_role_last_run=0,
                    reject_location_last_run=0,
                    reject_hard_requirement_last_run=0,
                    reject_company_last_run=0,
                    reject_other_targeting_last_run=0,
                    accounting_delta_last_run=0,
                    request_count_last_run=0,
                    error_count_last_run=1,
                    last_duration_ms=NULL,
                    provider_used_last_run='scheduler',
                    filter_summary_json=?,
                    last_run_id=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE lower(source_name)=lower(?)
                """,
                (str(error)[:2000], failure_detail, run_id, source_name),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"Runner failure rejected for unregistered source: {source_name}"
                )
        elif connection.execute(
            "SELECT 1 FROM source_health WHERE lower(source_name)=lower(?)",
            (source_name,),
        ).fetchone() is None:
            raise RuntimeError(f"Runner failure rejected for unregistered source: {source_name}")
        connection.execute(
            """
            INSERT INTO source_runs(
              run_id,source_name,provider,completed_at,run_status,
              request_count,raw_count,normalized_count,duplicate_count,
              eligible_count,new_eligible_count,reject_role_count,
              reject_location_count,reject_hard_requirement_count,
              reject_company_count,reject_other_targeting_count,
              accounting_delta,duration_ms,error_count,detail_json
            ) VALUES (?,?,?,CURRENT_TIMESTAMP,'failed',0,0,0,0,0,0,0,0,0,0,0,0,NULL,1,?)
            """,
            (
                run_id,
                source_name,
                "scheduler",
                failure_detail,
            ),
        )
        for stage in ("FETCH", "NORMALIZE", "DEDUPE", "TARGET", "PERSIST", "TELEGRAM", "DOWNSTREAM"):
            connection.execute(
                """
                INSERT INTO source_run_stages(run_id,stage,item_count,stage_status)
                VALUES (?,?,0,?)
                """,
                (run_id, stage, "failed" if stage == "FETCH" else "not_run"),
            )
        connection.execute(
            """
            INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
            VALUES(NULL,'source_runner_failure','randomized_source_runner','failed',?)
            """,
            (
                json.dumps(
                    {
                        "source": source_name,
                        "run_id": run_id,
                        "worker_status": status,
                        "worker_returncode": int(returncode),
                        "error": str(error)[:1000],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    try:
        from app.telegram_run_visibility import enqueue_source_run_summary

        enqueue_source_run_summary(run_id)
    except Exception:
        # The listener's committed-run reconciliation owns durable recovery.
        pass
    return run_id


def _restore_worker_metrics_after_runner_failure(
    source_name: str,
    payload: dict[str, Any],
) -> None:
    # Supervisor failure may mark orchestration failed, but must not erase
    # provider facts returned by the child for the same attempt.
    if not isinstance(payload, dict) or not payload:
        return

    summary = payload.get("discovery_summary")
    if not isinstance(summary, dict):
        summary = {}

    raw = payload.get("raw_jobs_found", summary.get("raw_jobs_found"))
    eligible = payload.get(
        "jobs_after_dashboard_filters",
        summary.get("eligible", summary.get("unique_jobs_ready")),
    )
    if raw is None and eligible is None:
        return

    def as_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    providers = summary.get("providers_with_results") or []
    if isinstance(providers, list):
        provider_used = ",".join(str(v) for v in providers if str(v).strip())
    else:
        provider_used = str(providers or "")
    if not provider_used:
        provider_used = str(payload.get("site_name") or "worker")

    sql = (
        "UPDATE source_health SET "
        "jobs_found_last_run=?, raw_jobs_last_run=?, normalized_jobs_last_run=?, "
        "eligible_jobs_last_run=?, inserted_jobs_last_run=?, duplicate_jobs_last_run=?, "
        "rejected_jobs_last_run=?, reject_role_last_run=?, reject_location_last_run=?, "
        "reject_hard_requirement_last_run=?, reject_company_last_run=?, "
        "reject_other_targeting_last_run=?, provider_used_last_run=?, "
        "filter_summary_json=?, request_count_last_run=? WHERE source_name=?"
    )

    connection = get_connection()
    try:
        connection.execute(
            sql,
            (
                as_int(raw),
                as_int(raw),
                as_int(summary.get("raw_normalized", summary.get("normalized_jobs"))),
                as_int(eligible),
                as_int(payload.get("jobs_inserted")),
                as_int(payload.get(
                    "database_duplicates",
                    summary.get("duplicate", summary.get("duplicates_within_run")),
                )),
                as_int(summary.get("rejected_jobs")),
                as_int(summary.get("reject_role", summary.get("excluded_by_role"))),
                as_int(summary.get("reject_location", summary.get("excluded_by_location"))),
                as_int(summary.get("reject_hard_requirement", summary.get("excluded_by_hard_reject"))),
                as_int(summary.get("reject_company", summary.get("excluded_by_company_blacklist"))),
                as_int(summary.get("reject_other_targeting", summary.get("excluded_by_other_targeting"))),
                provider_used,
                json.dumps(summary, ensure_ascii=False, default=str),
                as_int(summary.get("request_count")),
                source_name,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def run_one(source: dict[str, Any]) -> dict[str, Any]:
    source_name = str(source.get("source_name") or "")
    timer_before_start = get_adapter_timer(source_name, row=source)
    scheduled_due_at = str(timer_before_start.get("next_allowed_at") or "")
    module = discover_worker(source_name)
    if not module:
        schedule = mark_source_completed(
            source_name,
            success=False,
            worker_status="no_worker_module",
            worker_returncode=127,
        )
        _record_runner_failure(
            source_name,
            status="no_worker_module",
            returncode=127,
            error="No configured worker module exists for this enabled source.",
        )
        return {
            "source": source_name,
            "status": "skipped",
            "reason": "no_worker_module",
            "schedule": schedule,
        }

    timeout_seconds = _source_timeout_seconds(source_name)
    mark_source_started(
        source_name,
        reservation_minutes=max(5, (timeout_seconds // 60) + 2),
    )
    command = [
        sys.executable, "-u", "-m", module, "--run-now"
    ]

    completed: subprocess.CompletedProcess[str] | None = None
    timed_out = False
    runner_error: str | None = None
    stdout = ""
    stderr = ""
    payload: dict[str, Any] = {}
    blocked = False
    deferred = False
    success = False
    status = "failed"
    returncode = 1

    try:
        completed, timed_out, runner_error = _run_worker_command(
            command,
            timeout_seconds=timeout_seconds,
        )
        returncode = int(completed.returncode)
        full_stdout = completed.stdout or ""
        full_stderr = completed.stderr or ""
        # Parse complete worker output first. Bound only retained log tails.
        payload = _last_json(full_stdout)
        stdout = full_stdout[-20000:]
        stderr = full_stderr[-20000:]
        combined = full_stdout + "\n" + full_stderr
        blocked = _is_blocked(combined, payload)
        deferred = _is_deferred(combined, payload)
        success = (
            not timed_out
            and completed.returncode == 0
            and not blocked
            and not deferred
            and payload.get("success", True) is not False
        )
        status = (
            "timeout"
            if timed_out
            else "deferred"
            if deferred
            else "completed"
            if success
            else "blocked"
            if blocked
            else "failed"
        )
    except Exception as error:
        runner_error = f"{type(error).__name__}: {error}"
        stderr = (stderr + "\n" + runner_error).strip()
        status = "runner_exception"
        returncode = 125
    finally:
        schedule = mark_source_completed(
            source_name,
            success=success or deferred,
            blocked=blocked,
            deferred=deferred,
            worker_status=status,
            worker_returncode=returncode,
        )
        if not (success or deferred):
            failure_detail = runner_error or (
                f"Worker finished with scheduler status {status} and return code {returncode}."
            )
            _record_runner_failure(
                source_name,
                status=status,
                returncode=returncode,
                error=failure_detail,
            )
            _restore_worker_metrics_after_runner_failure(source_name, payload)

    due_incident: dict[str, Any] = {}
    if deferred:
        try:
            from app.telegram_run_visibility import enqueue_due_incident

            reason_code, reason_text = _deferred_incident_reason(payload)
            due_incident = enqueue_due_incident(
                source_name,
                scheduled_due_at,
                reason_code=reason_code,
                reason_text=reason_text,
                schedule_state="deferred",
            )
        except Exception as error:
            due_incident = {
                "queued": False,
                "reason": f"incident_enqueue_deferred:{type(error).__name__}",
            }

    notification_delivery: dict[str, Any] = {}
    try:
        from app.telegram_run_visibility import (
            deliver_pending_operational_cards,
            reconcile_terminal_run_outbox,
        )

        reconcile_terminal_run_outbox(limit=20)
        notification_delivery = deliver_pending_operational_cards(limit=5)
    except Exception as error:
        notification_delivery = {
            "attempted": 0,
            "sent": 0,
            "failed": 0,
            "reason": f"outbox_delivery_deferred:{type(error).__name__}",
        }

    return {
        "source": source_name,
        "module": module,
        "status": status,
        "returncode": returncode,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "runner_error": runner_error,
        "stdout_tail": stdout[-3000:],
        "stderr_tail": stderr[-3000:],
        "worker_payload": payload,
        "schedule": schedule,
        "due_incident": due_incident,
        "operational_notification_delivery": notification_delivery,
    }



def main() -> None:
    from app.database import get_setting

    orchestration = get_setting("orchestration", {}) or {}
    if bool(orchestration.get("maintenance_mode", False)):
        print(json.dumps({
            "success": True,
            "mode": "maintenance",
            "status": "skipped",
            "reason": "canonical_orchestration_maintenance_mode",
            "network_request_made": False,
            "telegram_messages": 0,
            "n8n_calls": 0,
        }, indent=2))
        return

    # AADIL_OPT_US_NATIONWIDE_INTEGRITY_V1
    try:
        import json as _aadil_json_v1
        from app.opt_us_nationwide_integrity_v1 import reconcile_n8n_queue as _aadil_reconcile_n8n_queue_v1
        _aadil_reconcile_result_v1 = _aadil_reconcile_n8n_queue_v1()
        if _aadil_reconcile_result_v1.get('terminalized'):
            print(_aadil_json_v1.dumps({'n8n_queue_reconciled': _aadil_reconcile_result_v1['terminalized']}, default=str), flush=True)
    except Exception as _aadil_reconcile_error_v1:
        print(f'n8n queue reconciliation warning: {type(_aadil_reconcile_error_v1).__name__}', flush=True)

    args = parse_args()
    mode = (
        "manual_ready_only"
        if args.run_now
        else "scheduled_randomized"
    )
    try:
        acquire_lock(mode)
    except RuntimeError as error:
        message = str(error)
        if "Another randomized source runner is active:" in message:
            print(json.dumps({
                "success": True,
                "mode": mode,
                "status": "skipped_existing_runner",
                "reason": message,
                "one_source_per_tick": True,
                "network_request_made": False,
                "telegram_messages": 0,
                "n8n_calls": 0,
            }, indent=2, ensure_ascii=False, default=str))
            return
        raise

    try:
        from app.stale_worker_reconciler_v1 import (
            reconcile_dashboard_stale_workers,
        )
        reconcile_dashboard_stale_workers()
        sources = load_enabled_sources()
        timers = {
            str(source.get("source_name") or ""):
                get_adapter_timer(
                    str(source.get("source_name") or ""),
                    row=source,
                )
            for source in sources
        }
        ready = [
            source
            for source in sources
            if timers[str(source.get("source_name") or "")]["due"]
        ]

        # Preserve a random tie-break while always serving the oldest overdue
        # source first, preventing ready JobSpy boards from starving.
        secrets.SystemRandom().shuffle(ready)
        ready.sort(key=lambda source: _due_sort_key(source, timers))

        reason = active_work_reason()
        if reason and ready:
            results = []
            for source in ready:
                source_name = str(
                    source.get("source_name") or ""
                )
                due_incident: dict[str, Any] = {}
                try:
                    from app.telegram_run_visibility import enqueue_due_incident

                    due_incident = enqueue_due_incident(
                        source_name,
                        str((timers.get(source_name) or {}).get("next_allowed_at") or ""),
                        reason_code="serialized_queue_wait",
                        reason_text="Downstream production work is active; the serialized source lane is waiting",
                        schedule_state="deferred",
                    )
                except Exception as error:
                    due_incident = {
                        "queued": False,
                        "reason": f"incident_enqueue_deferred:{type(error).__name__}",
                    }
                results.append(
                    {
                        "source": source_name,
                        "status": "deferred",
                        "reason": reason,
                        "due_incident": due_incident,
                        "schedule": mark_source_completed(
                            source_name,
                            success=True,
                            deferred=True,
                            worker_status="active_work_deferred",
                            worker_returncode=0,
                        ),
                    }
                )
            notification_delivery: dict[str, Any] = {}
            try:
                from app.telegram_run_visibility import deliver_pending_operational_cards

                notification_delivery = deliver_pending_operational_cards(limit=1)
            except Exception as error:
                notification_delivery = {
                    "attempted": 0,
                    "sent": 0,
                    "failed": 0,
                    "reason": f"outbox_delivery_deferred:{type(error).__name__}",
                }
            print(json.dumps({
                "success": True,
                "mode": mode,
                "worker_action": "defer",
                "reason": reason,
                "results": results,
                "operational_notification_delivery": notification_delivery,
                "network_request_made": False,
                "telegram_messages": 0,
                "n8n_calls": 0,
            }, indent=2, default=str))
            return

        if args.max_sources > 0:
            maximum = max(1, args.max_sources)
        else:
            # One source per invocation for both launchd and /run. This keeps
            # the global lane bounded and prevents a manual bulk batch from
            # monopolizing every automatic tick.
            maximum = 1

        selected = ready[:maximum]
        results = [run_one(source) for source in selected]
        if not selected:
            # A quiet scheduler tick may retry already-persisted cards, but it
            # never creates a heartbeat message of its own.
            try:
                from app.telegram_run_visibility import (
                    deliver_pending_operational_cards,
                    reconcile_missed_due_incidents,
                    reconcile_stale_delivery_claims,
                    reconcile_terminal_run_outbox,
                )

                reconcile_stale_delivery_claims()
                reconcile_terminal_run_outbox(limit=20)
                reconcile_missed_due_incidents(max_new=1)
                deliver_pending_operational_cards(limit=5)
            except Exception:
                pass
        failed = sum(
            item["status"]
            in {"failed", "blocked", "timeout", "runner_exception"}
            for item in results
        )
        print(json.dumps({
            "success": failed == 0,
            "mode": mode,
            "enabled_sources": len(sources),
            "ready_sources": len(ready),
            "selected_sources": len(selected),
            "one_source_per_tick": True,
            "selection_policy": "oldest_due_with_random_tie_break",
            "results": results,
            "n8n_calls": 0,
        }, indent=2, ensure_ascii=False, default=str))
        if failed:
            raise SystemExit(1)
    finally:
        release_lock()



if __name__ == "__main__":
    main()
