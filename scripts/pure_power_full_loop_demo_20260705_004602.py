from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


PROJECT = Path.cwd()
HUNTER_DB = PROJECT / "data/hunter.db"
N8N_DB = Path.home() / ".n8n/database.sqlite"

JOB_ID = 26
WORKFLOW_ID = "L1u2xZkgFpi7KEuv"

REPORT = Path(sys.argv[1])
LISTENER_LOG = Path(sys.argv[2])

APPROVAL_TIMEOUT = 20 * 60
RUN_TIMEOUT = 35 * 60


def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def load_environment() -> dict[str, str]:
    environment = os.environ.copy()

    for key, value in dotenv_values(
        PROJECT / ".env"
    ).items():
        if key and value is not None:
            environment[key] = value

    return environment


ENV = load_environment()


def first_env(*keys: str) -> str:
    for key in keys:
        value = str(
            ENV.get(
                key,
                "",
            )
        ).strip()

        if value:
            return value

    return ""


BOT_TOKEN = first_env(
    "TELEGRAM_BOT_TOKEN",
    "TG_BOT_TOKEN",
    "TELEGRAM_TOKEN",
    "BOT_TOKEN",
)

CHAT_ID = first_env(
    "TELEGRAM_CHAT_ID",
    "TG_CHAT_ID",
    "TELEGRAM_TARGET_CHAT_ID",
    "CHAT_ID",
)

if not BOT_TOKEN or not CHAT_ID:
    available = sorted(
        key
        for key, value in ENV.items()
        if (
            "TELEGRAM" in key.upper()
            and value
        )
    )

    raise RuntimeError(
        "Telegram credentials were not found in .env. "
        f"Configured Telegram-related keys: {available}"
    )


def telegram_api(
    method: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = (
        "https://api.telegram.org/bot"
        + BOT_TOKEN
        + "/"
        + method
    )

    encoded = urllib.parse.urlencode(
        {
            key: (
                json.dumps(value)
                if isinstance(
                    value,
                    (
                        dict,
                        list,
                    ),
                )
                else str(value)
            )
            for key, value in payload.items()
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={
            "Content-Type":
                "application/x-www-form-urlencoded",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=45,
    ) as response:
        body = response.read().decode(
            "utf-8",
            errors="replace",
        )

    result = json.loads(body)

    if result.get("ok") is not True:
        raise RuntimeError(
            f"Telegram API {method} failed: {result}"
        )

    return result


def discover_callback_data(
    job_id: int,
) -> tuple[str, str]:
    ranked: list[
        tuple[
            int,
            str,
            str,
        ]
    ] = []

    context = {
        "job_id": job_id,
        "row_id": job_id,
        "id": job_id,
        "job": {
            "id": job_id,
            "job_id": job_id,
            "row_id": job_id,
        },
        "item": {
            "id": job_id,
            "job_id": job_id,
            "row_id": job_id,
        },
        "payload": {
            "id": job_id,
            "job_id": job_id,
            "row_id": job_id,
        },
        "str": str,
        "int": int,
    }

    for path in sorted(
        (
            PROJECT / "app"
        ).rglob("*.py")
    ):
        try:
            text = path.read_text(
                errors="replace"
            )
        except OSError:
            continue

        lowered = text.lower()

        if not any(
            term in lowered
            for term in (
                "callback_data",
                "send_to_n8n",
                "send_n8n",
                "telegram_manual",
            )
        ):
            continue

        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None

        expressions: list[
            tuple[
                ast.AST,
                int,
            ]
        ] = []

        if tree is not None:
            for node in ast.walk(tree):
                if (
                    isinstance(
                        node,
                        ast.keyword,
                    )
                    and node.arg == "callback_data"
                ):
                    expressions.append(
                        (
                            node.value,
                            100,
                        )
                    )

                elif isinstance(
                    node,
                    ast.Dict,
                ):
                    for key_node, value_node in zip(
                        node.keys,
                        node.values,
                    ):
                        if (
                            isinstance(
                                key_node,
                                ast.Constant,
                            )
                            and key_node.value
                            == "callback_data"
                        ):
                            expressions.append(
                                (
                                    value_node,
                                    100,
                                )
                            )

            for expression, score in expressions:
                segment = (
                    ast.get_source_segment(
                        text,
                        expression,
                    )
                    or ""
                )

                segment_lower = segment.lower()

                if "n8n" not in segment_lower:
                    continue

                try:
                    value = eval(
                        compile(
                            ast.Expression(
                                expression
                            ),
                            str(path),
                            "eval",
                        ),
                        {
                            "__builtins__": {},
                        },
                        context,
                    )
                except Exception:
                    value = None

                if not isinstance(
                    value,
                    str,
                ):
                    continue

                if (
                    "{"
                    in value
                    and "}"
                    in value
                ):
                    try:
                        value = value.format(
                            **context
                        )
                    except Exception:
                        pass

                if str(job_id) not in value:
                    continue

                if len(
                    value.encode("utf-8")
                ) > 64:
                    continue

                if "send" in value.lower():
                    score += 25

                if "approve" in value.lower():
                    score += 20

                if "dispatch" in value.lower():
                    score += 10

                ranked.append(
                    (
                        score,
                        value,
                        str(path),
                    )
                )

            for node in ast.walk(tree):
                if not (
                    isinstance(
                        node,
                        ast.Constant,
                    )
                    and isinstance(
                        node.value,
                        str,
                    )
                ):
                    continue

                literal = node.value
                literal_lower = literal.lower()

                if "n8n" not in literal_lower:
                    continue

                if not any(
                    word in literal_lower
                    for word in (
                        "send",
                        "approve",
                        "dispatch",
                    )
                ):
                    continue

                if literal.endswith(
                    (
                        ":",
                        "_",
                        "-",
                        "|",
                    )
                ):
                    value = (
                        literal
                        + str(job_id)
                    )

                    if len(
                        value.encode(
                            "utf-8"
                        )
                    ) <= 64:
                        ranked.append(
                            (
                                40,
                                value,
                                str(path),
                            )
                        )

        patterns = [
            r'["\']((?:send|approve|dispatch)[^"\']*n8n[^"\']*[:_|-])["\']',
            r'["\']([^"\']*n8n[^"\']*(?:send|approve|dispatch)[^"\']*[:_|-])["\']',
        ]

        for pattern in patterns:
            for match in re.finditer(
                pattern,
                text,
                flags=re.IGNORECASE,
            ):
                value = (
                    match.group(1)
                    + str(job_id)
                )

                if len(
                    value.encode("utf-8")
                ) <= 64:
                    ranked.append(
                        (
                            30,
                            value,
                            str(path),
                        )
                    )

    ranked = sorted(
        set(ranked),
        key=lambda item: (
            -item[0],
            item[1],
            item[2],
        ),
    )

    if not ranked:
        raise RuntimeError(
            "Could not discover the existing Telegram "
            "'Send to n8n' callback_data pattern. "
            "No notification was sent."
        )

    _, callback_data, source = ranked[0]

    return callback_data, source


def ensure_listener_running() -> dict[str, Any]:
    probe = subprocess.run(
        [
            "pgrep",
            "-af",
            "telegram.*listener|telegram_listener",
        ],
        capture_output=True,
        text=True,
    )

    if (
        probe.returncode == 0
        and probe.stdout.strip()
    ):
        return {
            "started": False,
            "processes":
                probe.stdout.strip().splitlines(),
        }

    candidates = [
        (
            "app.telegram_listener",
            PROJECT
            / "app"
            / "telegram_listener.py",
        ),
        (
            "app.telegram_worker",
            PROJECT
            / "app"
            / "telegram_worker.py",
        ),
        (
            "app.telegram_bot",
            PROJECT
            / "app"
            / "telegram_bot.py",
        ),
    ]

    module = next(
        (
            name
            for name, path in candidates
            if path.exists()
        ),
        None,
    )

    if module is None:
        raise RuntimeError(
            "No Telegram listener module was found under app/."
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
            module,
        ],
        cwd=PROJECT,
        env=ENV,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    time.sleep(4)

    if process.poll() is not None:
        tail = LISTENER_LOG.read_text(
            errors="replace"
        )[-4000:]

        raise RuntimeError(
            "Telegram listener exited immediately.\n"
            + tail
        )

    return {
        "started": True,
        "module": module,
        "pid": process.pid,
        "log": str(LISTENER_LOG),
    }


def connect_hunter() -> sqlite3.Connection:
    connection = sqlite3.connect(
        HUNTER_DB,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return connection


def connect_n8n() -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{N8N_DB}?mode=ro",
        uri=True,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    return connection


def prepare_job() -> dict[str, Any]:
    connection = connect_hunter()

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        job = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE id = ?
            """,
            (
                JOB_ID,
            ),
        ).fetchone()

        if job is None:
            raise RuntimeError(
                "Pure Power sample job 26 is missing."
            )

        open_rows = connection.execute(
            """
            SELECT
                id,
                queue_status
            FROM n8n_dispatch_queue
            WHERE
                job_id = ?
                AND queue_status IN (
                    'pending',
                    'dispatching',
                    'accepted'
                )
            """,
            (
                JOB_ID,
            ),
        ).fetchall()

        for row in open_rows:
            connection.execute(
                """
                UPDATE n8n_dispatch_queue
                SET
                    queue_status = 'failed',
                    last_error = ?,
                    completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    (
                        "Closed before the clean Telegram "
                        "approval test. The previous execution "
                        "did not complete the writer/callback loop."
                    ),
                    row["id"],
                ),
            )

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(jobs)"
            ).fetchall()
        }

        updates = {
            "status": "telegram_notified",
            "sent_to_n8n": 0,
            "telegram_sent": 0,
            "updated_at": now_iso(),
        }

        if "job_status" in columns:
            updates[
                "job_status"
            ] = "telegram_notified"

        assignments = ", ".join(
            f'"{key}" = ?'
            for key in updates
            if key in columns
        )

        values = [
            value
            for key, value in updates.items()
            if key in columns
        ]

        connection.execute(
            f"""
            UPDATE jobs
            SET {assignments}
            WHERE id = ?
            """,
            values + [JOB_ID],
        )

        connection.commit()

        final_job = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE id = ?
            """,
            (
                JOB_ID,
            ),
        ).fetchone()

        return dict(final_job)

    finally:
        connection.close()


def capture_baseline() -> dict[str, int]:
    hunter = connect_hunter()

    try:
        queue_id = hunter.execute(
            """
            SELECT COALESCE(MAX(id), 0)
            FROM n8n_dispatch_queue
            """
        ).fetchone()[0]

        event_id = hunter.execute(
            """
            SELECT COALESCE(MAX(id), 0)
            FROM events
            """
        ).fetchone()[0]

    finally:
        hunter.close()

    n8n = connect_n8n()

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
        "queue": int(queue_id),
        "event": int(event_id),
        "execution": int(execution_id),
    }


def mark_telegram_sent(
    message_id: int,
) -> None:
    connection = connect_hunter()

    try:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(jobs)"
            ).fetchall()
        }

        updates: dict[str, Any] = {
            "telegram_sent": 1,
            "updated_at": now_iso(),
        }

        if "telegram_message_id" in columns:
            updates[
                "telegram_message_id"
            ] = message_id

        assignments = ", ".join(
            f'"{key}" = ?'
            for key in updates
        )

        connection.execute(
            f"""
            UPDATE jobs
            SET {assignments}
            WHERE id = ?
            """,
            list(
                updates.values()
            )
            + [JOB_ID],
        )

        connection.commit()

    finally:
        connection.close()


def approval_state(
    baseline_event: int,
    baseline_queue: int,
) -> dict[str, Any]:
    connection = connect_hunter()

    try:
        job = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE id = ?
            """,
            (
                JOB_ID,
            ),
        ).fetchone()

        queue = connection.execute(
            """
            SELECT *
            FROM n8n_dispatch_queue
            WHERE
                job_id = ?
                AND id > ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                JOB_ID,
                baseline_queue,
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
                ORDER BY id ASC
                """,
                (
                    JOB_ID,
                    baseline_event,
                ),
            ).fetchall()
        ]

        approved_event = any(
            any(
                token
                in (
                    str(
                        event.get(
                            "event_type",
                            "",
                        )
                    )
                    + " "
                    + str(
                        event.get(
                            "event_status",
                            "",
                        )
                    )
                    + " "
                    + str(
                        event.get(
                            "payload_json",
                            "",
                        )
                    )
                ).lower()
                for token in (
                    "approved_for_n8n",
                    "telegram_manual",
                    "send_to_n8n",
                    "n8n_approved",
                )
            )
            for event in events
        )

        status = str(
            (
                job["status"]
                if (
                    job
                    and "status"
                    in job.keys()
                )
                else ""
            )
        ).lower()

        approved = (
            status
            == "approved_for_n8n"
            or approved_event
            or queue is not None
        )

        return {
            "approved": approved,
            "status": status,
            "job":
                dict(job)
                if job
                else None,
            "queue":
                dict(queue)
                if queue
                else None,
            "events": events,
        }

    finally:
        connection.close()


def ensure_dispatched() -> dict[str, Any]:
    from app.n8n_dispatch import (
        dispatch_pending,
        ensure_schema,
        get_connection,
        insert_queue_item,
    )

    connection = get_connection()

    try:
        ensure_schema(connection)

        queue = connection.execute(
            """
            SELECT *
            FROM n8n_dispatch_queue
            WHERE
                job_id = ?
                AND queue_status IN (
                    'pending',
                    'dispatching',
                    'accepted'
                )
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                JOB_ID,
            ),
        ).fetchone()

        if queue is None:
            job = connection.execute(
                """
                SELECT *
                FROM jobs
                WHERE id = ?
                """,
                (
                    JOB_ID,
                ),
            ).fetchone()

            if job is None:
                raise RuntimeError(
                    "Pure Power job disappeared before dispatch."
                )

            job_dict = dict(job)

            status = str(
                job_dict.get(
                    "status",
                    "",
                )
            ).lower()

            if status != "approved_for_n8n":
                raise RuntimeError(
                    "Telegram interaction was detected, "
                    "but the job was not marked approved_for_n8n. "
                    f"Current status: {status!r}"
                )

            connection.execute(
                "BEGIN IMMEDIATE"
            )

            queue = insert_queue_item(
                connection,
                job_dict,
                "telegram_manual",
                "production",
            )

            connection.commit()

        queue_dict = dict(queue)

    finally:
        connection.close()

    if queue_dict[
        "queue_status"
    ] == "pending":
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
                "Expected exactly one n8n call: "
                + json.dumps(
                    result,
                    default=str,
                )
            )

    connection = connect_hunter()

    try:
        final_queue = connection.execute(
            """
            SELECT *
            FROM n8n_dispatch_queue
            WHERE id = ?
            """,
            (
                queue_dict["id"],
            ),
        ).fetchone()

        return dict(final_queue)

    finally:
        connection.close()


def wait_for_completion(
    queue_id: int,
    baseline_execution: int,
) -> dict[str, Any]:
    deadline = (
        time.time()
        + RUN_TIMEOUT
    )

    last_line = None

    while time.time() < deadline:
        hunter = connect_hunter()

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
                    JOB_ID,
                ),
            ).fetchone()

            result_columns = {
                row["name"]
                for row in hunter.execute(
                    "PRAGMA table_info(n8n_results)"
                ).fetchall()
            }

            result_job_column = next(
                (
                    column
                    for column in (
                        "job_id",
                        "row_id",
                        "hunter_row_id",
                    )
                    if column in result_columns
                ),
                None,
            )

            results = []

            if result_job_column:
                results = [
                    dict(row)
                    for row in hunter.execute(
                        f"""
                        SELECT *
                        FROM n8n_results
                        WHERE "{result_job_column}" = ?
                        ORDER BY rowid DESC
                        """,
                        (
                            JOB_ID,
                        ),
                    ).fetchall()
                ]

        finally:
            hunter.close()

        n8n = connect_n8n()

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
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    WORKFLOW_ID,
                    baseline_execution,
                ),
            ).fetchone()

        finally:
            n8n.close()

        queue_status = str(
            (
                queue["queue_status"]
                if queue
                else "missing"
            )
        )

        execution_status = str(
            (
                execution["status"]
                if execution
                else "waiting"
            )
        )

        line = (
            f"queue={queue_status} | "
            f"n8n={execution_status} | "
            f"callback_results={len(results)}"
        )

        if line != last_line:
            print(
                now_iso(),
                line,
                flush=True,
            )

            last_line = line

        if queue_status in {
            "completed",
            "failed",
            "cancelled",
            "canceled",
        }:
            return {
                "queue":
                    dict(queue)
                    if queue
                    else None,
                "job":
                    dict(job)
                    if job
                    else None,
                "results": results,
                "execution":
                    dict(execution)
                    if execution
                    else None,
            }

        if execution_status.lower() in {
            "error",
            "crashed",
            "cancelled",
            "canceled",
        }:
            time.sleep(5)

            return {
                "queue":
                    dict(queue)
                    if queue
                    else None,
                "job":
                    dict(job)
                    if job
                    else None,
                "results": results,
                "execution":
                    dict(execution)
                    if execution
                    else None,
            }

        time.sleep(5)

    raise TimeoutError(
        "Timed out waiting for the complete n8n cycle and callback."
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

    memo: dict[
        int,
        Any,
    ] = {}

    def resolve(
        value: Any,
    ) -> Any:
        if isinstance(
            value,
            str,
        ):
            if value.startswith("\\"):
                return value[1:]

            if re.fullmatch(
                r"\d+",
                value,
            ):
                index = int(value)

                if (
                    0
                    <= index
                    < len(packed)
                ):
                    return decode_index(
                        index
                    )

            return value

        if isinstance(
            value,
            list,
        ):
            return [
                resolve(item)
                for item in value
            ]

        if isinstance(
            value,
            dict,
        ):
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

        if isinstance(
            value,
            dict,
        ):
            target: dict[
                str,
                Any,
            ] = {}

            memo[index] = target

            for key, child in value.items():
                target[key] = resolve(
                    child
                )

            return target

        if isinstance(
            value,
            list,
        ):
            target_list: list[
                Any
            ] = []

            memo[index] = target_list

            target_list.extend(
                resolve(child)
                for child in value
            )

            return target_list

        decoded = (
            value[1:]
            if (
                isinstance(
                    value,
                    str,
                )
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
    if isinstance(
        value,
        dict,
    ):
        if target in value:
            return value[target]

        for child in value.values():
            found = find_key(
                child,
                target,
            )

            if found is not None:
                return found

    elif isinstance(
        value,
        list,
    ):
        for child in value:
            found = find_key(
                child,
                target,
            )

            if found is not None:
                return found

    return None


def first_output_json(
    entry: dict[str, Any],
) -> dict[str, Any] | None:
    main = (
        (
            entry.get(
                "data"
            )
            or {}
        ).get(
            "main"
        )
        or []
    )

    for group in main:
        if not isinstance(
            group,
            list,
        ):
            continue

        for item in group:
            if (
                isinstance(
                    item,
                    dict,
                )
                and isinstance(
                    item.get(
                        "json"
                    ),
                    dict,
                )
            ):
                return item["json"]

    return None


def audit_execution(
    execution_id: int,
) -> dict[str, Any]:
    n8n = connect_n8n()

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
            "Stored n8n execution data is missing."
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

    writer_entries = (
        run_data.get(
            "Apps Script Docs and Sheets Writer"
        )
        or []
    )

    callback_entries = (
        run_data.get(
            "POST Localhost Completion Callback v0.5"
        )
        or []
    )

    writer_output = (
        first_output_json(
            writer_entries[-1]
        )
        if writer_entries
        else None
    )

    callback_output = (
        first_output_json(
            callback_entries[-1]
        )
        if callback_entries
        else None
    )

    required = {
        "Universal Manual Job Parser V7.4",
        "Run HR Agent V7.7",
        "OpenAI HR Resume Improver V7.8",
        "Run HR Agent V7.8 Final",
        "Apps Script Docs and Sheets Writer",
        "POST Localhost Completion Callback v0.5",
    }

    missing_required = sorted(
        required - executed
    )

    contact_nodes = {
        "Perplexity Company and Contact Research",
        "SerpAPI People Search",
    }

    contacts_executed = sorted(
        contact_nodes & executed
    )

    return {
        "executed_node_count":
            len(executed),
        "missing_required_nodes":
            missing_required,
        "contacts_executed":
            contacts_executed,
        "writer_executed":
            bool(writer_entries),
        "callback_executed":
            bool(callback_entries),
        "writer_output":
            writer_output,
        "callback_output":
            callback_output,
        "last_node_executed":
            find_key(
                decoded,
                "lastNodeExecuted",
            ),
    }


def collect_urls(
    value: Any,
    path: str = "root",
    output: dict[str, str] | None = None,
) -> dict[str, str]:
    if output is None:
        output = {}

    if isinstance(
        value,
        dict,
    ):
        for key, child in value.items():
            child_path = (
                f"{path}.{key}"
            )

            if (
                isinstance(
                    child,
                    str,
                )
                and child.startswith(
                    (
                        "http://",
                        "https://",
                    )
                )
                and any(
                    token
                    in key.lower()
                    for token in (
                        "url",
                        "link",
                        "doc",
                        "pdf",
                        "sheet",
                        "resume",
                        "cover",
                    )
                )
            ):
                output[
                    child_path
                ] = child

            collect_urls(
                child,
                child_path,
                output,
            )

    elif isinstance(
        value,
        list,
    ):
        for index, child in enumerate(
            value
        ):
            collect_urls(
                child,
                f"{path}[{index}]",
                output,
            )

    return output


job = prepare_job()
baseline = capture_baseline()

callback_data, callback_source = (
    discover_callback_data(
        JOB_ID
    )
)

listener = ensure_listener_running()

notification_text = (
    "🧪 FULL LOOP APPROVAL TEST\n\n"
    "Pure Power Engineering\n"
    "HR & Talent Intern (Fall 2026)\n"
    "Hoboken, New Jersey | Hybrid\n"
    "Hunter score: 91\n"
    "Source: Paylocity\n\n"
    "Press Send to n8n to authorize:\n"
    "Resume → ATS → HR Agent → Contacts → "
    "Google Sheet/Docs → FastAPI callback."
)

sent = telegram_api(
    "sendMessage",
    {
        "chat_id": CHAT_ID,
        "text": notification_text,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text":
                            "✅ Send to n8n",
                        "callback_data":
                            callback_data,
                    }
                ]
            ]
        },
        "disable_web_page_preview":
            True,
    },
)

message_id = int(
    sent["result"]["message_id"]
)

mark_telegram_sent(
    message_id
)

print()
print(
    "Telegram approval notification sent."
)

print(
    f"Telegram message ID: {message_id}"
)

print(
    "Callback pattern source:",
    callback_source,
)

print()
print(
    "PRESS 'SEND TO N8N' IN TELEGRAM."
)
print()

approval_deadline = (
    time.time()
    + APPROVAL_TIMEOUT
)

approved = None
last_status = None

while time.time() < approval_deadline:
    state = approval_state(
        baseline["event"],
        baseline["queue"],
    )

    line = (
        f"job_status={state['status']} | "
        f"queue={'yes' if state['queue'] else 'no'} | "
        f"new_events={len(state['events'])}"
    )

    if line != last_status:
        print(
            now_iso(),
            "waiting_for_approval |",
            line,
            flush=True,
        )

        last_status = line

    if state["approved"]:
        approved = state
        break

    time.sleep(3)

if approved is None:
    telegram_api(
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text":
                "⚠️ Pure Power full-loop test timed out before approval.",
        },
    )

    raise TimeoutError(
        "Telegram approval was not received within 20 minutes."
    )

queue = ensure_dispatched()

print(
    "Telegram approval accepted."
)

print(
    f"Queue {queue['id']}: "
    f"{queue['queue_status']}"
)

completion = wait_for_completion(
    int(
        queue["id"]
    ),
    baseline["execution"],
)

execution = (
    completion.get(
        "execution"
    )
    or {}
)

queue_final = (
    completion.get(
        "queue"
    )
    or {}
)

results = (
    completion.get(
        "results"
    )
    or []
)

if str(
    execution.get(
        "status",
        "",
    )
).lower() != "success":
    raise RuntimeError(
        "n8n execution did not succeed: "
        + json.dumps(
            execution,
            default=str,
        )
    )

if str(
    queue_final.get(
        "queue_status",
        "",
    )
).lower() != "completed":
    raise RuntimeError(
        "FastAPI callback did not complete the queue: "
        + json.dumps(
            queue_final,
            default=str,
        )
    )

if not results:
    raise RuntimeError(
        "No n8n_results callback row was created."
    )

route = audit_execution(
    int(
        execution["id"]
    )
)

if route[
    "missing_required_nodes"
]:
    raise RuntimeError(
        "Required production nodes were skipped: "
        + ", ".join(
            route[
                "missing_required_nodes"
            ]
        )
    )

if not route[
    "writer_executed"
]:
    raise RuntimeError(
        "Apps Script writer did not execute."
    )

if not route[
    "callback_executed"
]:
    raise RuntimeError(
        "FastAPI callback node did not execute."
    )

if not route[
    "contacts_executed"
]:
    raise RuntimeError(
        "Neither Perplexity nor SerpAPI contact research executed."
    )

all_data = {
    "job":
        completion.get(
            "job"
        ),
    "queue":
        queue_final,
    "results":
        results,
    "route":
        route,
}

urls = collect_urls(
    all_data
)

report = {
    "success": True,
    "completed_at": now_iso(),
    "job_id": JOB_ID,
    "company":
        "Pure Power Engineering",
    "title":
        "HR & Talent Intern (Fall 2026)",
    "telegram": {
        "approval_message_id":
            message_id,
        "callback_data_source":
            callback_source,
        "listener":
            listener,
    },
    "queue":
        queue_final,
    "execution":
        execution,
    "route":
        route,
    "n8n_results":
        results,
    "output_urls":
        urls,
    "google_sheet_id":
        "1TWDGSNf6jk-rH7y4mcmgBl-yqmijUhYsFEdaq7n4zhE",
}

REPORT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

REPORT.write_text(
    json.dumps(
        report,
        indent=2,
        ensure_ascii=False,
        default=str,
    )
    + "\n"
)

summary_lines = [
    "✅ PURE POWER FULL LOOP COMPLETED",
    "",
    (
        "n8n execution: "
        + str(
            execution["id"]
        )
        + " success"
    ),
    (
        "Queue: "
        + str(
            queue_final["id"]
        )
        + " completed"
    ),
    "Resume + ATS + HR Agent: completed",
    (
        "Contact research: "
        + ", ".join(
            route[
                "contacts_executed"
            ]
        )
    ),
    "Google Sheet/Docs writer: completed",
    "FastAPI callback: completed",
]

for label, url in list(
    urls.items()
)[:6]:
    summary_lines.append(
        label.split(".")[-1]
        + ": "
        + url
    )

telegram_api(
    "sendMessage",
    {
        "chat_id":
            CHAT_ID,
        "text":
            "\n".join(
                summary_lines
            ),
        "disable_web_page_preview":
            True,
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

print(
    "Final report:",
    REPORT,
)
