from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from app.database import (
    ROOT_DIR,
    get_connection,
    get_setting,
)
from app.telegram_client import (
    telegram_request,
)


load_dotenv(ROOT_DIR / ".env")

CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
).strip()

HEARTBEAT_PATH = (
    ROOT_DIR
    / "data"
    / "telegram_listener_heartbeat.json"
)

LOCK_PATH = (
    ROOT_DIR
    / "data"
    / "telegram_listener.lock"
)


class AuditFailure(Exception):
    pass


results: list[
    tuple[str, bool, str]
] = []


def check(
    name: str,
    function: Callable[[], str],
) -> None:
    try:
        detail = function()
        results.append(
            (name, True, detail)
        )

        print(
            f"PASS  {name}: {detail}"
        )

    except Exception as error:
        results.append(
            (
                name,
                False,
                str(error),
            )
        )

        print(
            f"FAIL  {name}: {error}"
        )


def check_credentials() -> str:
    if not os.getenv(
        "TELEGRAM_BOT_TOKEN",
        "",
    ).strip():
        raise AuditFailure(
            "TELEGRAM_BOT_TOKEN is missing"
        )

    if not CHAT_ID:
        raise AuditFailure(
            "TELEGRAM_CHAT_ID is missing"
        )

    return "token and chat ID configured"


def check_bot_identity() -> str:
    response = telegram_request(
        "getMe"
    )

    bot = response["result"]

    return (
        "@"
        + str(bot.get("username"))
    )


def check_no_webhook() -> str:
    response = telegram_request(
        "getWebhookInfo"
    )

    webhook_url = str(
        response["result"].get("url")
        or ""
    )

    if webhook_url:
        raise AuditFailure(
            "Telegram webhook is configured; "
            "polling and webhook mode conflict"
        )

    return "no webhook configured"


def check_private_chat() -> str:
    response = telegram_request(
        "getChat",
        {
            "chat_id": CHAT_ID,
        },
    )

    chat = response["result"]

    if chat.get("type") != "private":
        raise AuditFailure(
            "configured chat is not private"
        )

    return "authorized private chat verified"


def check_listener_process() -> str:
    result = subprocess.run(
        [
            "pgrep",
            "-fl",
            "app.telegram_listener",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    matching_lines = [
        line
        for line in result.stdout.splitlines()
        if "python" in line.lower()
    ]

    if len(matching_lines) != 1:
        raise AuditFailure(
            "expected exactly one listener, "
            f"found {len(matching_lines)}"
        )

    return matching_lines[0]


def check_lock_file() -> str:
    if not LOCK_PATH.exists():
        raise AuditFailure(
            "listener lock file is missing"
        )

    pid_text = (
        LOCK_PATH.read_text()
        .strip()
    )

    if not pid_text.isdigit():
        raise AuditFailure(
            "listener lock does not contain a PID"
        )

    pid = int(pid_text)

    try:
        os.kill(pid, 0)
    except OSError as error:
        raise AuditFailure(
            f"listener PID {pid} is not alive"
        ) from error

    return f"listener PID {pid} is alive"


def check_heartbeat() -> str:
    if not HEARTBEAT_PATH.exists():
        raise AuditFailure(
            "heartbeat file is missing"
        )

    payload = json.loads(
        HEARTBEAT_PATH.read_text()
    )

    if payload.get("state") != "online":
        raise AuditFailure(
            "listener heartbeat is not online: "
            + str(payload.get("state"))
        )

    updated_at = datetime.fromisoformat(
        str(payload["updated_at"])
    )

    age_seconds = (
        datetime.now(timezone.utc)
        - updated_at
    ).total_seconds()

    if age_seconds > 15:
        raise AuditFailure(
            f"heartbeat is stale: "
            f"{age_seconds:.1f} seconds"
        )

    return (
        f"online, age "
        f"{age_seconds:.1f} seconds"
    )


def check_runtime_setting() -> str:
    runtime = get_setting(
        "runtime",
        {},
    )

    if not runtime.get(
        "telegram_enabled"
    ):
        raise AuditFailure(
            "runtime.telegram_enabled is false"
        )

    return "runtime setting enabled"


def check_database_cards() -> str:
    connection = get_connection()

    try:
        sent_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM jobs
            WHERE telegram_sent = 1
            """
        ).fetchone()[0]

        missing_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM jobs AS job
            WHERE job.telegram_sent = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM events AS event
                  WHERE event.job_id = job.id
                    AND event.event_type = 'telegram_job_card_sent'
                    AND event.event_status = 'completed'
              )
            """
        ).fetchone()[0]

    finally:
        connection.close()

    if missing_count:
        raise AuditFailure(
            f"{missing_count} of {sent_count} jobs marked sent "
            "lack a completed message mapping"
        )

    return (
        f"{sent_count} sent job card mapping(s); none missing"
    )


def check_sync_events() -> str:
    connection = get_connection()

    try:
        failed = connection.execute(
            """
            SELECT COUNT(*)
            FROM events AS failed
            WHERE failed.event_type = 'telegram_card_sync'
              AND failed.event_status = 'failed'
              AND NOT EXISTS (
                  SELECT 1
                  FROM events AS recovered
                  WHERE recovered.job_id = failed.job_id
                    AND recovered.id > failed.id
                    AND (
                        (
                            recovered.event_type = 'telegram_card_sync'
                            AND recovered.event_status = 'completed'
                        )
                        OR (
                            recovered.event_type = 'telegram_job_card_sent'
                            AND recovered.event_status = 'completed'
                        )
                    )
              )
            """
        ).fetchone()[0]

    finally:
        connection.close()

    if failed:
        raise AuditFailure(
            f"{failed} failed Telegram sync event(s)"
        )

    return "no unresolved failed synchronization events"


def check_send_edit_delete() -> str:
    sent = telegram_request(
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": (
                "Telegram audit test. "
                "This message will be removed."
            ),
        },
    )

    message_id = int(
        sent["result"]["message_id"]
    )

    telegram_request(
        "editMessageText",
        {
            "chat_id": CHAT_ID,
            "message_id": str(
                message_id
            ),
            "text": (
                "Telegram audit edit passed. "
                "Removing message."
            ),
        },
    )

    telegram_request(
        "deleteMessage",
        {
            "chat_id": CHAT_ID,
            "message_id": str(
                message_id
            ),
        },
    )

    return (
        "send, edit, and delete passed"
    )


def main() -> None:
    print(
        "Aadil HR Hunter Telegram Audit"
    )
    print("=" * 38)

    check(
        "Credentials",
        check_credentials,
    )

    check(
        "Bot identity",
        check_bot_identity,
    )

    check(
        "Polling configuration",
        check_no_webhook,
    )

    check(
        "Authorized chat",
        check_private_chat,
    )

    check(
        "Single listener",
        check_listener_process,
    )

    check(
        "Listener lock",
        check_lock_file,
    )

    check(
        "Listener heartbeat",
        check_heartbeat,
    )

    check(
        "Runtime setting",
        check_runtime_setting,
    )

    check(
        "Job card mappings",
        check_database_cards,
    )

    check(
        "Synchronization history",
        check_sync_events,
    )

    check(
        "Telegram API lifecycle",
        check_send_edit_delete,
    )

    failed_results = [
        result
        for result in results
        if not result[1]
    ]

    print()
    print("=" * 38)

    if failed_results:
        print(
            "TELEGRAM AUDIT: FAILED"
        )

        print(
            f"Failed checks: "
            f"{len(failed_results)}"
        )

        sys.exit(1)

    print(
        "TELEGRAM AUDIT: ALL CHECKS PASSED"
    )


if __name__ == "__main__":
    main()
