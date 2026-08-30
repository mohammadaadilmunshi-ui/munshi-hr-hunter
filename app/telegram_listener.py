from __future__ import annotations

import asyncio
import fcntl
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from dotenv import load_dotenv
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.actions import (
    VALID_ACTIONS,
    apply_job_action,
)
from app.database import ROOT_DIR
from app.telegram_client import edit_job_card
from app.force_remove import force_remove_command, force_remove_callback, maybe_handle_force_remove_callback
from app.telegram_button_router import install_unified_button_router
from app.telegram_transport_v3 import configure_application_builder


load_dotenv(ROOT_DIR / ".env")

BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
).strip()

ALLOWED_CHAT_ID = int(
    os.getenv(
        "TELEGRAM_CHAT_ID",
        "0",
    )
)

DATA_DIR = ROOT_DIR / "data"

LOCK_PATH = (
    DATA_DIR
    / "telegram_listener.lock"
)

HEARTBEAT_PATH = (
    DATA_DIR
    / "telegram_listener_heartbeat.json"
)

HEARTBEAT_SECONDS = 5


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def acquire_listener_lock() -> IO[str]:
    """
    Prevent two Telegram polling listeners from running
    simultaneously for the same local project.
    """
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    lock_handle = LOCK_PATH.open(
        "w",
        encoding="utf-8",
    )

    try:
        fcntl.flock(
            lock_handle.fileno(),
            fcntl.LOCK_EX
            | fcntl.LOCK_NB,
        )
    except BlockingIOError as error:
        lock_handle.close()

        raise SystemExit(
            "Another Telegram listener is already running. "
            "Only one listener is allowed."
        ) from error

    lock_handle.write(str(os.getpid()))
    lock_handle.flush()

    return lock_handle


def write_heartbeat(
    state: str,
    *,
    last_error: str | None = None,
) -> None:
    """
    Atomically write listener health for the dashboard
    and diagnostic tools.
    """
    payload = {
        "state": state,
        "pid": os.getpid(),
        "updated_at": utc_now(),
        "chat_configured": bool(
            ALLOWED_CHAT_ID
        ),
        "last_error": last_error,
    }

    temporary_path = HEARTBEAT_PATH.with_suffix(
        ".tmp"
    )

    temporary_path.write_text(
        json.dumps(
            payload,
            indent=2,
        ),
        encoding="utf-8",
    )

    temporary_path.replace(
        HEARTBEAT_PATH
    )


def heartbeat_worker(
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            write_heartbeat("online")
        except Exception as error:
            print(
                "Heartbeat write error:",
                repr(error),
                flush=True,
            )

        stop_event.wait(
            HEARTBEAT_SECONDS
        )


def operational_outbox_worker(stop_event: threading.Event) -> None:
    """Drain persisted adapter cards without coupling sends to polling updates."""
    while not stop_event.is_set():
        try:
            from app.telegram_run_visibility import (
                deliver_pending_operational_cards,
                reconcile_missed_due_incidents,
                reconcile_stale_delivery_claims,
                reconcile_terminal_run_outbox,
            )

            reconcile_stale_delivery_claims()
            reconcile_terminal_run_outbox(limit=100)
            reconcile_missed_due_incidents(max_new=1)
            deliver_pending_operational_cards(limit=10)
        except Exception as error:
            print(
                "Operational Telegram outbox deferred: "
                f"{type(error).__name__}",
                flush=True,
            )
        stop_event.wait(20)


async def safe_answer(
    query: Any,
    text: str,
    *,
    show_alert: bool = False,
) -> None:
    # AADIL_TELEGRAM_TRANSPORT_CALLBACK_RESILIENCE_V3
    if query is None:
        return

    try:
        await asyncio.wait_for(
            query.answer(
                str(text or "")[:180],
                show_alert=show_alert,
            ),
            timeout=2.5,
        )
    except BadRequest as error:
        error_text = str(error).lower()
        expired_messages = {
            "query is too old",
            "query id is invalid",
            "response timeout expired",
            "query_id_invalid",
        }
        if not any(
            message in error_text
            for message in expired_messages
        ):
            print(
                "Telegram callback acknowledgement rejected: "
                f"{error!r}",
                flush=True,
            )
    except Exception as error:
        # Telegram presentation failure must not cancel local work.
        print(
            "Telegram callback acknowledgement skipped: "
            f"{error!r}",
            flush=True,
        )


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if update.effective_chat is None:
        return

    if (
        update.effective_chat.id
        != ALLOWED_CHAT_ID
    ):
        return

    if update.effective_message is not None:
        await update.effective_message.reply_text(
            "✅ Aadil HR Hunter is online.\n\n"
            "Telegram job actions are active."
        )


async def text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    # AADIL_TELEGRAM_MANUAL_CAPTURE_GATE_V1
    if update.effective_chat is not None and update.effective_message is not None:
        _manual_chat_id = int(update.effective_chat.id)
        if _manual_chat_id == ALLOWED_CHAT_ID:
            from app.manual_input import append_capture

            _manual_capture = await asyncio.to_thread(
                append_capture,
                _manual_chat_id,
                str(update.effective_message.text or ""),
            )

            if _manual_capture.get("capturing"):
                await update.effective_message.reply_text(
                    str(_manual_capture.get("message") or "Captured.")
                )
                return
    del context

    if update.effective_chat is None:
        return

    if (
        update.effective_chat.id
        != ALLOWED_CHAT_ID
    ):
        return

    if update.effective_message is not None:
        await update.effective_message.reply_text(
            "✅ Bot listener is online.\n"
            "Use the buttons under a job card "
            "to update its status."
        )


# AADIL_TELEGRAM_COMMANDS_V4
async def start_with_controls_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await start_command(update, context)

    if update.effective_chat is None:
        return

    chat_id = int(update.effective_chat.id)

    if chat_id != ALLOWED_CHAT_ID:
        return

    from app.telegram_control_center import send_control_panel

    await asyncio.to_thread(
        send_control_panel,
        chat_id,
    )


async def menu_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if (
        update.effective_chat is None
        or update.effective_message is None
    ):
        return

    chat_id = int(update.effective_chat.id)

    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    from app.telegram_control_center import send_control_panel

    await asyncio.to_thread(
        send_control_panel,
        chat_id,
    )


async def run_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if (
        update.effective_chat is None
        or update.effective_message is None
    ):
        return

    chat_id = int(update.effective_chat.id)

    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    from app.telegram_control_center import start_enabled_sources

    result = await asyncio.to_thread(
        start_enabled_sources,
        chat_id,
    )

    await update.effective_message.reply_text(
        str(
            result.get("message")
            or "Run request processed."
        )
    )


async def stored_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if (
        update.effective_chat is None
        or update.effective_message is None
    ):
        return

    chat_id = int(update.effective_chat.id)

    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    from app.telegram_control_center import send_latest_stored_jobs

    result = await asyncio.to_thread(
        send_latest_stored_jobs
    )

    await update.effective_message.reply_text(
        str(
            result.get("message")
            or "Stored-job request processed."
        )
    )


async def recent_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if (
        update.effective_chat is None
        or update.effective_message is None
    ):
        return

    chat_id = int(update.effective_chat.id)

    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    limit = 5

    if context.args:
        try:
            limit = max(1, min(int(context.args[0]), 10))
        except ValueError:
            await update.effective_message.reply_text(
                "Usage: /recent [1-10]"
            )
            return

    from app.telegram_control_center import send_recent_job_cards

    result = await asyncio.to_thread(
        send_recent_job_cards,
        chat_id,
        limit,
    )

    await update.effective_message.reply_text(
        str(
            result.get("message")
            or "Recent-job request processed."
        )
    )


async def job_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if (
        update.effective_chat is None
        or update.effective_message is None
    ):
        return

    chat_id = int(update.effective_chat.id)

    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /job <job_id>"
        )
        return

    try:
        job_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "Job ID must be a number."
        )
        return

    from app.telegram_control_center import send_job_by_id

    result = await asyncio.to_thread(
        send_job_by_id,
        chat_id,
        job_id,
    )

    await update.effective_message.reply_text(
        str(
            result.get("message")
            or "Job request processed."
        )
    )


async def find_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if (
        update.effective_chat is None
        or update.effective_message is None
    ):
        return

    chat_id = int(update.effective_chat.id)

    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    query = " ".join(context.args or []).strip()

    if not query:
        await update.effective_message.reply_text(
            "Usage: /find <company or job title>"
        )
        return

    from app.telegram_control_center import find_stored_job_cards

    result = await asyncio.to_thread(
        find_stored_job_cards,
        chat_id,
        query,
        5,
    )

    await update.effective_message.reply_text(
        str(
            result.get("message")
            or "Search request processed."
        )
    )


async def sources_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if (
        update.effective_chat is None
        or update.effective_message is None
    ):
        return

    chat_id = int(update.effective_chat.id)

    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    from app.telegram_control_center import source_status_text

    source_text = await asyncio.to_thread(
        source_status_text
    )

    await update.effective_message.reply_text(
        source_text,
        parse_mode="HTML",
    )


# AADIL_JOB_BOARD_HEALTH_COMMANDS_V2_7_2
async def health_job_boards_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if (
        update.effective_chat is None
        or update.effective_message is None
    ):
        return

    chat_id = int(update.effective_chat.id)

    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    from app.job_board_health_telegram import (
        send_job_board_health,
    )

    requested = " ".join(
        context.args or []
    ).strip()

    result = await asyncio.to_thread(
        send_job_board_health,
        chat_id,
        requested,
    )

    if not result.get("success"):
        await update.effective_message.reply_text(
            str(
                result.get("message")
                or "Job-board health report failed."
            )
        )
async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if (
        update.effective_chat is None
        or update.effective_message is None
    ):
        return

    chat_id = int(update.effective_chat.id)

    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    from app.telegram_control_center import system_status_text

    status_text = await asyncio.to_thread(
        system_status_text
    )

    await update.effective_message.reply_text(
        status_text,
        parse_mode="HTML",
    )


async def queue_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if (
        update.effective_chat is None
        or update.effective_message is None
    ):
        return

    chat_id = int(update.effective_chat.id)

    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    from app.telegram_control_center import queue_status_text

    queue_text = await asyncio.to_thread(
        queue_status_text
    )

    await update.effective_message.reply_text(
        queue_text,
        parse_mode="HTML",
    )

# AADIL_TELEGRAM_MANUAL_INPUT_COMMANDS_V1
async def manual_input_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = int(update.effective_chat.id)
    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text("Unauthorized Telegram chat.")
        return

    from app.manual_input import start_capture

    result = await asyncio.to_thread(start_capture, chat_id)
    await update.effective_message.reply_text(str(result["message"]))


async def manual_inline_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = int(update.effective_chat.id)
    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text("Unauthorized Telegram chat.")
        return

    from app.manual_input import create_inline_run

    text = str(update.effective_message.text or "")
    result = await asyncio.to_thread(create_inline_run, chat_id, text)
    await update.effective_message.reply_text(str(result["message"]))


async def manual_done_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = int(update.effective_chat.id)
    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text("Unauthorized Telegram chat.")
        return

    from app.manual_input import finish_capture

    result = await asyncio.to_thread(finish_capture, chat_id)
    await update.effective_message.reply_text(str(result["message"]))


async def manual_cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = int(update.effective_chat.id)
    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text("Unauthorized Telegram chat.")
        return

    from app.manual_input import cancel_capture

    result = await asyncio.to_thread(cancel_capture, chat_id)
    await update.effective_message.reply_text(str(result["message"]))


async def manual_status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = int(update.effective_chat.id)
    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text("Unauthorized Telegram chat.")
        return

    from app.manual_input import manual_status_text

    text = await asyncio.to_thread(manual_status_text, chat_id)
    await update.effective_message.reply_text(text)

async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    # AADIL_FORCE_REMOVE_CALLBACK_INTERCEPT_V1_1
    if await maybe_handle_force_remove_callback(update, context):
        return

    del context

    query = update.callback_query

    if query is None:
        return

    message = query.message

    if message is None:
        await safe_answer(
            query,
            "The original job card is unavailable.",
            show_alert=True,
        )
        return

    chat_id = int(message.chat.id)
    message_id = int(
        message.message_id
    )

    if chat_id != ALLOWED_CHAT_ID:
        await safe_answer(
            query,
            "Unauthorized Telegram chat.",
            show_alert=True,
        )
        return

    callback_data = str(
        query.data or ""
    )
    # AADIL_JOB_BOARD_HEALTH_CALLBACK_V2_7_2
    if callback_data.startswith("cc:jbh:"):
        from app.job_board_health_telegram import (
            handle_job_board_health_callback,
        )

        (
            success,
            health_message,
            show_alert,
        ) = await asyncio.to_thread(
            handle_job_board_health_callback,
            callback_data,
            chat_id,
            message_id,
        )

        await safe_answer(
            query,
            health_message,
            show_alert=show_alert,
        )
        return


    # AADIL_LIVE_TELEGRAM_CALLBACKS_V2
    # Explicit strings are preserved for runtime verification:
    # app:desc: app:audit: app:runs: app:run: app:applied: app:adapters:
    # cc:stored: cc:recent: cc:run: cc:status cc:queue cc:sources cc:timers
    if callback_data.startswith(
        (
            "app:desc:",
            "app:audit:",
            "app:runs:",
            "app:run:",
            "app:applied:",
            "app:adapters:",
            "cc:stored:",
            "cc:recent:",
            "cc:run:",
            "cc:status",
            "cc:queue",
            "cc:sources",
            "cc:timers",
            "cc:applied",
            "cc:runs",
        )
    ):
        from app.live_telegram_controls_v2 import handle_live_callback

        success, live_message, show_alert = await asyncio.to_thread(
            handle_live_callback,
            callback_data,
            chat_id,
        )
        await safe_answer(
            query,
            live_message,
            show_alert=show_alert,
        )
        return

    # AADIL_CONTROL_CENTER_CALLBACKS_V1
    if callback_data.startswith("cc:"):
        from app.telegram_control_center import handle_control_callback

        success, control_message, show_alert = await asyncio.to_thread(
            handle_control_callback,
            callback_data,
            chat_id,
        )

        await safe_answer(
            query,
            control_message,
            show_alert=show_alert,
        )
        return

    # AADIL_UNIFIED_ATS_APPLICATION_CALLBACKS_V1
    if callback_data.startswith("app:"):
        from app.application_runs_v1 import handle_application_callback

        success, application_message, show_alert = await asyncio.to_thread(
            handle_application_callback,
            callback_data,
            chat_id,
        )
        await safe_answer(
            query,
            application_message,
            show_alert=show_alert,
        )
        return

    parts = callback_data.split(
        ":",
        2,
    )

    print(
        f"Callback received: {callback_data}",
        flush=True,
    )

    if (
        len(parts) != 3
        or parts[0] != "job"
    ):
        await safe_answer(
            query,
            "Invalid job action.",
            show_alert=True,
        )
        return

    try:
        job_id = int(parts[1])
    except ValueError:
        await safe_answer(
            query,
            "Invalid job ID.",
            show_alert=True,
        )
        return

    action = parts[2]

    # AADIL_APPROVED_NOOP_RUN_BRIDGE_V1
    if action == "noop":
        try:
            from app.actions import get_job

            _aadil_job = get_job(job_id)
            _aadil_job_data = (
                dict(_aadil_job)
                if _aadil_job
                else {}
            )
        except Exception:
            _aadil_job_data = {}

        _aadil_status = str(
            _aadil_job_data.get("status")
            or ""
        ).strip().lower()
        _aadil_sent = str(
            _aadil_job_data.get("sent_to_n8n")
            or "0"
        ).strip().lower() not in {
            "",
            "0",
            "false",
            "none",
        }

        if (
            _aadil_status == "approved_for_n8n"
            and not _aadil_sent
        ):
            action = "approve_for_n8n"
        else:
            await safe_answer(
                query,
                "This is already the current job state.",
            )
            return

    if action not in VALID_ACTIONS:
        await safe_answer(
            query,
            "Unsupported job action.",
            show_alert=True,
        )
        return

    # Keep SQLite and worker startup off the polling loop.
    success, action_message = await asyncio.to_thread(
        apply_job_action,
        job_id=job_id,
        action=action,
        actor="telegram",
    )

    await safe_answer(
        query,
        action_message,
        show_alert=not success,
    )

    if not success:
        return

    changed = False
    card_error = None

    try:
        changed = await asyncio.to_thread(
            edit_job_card,
            chat_id,
            message_id,
            job_id,
            action_message,
        )
    except Exception as error:
        # The action and n8n worker may already have succeeded.
        # A card refresh outage is presentation failure only.
        card_error = repr(error)
        print(
            "Telegram card refresh deferred: "
            f"job={job_id} "
            f"action={action} "
            f"error={card_error}",
            flush=True,
        )

    print(
        "Telegram action completed: "
        f"job={job_id} "
        f"action={action} "
        f"card_updated={changed} "
        f"card_error={card_error}",
        flush=True,
    )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del update

    error_text = repr(
        context.error
    )

    print(
        "Telegram listener error:",
        error_text,
        flush=True,
    )

    try:
        write_heartbeat(
            "degraded",
            last_error=error_text,
        )
    except Exception:
        pass



# AADIL_FORCE_RERUN_RUNTIME_COMMANDS_V1_1
async def rerun_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = int(update.effective_chat.id)
    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    args = list(getattr(context, "args", None) or [])
    if not args:
        await update.effective_message.reply_text(
            "Use /rerun <job_id>. Example: /rerun 310\n\n"
            "This creates a new Part 2/3 child run and preserves the original job and outputs."
        )
        return

    try:
        job_id = int(str(args[0]).strip())
    except (TypeError, ValueError):
        await update.effective_message.reply_text(
            "Invalid job ID. Use /rerun <job_id>."
        )
        return

    from app.force_rerun_v1 import request_force_rerun

    result = await asyncio.to_thread(
        request_force_rerun,
        job_id,
        chat_id,
    )
    await update.effective_message.reply_text(
        str(result.get("message") or "Force rerun request processed.")
    )


async def runtime_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = int(update.effective_chat.id)
    if chat_id != ALLOWED_CHAT_ID:
        await update.effective_message.reply_text(
            "Unauthorized Telegram chat."
        )
        return

    from app.source_runtime_state_v1 import telegram_runtime_section

    text = await asyncio.to_thread(telegram_runtime_section)
    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
    )

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing from .env"
        )

    if not ALLOWED_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing from .env"
        )

    lock_handle = acquire_listener_lock()

    stop_event = threading.Event()

    heartbeat_thread = threading.Thread(
        target=heartbeat_worker,
        args=(stop_event,),
        name="telegram-heartbeat",
        daemon=True,
    )

    heartbeat_thread.start()

    outbox_thread = threading.Thread(
        target=operational_outbox_worker,
        args=(stop_event,),
        name="telegram-operational-outbox",
        daemon=True,
    )
    outbox_thread.start()

    # AADIL_TELEGRAM_TRANSPORT_CALLBACK_RESILIENCE_V3
    application = (
        configure_application_builder(
            ApplicationBuilder().token(BOT_TOKEN)
        )
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_with_controls_command,
        )
    )

    application.add_handler(
        CommandHandler("forceremove", force_remove_command)
    )
    application.add_handler(
        CommandHandler(
            "manual_input",
            manual_input_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "manual",
            manual_inline_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "manual_done",
            manual_done_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "manual_cancel",
            manual_cancel_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "manual_status",
            manual_status_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "menu",
            menu_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "run",
            run_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "stored",
            stored_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "recent",
            recent_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "job",
            job_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "find",
            find_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "health_job_boards",
            health_job_boards_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "health_boards",
            health_job_boards_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "board_health",
            health_job_boards_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "health_job_bords",
            health_job_boards_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "sources",
            sources_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "status",
            status_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "queue",
            queue_command,
        )
    )

    # AADIL_FORCE_RERUN_RUNTIME_HANDLER_REGISTRATION_V1_1
    application.add_handler(
        CommandHandler(
            "rerun",
            rerun_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "runtime",
            runtime_command,
        )
    )

    # AADIL_FORCE_REMOVE_DIRECT_CALLBACK_V1_2
    application.add_handler(
        CallbackQueryHandler(
            force_remove_callback,
            pattern=r"^force_remove:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            handle_callback,
            pattern=r"^(?:job:|app:|cc:)",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            text_message,
        )
    )

    # AADIL_TELEGRAM_SCORECARDS_V1 — BEGIN
    from app.telegram_scorecards_v1 import (
        last24h_command,
        scorecards_command,
    )
    application.add_handler(
        CommandHandler(
            "scorecards",
            scorecards_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "last24h",
            last24h_command,
        )
    )
    # AADIL_TELEGRAM_SCORECARDS_V1 — END

    # AADIL_TELEGRAM_SIDE_MENU_SCORECARDS_V1_2 — BEGIN
    from app.telegram_side_menu_scorecards_v1_2 import (
        jobs_50_59_command,
        jobs_60_69_command,
        jobs_70_79_command,
        jobs_80_89_command,
        jobs_90_94_command,
        jobs_95plus_command,
        jobs_under50_command,
    )

    application.add_handler(
        CommandHandler(
            "jobs_95plus",
            jobs_95plus_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "jobs_90_94",
            jobs_90_94_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "jobs_80_89",
            jobs_80_89_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "jobs_70_79",
            jobs_70_79_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "jobs_60_69",
            jobs_60_69_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "jobs_50_59",
            jobs_50_59_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "jobs_under50",
            jobs_under50_command,
        )
    )
    # AADIL_TELEGRAM_SIDE_MENU_SCORECARDS_V1_2 — END

    # AADIL_INSTALL_UNIFIED_TELEGRAM_BUTTON_ROUTER_V1
    install_unified_button_router(application)

    application.add_error_handler(
        error_handler
    )

    print(
        "Telegram listener started securely.",
        flush=True,
    )

    print(
        f"Listener PID: {os.getpid()}",
        flush=True,
    )

    print(
        "Press Control + C to stop.",
        flush=True,
    )

    try:
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=[
                "message",
                "callback_query",
            ],
        )

    finally:
        stop_event.set()

        heartbeat_thread.join(
            timeout=2,
        )
        outbox_thread.join(timeout=2)

        try:
            write_heartbeat("offline")
        except Exception:
            pass

        try:
            fcntl.flock(
                lock_handle.fileno(),
                fcntl.LOCK_UN,
            )
        finally:
            lock_handle.close()


if __name__ == "__main__":
    main()
