from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.database import (
    ROOT_DIR,
    get_connection,
    get_setting,
    save_setting,
)


ENV_PATH = ROOT_DIR / ".env"
load_dotenv(ENV_PATH)

BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
).strip()

API_BASE = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
)


def telegram_request(
    method: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing from .env"
        )

    encoded_data = None

    if payload is not None:
        encoded_data = urllib.parse.urlencode(
            payload
        ).encode("utf-8")

    request = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=encoded_data,
        method="POST" if payload is not None else "GET",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )
    except urllib.error.HTTPError as error:
        body = error.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Telegram HTTP error {error.code}: {body}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Telegram connection error: {error}"
        ) from error

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram API returned an error: "
            + json.dumps(result)
        )

    return result


def save_env_value(
    key: str,
    value: str,
) -> None:
    existing_lines = (
        ENV_PATH.read_text().splitlines()
        if ENV_PATH.exists()
        else []
    )

    updated_lines = [
        line
        for line in existing_lines
        if not line.startswith(f"{key}=")
    ]

    updated_lines.append(f"{key}={value}")
    ENV_PATH.write_text(
        "\n".join(updated_lines) + "\n"
    )


def find_private_chat_id(
    updates: list[dict[str, Any]],
) -> tuple[int, str]:
    for update in reversed(updates):
        message = update.get("message") or {}
        chat = message.get("chat") or {}

        if chat.get("type") != "private":
            continue

        chat_id = chat.get("id")

        if chat_id is None:
            continue

        first_name = (
            chat.get("first_name")
            or chat.get("username")
            or "Aadil"
        )

        return int(chat_id), str(first_name)

    raise RuntimeError(
        "No private Telegram chat was found. "
        "Open the bot, send /start and one normal message, "
        "then run this command again."
    )


def main() -> None:
    bot_response = telegram_request("getMe")
    bot = bot_response["result"]

    print(
        "Bot verified:",
        f"@{bot.get('username')}",
    )

    webhook_response = telegram_request(
        "getWebhookInfo"
    )

    webhook_url = (
        webhook_response["result"].get("url")
        or ""
    )

    if webhook_url:
        raise RuntimeError(
            "This bot currently has a Telegram webhook. "
            "Long polling cannot be used until it is removed."
        )

    updates_response = telegram_request(
        "getUpdates",
        {
            "timeout": 0,
            "allowed_updates": json.dumps(
                [
                    "message",
                    "callback_query",
                ]
            ),
        },
    )

    updates = updates_response.get(
        "result",
        [],
    )

    chat_id, first_name = find_private_chat_id(
        updates
    )

    save_env_value(
        "TELEGRAM_CHAT_ID",
        str(chat_id),
    )

    test_message = (
        "✅ Aadil HR Hunter is connected.\n\n"
        "Local database: ready\n"
        "Scoring engine: ready\n"
        "Telegram actions: setup in progress\n"
        "n8n: still disconnected\n"
        "Real job sources: still disabled"
    )

    send_response = telegram_request(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": test_message,
        },
    )

    runtime = get_setting("runtime", {})
    runtime["telegram_enabled"] = True
    save_setting("runtime", runtime)

    connection = get_connection()

    try:
        connection.execute(
            """
            INSERT INTO events (
                job_id,
                event_type,
                actor,
                event_status,
                payload_json
            )
            VALUES (
                NULL,
                'telegram_connected',
                'telegram_setup',
                'completed',
                ?
            )
            """,
            (
                json.dumps(
                    {
                        "bot_username":
                            bot.get("username"),
                        "chat_id": chat_id,
                        "message_id":
                            send_response[
                                "result"
                            ].get("message_id"),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()
    finally:
        connection.close()

    print("Private chat found:", first_name)
    print("Chat ID saved securely in .env")
    print(
        "Test message ID:",
        send_response["result"].get(
            "message_id"
        ),
    )
    print("Telegram connection test: OK")


if __name__ == "__main__":
    main()
