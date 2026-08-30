from __future__ import annotations

import fcntl
import html
import json
import math
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.database import DB_PATH, ROOT_DIR, get_connection, get_setting, save_setting
from app.ui_time import format_local_clock


RUN_SUMMARY_KIND = "adapter_run_summary"
DUE_INCIDENT_KIND = "adapter_due_incident"
RECOVERY_KIND = "adapter_due_recovery"
SENT_EVENT = "telegram_adapter_summary_delivered"
FAILED_EVENT = "telegram_adapter_summary_delivery_failed"
GENERATED_EVENT = "telegram_adapter_summary_generated"
INCIDENT_EVENT = "telegram_adapter_due_incident_generated"
RECOVERY_EVENT = "telegram_adapter_due_recovery_generated"
DELIVERY_LOCK = ROOT_DIR / "data" / ".locks" / "telegram_operational_outbox.lock"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS telegram_operational_outbox (
    logical_id TEXT PRIMARY KEY,
    notification_kind TEXT NOT NULL,
    source_name TEXT,
    run_id TEXT,
    incident_id TEXT,
    delivery_state TEXT NOT NULL DEFAULT 'pending',
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_token TEXT,
    lease_expires_at TEXT,
    telegram_message_id INTEGER,
    last_error_class TEXT,
    last_error_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT,
    FOREIGN KEY(run_id) REFERENCES source_runs(run_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_telegram_operational_outbox_delivery
ON telegram_operational_outbox(delivery_state, next_attempt_at, created_at);
CREATE INDEX IF NOT EXISTS idx_telegram_operational_outbox_run
ON telegram_operational_outbox(run_id);
CREATE TABLE IF NOT EXISTS telegram_adapter_incidents (
    incident_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    due_at TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_text TEXT NOT NULL,
    incident_state TEXT NOT NULL DEFAULT 'open',
    alert_logical_id TEXT NOT NULL UNIQUE,
    recovery_logical_id TEXT UNIQUE,
    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    resolved_run_id TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(resolved_run_id) REFERENCES source_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_telegram_adapter_incidents_open
ON telegram_adapter_incidents(incident_state, source_name, due_at);
"""


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owned = connection is None
    db = connection or get_connection()
    try:
        db.executescript(SCHEMA_SQL)
        if owned:
            db.commit()
    finally:
        if owned:
            db.close()


def ensure_contract_activation() -> dict[str, Any]:
    settings = dict(get_setting("source_run_notifications", {}) or {})
    if not bool(settings.get("enabled")):
        return settings
    if not str(settings.get("contract_enabled_at") or "").strip():
        settings["contract_enabled_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        save_setting(
            "source_run_notifications",
            settings,
            changed_by="telegram_run_visibility:contract_activation",
        )
    return settings


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _clean_error(value: Any) -> str:
    """Keep operational meaning while excluding URLs, tokens, and tracebacks."""
    text = " ".join(str(value or "").replace("\n", " ").split())
    lowered = text.casefold()
    if any(term in lowered for term in ("token=", "api_key", "apikey", "authorization:", "bearer ")):
        return "Sensitive provider detail is available in protected logs."
    if "http" in lowered:
        words = [word for word in text.split() if not word.casefold().startswith("http")]
        text = " ".join(words)
    return text[:240] or "Detailed evidence is available in System / Diagnostics."


def _record_event(
    connection: sqlite3.Connection,
    event_type: str,
    status: str,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
        VALUES(NULL,?,'telegram_run_visibility',?,?)
        """,
        (event_type, status, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )


def _terminal_status(run: dict[str, Any], detail: dict[str, Any]) -> tuple[str, str, str]:
    status = str(run.get("run_status") or "").casefold()
    worker_status = str(detail.get("worker_status") or "").casefold()
    raw = _as_int(run.get("raw_count"))
    normalized = _as_int(run.get("normalized_count"))
    duplicates = _as_int(run.get("duplicate_count"))
    eligible = _as_int(run.get("eligible_count"))
    new_eligible = _as_int(run.get("new_eligible_count"))
    rejected = sum(
        _as_int(run.get(key))
        for key in (
            "reject_role_count",
            "reject_location_count",
            "reject_hard_requirement_count",
            "reject_company_count",
            "reject_other_targeting_count",
        )
    )
    if "timeout" in status or "timeout" in worker_status:
        return "timeout", "Timed out", "❌"
    if status in {"failed", "error", "crashed", "blocked"}:
        return "failed", _failure_label(detail), "❌"
    if status in {"degraded", "partial", "partial_success"} or _as_int(run.get("error_count")):
        return "degraded", "Completed with partial provider failures", "⚠️"
    if raw == 0:
        return "completed_zero", "Completed · no current opportunities", "✅"
    if normalized > 0 and duplicates >= normalized:
        return "duplicates_only", "Completed · duplicates only", "✅"
    if eligible == 0 and rejected > 0:
        return "filtered_only", "Completed · all opportunities filtered", "✅"
    if new_eligible == 0:
        return "no_new", "Completed · no new opportunities", "✅"
    return "completed", "Completed", "✅"


def _failure_label(detail: dict[str, Any]) -> str:
    worker_status = str(detail.get("worker_status") or "").casefold()
    error = str(detail.get("error") or "").casefold()
    combined = f"{worker_status} {error}"
    if "timeout" in combined:
        return "Timed out"
    if "auth" in combined or "credential" in combined or "401" in combined:
        return "Authentication required"
    if "config" in combined or "no_worker_module" in combined:
        return "Configuration required"
    if "network" in combined or "connect" in combined or "dns" in combined:
        return "Network unavailable"
    if "429" in combined or "rate" in combined:
        return "Provider rate limited"
    if "http" in combined or "provider" in combined:
        return "Provider request failed"
    return "Adapter run failed"


def _run_snapshot(connection: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT r.*, h.enabled, h.last_http_status, h.last_error,
               s.next_run_at, s.schedule_state, s.schedule_reason
        FROM source_runs r
        LEFT JOIN source_health h ON lower(h.source_name)=lower(r.source_name)
        LEFT JOIN source_random_schedule s ON lower(s.source_name)=lower(r.source_name)
        WHERE r.run_id=?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    run = dict(row)
    detail = _safe_json(run.pop("detail_json", "{}"), {})
    outcome_code, outcome_label, icon = _terminal_status(run, detail)
    rejected = sum(
        _as_int(run.get(key))
        for key in (
            "reject_role_count",
            "reject_location_count",
            "reject_hard_requirement_count",
            "reject_company_count",
            "reject_other_targeting_count",
        )
    )
    error_text = detail.get("error") or run.get("last_error") or ""
    return {
        "run_id": str(run["run_id"]),
        "source_name": str(run["source_name"]),
        "provider": str(run.get("provider") or run["source_name"]),
        "run_status": str(run.get("run_status") or "unknown"),
        "outcome_code": outcome_code,
        "outcome_label": outcome_label,
        "icon": icon,
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "next_run_at": run.get("next_run_at"),
        "schedule_state": run.get("schedule_state"),
        "schedule_reason": run.get("schedule_reason"),
        "request_count": _as_int(run.get("request_count")),
        "raw_count": _as_int(run.get("raw_count")),
        "normalized_count": _as_int(run.get("normalized_count")),
        "duplicate_count": _as_int(run.get("duplicate_count")),
        "rejected_count": rejected,
        "eligible_count": _as_int(run.get("eligible_count")),
        "new_eligible_count": _as_int(run.get("new_eligible_count")),
        "stored_count": _as_int(run.get("new_eligible_count")),
        "duration_ms": run.get("duration_ms"),
        "error_count": _as_int(run.get("error_count")),
        "http_status": _as_int(run.get("last_http_status")) or None,
        "successful_requests": _as_int(detail.get("successful_companies") or detail.get("successful_requests")),
        "failed_requests": _as_int(detail.get("failed_companies") or detail.get("failed_requests")),
        "failure_detail": _clean_error(error_text) if error_text else "",
    }


def _insert_outbox(
    connection: sqlite3.Connection,
    *,
    logical_id: str,
    kind: str,
    source_name: str,
    payload: dict[str, Any],
    run_id: str | None = None,
    incident_id: str | None = None,
    delay_seconds: int = 3,
) -> bool:
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO telegram_operational_outbox(
          logical_id,notification_kind,source_name,run_id,incident_id,
          delivery_state,payload_json,next_attempt_at
        ) VALUES(?,?,?,?,?,'pending',?,datetime('now',?))
        """,
        (
            logical_id,
            kind,
            source_name,
            run_id,
            incident_id,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            f"+{max(0, int(delay_seconds))} seconds",
        ),
    )
    return cursor.rowcount == 1


def enqueue_source_run_summary(run_id: str) -> dict[str, Any]:
    """Create one durable terminal summary after source_runs has committed."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        snapshot = _run_snapshot(connection, run_id)
        if snapshot is None:
            connection.rollback()
            return {"queued": False, "reason": "run_not_committed", "run_id": run_id}
        if not snapshot.get("completed_at"):
            connection.rollback()
            return {"queued": False, "reason": "run_not_terminal", "run_id": run_id}
        logical_id = f"adapter_run_summary:{run_id}"
        created = _insert_outbox(
            connection,
            logical_id=logical_id,
            kind=RUN_SUMMARY_KIND,
            source_name=str(snapshot["source_name"]),
            run_id=run_id,
            payload=snapshot,
        )
        if created:
            _record_event(
                connection,
                GENERATED_EVENT,
                "pending",
                {
                    "logical_id": logical_id,
                    "run_id": run_id,
                    "source": snapshot["source_name"],
                    "outcome": snapshot["outcome_code"],
                },
            )
        recovered = _resolve_incidents(connection, snapshot)
        connection.commit()
        return {
            "queued": created,
            "already_exists": not created,
            "logical_id": logical_id,
            "run_id": run_id,
            "recovery_events": recovered,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def enqueue_committed_payload(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return {"queued": False, "reason": "awaiting_canonical_run_commit"}
    return enqueue_source_run_summary(run_id)


def enqueue_due_incident(
    source_name: str,
    due_at: str,
    *,
    reason_code: str,
    reason_text: str,
    schedule_state: str = "deferred",
) -> dict[str, Any]:
    """Persist an incident when a selected due adapter cannot start.

    This runs before schedule advancement would make the missed due window
    disappear. Delivery slots are globally staggered so several deferred
    adapters cannot create a Telegram burst after sleep or shared work.
    """
    settings = ensure_contract_activation()
    if not bool(settings.get("enabled")):
        return {"queued": False, "reason": "run_summary_policy_disabled"}
    source = str(source_name or "").strip()
    due = str(due_at or "").strip()
    if not source or not due:
        return {"queued": False, "reason": "missing_source_or_due_time"}
    stable_due = due.replace(" ", "T").replace("+00:00", "Z")
    incident_id = f"{source}:{stable_due}"
    logical_id = f"adapter_due_incident:{incident_id}"
    interval_minutes = max(
        5,
        _as_int(settings.get("missed_due_alert_interval_minutes") or 30),
    )
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        enabled = connection.execute(
            "SELECT enabled FROM source_health WHERE lower(source_name)=lower(?)",
            (source,),
        ).fetchone()
        if enabled is None or not bool(enabled["enabled"]):
            connection.rollback()
            return {"queued": False, "reason": "source_not_enabled", "source_name": source}
        existing = connection.execute(
            "SELECT alert_logical_id FROM telegram_adapter_incidents WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
        if existing is not None:
            connection.rollback()
            return {
                "queued": False,
                "already_exists": True,
                "logical_id": str(existing["alert_logical_id"]),
            }
        anchor = connection.execute(
            """
            SELECT COALESCE(sent_at,next_attempt_at,created_at) AS delivery_anchor
            FROM telegram_operational_outbox
            WHERE notification_kind=? AND delivery_state<>'cancelled'
            ORDER BY datetime(COALESCE(sent_at,next_attempt_at,created_at)) DESC
            LIMIT 1
            """,
            (DUE_INCIDENT_KIND,),
        ).fetchone()
        delay_seconds = 0
        if anchor and anchor["delivery_anchor"]:
            try:
                anchor_at = datetime.fromisoformat(
                    str(anchor["delivery_anchor"]).replace("Z", "+00:00")
                )
                if anchor_at.tzinfo is None:
                    anchor_at = anchor_at.replace(tzinfo=timezone.utc)
                slot_at = anchor_at.astimezone(timezone.utc) + timedelta(
                    minutes=interval_minutes
                )
                delay_seconds = max(
                    0,
                    math.ceil(
                        (slot_at - datetime.now(timezone.utc)).total_seconds()
                    ),
                )
            except ValueError:
                delay_seconds = interval_minutes * 60
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO telegram_adapter_incidents(
              incident_id,source_name,due_at,reason_code,reason_text,alert_logical_id
            ) VALUES(?,?,?,?,?,?)
            """,
            (incident_id, source, due, reason_code, reason_text, logical_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return {"queued": False, "already_exists": True, "logical_id": logical_id}
        payload = {
            "source_name": source,
            "due_at": due,
            "reason_code": reason_code,
            "reason_text": reason_text,
            "schedule_state": schedule_state,
        }
        _insert_outbox(
            connection,
            logical_id=logical_id,
            kind=DUE_INCIDENT_KIND,
            source_name=source,
            incident_id=incident_id,
            payload=payload,
            delay_seconds=delay_seconds,
        )
        _record_event(
            connection,
            INCIDENT_EVENT,
            "pending",
            {
                "logical_id": logical_id,
                "source": source,
                "reason": reason_code,
                "delivery_delay_seconds": delay_seconds,
            },
        )
        connection.commit()
        return {
            "queued": True,
            "logical_id": logical_id,
            "delivery_delay_seconds": delay_seconds,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def reconcile_terminal_run_outbox(*, limit: int = 100) -> dict[str, Any]:
    """Recover committed summaries missed by a crashed post-commit caller.

    The contract activation timestamp prevents a first deployment from replaying
    the project's entire historical source-run archive into Telegram.
    """
    settings = ensure_contract_activation()
    activated_at = str(settings.get("contract_enabled_at") or "").strip()
    if not bool(settings.get("enabled")) or not activated_at:
        return {"reconciled": 0, "reason": "contract_not_active"}
    connection = get_connection()
    try:
        ensure_schema(connection)
        rows = connection.execute(
            """
            SELECT r.run_id
            FROM source_runs r
            LEFT JOIN telegram_operational_outbox o
              ON o.logical_id='adapter_run_summary:' || r.run_id
            WHERE r.completed_at IS NOT NULL AND datetime(r.completed_at)>=datetime(?)
              AND o.logical_id IS NULL
            ORDER BY datetime(r.completed_at) LIMIT ?
            """,
            (activated_at, max(0, int(limit))),
        ).fetchall()
    finally:
        connection.close()
    reconciled = 0
    for row in rows:
        result = enqueue_source_run_summary(str(row["run_id"]))
        reconciled += int(bool(result.get("queued")))
    return {"reconciled": reconciled, "examined": len(rows)}


def _resolve_incidents(connection: sqlite3.Connection, snapshot: dict[str, Any]) -> int:
    if snapshot.get("outcome_code") not in {
        "completed", "completed_zero", "duplicates_only", "filtered_only", "no_new", "degraded"
    }:
        return 0
    incidents = connection.execute(
        """
        SELECT i.*, o.delivery_state AS alert_delivery_state
        FROM telegram_adapter_incidents i
        LEFT JOIN telegram_operational_outbox o ON o.logical_id=i.alert_logical_id
        WHERE i.incident_state='open' AND lower(i.source_name)=lower(?)
        ORDER BY i.opened_at
        """,
        (snapshot["source_name"],),
    ).fetchall()
    recovered = 0
    for row in incidents:
        incident = dict(row)
        recovery_id = f"adapter_due_recovery:{incident['incident_id']}"
        if incident.get("alert_delivery_state") == "sent":
            payload = {
                "source_name": snapshot["source_name"],
                "incident_id": incident["incident_id"],
                "previous_condition": incident["reason_text"],
                "recovered_at": snapshot.get("completed_at"),
                "run": snapshot,
            }
            if _insert_outbox(
                connection,
                logical_id=recovery_id,
                kind=RECOVERY_KIND,
                source_name=str(snapshot["source_name"]),
                run_id=str(snapshot["run_id"]),
                incident_id=str(incident["incident_id"]),
                payload=payload,
            ):
                recovered += 1
                _record_event(connection, RECOVERY_EVENT, "pending", {"logical_id": recovery_id, "source": snapshot["source_name"]})
        else:
            connection.execute(
                """
                UPDATE telegram_operational_outbox
                SET delivery_state='cancelled',updated_at=CURRENT_TIMESTAMP,
                    last_error_class='incident_cleared_before_delivery',
                    last_error_text='The adapter started before the delayed-run alert was delivered.'
                WHERE logical_id=? AND delivery_state IN ('pending','retry')
                """,
                (incident["alert_logical_id"],),
            )
        connection.execute(
            """
            UPDATE telegram_adapter_incidents
            SET incident_state='resolved',resolved_at=CURRENT_TIMESTAMP,
                resolved_run_id=?,recovery_logical_id=?,updated_at=CURRENT_TIMESTAMP
            WHERE incident_id=?
            """,
            (snapshot["run_id"], recovery_id if incident.get("alert_delivery_state") == "sent" else None, incident["incident_id"]),
        )
    return recovered


def reconcile_missed_due_incidents(
    *,
    now: datetime | None = None,
    grace_minutes: int = 12,
    max_new: int = 1,
) -> dict[str, Any]:
    """Queue bounded, deduplicated alerts for genuinely overdue enabled sources."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = (current.astimezone(timezone.utc) - timedelta(minutes=max(1, grace_minutes))).strftime("%Y-%m-%d %H:%M:%S")
    # Resolve policy before taking the write lock.  Both the listener and the
    # scheduled runner reconcile incidents, so the global escalation gate and
    # insertion must share one transaction.  Reading the gate before BEGIN
    # IMMEDIATE permits a classic check-then-insert race during a busy backlog.
    settings = ensure_contract_activation()
    alert_interval = max(
        5,
        _as_int(settings.get("missed_due_alert_interval_minutes") or 30),
    )
    recent_cutoff = (
        current.astimezone(timezone.utc) - timedelta(minutes=alert_interval)
    ).strftime("%Y-%m-%d %H:%M:%S")
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT h.source_name,s.next_run_at,s.schedule_state,s.schedule_reason,
                   h.health_status,h.last_error
            FROM source_health h
            JOIN source_random_schedule s ON lower(s.source_name)=lower(h.source_name)
            WHERE h.enabled=1 AND s.next_run_at IS NOT NULL
              AND datetime(s.next_run_at)<=datetime(?)
              AND lower(COALESCE(s.schedule_state,'')) NOT IN ('running','disabled')
            ORDER BY datetime(s.next_run_at), lower(h.source_name)
            """,
            (cutoff,),
        ).fetchall()
        recent_alert = connection.execute(
            """
            SELECT 1 FROM telegram_adapter_incidents
            WHERE datetime(opened_at)>datetime(?)
            ORDER BY datetime(opened_at) DESC LIMIT 1
            """,
            (recent_cutoff,),
        ).fetchone()
        if recent_alert is not None:
            connection.commit()
            return {
                "overdue": len(rows),
                "created": [],
                "bounded": bool(rows),
                "reason": "global_incident_alert_interval",
            }
        orchestration = get_setting("orchestration", {}) or {}
        created: list[str] = []
        for row in rows:
            if len(created) >= max(0, int(max_new)):
                break
            item = dict(row)
            due_at = str(item["next_run_at"])
            reason_code, reason_text = _incident_reason(item, orchestration)
            stable_due = due_at.replace(" ", "T").replace("+00:00", "Z")
            incident_id = f"{item['source_name']}:{stable_due}"
            logical_id = f"adapter_due_incident:{incident_id}"
            incident_cursor = connection.execute(
                """
                INSERT OR IGNORE INTO telegram_adapter_incidents(
                  incident_id,source_name,due_at,reason_code,reason_text,alert_logical_id
                ) VALUES(?,?,?,?,?,?)
                """,
                (incident_id, item["source_name"], due_at, reason_code, reason_text, logical_id),
            )
            if incident_cursor.rowcount != 1:
                continue
            payload = {
                "source_name": item["source_name"],
                "due_at": due_at,
                "reason_code": reason_code,
                "reason_text": reason_text,
                "schedule_state": item.get("schedule_state"),
            }
            _insert_outbox(
                connection,
                logical_id=logical_id,
                kind=DUE_INCIDENT_KIND,
                source_name=str(item["source_name"]),
                incident_id=incident_id,
                payload=payload,
                delay_seconds=0,
            )
            _record_event(connection, INCIDENT_EVENT, "pending", {"logical_id": logical_id, "source": item["source_name"], "reason": reason_code})
            created.append(logical_id)
        connection.commit()
        return {"overdue": len(rows), "created": created, "bounded": len(rows) > len(created)}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _incident_reason(row: dict[str, Any], orchestration: dict[str, Any]) -> tuple[str, str]:
    if bool(orchestration.get("maintenance_mode")):
        return "maintenance_mode", "Canonical maintenance mode is active"
    state = str(row.get("schedule_state") or "").casefold()
    reason = str(row.get("schedule_reason") or "").casefold()
    health = str(row.get("health_status") or "").casefold()
    if "backoff" in state or "rate" in state or "backoff" in reason:
        return "failure_backoff", "Provider failure backoff remains active"
    if health in {"setup_required", "configuration_required", "not_configured"}:
        return "configuration_required", "Adapter configuration requires attention"
    try:
        from app.randomized_source_runner import inspect_source_runner_lock

        lock = inspect_source_runner_lock()
        if lock.get("state") == "active":
            return "worker_lock_active", "The serialized source worker is still active"
    except Exception:
        pass
    connection = get_connection()
    try:
        recent = connection.execute(
            """
            SELECT source_name FROM source_random_schedule
            WHERE lower(source_name)<>lower(?) AND last_started_at IS NOT NULL
              AND datetime(last_started_at)>=datetime('now','-15 minutes')
            ORDER BY datetime(last_started_at) DESC LIMIT 1
            """,
            (row.get("source_name"),),
        ).fetchone()
    finally:
        connection.close()
    if recent:
        return (
            "serialized_queue_wait",
            f"The single-worker lane recently served {recent['source_name']}; this adapter is awaiting its turn",
        )
    return "scheduler_delay_unknown", "The scheduler has not started this due adapter; investigation is required"


def _field(lines: list[str], label: str, value: Any) -> None:
    lines.extend([f"<b>{html.escape(label)}</b>", html.escape(str(value)), ""])


def _duration(value: Any) -> str:
    try:
        seconds = max(0.0, float(value or 0) / 1000.0)
    except (TypeError, ValueError):
        return "Not measured"
    if seconds < 1:
        return f"{round(seconds, 1)} sec"
    if seconds < 120:
        return f"{round(seconds):,} sec"
    return f"{round(seconds / 60, 1)} min"


def format_operational_card(kind: str, payload: dict[str, Any]) -> str:
    divider = "━━━━━━━━━━━━━━━━━━━━"
    if kind == RUN_SUMMARY_KIND:
        run = payload
        title = "ADAPTER FAILED" if run.get("outcome_code") in {"failed", "timeout"} else "ADAPTER DEGRADED" if run.get("outcome_code") == "degraded" else "ADAPTER RUN"
        lines = [divider, f"{run.get('icon', 'ℹ️')} <b>MUNSHI APPLY · {title}</b>", divider, ""]
        _field(lines, "Provider", run.get("source_name") or "Not available")
        _field(lines, "Status", run.get("outcome_label") or "Not available")
        _field(lines, "Started", format_local_clock(run.get("started_at")))
        _field(lines, "Completed", format_local_clock(run.get("completed_at")))
        _field(lines, "Provider requests", f"{_as_int(run.get('request_count')):,}")
        _field(lines, "Records scanned", f"{_as_int(run.get('raw_count')):,}")
        _field(lines, "Normalized", f"{_as_int(run.get('normalized_count')):,}")
        _field(lines, "Duplicates", f"{_as_int(run.get('duplicate_count')):,}")
        _field(lines, "Rejected by targeting", f"{_as_int(run.get('rejected_count')):,}")
        _field(lines, "Eligible", f"{_as_int(run.get('eligible_count')):,}")
        _field(lines, "New eligible", f"{_as_int(run.get('new_eligible_count')):,}")
        _field(lines, "New jobs stored", f"{_as_int(run.get('stored_count')):,}")
        if run.get("outcome_code") == "degraded":
            _field(lines, "Successful requests", f"{_as_int(run.get('successful_requests')):,}")
            _field(lines, "Failed requests", f"{_as_int(run.get('failed_requests')):,}")
        if run.get("outcome_code") in {"failed", "timeout", "degraded"}:
            if run.get("http_status"):
                _field(lines, "HTTP status", run["http_status"])
            if run.get("failure_detail"):
                _field(lines, "Operational detail", run["failure_detail"])
            _field(lines, "Retry policy", "Failure backoff" if str(run.get("schedule_state")) == "failure_backoff" else "Canonical scheduler policy")
        _field(lines, "Duration", _duration(run.get("duration_ms")))
        _field(lines, "Next due", format_local_clock(run.get("next_run_at"), empty="Waiting for schedule"))
        lines.extend(["<b>Run ID</b>", "Available in System / Diagnostics", divider])
        return "\n".join(lines)[:4000]
    if kind == DUE_INCIDENT_KIND:
        lines = [divider, "⚠️ <b>MUNSHI APPLY · ADAPTER RUN DELAYED</b>", divider, ""]
        _field(lines, "Provider", payload.get("source_name") or "Not available")
        _field(lines, "Scheduled", format_local_clock(payload.get("due_at")))
        _field(lines, "Current status", "Run has not started")
        _field(lines, "Reason", payload.get("reason_text") or "Investigation required")
        lines.append(divider)
        return "\n".join(lines)[:4000]
    run = dict(payload.get("run") or {})
    lines = [divider, "✅ <b>MUNSHI APPLY · ADAPTER RECOVERED</b>", divider, ""]
    _field(lines, "Provider", payload.get("source_name") or "Not available")
    _field(lines, "Previous condition", payload.get("previous_condition") or "Run was delayed")
    _field(lines, "Recovered", format_local_clock(payload.get("recovered_at")))
    _field(lines, "Result", run.get("outcome_label") or "Completed")
    _field(lines, "Records scanned", f"{_as_int(run.get('raw_count')):,}")
    _field(lines, "Eligible", f"{_as_int(run.get('eligible_count')):,}")
    _field(lines, "New eligible", f"{_as_int(run.get('new_eligible_count')):,}")
    _field(lines, "Duration", _duration(run.get("duration_ms")))
    _field(lines, "Next due", format_local_clock(run.get("next_run_at"), empty="Waiting for schedule"))
    lines.append(divider)
    return "\n".join(lines)[:4000]


def _refresh_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("source_name") or "")
    if not source:
        return payload
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT next_run_at,schedule_state,schedule_reason FROM source_random_schedule WHERE lower(source_name)=lower(?)",
            (source,),
        ).fetchone()
        if row:
            payload = {**payload, **dict(row)}
    finally:
        connection.close()
    return payload


def _claim_next(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT * FROM telegram_operational_outbox
        WHERE delivery_state IN ('pending','retry')
          AND datetime(next_attempt_at)<=CURRENT_TIMESTAMP
        ORDER BY datetime(next_attempt_at),datetime(created_at) LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    token = uuid.uuid4().hex
    cursor = connection.execute(
        """
        UPDATE telegram_operational_outbox
        SET delivery_state='sending',lease_token=?,lease_expires_at=datetime('now','+90 seconds'),
            attempt_count=attempt_count+1,updated_at=CURRENT_TIMESTAMP
        WHERE logical_id=? AND delivery_state IN ('pending','retry')
        """,
        (token, row["logical_id"]),
    )
    if cursor.rowcount != 1:
        return None
    claimed = dict(row)
    claimed["lease_token"] = token
    claimed["attempt_count"] = _as_int(row["attempt_count"]) + 1
    return claimed


def _retry_delay(attempt: int) -> int:
    return min(1800, max(15, 15 * (2 ** min(max(attempt - 1, 0), 7))))


def deliver_pending_operational_cards(
    *,
    limit: int = 5,
    send_function: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = ensure_contract_activation()
    runtime = get_setting("runtime", {}) or {}
    if not bool(settings.get("enabled")):
        return {"attempted": 0, "sent": 0, "failed": 0, "reason": "run_summary_policy_disabled"}
    if not bool(runtime.get("telegram_enabled")):
        return {"attempted": 0, "sent": 0, "failed": 0, "reason": "telegram_runtime_disabled"}
    DELIVERY_LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = DELIVERY_LOCK.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"attempted": 0, "sent": 0, "failed": 0, "reason": "delivery_worker_already_active"}
        if send_function is None:
            from app.telegram_client import CHAT_ID, telegram_request

            if not CHAT_ID:
                return {"attempted": 0, "sent": 0, "failed": 0, "reason": "telegram_chat_not_configured"}

            def send_function(method: str, payload: dict[str, Any]) -> dict[str, Any]:
                return telegram_request(method, payload)
            chat_id: Any = CHAT_ID
        else:
            chat_id = "test-chat"
        totals = {"attempted": 0, "sent": 0, "failed": 0, "logical_ids": []}
        for _ in range(max(0, int(limit))):
            connection = get_connection()
            try:
                ensure_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                claimed = _claim_next(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            if claimed is None:
                break
            totals["attempted"] += 1
            payload = _safe_json(claimed.get("payload_json"), {})
            if claimed["notification_kind"] in {RUN_SUMMARY_KIND, RECOVERY_KIND}:
                if claimed["notification_kind"] == RUN_SUMMARY_KIND:
                    payload = _refresh_schedule(payload)
                else:
                    payload["run"] = _refresh_schedule(dict(payload.get("run") or {}))
            message = format_operational_card(str(claimed["notification_kind"]), payload)
            try:
                response = send_function(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": "true",
                    },
                )
                message_id = _as_int((response.get("result") or {}).get("message_id"))
                if message_id <= 0:
                    raise RuntimeError("Telegram did not return a message identity")
            except Exception as error:
                error_class = type(error).__name__
                error_text = _clean_error(error)
                delay = _retry_delay(_as_int(claimed.get("attempt_count")))
                connection = get_connection()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        """
                        UPDATE telegram_operational_outbox
                        SET delivery_state='retry',next_attempt_at=datetime('now',?),
                            lease_token=NULL,lease_expires_at=NULL,last_error_class=?,last_error_text=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE logical_id=? AND lease_token=? AND delivery_state='sending'
                        """,
                        (f"+{delay} seconds", error_class, error_text, claimed["logical_id"], claimed["lease_token"]),
                    )
                    _record_event(connection, FAILED_EVENT, "retry", {"logical_id": claimed["logical_id"], "source": claimed.get("source_name"), "error_class": error_class, "retry_seconds": delay})
                    connection.commit()
                finally:
                    connection.close()
                totals["failed"] += 1
                continue
            connection = get_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE telegram_operational_outbox
                    SET delivery_state='sent',telegram_message_id=?,sent_at=CURRENT_TIMESTAMP,
                        lease_token=NULL,lease_expires_at=NULL,last_error_class=NULL,last_error_text=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE logical_id=? AND lease_token=? AND delivery_state='sending'
                    """,
                    (message_id, claimed["logical_id"], claimed["lease_token"]),
                )
                _record_event(connection, SENT_EVENT, "completed", {"logical_id": claimed["logical_id"], "source": claimed.get("source_name"), "notification_kind": claimed["notification_kind"], "message_id": message_id})
                connection.commit()
            finally:
                connection.close()
            totals["sent"] += 1
            totals["logical_ids"].append(claimed["logical_id"])
        return totals
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


def reconcile_stale_delivery_claims() -> dict[str, int]:
    """Do not blindly resend an attempt whose Telegram outcome is ambiguous."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        cursor = connection.execute(
            """
            UPDATE telegram_operational_outbox
            SET delivery_state='uncertain',lease_token=NULL,lease_expires_at=NULL,
                last_error_class='delivery_outcome_uncertain',
                last_error_text='A sender stopped after attempting delivery; automatic replay is withheld to prevent duplicate owner-visible cards.',
                updated_at=CURRENT_TIMESTAMP
            WHERE delivery_state='sending' AND datetime(lease_expires_at)<CURRENT_TIMESTAMP
            """
        )
        connection.commit()
        return {"uncertain": cursor.rowcount}
    finally:
        connection.close()


def operational_summary_health() -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        counts = {
            str(row["delivery_state"]): _as_int(row["count"])
            for row in connection.execute(
                "SELECT delivery_state,COUNT(*) count FROM telegram_operational_outbox GROUP BY delivery_state"
            )
        }
        kind_counts = {
            str(row["notification_kind"]): {
                "generated": _as_int(row["generated"]),
                "delivered": _as_int(row["delivered"]),
            }
            for row in connection.execute(
                """
                SELECT notification_kind,COUNT(*) generated,
                       COALESCE(SUM(delivery_state='sent'),0) delivered
                FROM telegram_operational_outbox GROUP BY notification_kind
                """
            )
        }
        latest = connection.execute(
            """
            SELECT source_name,notification_kind,delivery_state,created_at,sent_at
            FROM telegram_operational_outbox ORDER BY datetime(created_at) DESC LIMIT 1
            """
        ).fetchone()
        latest_sent = connection.execute(
            """
            SELECT source_name,notification_kind,sent_at
            FROM telegram_operational_outbox WHERE delivery_state='sent'
            ORDER BY datetime(sent_at) DESC LIMIT 1
            """
        ).fetchone()
        settings = get_setting("source_run_notifications", {}) or {}
        return {
            "enabled": bool(settings.get("enabled")),
            "counts": counts,
            "kind_counts": kind_counts,
            "generated": sum(counts.values()),
            "delivered": counts.get("sent", 0),
            "pending": counts.get("pending", 0),
            "retrying": counts.get("retry", 0),
            "uncertain": counts.get("uncertain", 0),
            "failed": counts.get("failed", 0),
            "terminal_generated": kind_counts.get(RUN_SUMMARY_KIND, {}).get("generated", 0),
            "terminal_delivered": kind_counts.get(RUN_SUMMARY_KIND, {}).get("delivered", 0),
            "incident_story_generated": sum(
                kind_counts.get(kind, {}).get("generated", 0)
                for kind in (DUE_INCIDENT_KIND, RECOVERY_KIND)
            ),
            "latest": dict(latest) if latest else None,
            "latest_sent": dict(latest_sent) if latest_sent else None,
        }
    finally:
        connection.close()


def outbox_database_path() -> Path:
    return DB_PATH
