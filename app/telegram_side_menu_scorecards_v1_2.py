from __future__ import annotations

import asyncio
from typing import Any

FEATURE_MARKER = "AADIL_TELEGRAM_SIDE_MENU_SCORECARDS_V1_2"

SIDE_MENU_COMMAND_SPECS: tuple[dict[str, str], ...] = (
    {
        "command": "scorecards",
        "description": "Past 24h jobs grouped by Hunter score",
        "bucket": "",
    },
    {
        "command": "last24h",
        "description": "Open the 24-hour scorecard browser",
        "bucket": "",
    },
    {
        "command": "jobs_95plus",
        "description": "Past 24h jobs scoring 95+",
        "bucket": "95p",
    },
    {
        "command": "jobs_90_94",
        "description": "Past 24h jobs scoring 90-94",
        "bucket": "90_94",
    },
    {
        "command": "jobs_80_89",
        "description": "Past 24h jobs scoring 80-89",
        "bucket": "80_89",
    },
    {
        "command": "jobs_70_79",
        "description": "Past 24h jobs scoring 70-79",
        "bucket": "70_79",
    },
    {
        "command": "jobs_60_69",
        "description": "Past 24h jobs scoring 60-69",
        "bucket": "60_69",
    },
    {
        "command": "jobs_50_59",
        "description": "Past 24h jobs scoring 50-59",
        "bucket": "50_59",
    },
    {
        "command": "jobs_under50",
        "description": "Past 24h jobs scoring under 50",
        "bucket": "u50",
    },
)

COMMAND_TO_BUCKET = {
    str(item["command"]): str(item["bucket"])
    for item in SIDE_MENU_COMMAND_SPECS
    if item.get("bucket")
}


def side_menu_command_specs() -> list[dict[str, str]]:
    return [
        {
            "command": str(item["command"]),
            "description": str(item["description"]),
        }
        for item in SIDE_MENU_COMMAND_SPECS
    ]


def _allowed_chat_id() -> int:
    from app.telegram_client import CHAT_ID

    try:
        return int(str(CHAT_ID or "0").strip())
    except (TypeError, ValueError):
        return 0


async def _send_bucket_command(
    update: Any,
    context: Any,
    bucket_key: str,
) -> None:
    del context

    chat = getattr(update, "effective_chat", None)
    message = getattr(update, "effective_message", None)

    if chat is None or message is None:
        return

    chat_id = int(chat.id)

    if chat_id != _allowed_chat_id():
        await message.reply_text("Unauthorized Telegram chat.")
        return

    try:
        from app.telegram_scorecards_v1 import send_bucket_page

        await asyncio.to_thread(
            send_bucket_page,
            chat_id,
            bucket_key,
            0,
        )
    except Exception as error:
        await message.reply_text(
            f"Could not open this 24-hour score range: {error}"
        )


async def jobs_under50_command(update: Any, context: Any) -> None:
    await _send_bucket_command(update, context, "u50")


async def jobs_50_59_command(update: Any, context: Any) -> None:
    await _send_bucket_command(update, context, "50_59")


async def jobs_60_69_command(update: Any, context: Any) -> None:
    await _send_bucket_command(update, context, "60_69")


async def jobs_70_79_command(update: Any, context: Any) -> None:
    await _send_bucket_command(update, context, "70_79")


async def jobs_80_89_command(update: Any, context: Any) -> None:
    await _send_bucket_command(update, context, "80_89")


async def jobs_90_94_command(update: Any, context: Any) -> None:
    await _send_bucket_command(update, context, "90_94")


async def jobs_95plus_command(update: Any, context: Any) -> None:
    await _send_bucket_command(update, context, "95p")


def self_test() -> dict[str, Any]:
    names = [
        str(item["command"])
        for item in SIDE_MENU_COMMAND_SPECS
    ]
    buckets = {
        str(item["command"]): str(item["bucket"])
        for item in SIDE_MENU_COMMAND_SPECS
        if item.get("bucket")
    }

    return {
        "marker": FEATURE_MARKER,
        "command_count": len(names),
        "ordered_commands": names,
        "bucket_commands": buckets,
        "unique_commands": len(names) == len(set(names)),
        "valid_lengths": all(
            1 <= len(name) <= 32
            for name in names
        ),
    }
