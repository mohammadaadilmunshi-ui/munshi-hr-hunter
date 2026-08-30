from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


PROJECT = Path.cwd()

# Ensure project modules such as app.n8n_dispatch are
# importable when this file is executed from scripts/.
project_root = str(PROJECT.resolve())
if project_root not in sys.path:
    sys.path.insert(0, project_root)

HUNTER_DB = PROJECT / "data/hunter.db"
N8N_DB = Path.home() / ".n8n/database.sqlite"

SOURCE_JOB_ID = 26
WORKFLOW_ID = "L1u2xZkgFpi7KEuv"

REPORT_PATH = Path(sys.argv[1])
STATE_PATH = Path(sys.argv[2])
LISTENER_LOG = Path(sys.argv[3])

APPROVAL_TIMEOUT = 20 * 60
EXECUTION_TIMEOUT = 40 * 60
CALLBACK_GRACE = 180


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def quote_identifier(
    value: str,
) -> str:
    return (
        '"'
        + value.replace(
            '"',
            '""',
        )
        + '"'
    )


environment = os.environ.copy()

for key, value in dotenv_values(
    PROJECT / ".env"
).items():
    if key and value is not None:
        environment[key] = value
        os.environ[key] = value


from app.n8n_dispatch import (  # noqa: E402
    dispatch_pending,
    ensure_schema,
    get_connection as get_dispatch_connection,
    insert_queue_item,
)

from app.telegram_client import (  # noqa: E402
    CHAT_ID,
    send_job_card,
    telegram_request,
)


def save_state(
    **updates: Any,
) -> None:
    current: dict[str, Any] = {}

    if STATE_PATH.exists():
        try:
            current = json.loads(
                STATE_PATH.read_text()
            )
        except Exception:
            current = {}

    current.update(updates)

    current["updated_at"] = utc_now()

    STATE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATE_PATH.write_text(
        json.dumps(
            current,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
        + "\n"
    )


def hunter_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(
        HUNTER_DB,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return connection


def n8n_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{N8N_DB}?mode=ro",
        uri=True,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    return connection


def unique_indexes(
    connection: sqlite3.Connection,
) -> list[list[str]]:
    output: list[list[str]] = []

    for index in connection.execute(
        "PRAGMA index_list(jobs)"
    ).fetchall():
        if int(index["unique"] or 0) != 1:
            continue

        index_name = str(
            index["name"]
        )

        columns = [
            str(row["name"])
            for row in connection.execute(
                "PRAGMA index_info("
                + quote_identifier(index_name)
                + ")"
            ).fetchall()
            if row["name"] is not None
        ]

        if columns:
            output.append(columns)

    return output


def row_conflicts(
    connection: sqlite3.Connection,
    columns: list[str],
    values: dict[str, Any],
) -> bool:
    if any(
        values.get(column) is None
        for column in columns
    ):
        return False

    conditions = []
    parameters = []

    for column in columns:
        conditions.append(
            quote_identifier(column)
            + " = ?"
        )

        parameters.append(
            values.get(column)
        )

    row = connection.execute(
        """
        SELECT id
        FROM jobs
        WHERE
        """
        + " AND ".join(conditions)
        + " LIMIT 1",
        parameters,
    ).fetchone()

    return row is not None


def mutate_unique_value(
    column: str,
    value: Any,
    suffix: str,
    attempt: int,
    declared_type: str,
) -> Any:
    marker = (
        suffix
        + "-"
        + str(attempt)
    )

    if column in {
        "job_fingerprint",
        "url_fingerprint",
    }:
        return hashlib.sha256(
            (
                str(value or "")
                + "|"
                + marker
            ).encode("utf-8")
        ).hexdigest()

    if column in {
        "job_url",
        "apply_url",
    }:
        base = str(value or "").split(
            "#",
            1,
        )[0]

        return (
            base
            + "#"
            + marker
        )

    upper_type = declared_type.upper()

    if any(
        token in upper_type
        for token in (
            "INT",
            "REAL",
            "FLOAT",
            "DOUBLE",
            "NUMERIC",
        )
    ):
        try:
            return int(value or 0) + (
                10_000_000
                + attempt
            )
        except Exception:
            return (
                10_000_000
                + attempt
            )

    return (
        str(value or "")
        + "|"
        + marker
    )


def create_fresh_test_job() -> dict[str, Any]:
    connection = hunter_connection()

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        source = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE id = ?
            """,
            (
                SOURCE_JOB_ID,
            ),
        ).fetchone()

        if source is None:
            raise RuntimeError(
                "Source Pure Power job 26 is missing."
            )

        table_info = [
            dict(row)
            for row in connection.execute(
                "PRAGMA table_info(jobs)"
            ).fetchall()
        ]

        column_metadata = {
            str(row["name"]): row
            for row in table_info
        }

        insert_columns = [
            str(row["name"])
            for row in table_info
            if str(row["name"]) != "id"
        ]

        values = {
            column: source[column]
            for column in insert_columns
        }

        stamp = datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%d%H%M%S"
        )

        suffix = (
            "full-loop-"
            + stamp
            + "-"
            + uuid.uuid4().hex[:8]
        )

        original_fingerprint = str(
            source["job_fingerprint"]
            or ""
        )

        values["job_fingerprint"] = (
            hashlib.sha256(
                (
                    original_fingerprint
                    + "|"
                    + suffix
                ).encode("utf-8")
            ).hexdigest()
        )

        if "url_fingerprint" in values:
            values["url_fingerprint"] = (
                hashlib.sha256(
                    (
                        str(
                            source[
                                "url_fingerprint"
                            ]
                            or source[
                                "apply_url"
                            ]
                            or ""
                        )
                        + "|"
                        + suffix
                    ).encode("utf-8")
                ).hexdigest()
            )

        if "ats_job_id" in values:
            values["ats_job_id"] = (
                str(
                    source[
                        "ats_job_id"
                    ]
                    or "4290973"
                )
                + "-"
                + suffix
            )

        values["source"] = (
            "Paylocity/full_loop_retest"
        )

        values["status"] = "found"

        if "telegram_sent" in values:
            values["telegram_sent"] = 0

        if "sent_to_n8n" in values:
            values["sent_to_n8n"] = 0

        if "n8n_send_mode" in values:
            values["n8n_send_mode"] = None

        if "already_applied" in values:
            values["already_applied"] = 0

        if "hard_rejection_reason" in values:
            values["hard_rejection_reason"] = None

        if "match_label" in values:
            values["match_label"] = (
                "FULL LOOP RETEST"
            )

        timestamp_columns = {
            "first_seen_at",
            "last_seen_at",
            "created_at",
            "updated_at",
            "last_scored_at",
        }

        now = utc_now()

        for column in timestamp_columns:
            if column in values:
                values[column] = now

        indexes = unique_indexes(
            connection
        )

        preferred_mutable_columns = [
            "job_fingerprint",
            "url_fingerprint",
            "ats_job_id",
            "source",
            "job_url",
            "apply_url",
        ]

        for attempt in range(
            1,
            20,
        ):
            conflicting_indexes = [
                columns
                for columns in indexes
                if row_conflicts(
                    connection,
                    columns,
                    values,
                )
            ]

            if not conflicting_indexes:
                break

            for columns in conflicting_indexes:
                mutable_column = next(
                    (
                        column
                        for column
                        in preferred_mutable_columns
                        if column in columns
                    ),
                    None,
                )

                if mutable_column is None:
                    raise RuntimeError(
                        "Could not safely make this "
                        "unique jobs index distinct: "
                        + json.dumps(columns)
                    )

                metadata = column_metadata[
                    mutable_column
                ]

                values[mutable_column] = (
                    mutate_unique_value(
                        mutable_column,
                        values.get(
                            mutable_column
                        ),
                        suffix,
                        attempt,
                        str(
                            metadata.get(
                                "type"
                            )
                            or "TEXT"
                        ),
                    )
                )

        else:
            raise RuntimeError(
                "Could not produce a unique cloned job."
            )

        quoted_columns = ", ".join(
            quote_identifier(column)
            for column in insert_columns
        )

        placeholders = ", ".join(
            "?"
            for _ in insert_columns
        )

        cursor = connection.execute(
            f"""
            INSERT INTO jobs (
                {quoted_columns}
            )
            VALUES (
                {placeholders}
            )
            """,
            [
                values[column]
                for column in insert_columns
            ],
        )

        test_job_id = int(
            cursor.lastrowid
        )

        connection.execute(
            """
            INSERT INTO events (
                job_id,
                event_type,
                actor,
                event_status,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                test_job_id,
                "full_loop_test_clone_created",
                "fresh_full_loop_driver",
                "completed",
                json.dumps(
                    {
                        "source_job_id":
                            SOURCE_JOB_ID,
                        "test_job_id":
                            test_job_id,
                        "test_suffix":
                            suffix,
                        "original_result_preserved":
                            True,
                        "callback_data":
                            (
                                f"job:{test_job_id}:"
                                "approve_for_n8n"
                            ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()

        created = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE id = ?
            """,
            (
                test_job_id,
            ),
        ).fetchone()

        return dict(created)

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def process_alive(
    pid: int,
) -> bool:
    try:
        os.kill(
            pid,
            0,
        )
    except OSError:
        return False

    return True


def process_command(
    pid: int,
) -> str:
    result = subprocess.run(
        [
            "ps",
            "-p",
            str(pid),
            "-o",
            "command=",
        ],
        capture_output=True,
        text=True,
    )

    return result.stdout.strip()


def find_listener() -> int | None:
    probe = subprocess.run(
        [
            "pgrep",
            "-f",
            "[a]pp.telegram_listener",
        ],
        capture_output=True,
        text=True,
    )

    if probe.returncode != 0:
        return None

    for raw_pid in probe.stdout.split():
        try:
            pid = int(
                raw_pid
            )
        except ValueError:
            continue

        if (
            pid != os.getpid()
            and process_alive(pid)
            and "app.telegram_listener"
            in process_command(pid)
        ):
            return pid

    return None


def ensure_listener() -> dict[str, Any]:
    existing = find_listener()

    if existing is not None:
        return {
            "started": False,
            "pid": existing,
            "status": "already_running",
        }

    lock_path = (
        PROJECT
        / "data"
        / "telegram_listener.lock"
    )

    if lock_path.exists():
        try:
            stale_pid = int(
                lock_path.read_text().strip()
            )
        except Exception:
            stale_pid = 0

        if (
            stale_pid <= 0
            or not process_alive(stale_pid)
            or "app.telegram_listener"
            not in process_command(
                stale_pid
            )
        ):
            lock_path.unlink(
                missing_ok=True
            )

    LISTENER_LOG.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_file = LISTENER_LOG.open(
        "ab",
        buffering=0,
    )

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.telegram_listener",
        ],
        cwd=PROJECT,
        env=environment,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    deadline = time.time() + 45

    while time.time() < deadline:
        if process.poll() is not None:
            tail = ""

            if LISTENER_LOG.exists():
                tail = LISTENER_LOG.read_text(
                    errors="replace"
                )[-5000:]

            raise RuntimeError(
                "Telegram listener exited during startup.\n"
                + tail
            )

        detected = find_listener()

        if detected is not None:
            return {
                "started": True,
                "pid": detected,
                "status": "online",
                "log": str(
                    LISTENER_LOG
                ),
            }

        time.sleep(1)

    raise RuntimeError(
        "Telegram listener did not become ready."
    )


def capture_baseline() -> dict[str, int]:
    hunter = hunter_connection()

    try:
        event_id = hunter.execute(
            """
            SELECT COALESCE(MAX(id), 0)
            FROM events
            """
        ).fetchone()[0]

        queue_id = hunter.execute(
            """
            SELECT COALESCE(MAX(id), 0)
            FROM n8n_dispatch_queue
            """
        ).fetchone()[0]

        result_id = hunter.execute(
            """
            SELECT COALESCE(MAX(id), 0)
            FROM n8n_results
            """
        ).fetchone()[0]

    finally:
        hunter.close()

    n8n = n8n_connection()

    try:
        execution_id = n8n.execute(
            """
            SELECT COALESCE(MAX(id), 0)
            FROM execution_entity
            WHERE workflowId = ?
            """,
            (
                WORKFLOW_ID,
            ),
        ).fetchone()[0]

    finally:
        n8n.close()

    return {
        "event_id": int(
            event_id
        ),
        "queue_id": int(
            queue_id
        ),
        "result_id": int(
            result_id
        ),
        "execution_id": int(
            execution_id
        ),
    }


def approval_state(
    job_id: int,
    starting_event_id: int,
) -> dict[str, Any]:
    connection = hunter_connection()

    try:
        job = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE id = ?
            """,
            (
                job_id,
            ),
        ).fetchone()

        events = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM events
                WHERE
                    job_id = ?
                    AND id > ?
                ORDER BY id
                """,
                (
                    job_id,
                    starting_event_id,
                ),
            ).fetchall()
        ]

    finally:
        connection.close()

    status = str(
        job["status"]
        if job is not None
        else ""
    ).strip().lower()

    approval_event = next(
        (
            event
            for event in events
            if event.get(
                "event_type"
            )
            == "job_action_approve_for_n8n"
        ),
        None,
    )

    return {
        "approved": (
            status
            == "approved_for_n8n"
            or approval_event
            is not None
        ),
        "status": status,
        "job":
            dict(job)
            if job is not None
            else None,
        "events": events,
        "approval_event":
            approval_event,
    }


def dispatch_job(
    job_id: int,
) -> dict[str, Any]:
    connection = get_dispatch_connection()

    try:
        ensure_schema(
            connection
        )

        open_rows = connection.execute(
            """
            SELECT *
            FROM n8n_dispatch_queue
            WHERE queue_status IN (
                'pending',
                'dispatching',
                'accepted'
            )
            """
        ).fetchall()

        if open_rows:
            raise RuntimeError(
                "An unfinished queue row appeared: "
                + json.dumps(
                    [
                        dict(row)
                        for row in open_rows
                    ],
                    default=str,
                )
            )

        job_row = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE id = ?
            """,
            (
                job_id,
            ),
        ).fetchone()

        if job_row is None:
            raise RuntimeError(
                "Test job disappeared before dispatch."
            )

        job = dict(
            job_row
        )

        if str(
            job.get(
                "status",
                "",
            )
        ).lower() != "approved_for_n8n":
            raise RuntimeError(
                "Telegram approval was not stored."
            )

        existing_results = connection.execute(
            """
            SELECT COUNT(*)
            FROM n8n_results
            WHERE job_id = ?
            """,
            (
                job_id,
            ),
        ).fetchone()[0]

        if existing_results:
            raise RuntimeError(
                "The fresh test job already has a result."
            )

        connection.execute(
            "BEGIN IMMEDIATE"
        )

        queue = insert_queue_item(
            connection,
            job,
            "telegram_manual",
            "production",
        )

        connection.commit()

        queue_id = int(
            queue["id"]
        )

    finally:
        connection.close()

    result = dispatch_pending(
        webhook_mode="production",
        dry_run=False,
        allow_disabled=False,
        limit=1,
    )

    if result.get("blocked"):
        raise RuntimeError(
            "Production dispatch was blocked: "
            + json.dumps(
                result,
                default=str,
            )
        )

    if int(
        result.get(
            "n8n_calls",
            0,
        )
        or 0
    ) != 1:
        raise RuntimeError(
            "Expected exactly one n8n request: "
            + json.dumps(
                result,
                default=str,
            )
        )

    dispatched = (
        result.get(
            "dispatched"
        )
        or []
    )

    if len(dispatched) != 1:
        raise RuntimeError(
            "Expected exactly one dispatched item."
        )

    dispatched_item = dispatched[0]

    if int(
        dispatched_item.get(
            "job_id",
            0,
        )
        or 0
    ) != job_id:
        raise RuntimeError(
            "A different job was dispatched."
        )

    if int(
        dispatched_item.get(
            "queue_id",
            0,
        )
        or 0
    ) != queue_id:
        raise RuntimeError(
            "A different queue row was dispatched."
        )

    if dispatched_item.get(
        "execution_scope"
    ) != "full":
        raise RuntimeError(
            "Execution scope was not full."
        )

    if dispatched_item.get(
        "result"
    ) != "accepted":
        raise RuntimeError(
            "Production webhook was not accepted."
        )

    return {
        "queue_id": queue_id,
        "dispatch_result": result,
    }


def wait_for_completion(
    job_id: int,
    queue_id: int,
    baseline: dict[str, int],
) -> dict[str, Any]:
    deadline = (
        time.time()
        + EXECUTION_TIMEOUT
    )

    last_line = None
    execution_success_at = None

    while time.time() < deadline:
        hunter = hunter_connection()

        try:
            queue = hunter.execute(
                """
                SELECT *
                FROM n8n_dispatch_queue
                WHERE id = ?
                """,
                (
                    queue_id,
                ),
            ).fetchone()

            job = hunter.execute(
                """
                SELECT *
                FROM jobs
                WHERE id = ?
                """,
                (
                    job_id,
                ),
            ).fetchone()

            result = hunter.execute(
                """
                SELECT *
                FROM n8n_results
                WHERE
                    job_id = ?
                    AND id > ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (
                    job_id,
                    baseline[
                        "result_id"
                    ],
                ),
            ).fetchone()

        finally:
            hunter.close()

        n8n = n8n_connection()

        try:
            execution = n8n.execute(
                """
                SELECT
                    id,
                    workflowId,
                    mode,
                    status,
                    startedAt,
                    stoppedAt,
                    finished
                FROM execution_entity
                WHERE
                    workflowId = ?
                    AND id > ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (
                    WORKFLOW_ID,
                    baseline[
                        "execution_id"
                    ],
                ),
            ).fetchone()

        finally:
            n8n.close()

        queue_status = str(
            queue["queue_status"]
            if queue is not None
            else "missing"
        ).lower()

        execution_status = str(
            execution["status"]
            if execution is not None
            else "waiting"
        ).lower()

        line = (
            f"queue={queue_status} | "
            f"n8n={execution_status} | "
            f"callback_result="
            f"{'yes' if result else 'no'}"
        )

        if line != last_line:
            print(
                utc_now(),
                line,
                flush=True,
            )

            last_line = line

        if execution_status == "success":
            if execution_success_at is None:
                execution_success_at = (
                    time.time()
                )

        if (
            queue_status == "completed"
            and execution_status == "success"
            and result is not None
        ):
            return {
                "job": dict(
                    job
                ),
                "queue": dict(
                    queue
                ),
                "result": dict(
                    result
                ),
                "execution": dict(
                    execution
                ),
            }

        if execution_status in {
            "error",
            "crashed",
            "canceled",
            "cancelled",
        }:
            raise RuntimeError(
                "n8n execution failed: "
                + json.dumps(
                    dict(execution),
                    default=str,
                )
            )

        if queue_status in {
            "failed",
            "canceled",
            "cancelled",
        }:
            raise RuntimeError(
                "Queue failed: "
                + json.dumps(
                    dict(queue),
                    default=str,
                )
            )

        if (
            execution_success_at
            is not None
            and (
                time.time()
                - execution_success_at
            )
            > CALLBACK_GRACE
            and queue_status
            != "completed"
        ):
            raise RuntimeError(
                "n8n finished, but the FastAPI callback "
                "did not complete within three minutes."
            )

        time.sleep(5)

    raise TimeoutError(
        "Timed out waiting for the complete production cycle."
    )


def decode_flatted(
    raw_text: str,
) -> Any:
    packed = json.loads(
        raw_text
    )

    if not isinstance(
        packed,
        list,
    ):
        return packed

    memo: dict[int, Any] = {}

    def resolve(
        value: Any,
    ) -> Any:
        if isinstance(value, str):
            if value.startswith("\\"):
                return value[1:]

            if re.fullmatch(
                r"\d+",
                value,
            ):
                index = int(
                    value
                )

                if (
                    0
                    <= index
                    < len(packed)
                ):
                    return decode_index(
                        index
                    )

            return value

        if isinstance(value, list):
            return [
                resolve(item)
                for item in value
            ]

        if isinstance(value, dict):
            return {
                key: resolve(child)
                for key, child
                in value.items()
            }

        return value

    def decode_index(
        index: int,
    ) -> Any:
        if index in memo:
            return memo[index]

        value = packed[index]

        if isinstance(value, dict):
            output: dict[str, Any] = {}

            memo[index] = output

            for key, child in value.items():
                output[key] = resolve(
                    child
                )

            return output

        if isinstance(value, list):
            output_list: list[Any] = []

            memo[index] = output_list

            output_list.extend(
                resolve(child)
                for child in value
            )

            return output_list

        decoded = (
            value[1:]
            if (
                isinstance(value, str)
                and value.startswith("\\")
            )
            else value
        )

        memo[index] = decoded

        return decoded

    return (
        decode_index(0)
        if packed
        else {}
    )


def find_key(
    value: Any,
    target: str,
) -> Any:
    if isinstance(value, dict):
        if target in value:
            return value[target]

        for child in value.values():
            found = find_key(
                child,
                target,
            )

            if found is not None:
                return found

    elif isinstance(value, list):
        for child in value:
            found = find_key(
                child,
                target,
            )

            if found is not None:
                return found

    return None


def audit_execution(
    execution_id: int,
) -> dict[str, Any]:
    n8n = n8n_connection()

    try:
        row = n8n.execute(
            """
            SELECT data
            FROM execution_data
            WHERE executionId = ?
            """,
            (
                execution_id,
            ),
        ).fetchone()

    finally:
        n8n.close()

    if row is None:
        raise RuntimeError(
            "Stored execution data is missing."
        )

    decoded = decode_flatted(
        str(
            row["data"]
        )
    )

    run_data = find_key(
        decoded,
        "runData",
    )

    if not isinstance(
        run_data,
        dict,
    ):
        raise RuntimeError(
            "Could not decode n8n runData."
        )

    executed = set(
        run_data
    )

    required_nodes = {
        "Localhost Hunter Webhook v0.5",
        "Restore Payload + Apply Localhost Overrides v0.5",
        "Universal Manual Job Parser V7.4",
        "Gemini Deep Job Insight Booster",
        "OpenAI Resume and Cover Letter Booster",
        "Perplexity Company and Contact Research",
        "SerpAPI People Search",
        "Run HR Agent V7.7",
        "OpenAI HR Resume Improver V7.8",
        "Run HR Agent V7.8 Final",
        "Recalculate ATS Score V7.8",
        "Prepare Apply Package",
        "Apps Script Docs and Sheets Writer",
        "Parse Apps Script Writer Response",
        "POST Localhost Completion Callback v0.5",
    }

    missing = sorted(
        required_nodes - executed
    )

    node_summary = {}

    for node_name in sorted(
        required_nodes
    ):
        entries = (
            run_data.get(
                node_name
            )
            or []
        )

        node_summary[
            node_name
        ] = {
            "executed": bool(
                entries
            ),
            "execution_count": len(
                entries
            ),
            "has_error": any(
                bool(
                    entry.get(
                        "error"
                    )
                )
                for entry in entries
                if isinstance(
                    entry,
                    dict,
                )
            ),
        }

    return {
        "executed_node_count": len(
            executed
        ),
        "missing_required_nodes":
            missing,
        "node_summary":
            node_summary,
        "last_node_executed":
            find_key(
                decoded,
                "lastNodeExecuted",
            ),
    }


def validate_outputs(
    completion: dict[str, Any],
    route: dict[str, Any],
) -> dict[str, Any]:
    job = completion["job"]
    queue = completion["queue"]
    result = completion["result"]
    execution = completion[
        "execution"
    ]

    failures: list[str] = []

    if str(
        queue.get(
            "queue_status",
            "",
        )
    ).lower() != "completed":
        failures.append(
            "Queue did not complete."
        )

    if str(
        execution.get(
            "status",
            "",
        )
    ).lower() != "success":
        failures.append(
            "n8n execution did not succeed."
        )

    if int(
        job.get(
            "sent_to_n8n",
            0,
        )
        or 0
    ) != 1:
        failures.append(
            "Job is not marked sent_to_n8n."
        )

    if route[
        "missing_required_nodes"
    ]:
        failures.append(
            "Required nodes were skipped: "
            + ", ".join(
                route[
                    "missing_required_nodes"
                ]
            )
        )

    if result.get(
        "final_ats_score"
    ) is None:
        failures.append(
            "Final ATS score is missing."
        )

    if not str(
        result.get(
            "google_sheet_url"
        )
        or ""
    ).strip():
        failures.append(
            "Google Sheet URL is missing."
        )

    if not (
        str(
            result.get(
                "resume_doc_url"
            )
            or ""
        ).strip()
        or str(
            result.get(
                "resume_pdf_url"
            )
            or ""
        ).strip()
    ):
        failures.append(
            "Resume Doc/PDF URL is missing."
        )

    if not str(
        result.get(
            "cover_letter_doc_url"
        )
        or ""
    ).strip():
        failures.append(
            "Cover-letter URL is missing."
        )

    if failures:
        raise RuntimeError(
            "Full-loop output verification failed:\n- "
            + "\n- ".join(
                failures
            )
        )

    return {
        "n8n_status":
            result.get(
                "n8n_status"
            ),
        "final_ats_score":
            result.get(
                "final_ats_score"
            ),
        "resume_doc_url":
            result.get(
                "resume_doc_url"
            ),
        "resume_pdf_url":
            result.get(
                "resume_pdf_url"
            ),
        "cover_letter_doc_url":
            result.get(
                "cover_letter_doc_url"
            ),
        "google_sheet_url":
            result.get(
                "google_sheet_url"
            ),
        "recruiter_found":
            result.get(
                "recruiter_found"
            ),
        "outreach_draft_created":
            result.get(
                "outreach_draft_created"
            ),
    }


def send_failure_notice(
    message: str,
) -> None:
    if not CHAT_ID:
        return

    try:
        telegram_request(
            "sendMessage",
            {
                "chat_id": CHAT_ID,
                "text": (
                    "❌ Pure Power fresh full-loop test failed\n\n"
                    + message[:3000]
                ),
                "disable_web_page_preview":
                    "true",
            },
        )
    except Exception:
        pass


test_job = create_fresh_test_job()

test_job_id = int(
    test_job["id"]
)

save_state(
    stage="test_job_created",
    source_job_id=SOURCE_JOB_ID,
    test_job_id=test_job_id,
    test_job_fingerprint=test_job[
        "job_fingerprint"
    ],
    production_dispatch_started=False,
)

print()
print(
    "Fresh Pure Power test job created."
)

print(
    "Original job preserved:",
    SOURCE_JOB_ID,
)

print(
    "Fresh test job:",
    test_job_id,
)

print(
    "Fresh fingerprint:",
    test_job[
        "job_fingerprint"
    ],
)

listener = ensure_listener()

print()
print(
    "Telegram listener:",
    json.dumps(
        listener,
        ensure_ascii=False,
    ),
)

baseline = capture_baseline()

message_id = send_job_card(
    test_job_id
)

save_state(
    stage="telegram_card_sent",
    telegram_message_id=message_id,
    telegram_callback_data=(
        f"job:{test_job_id}:"
        "approve_for_n8n"
    ),
)

print()
print(
    "Telegram job card sent."
)

print(
    "Message ID:",
    message_id,
)

print(
    "Callback data:",
    (
        f"job:{test_job_id}:"
        "approve_for_n8n"
    ),
)

print()
print(
    "============================================================"
)

print(
    "PRESS '✅ APPROVE FOR N8N' IN TELEGRAM"
)

print(
    "============================================================"
)

approval_deadline = (
    time.time()
    + APPROVAL_TIMEOUT
)

approval = None
last_line = None

while time.time() < approval_deadline:
    state = approval_state(
        test_job_id,
        baseline[
            "event_id"
        ],
    )

    line = (
        f"status={state['status']} | "
        f"new_events="
        f"{len(state['events'])}"
    )

    if line != last_line:
        print(
            utc_now(),
            "waiting_for_approval |",
            line,
            flush=True,
        )

        last_line = line

    if state["approved"]:
        approval = state
        break

    time.sleep(3)

if approval is None:
    send_failure_notice(
        "Telegram approval was not received within 20 minutes."
    )

    raise TimeoutError(
        "Telegram approval timed out."
    )

save_state(
    stage="telegram_approved",
    approval_event=approval.get(
        "approval_event"
    ),
)

print()
print(
    "Telegram approval received."
)

dispatch = dispatch_job(
    test_job_id
)

queue_id = int(
    dispatch["queue_id"]
)

save_state(
    stage="production_dispatched",
    queue_id=queue_id,
    production_dispatch_started=True,
    dispatch=dispatch[
        "dispatch_result"
    ],
)

print()
print(
    "Production queue:",
    queue_id,
)

print(
    "Dispatch mode: telegram_manual"
)

print(
    "Execution scope: full"
)

print(
    "Production webhook: ACCEPTED"
)

try:
    completion = wait_for_completion(
        test_job_id,
        queue_id,
        baseline,
    )

    execution_id = int(
        completion[
            "execution"
        ]["id"]
    )

    route = audit_execution(
        execution_id
    )

    outputs = validate_outputs(
        completion,
        route,
    )

except Exception as error:
    save_state(
        stage="production_failed",
        error=str(error),
    )

    send_failure_notice(
        str(error)
    )

    raise

report = {
    "success": True,
    "completed_at": utc_now(),
    "original_job": {
        "id": SOURCE_JOB_ID,
        "preserved": True,
    },
    "test_job": {
        "id": test_job_id,
        "company":
            test_job.get(
                "company_name"
            ),
        "title":
            test_job.get(
                "title"
            ),
        "fingerprint":
            test_job.get(
                "job_fingerprint"
            ),
    },
    "telegram": {
        "message_id":
            message_id,
        "callback_data":
            (
                f"job:{test_job_id}:"
                "approve_for_n8n"
            ),
        "approval_received":
            True,
        "listener":
            listener,
    },
    "dispatch":
        dispatch,
    "completion":
        completion,
    "route_audit":
        route,
    "outputs":
        outputs,
    "google_sheet": (
        "https://docs.google.com/"
        "spreadsheets/d/"
        "1TWDGSNf6jk-rH7y4mcmgBl-yqmijUhYsFEdaq7n4zhE/edit"
    ),
}

REPORT_PATH.parent.mkdir(
    parents=True,
    exist_ok=True,
)

REPORT_PATH.write_text(
    json.dumps(
        report,
        indent=2,
        ensure_ascii=False,
        default=str,
    )
    + "\n"
)

save_state(
    stage="completed",
    success=True,
    execution_id=completion[
        "execution"
    ]["id"],
    result_id=completion[
        "result"
    ]["id"],
    outputs=outputs,
)

summary = [
    "✅ PURE POWER FULL LOOP COMPLETED",
    "",
    (
        "Test job: "
        + str(
            test_job_id
        )
    ),
    (
        "n8n execution: "
        + str(
            completion[
                "execution"
            ]["id"]
        )
        + " SUCCESS"
    ),
    (
        "Queue: "
        + str(
            completion[
                "queue"
            ]["id"]
        )
        + " COMPLETED"
    ),
    (
        "Final ATS score: "
        + str(
            outputs[
                "final_ats_score"
            ]
        )
    ),
    "Resume generation: completed",
    "Cover letter: completed",
    "Contact research: completed",
    "Google Sheet/Docs writer: completed",
    "FastAPI callback: completed",
]

for label in (
    "resume_doc_url",
    "resume_pdf_url",
    "cover_letter_doc_url",
    "google_sheet_url",
):
    value = str(
        outputs.get(
            label
        )
        or ""
    ).strip()

    if value:
        summary.append(
            label
            + ": "
            + value
        )

telegram_request(
    "sendMessage",
    {
        "chat_id": CHAT_ID,
        "text": "\n".join(
            summary
        ),
        "disable_web_page_preview":
            "true",
    },
)

print()
print(
    json.dumps(
        report,
        indent=2,
        ensure_ascii=False,
        default=str,
    )
)

print()
print(
    "============================================================"
)

print(
    "FULL TELEGRAM → N8N LOOP VERIFIED"
)

print(
    "============================================================"
)

print()
print(
    "Final report:",
    REPORT_PATH,
)
