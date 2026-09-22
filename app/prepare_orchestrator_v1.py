"""Durable preparation orchestration authority for MUNSHI.

This module coordinates preparation work without performing external actions.

The orchestrator owns:
- one idempotent preparation operation per explicit request;
- an immutable task graph bound to exact job + Candidate Truth snapshots;
- dependency-aware task claiming so independent workers may run concurrently;
- leases/recovery for worker crashes;
- NEEDS_INPUT and FAILED_SAFELY behavior;
- deterministic READY_FOR_REVIEW convergence.

The orchestrator does NOT:
- submit an application;
- open a browser;
- send email;
- call n8n;
- call a recruiter-search provider;
- generate a resume itself.

Those capabilities belong to worker adapters that claim tasks from this graph.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import get_connection
from app.phase67_common import safe_owned_job_snapshot, sha256_json
from app.phase8_application_truth import current_application_truth_projection
from app.tenant_foundation import (
    current_owner,
    current_owner_readonly,
    ensure_schema as ensure_tenant_schema,
)


ORCHESTRATOR_VERSION = "munshi-prepare-orchestrator-v1"

_LOGGER = logging.getLogger(__name__)
_CREATE_BUSY_TIMEOUT_MS = 250
_CREATE_BUSY_BACKOFF_SECONDS = (0.05, 0.1, 0.2, 0.35)

ENGINES = frozenset({"NATIVE", "N8N"})
OPERATION_STATUSES = frozenset(
    {
        "QUEUED",
        "RUNNING",
        "NEEDS_INPUT",
        "READY_FOR_REVIEW",
        "FAILED_SAFELY",
        "CANCELLED",
    }
)
TASK_STATUSES = frozenset(
    {
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "NEEDS_INPUT",
        "FAILED",
        "CANCELLED",
    }
)

TASK_RESUME = "RESUME_PREPARE"
TASK_COVER_LETTER_DECISION = "COVER_LETTER_DECISION"
TASK_RECRUITER_DISCOVERY = "RECRUITER_DISCOVERY"
TASK_ANSWER_RESOLUTION = "ANSWER_RESOLUTION"
TASK_APPLICATION_PREFLIGHT = "APPLICATION_PREFLIGHT"
TASK_ATS_FORM_PREPARATION = "ATS_FORM_PREPARATION"

TASK_KINDS = (
    TASK_RESUME,
    TASK_COVER_LETTER_DECISION,
    TASK_RECRUITER_DISCOVERY,
    TASK_ANSWER_RESOLUTION,
    TASK_APPLICATION_PREFLIGHT,
    TASK_ATS_FORM_PREPARATION,
)

TASK_DEFINITIONS: dict[str, dict[str, Any]] = {
    TASK_RESUME: {
        "required": True,
        "depends_on": (),
        "max_attempts": 2,
    },
    TASK_COVER_LETTER_DECISION: {
        "required": True,
        "depends_on": (),
        "max_attempts": 2,
    },
    TASK_RECRUITER_DISCOVERY: {
        "required": False,
        "depends_on": (),
        "max_attempts": 2,
    },
    TASK_ANSWER_RESOLUTION: {
        "required": True,
        "depends_on": (),
        "max_attempts": 2,
    },
    TASK_APPLICATION_PREFLIGHT: {
        "required": True,
        "depends_on": (TASK_RESUME, TASK_ANSWER_RESOLUTION),
        "max_attempts": 2,
    },
    TASK_ATS_FORM_PREPARATION: {
        "required": True,
        "depends_on": (
            TASK_APPLICATION_PREFLIGHT,
            TASK_COVER_LETTER_DECISION,
        ),
        "max_attempts": 2,
    },
}

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS prepare_operations_v1(
        operation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        engine TEXT NOT NULL CHECK(engine IN ('NATIVE','N8N')),
        status TEXT NOT NULL CHECK(status IN (
            'QUEUED','RUNNING','NEEDS_INPUT','READY_FOR_REVIEW',
            'FAILED_SAFELY','CANCELLED'
        )),
        idempotency_key TEXT NOT NULL,
        orchestrator_version TEXT NOT NULL,
        job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
        application_truth_projection_digest TEXT NOT NULL
            CHECK(length(application_truth_projection_digest)=64),
        candidate_profile_digest TEXT NOT NULL CHECK(length(candidate_profile_digest)=64),
        graph_digest TEXT NOT NULL CHECK(length(graph_digest)=64),
        unresolved_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        started_at TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,idempotency_key),
        UNIQUE(tenant_id,user_id,operation_id),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_prepare_operations_owner_status
       ON prepare_operations_v1(tenant_id,user_id,status,updated_at DESC);""",
    """CREATE TABLE IF NOT EXISTS prepare_tasks_v1(
        task_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        task_kind TEXT NOT NULL CHECK(task_kind IN (
            'RESUME_PREPARE','COVER_LETTER_DECISION','RECRUITER_DISCOVERY',
            'ANSWER_RESOLUTION','APPLICATION_PREFLIGHT','ATS_FORM_PREPARATION'
        )),
        status TEXT NOT NULL CHECK(status IN (
            'PENDING','RUNNING','SUCCEEDED','NEEDS_INPUT','FAILED','CANCELLED'
        )),
        required INTEGER NOT NULL CHECK(required IN (0,1)),
        depends_on_json TEXT NOT NULL,
        input_json TEXT NOT NULL,
        input_digest TEXT NOT NULL CHECK(length(input_digest)=64),
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 2 CHECK(max_attempts>=1),
        lease_owner TEXT,
        lease_expires_at TEXT,
        output_json TEXT,
        output_digest TEXT CHECK(output_digest IS NULL OR length(output_digest)=64),
        unresolved_json TEXT NOT NULL DEFAULT '[]',
        error_code TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        started_at TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,operation_id,task_kind),
        FOREIGN KEY(operation_id)
          REFERENCES prepare_operations_v1(operation_id) ON DELETE CASCADE,
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_prepare_tasks_claim
       ON prepare_tasks_v1(tenant_id,user_id,status,operation_id,created_at);""",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any, label: str, maximum: int = 240) -> str:
    result = " ".join(str(value or "").split())
    if not result or len(result) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return result


def _engine(value: Any) -> str:
    result = _text(value, "Prepare engine", 20).upper()
    if result not in ENGINES:
        raise ValueError(f"Unsupported Prepare engine: {result}")
    return result


def _now() -> datetime:
    return datetime.now(UTC)


def _now_text() -> str:
    return _now().isoformat()


def _timestamp(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid timestamp.") from error
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone.")
    return parsed.astimezone(UTC)


def _task_id(operation_id: str, task_kind: str) -> str:
    raw = f"{operation_id}:{task_kind}"
    return "prepare-task-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _operation_id(
    owner: Any,
    *,
    application_id: str,
    job_id: int,
    engine: str,
    idempotency_key: str,
) -> str:
    raw = (
        f"{owner.tenant_id}:{owner.user_id}:{application_id}:"
        f"{job_id}:{engine}:{idempotency_key}:{ORCHESTRATOR_VERSION}"
    )
    return "prepare-op-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _bindings(job_id: int) -> dict[str, str]:
    job_snapshot = safe_owned_job_snapshot(int(job_id))
    job_digest = str(job_snapshot["job_snapshot_sha256"])
    truth = current_application_truth_projection(job_id=int(job_id))
    projection_digest = str(truth.get("projection_digest") or "")
    candidate = dict(truth.get("candidate_profile_binding") or {})
    candidate_digest = str(candidate.get("profile_digest") or "")
    for label, value in (
        ("job snapshot", job_digest),
        ("application truth projection", projection_digest),
        ("candidate profile", candidate_digest),
    ):
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError(f"Prepare {label} binding is incomplete.")
    if str((truth.get("job_context") or {}).get("job_snapshot_sha256") or "") != job_digest:
        raise RuntimeError("Application Truth is stale for the current job snapshot.")
    return {
        "job_snapshot_sha256": job_digest,
        "application_truth_projection_digest": projection_digest,
        "candidate_profile_digest": candidate_digest,
    }


def _graph(engine: str, bindings: Mapping[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for kind in TASK_KINDS:
        definition = TASK_DEFINITIONS[kind]
        payload = {
            "version": ORCHESTRATOR_VERSION,
            "task_kind": kind,
            "engine": engine if kind == TASK_RESUME else None,
            "job_snapshot_sha256": bindings["job_snapshot_sha256"],
            "application_truth_projection_digest": (
                bindings["application_truth_projection_digest"]
            ),
            "candidate_profile_digest": bindings["candidate_profile_digest"],
            "submission_authority": False,
        }
        result.append(
            {
                "task_kind": kind,
                "required": bool(definition["required"]),
                "depends_on": list(definition["depends_on"]),
                "max_attempts": int(definition["max_attempts"]),
                "input": payload,
                "input_digest": _digest(payload),
            }
        )
    return result


def _operation_row(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    operation_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT * FROM prepare_operations_v1
           WHERE operation_id=? AND tenant_id=? AND user_id=?""",
        (operation_id, owner.tenant_id, owner.user_id),
    ).fetchone()


def _task_rows(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    operation_id: str,
) -> list[sqlite3.Row]:
    return connection.execute(
        """SELECT * FROM prepare_tasks_v1
           WHERE operation_id=? AND tenant_id=? AND user_id=?
           ORDER BY created_at,task_kind""",
        (operation_id, owner.tenant_id, owner.user_id),
    ).fetchall()


def _refresh_operation(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    operation_id: str,
) -> str:
    rows = _task_rows(
        connection,
        owner=owner,
        operation_id=operation_id,
    )
    if not rows:
        raise RuntimeError("Prepare operation has no tasks.")

    required = [row for row in rows if int(row["required"]) == 1]
    unresolved: list[str] = []
    for row in rows:
        try:
            values = json.loads(str(row["unresolved_json"] or "[]"))
        except json.JSONDecodeError:
            values = []
        if isinstance(values, list):
            unresolved.extend(str(value) for value in values if str(value).strip())

    # FAILED is terminal. Retryable failures are requeued as PENDING by
    # fail_task(), so any required task left in FAILED must fail the operation
    # safely even when the failure was declared non-retryable on attempt 1.
    required_failed = any(
        str(row["status"]) == "FAILED"
        for row in required
    )
    required_needs_input = any(
        str(row["status"]) == "NEEDS_INPUT" for row in required
    )
    required_succeeded = all(
        str(row["status"]) == "SUCCEEDED" for row in required
    )

    operation = _operation_row(
        connection,
        owner=owner,
        operation_id=operation_id,
    )
    if operation is None:
        raise RuntimeError("Prepare operation disappeared.")
    current = str(operation["status"])

    if current == "CANCELLED":
        target = "CANCELLED"
    elif required_failed:
        target = "FAILED_SAFELY"
    elif required_needs_input:
        target = "NEEDS_INPUT"
    elif required_succeeded:
        target = "READY_FOR_REVIEW"
    elif current == "QUEUED":
        target = "RUNNING"
    else:
        target = "RUNNING"

    completed_at = _now_text() if target in {
        "READY_FOR_REVIEW",
        "FAILED_SAFELY",
        "CANCELLED",
    } else None

    connection.execute(
        """UPDATE prepare_operations_v1
              SET status=?,
                  unresolved_json=?,
                  started_at=COALESCE(started_at,CURRENT_TIMESTAMP),
                  completed_at=CASE
                      WHEN ? IS NOT NULL THEN COALESCE(completed_at,?)
                      ELSE NULL
                  END,
                  updated_at=CURRENT_TIMESTAMP
            WHERE operation_id=? AND tenant_id=? AND user_id=?""",
        (
            target,
            _canonical(sorted(set(unresolved))),
            completed_at,
            completed_at,
            operation_id,
            owner.tenant_id,
            owner.user_id,
        ),
    )
    return target


def start_prepare(
    *,
    application_id: str,
    job_id: int,
    engine: str,
    idempotency_key: str,
) -> dict[str, Any]:
    application_id = _text(application_id, "Application id", 240)
    idempotency_key = _text(idempotency_key, "Idempotency key", 240)
    resolved_engine = _engine(engine)
    try:
        resolved_job_id = int(job_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Job id must be an integer.") from error
    if resolved_job_id <= 0:
        raise ValueError("Job id must be positive.")

    bindings = _bindings(resolved_job_id)
    graph = _graph(resolved_engine, bindings)
    graph_digest = sha256_json(graph)

    # Production startup owns schema installation. The click path probes first so
    # an already-ready dashboard never executes DDL while competing with workers.
    schema_connection = get_connection()
    try:
        tables = {
            str(row[0])
            for row in schema_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('prepare_operations_v1','prepare_tasks_v1','tenant_memberships')"
            )
        }
        if tables != {
            "prepare_operations_v1",
            "prepare_tasks_v1",
            "tenant_memberships",
        }:
            ensure_schema(schema_connection)
            schema_connection.commit()
            owner = current_owner(schema_connection)
            if schema_connection.in_transaction:
                schema_connection.commit()
        else:
            owner = current_owner_readonly(schema_connection)
    finally:
        schema_connection.close()

    operation_id = _operation_id(
        owner,
        application_id=application_id,
        job_id=resolved_job_id,
        engine=resolved_engine,
        idempotency_key=idempotency_key,
    )
    _LOGGER.info(
        "PREPARE_OPERATION_CREATE_BEGIN job_id=%s operation_id=%s application_id=%s",
        resolved_job_id,
        operation_id,
        application_id,
    )

    attempts = len(_CREATE_BUSY_BACKOFF_SECONDS) + 1
    for attempt in range(attempts):
        connection = get_connection()
        try:
            # This transaction only inserts the immutable operation/task graph.
            # It must fail fast enough for bounded retries instead of inheriting
            # the general 30-second worker timeout.
            connection.execute(f"PRAGMA busy_timeout={_CREATE_BUSY_TIMEOUT_MS}")
            connection.execute("BEGIN IMMEDIATE")

            prior = connection.execute(
                """SELECT * FROM prepare_operations_v1
                   WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
                (owner.tenant_id, owner.user_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                exact = (
                    str(prior["operation_id"]) == operation_id
                    and str(prior["application_id"]) == application_id
                    and int(prior["job_id"]) == resolved_job_id
                    and str(prior["engine"]) == resolved_engine
                    and str(prior["job_snapshot_sha256"])
                    == bindings["job_snapshot_sha256"]
                    and str(prior["application_truth_projection_digest"])
                    == bindings["application_truth_projection_digest"]
                    and str(prior["candidate_profile_digest"])
                    == bindings["candidate_profile_digest"]
                    and str(prior["graph_digest"]) == graph_digest
                )
                if not exact:
                    raise ValueError(
                        "Prepare idempotency key belongs to different or stale inputs."
                    )
                connection.commit()
                result = prepare_operation(str(prior["operation_id"]))
                result["replayed"] = True
                _LOGGER.info(
                    "PREPARE_OPERATION_REPLAYED job_id=%s operation_id=%s application_id=%s",
                    resolved_job_id,
                    operation_id,
                    application_id,
                )
                return result

            connection.execute(
                """INSERT INTO prepare_operations_v1(
                       operation_id,tenant_id,user_id,application_id,job_id,engine,
                       status,idempotency_key,orchestrator_version,
                       job_snapshot_sha256,application_truth_projection_digest,
                       candidate_profile_digest,graph_digest
                   ) VALUES(?,?,?,?,?,?,'QUEUED',?,?,?,?,?,?)""",
                (
                    operation_id,
                    owner.tenant_id,
                    owner.user_id,
                    application_id,
                    resolved_job_id,
                    resolved_engine,
                    idempotency_key,
                    ORCHESTRATOR_VERSION,
                    bindings["job_snapshot_sha256"],
                    bindings["application_truth_projection_digest"],
                    bindings["candidate_profile_digest"],
                    graph_digest,
                ),
            )

            for task in graph:
                connection.execute(
                    """INSERT INTO prepare_tasks_v1(
                           task_id,tenant_id,user_id,operation_id,task_kind,status,
                           required,depends_on_json,input_json,input_digest,max_attempts
                       ) VALUES(?,?,?,?,?,'PENDING',?,?,?,?,?)""",
                    (
                        _task_id(operation_id, str(task["task_kind"])),
                        owner.tenant_id,
                        owner.user_id,
                        operation_id,
                        task["task_kind"],
                        1 if task["required"] else 0,
                        _canonical(task["depends_on"]),
                        _canonical(task["input"]),
                        task["input_digest"],
                        int(task["max_attempts"]),
                    ),
                )

            _refresh_operation(
                connection,
                owner=owner,
                operation_id=operation_id,
            )
            connection.commit()
            result = prepare_operation(operation_id)
            result["replayed"] = False
            _LOGGER.info(
                "PREPARE_OPERATION_CREATED job_id=%s operation_id=%s application_id=%s",
                resolved_job_id,
                operation_id,
                application_id,
            )
            return result
        except Exception as error:
            connection.rollback()
            if sqlite_writer_busy(error) and attempt < attempts - 1:
                delay = _CREATE_BUSY_BACKOFF_SECONDS[attempt]
                _LOGGER.warning(
                    "PREPARE_OPERATION_CREATE_BUSY job_id=%s operation_id=%s application_id=%s attempt=%s retry_in_seconds=%.2f",
                    resolved_job_id,
                    operation_id,
                    application_id,
                    attempt + 1,
                    delay,
                )
                time.sleep(delay)
                continue
            _LOGGER.exception(
                "PREPARE_OPERATION_CREATE_FAILED job_id=%s operation_id=%s application_id=%s error_type=%s",
                resolved_job_id,
                operation_id,
                application_id,
                type(error).__name__,
            )
            raise
        finally:
            connection.close()

    raise RuntimeError("Prepare operation creation exhausted its bounded retry budget.")


def _dependency_states(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    operation_id: str,
) -> dict[str, str]:
    return {
        str(row["task_kind"]): str(row["status"])
        for row in _task_rows(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
    }


def _dependencies_satisfied(
    row: sqlite3.Row,
    *,
    states: Mapping[str, str],
) -> bool:
    try:
        dependencies = json.loads(str(row["depends_on_json"]))
    except json.JSONDecodeError:
        return False
    return all(states.get(str(dep)) == "SUCCEEDED" for dep in dependencies)


def claim_ready_tasks(
    *,
    operation_id: str,
    worker_id: str,
    limit: int = 4,
    lease_seconds: int = 300,
    task_kinds: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    operation_id = _text(operation_id, "Operation id", 240)
    worker_id = _text(worker_id, "Worker id", 160)
    if int(limit) < 1 or int(limit) > 20:
        raise ValueError("Claim limit must be between 1 and 20.")
    if int(lease_seconds) < 30 or int(lease_seconds) > 3600:
        raise ValueError("Lease seconds must be between 30 and 3600.")

    allowed_kinds = None
    if task_kinds is not None:
        allowed_kinds = {
            _text(value, "Task kind", 80).upper()
            for value in task_kinds
        }
        unknown = allowed_kinds - set(TASK_KINDS)
        if unknown:
            raise ValueError(f"Unsupported Prepare task kinds: {sorted(unknown)}")

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")

        operation = _operation_row(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        if operation is None:
            raise LookupError("Prepare operation was not found for this owner.")
        if str(operation["status"]) not in {"RUNNING", "QUEUED"}:
            connection.commit()
            return []

        states = _dependency_states(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        candidates = _task_rows(
            connection,
            owner=owner,
            operation_id=operation_id,
        )

        claimed: list[dict[str, Any]] = []
        lease_expires = (_now() + timedelta(seconds=int(lease_seconds))).isoformat()

        for row in candidates:
            if len(claimed) >= int(limit):
                break
            if str(row["status"]) != "PENDING":
                continue
            if allowed_kinds is not None and str(row["task_kind"]) not in allowed_kinds:
                continue
            if not _dependencies_satisfied(row, states=states):
                continue
            if int(row["attempts"]) >= int(row["max_attempts"]):
                continue

            updated = connection.execute(
                """UPDATE prepare_tasks_v1
                      SET status='RUNNING',
                          attempts=attempts+1,
                          lease_owner=?,
                          lease_expires_at=?,
                          started_at=COALESCE(started_at,CURRENT_TIMESTAMP),
                          updated_at=CURRENT_TIMESTAMP
                    WHERE task_id=? AND tenant_id=? AND user_id=?
                      AND status='PENDING'""",
                (
                    worker_id,
                    lease_expires,
                    str(row["task_id"]),
                    owner.tenant_id,
                    owner.user_id,
                ),
            )
            if int(updated.rowcount or 0) != 1:
                continue

            claimed_row = connection.execute(
                "SELECT * FROM prepare_tasks_v1 WHERE task_id=?",
                (str(row["task_id"]),),
            ).fetchone()
            if claimed_row is None:
                raise RuntimeError("Claimed Prepare task disappeared.")
            payload = dict(claimed_row)
            payload["input"] = json.loads(payload.pop("input_json"))
            payload["depends_on"] = json.loads(payload.pop("depends_on_json"))
            payload["submission_authority"] = False
            claimed.append(payload)
            states[str(row["task_kind"])] = "RUNNING"

        _refresh_operation(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        connection.commit()
        return claimed
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()



def heartbeat_task(
    *,
    task_id: str,
    worker_id: str,
    lease_seconds: int = 300,
) -> dict[str, Any]:
    """Extend one RUNNING Prepare task lease owned by the exact worker.

    This supports long-running cross-service background preparation without
    weakening exactly-one-worker ownership. It grants no submission authority.
    """
    task_id = _text(task_id, "Task id", 240)
    worker_id = _text(worker_id, "Worker id", 160)
    if int(lease_seconds) < 30 or int(lease_seconds) > 3600:
        raise ValueError("Lease seconds must be between 30 and 3600.")

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT * FROM prepare_tasks_v1
               WHERE task_id=? AND tenant_id=? AND user_id=?""",
            (task_id, owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Prepare task was not found for this owner.")
        if str(row["status"]) != "RUNNING":
            raise ValueError("Prepare task is not currently RUNNING.")
        if str(row["lease_owner"] or "") != worker_id:
            raise PermissionError("Prepare task lease belongs to another worker.")

        lease_expires = (_now() + timedelta(seconds=int(lease_seconds))).isoformat()
        updated = connection.execute(
            """UPDATE prepare_tasks_v1
                  SET lease_expires_at=?,updated_at=CURRENT_TIMESTAMP
                WHERE task_id=? AND tenant_id=? AND user_id=?
                  AND status='RUNNING' AND lease_owner=?""",
            (
                lease_expires,
                task_id,
                owner.tenant_id,
                owner.user_id,
                worker_id,
            ),
        )
        if int(updated.rowcount or 0) != 1:
            raise RuntimeError("Prepare task lease changed concurrently.")
        connection.commit()
        return {
            "task_id": task_id,
            "worker_id": worker_id,
            "status": "RUNNING",
            "lease_expires_at": lease_expires,
            "submission_authority": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()



def _task_for_worker(
    connection: sqlite3.Connection,
    *,
    owner: Any,
    task_id: str,
    worker_id: str,
) -> sqlite3.Row:
    row = connection.execute(
        """SELECT * FROM prepare_tasks_v1
           WHERE task_id=? AND tenant_id=? AND user_id=?""",
        (task_id, owner.tenant_id, owner.user_id),
    ).fetchone()
    if row is None:
        raise LookupError("Prepare task was not found for this owner.")
    if str(row["status"]) != "RUNNING":
        raise ValueError("Prepare task is not currently RUNNING.")
    if str(row["lease_owner"] or "") != worker_id:
        raise PermissionError("Prepare task lease belongs to another worker.")
    if not row["lease_expires_at"] or _timestamp(row["lease_expires_at"]) < _now():
        raise RuntimeError("Prepare task lease expired.")
    return row


def complete_task(
    *,
    task_id: str,
    worker_id: str,
    output: Mapping[str, Any],
) -> dict[str, Any]:
    task_id = _text(task_id, "Task id", 240)
    worker_id = _text(worker_id, "Worker id", 160)
    if not isinstance(output, Mapping):
        raise ValueError("Prepare task output must be an object.")
    output_dict = dict(output)
    output_dict["submission_authority"] = False
    output_json = _canonical(output_dict)
    output_digest = _digest(output_dict)

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = _task_for_worker(
            connection,
            owner=owner,
            task_id=task_id,
            worker_id=worker_id,
        )
        operation_id = str(row["operation_id"])
        connection.execute(
            """UPDATE prepare_tasks_v1
                  SET status='SUCCEEDED',
                      output_json=?,
                      output_digest=?,
                      unresolved_json='[]',
                      error_code=NULL,
                      error_message=NULL,
                      lease_owner=NULL,
                      lease_expires_at=NULL,
                      completed_at=CURRENT_TIMESTAMP,
                      updated_at=CURRENT_TIMESTAMP
                WHERE task_id=?""",
            (output_json, output_digest, task_id),
        )
        status = _refresh_operation(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        connection.commit()
        return {
            "task_id": task_id,
            "operation_id": operation_id,
            "task_status": "SUCCEEDED",
            "operation_status": status,
            "output_digest": output_digest,
            "submission_authority": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def requeue_task_after_internal_resolution(
    *,
    task_id: str,
    worker_id: str,
    output: Mapping[str, Any],
    resolution_kind: str,
) -> dict[str, Any]:
    """Atomically requeue a RUNNING task after trusted internal resolution.

    This is intentionally distinct from user-facing NEEDS_INPUT. It preserves
    value-free resolution metadata while avoiding a transient customer prompt
    when MUNSHI already resolved every normal field from canonical knowledge.
    """
    task_id = _text(task_id, "Task id", 240)
    worker_id = _text(worker_id, "Worker id", 160)
    kind = _text(resolution_kind, "Internal resolution kind", 120)
    if not isinstance(output, Mapping):
        raise ValueError("Prepare task output must be an object.")
    output_dict = dict(output)
    output_dict["internal_resolution_kind"] = kind
    output_dict["submission_authority"] = False
    output_json = _canonical(output_dict)
    output_digest = _digest(output_dict)

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = _task_for_worker(
            connection,
            owner=owner,
            task_id=task_id,
            worker_id=worker_id,
        )
        operation_id = str(row["operation_id"])
        connection.execute(
            """UPDATE prepare_tasks_v1
                  SET status='PENDING',
                      output_json=?,
                      output_digest=?,
                      unresolved_json='[]',
                      error_code=NULL,
                      error_message=NULL,
                      lease_owner=NULL,
                      lease_expires_at=NULL,
                      completed_at=NULL,
                      updated_at=CURRENT_TIMESTAMP
                WHERE task_id=?""",
            (output_json, output_digest, task_id),
        )
        status = _refresh_operation(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        connection.commit()
        return {
            "task_id": task_id,
            "operation_id": operation_id,
            "task_status": "PENDING",
            "operation_status": status,
            "internal_resolution_kind": kind,
            "output_digest": output_digest,
            "submission_authority": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def task_needs_input(
    *,
    task_id: str,
    worker_id: str,
    unresolved_keys: Sequence[str],
    output: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = _text(task_id, "Task id", 240)
    worker_id = _text(worker_id, "Worker id", 160)
    if isinstance(unresolved_keys, (str, bytes)):
        raise ValueError("Unresolved keys must be a list.")
    unresolved = sorted(
        {
            _text(value, "Unresolved key", 256)
            for value in unresolved_keys
        }
    )
    if not unresolved:
        raise ValueError("NEEDS_INPUT requires at least one unresolved key.")

    output_dict = dict(output or {})
    output_dict["submission_authority"] = False
    output_json = _canonical(output_dict)
    output_digest = _digest(output_dict)

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = _task_for_worker(
            connection,
            owner=owner,
            task_id=task_id,
            worker_id=worker_id,
        )
        operation_id = str(row["operation_id"])
        connection.execute(
            """UPDATE prepare_tasks_v1
                  SET status='NEEDS_INPUT',
                      output_json=?,
                      output_digest=?,
                      unresolved_json=?,
                      lease_owner=NULL,
                      lease_expires_at=NULL,
                      completed_at=CURRENT_TIMESTAMP,
                      updated_at=CURRENT_TIMESTAMP
                WHERE task_id=?""",
            (
                output_json,
                output_digest,
                _canonical(unresolved),
                task_id,
            ),
        )
        status = _refresh_operation(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        connection.commit()
        return {
            "task_id": task_id,
            "operation_id": operation_id,
            "task_status": "NEEDS_INPUT",
            "operation_status": status,
            "unresolved_keys": unresolved,
            "submission_authority": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def resolve_task_input(
    *,
    task_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    task_id = _text(task_id, "Task id", 240)
    idempotency_key = _text(idempotency_key, "Resolution idempotency key", 240)

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT * FROM prepare_tasks_v1
               WHERE task_id=? AND tenant_id=? AND user_id=?""",
            (task_id, owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Prepare task was not found for this owner.")

        operation_id = str(row["operation_id"])
        if str(row["status"]) == "PENDING":
            connection.commit()
            return {
                "task_id": task_id,
                "operation_id": operation_id,
                "replayed": True,
                "task_status": "PENDING",
            }
        if str(row["status"]) != "NEEDS_INPUT":
            raise ValueError("Only NEEDS_INPUT tasks can be resolved.")

        marker = _digest(
            {
                "task_id": task_id,
                "resolution_idempotency_key": idempotency_key,
            }
        )
        prior_output = {}
        if row["output_json"]:
            try:
                prior_output = json.loads(str(row["output_json"]))
            except json.JSONDecodeError:
                prior_output = {}
        if not isinstance(prior_output, dict):
            prior_output = {}
        prior_output["resolution_marker"] = marker
        prior_output["submission_authority"] = False

        connection.execute(
            """UPDATE prepare_tasks_v1
                  SET status='PENDING',
                      output_json=?,
                      output_digest=?,
                      unresolved_json='[]',
                      lease_owner=NULL,
                      lease_expires_at=NULL,
                      completed_at=NULL,
                      updated_at=CURRENT_TIMESTAMP
                WHERE task_id=?""",
            (
                _canonical(prior_output),
                _digest(prior_output),
                task_id,
            ),
        )
        status = _refresh_operation(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        connection.commit()
        return {
            "task_id": task_id,
            "operation_id": operation_id,
            "replayed": False,
            "task_status": "PENDING",
            "operation_status": status,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def sqlite_writer_busy(error: BaseException) -> bool:
    if not isinstance(error, sqlite3.OperationalError):
        return False
    text = str(error or "").casefold()
    return any(
        marker in text
        for marker in (
            "database is locked",
            "database table is locked",
            "database is busy",
        )
    )


def defer_task_for_contention(
    *,
    task_id: str,
    worker_id: str,
    reason_code: str = "SQLITE_WRITER_BUSY",
) -> dict[str, Any]:
    """Return one claimed task to PENDING without consuming its retry budget.

    This transition is reserved for infrastructure writer contention. Business,
    model, truth, and provider failures must continue through fail_task and
    consume attempts normally.
    """
    task_id = _text(task_id, "Task id", 240)
    worker_id = _text(worker_id, "Worker id", 160)
    reason_code = _text(reason_code, "Reason code", 120)

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = _task_for_worker(
            connection,
            owner=owner,
            task_id=task_id,
            worker_id=worker_id,
        )
        operation_id = str(row["operation_id"])
        attempts_before = int(row["attempts"])
        attempts_after = max(0, attempts_before - 1)

        connection.execute(
            """UPDATE prepare_tasks_v1
                  SET status='PENDING',
                      attempts=?,
                      error_code=?,
                      error_message='Deferred without consuming an attempt because the SQLite writer was busy.',
                      lease_owner=NULL,
                      lease_expires_at=NULL,
                      completed_at=NULL,
                      updated_at=CURRENT_TIMESTAMP
                WHERE task_id=? AND tenant_id=? AND user_id=?""",
            (
                attempts_after,
                reason_code,
                task_id,
                owner.tenant_id,
                owner.user_id,
            ),
        )
        status = _refresh_operation(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        connection.commit()
        return {
            "task_id": task_id,
            "operation_id": operation_id,
            "task_status": "PENDING",
            "operation_status": status,
            "attempts_before": attempts_before,
            "attempts_after": attempts_after,
            "attempt_refunded": attempts_after < attempts_before,
            "reason": reason_code,
            "submission_authority": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def fail_task(
    *,
    task_id: str,
    worker_id: str,
    error_code: str,
    error_message: str,
    retryable: bool,
) -> dict[str, Any]:
    task_id = _text(task_id, "Task id", 240)
    worker_id = _text(worker_id, "Worker id", 160)
    error_code = _text(error_code, "Error code", 120)
    error_message = _text(error_message, "Error message", 2000)

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = _task_for_worker(
            connection,
            owner=owner,
            task_id=task_id,
            worker_id=worker_id,
        )
        operation_id = str(row["operation_id"])
        attempts = int(row["attempts"])
        max_attempts = int(row["max_attempts"])
        next_status = "PENDING" if retryable and attempts < max_attempts else "FAILED"

        connection.execute(
            """UPDATE prepare_tasks_v1
                  SET status=?,
                      error_code=?,
                      error_message=?,
                      lease_owner=NULL,
                      lease_expires_at=NULL,
                      completed_at=CASE WHEN ?='FAILED' THEN CURRENT_TIMESTAMP ELSE NULL END,
                      updated_at=CURRENT_TIMESTAMP
                WHERE task_id=?""",
            (
                next_status,
                error_code,
                error_message,
                next_status,
                task_id,
            ),
        )
        status = _refresh_operation(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        connection.commit()
        return {
            "task_id": task_id,
            "operation_id": operation_id,
            "task_status": next_status,
            "operation_status": status,
            "retryable": bool(retryable),
            "submission_authority": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def cancel_prepare(
    *,
    operation_id: str,
    reason_code: str = "CANCELLED_BY_USER",
) -> dict[str, Any]:
    """Cancel one pre-submit Prepare operation without deleting its history."""
    operation_id = _text(operation_id, "Operation id", 240)
    reason_code = _text(reason_code, "Reason code", 120)

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        operation = _operation_row(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        if operation is None:
            raise LookupError("Prepare operation was not found for this owner.")
        if str(operation["status"]) == "CANCELLED":
            connection.commit()
            return {
                "operation_id": operation_id,
                "status": "CANCELLED",
                "replayed": True,
                "submission_authority": False,
            }
        if str(operation["status"]) == "READY_FOR_REVIEW":
            raise ValueError("A review-ready operation cannot be cancelled here.")

        connection.execute(
            """UPDATE prepare_operations_v1
                  SET status='CANCELLED',
                      completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP),
                      updated_at=CURRENT_TIMESTAMP
                WHERE operation_id=? AND tenant_id=? AND user_id=?""",
            (operation_id, owner.tenant_id, owner.user_id),
        )
        cancelled = connection.execute(
            """UPDATE prepare_tasks_v1
                  SET status='CANCELLED',
                      error_code=?,
                      error_message='Preparation was safely cancelled before approval.',
                      lease_owner=NULL,
                      lease_expires_at=NULL,
                      completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP),
                      updated_at=CURRENT_TIMESTAMP
                WHERE operation_id=? AND tenant_id=? AND user_id=?
                  AND status IN ('PENDING','RUNNING','NEEDS_INPUT')""",
            (
                reason_code,
                operation_id,
                owner.tenant_id,
                owner.user_id,
            ),
        )
        _refresh_operation(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        connection.commit()
        return {
            "operation_id": operation_id,
            "status": "CANCELLED",
            "cancelled_tasks": int(cancelled.rowcount or 0),
            "replayed": False,
            "submission_authority": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def recover_expired_leases(
    *,
    limit: int = 100,
    operation_id: str | None = None,
    job_id: int | None = None,
) -> dict[str, int]:
    if int(limit) < 1 or int(limit) > 1000:
        raise ValueError("Recovery limit must be between 1 and 1000.")
    if operation_id is not None:
        operation_id = _text(operation_id, "Operation id", 240)
    if job_id is not None and int(job_id) <= 0:
        raise ValueError("Job id must be positive.")

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        now_text = _now_text()
        filters = [
            "t.tenant_id=?",
            "t.user_id=?",
            "t.status='RUNNING'",
            "t.lease_expires_at IS NOT NULL",
            "t.lease_expires_at < ?",
        ]
        params: list[Any] = [owner.tenant_id, owner.user_id, now_text]
        if operation_id is not None:
            filters.append("t.operation_id=?")
            params.append(operation_id)
        if job_id is not None:
            filters.append("o.job_id=?")
            params.append(int(job_id))
        params.append(int(limit))
        rows = connection.execute(
            f"""SELECT t.* FROM prepare_tasks_v1 AS t
                 JOIN prepare_operations_v1 AS o
                   ON o.operation_id=t.operation_id
                WHERE {' AND '.join(filters)}
                ORDER BY t.lease_expires_at
                LIMIT ?""",
            tuple(params),
        ).fetchall()

        requeued = 0
        failed = 0
        operations: set[str] = set()
        for row in rows:
            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])
            next_status = "PENDING" if attempts < max_attempts else "FAILED"
            connection.execute(
                """UPDATE prepare_tasks_v1
                      SET status=?,
                          error_code='LEASE_EXPIRED',
                          error_message='Worker lease expired before completion.',
                          lease_owner=NULL,
                          lease_expires_at=NULL,
                          completed_at=CASE WHEN ?='FAILED' THEN CURRENT_TIMESTAMP ELSE NULL END,
                          updated_at=CURRENT_TIMESTAMP
                    WHERE task_id=?""",
                (next_status, next_status, str(row["task_id"])),
            )
            if next_status == "PENDING":
                requeued += 1
            else:
                failed += 1
            operations.add(str(row["operation_id"]))

        for operation_id in operations:
            _refresh_operation(
                connection,
                owner=owner,
                operation_id=operation_id,
            )
        connection.commit()
        return {
            "expired": len(rows),
            "requeued": requeued,
            "failed": failed,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def prepare_operation(operation_id: str) -> dict[str, Any]:
    operation_id = _text(operation_id, "Operation id", 240)
    connection = get_connection()
    try:
        owner = current_owner_readonly(connection)
        operation = _operation_row(
            connection,
            owner=owner,
            operation_id=operation_id,
        )
        if operation is None:
            raise LookupError("Prepare operation was not found for this owner.")

        tasks = []
        for row in _task_rows(
            connection,
            owner=owner,
            operation_id=operation_id,
        ):
            payload = dict(row)
            payload["depends_on"] = json.loads(payload.pop("depends_on_json"))
            payload["input"] = json.loads(payload.pop("input_json"))
            payload["unresolved"] = json.loads(payload.pop("unresolved_json"))
            if payload.get("output_json"):
                payload["output"] = json.loads(payload.pop("output_json"))
            else:
                payload.pop("output_json", None)
                payload["output"] = None
            payload["submission_authority"] = False
            tasks.append(payload)

        result = dict(operation)
        result["unresolved"] = json.loads(result.pop("unresolved_json"))
        result["tasks"] = tasks
        result["submission_authority"] = False
        result["external_actions_executed"] = False
        return result
    finally:
        connection.close()
