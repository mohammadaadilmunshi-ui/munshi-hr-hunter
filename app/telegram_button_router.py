from __future__ import annotations

import asyncio
import inspect
import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler

PROJECT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT / "logs"
ROUTER_LOG = LOG_DIR / "telegram_button_router.log"
STARTUP_REPORT = LOG_DIR / "telegram_button_router_startup.json"
import time

_STATE_KEY = "_aadil_unified_callback_router_v1"
_MARKER = "AADIL_UNIFIED_TELEGRAM_BUTTON_ROUTER_V1"

# AADIL_SCORECARD_CALLBACK_REPLAY_GUARD_V2
_SCORECARD_REPLAY_ID_TTL_SECONDS = 600.0
_SCORECARD_ACTION_DEBOUNCE_SECONDS = 12.0
_SCORECARD_REPLAY_CACHE_MAX = 4096
_scorecard_seen_callback_ids = {}
_scorecard_recent_actions = {}


def _scorecard_replay_claim(update, callback_data):
    data = str(callback_data or "")
    if not data.startswith("cc:sc:"):
        return {
            "scorecard": False,
            "suppress": False,
            "reason": "",
            "identity": None,
        }

    now = time.monotonic()
    query = getattr(update, "callback_query", None)
    callback_query_id = str(getattr(query, "id", "") or "")
    update_id = getattr(update, "update_id", None)

    message = getattr(query, "message", None)
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    message_id = getattr(message, "message_id", None)

    identity = callback_query_id
    if not identity and update_id is not None:
        identity = f"update:{update_id}"

    if len(_scorecard_seen_callback_ids) >= _SCORECARD_REPLAY_CACHE_MAX:
        for key, seen_at in list(_scorecard_seen_callback_ids.items()):
            if now - seen_at >= _SCORECARD_REPLAY_ID_TTL_SECONDS:
                _scorecard_seen_callback_ids.pop(key, None)

    if len(_scorecard_recent_actions) >= _SCORECARD_REPLAY_CACHE_MAX:
        for key, seen_at in list(_scorecard_recent_actions.items()):
            if now - seen_at >= _SCORECARD_ACTION_DEBOUNCE_SECONDS:
                _scorecard_recent_actions.pop(key, None)

    if identity:
        previous = _scorecard_seen_callback_ids.get(identity)
        if (
            previous is not None
            and now - previous < _SCORECARD_REPLAY_ID_TTL_SECONDS
        ):
            return {
                "scorecard": True,
                "suppress": True,
                "reason": "duplicate_callback_identity",
                "identity": identity,
            }
        _scorecard_seen_callback_ids[identity] = now

    action_key = (chat_id, message_id, data)
    previous = _scorecard_recent_actions.get(action_key)
    if (
        previous is not None
        and now - previous < _SCORECARD_ACTION_DEBOUNCE_SECONDS
    ):
        return {
            "scorecard": True,
            "suppress": True,
            "reason": "same_action_debounce",
            "identity": identity or None,
        }

    _scorecard_recent_actions[action_key] = now
    return {
        "scorecard": True,
        "suppress": False,
        "reason": "",
        "identity": identity or None,
    }

logger = logging.getLogger("aadil.telegram_button_router")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(payload: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with ROUTER_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _pattern_text(handler: CallbackQueryHandler) -> str:
    pattern = getattr(handler, "pattern", None)
    if pattern is None:
        return ""
    if hasattr(pattern, "pattern"):
        return str(pattern.pattern)
    return str(pattern)


def _callback_name(handler: CallbackQueryHandler) -> str:
    callback = getattr(handler, "callback", None)
    return (
        getattr(callback, "__qualname__", None)
        or getattr(callback, "__name__", None)
        or repr(callback)
    )


def _summary(entry: dict[str, Any]) -> dict[str, Any]:
    handler = entry["handler"]
    return {
        "group": entry["group"],
        "original_index": entry["index"],
        "callback": _callback_name(handler),
        "pattern": _pattern_text(handler),
        "specific": getattr(handler, "pattern", None) is not None,
        "block": getattr(handler, "block", None),
    }


async def _safe_answer(
    query: Any,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> bool:
    # AADIL_TELEGRAM_TRANSPORT_CALLBACK_RESILIENCE_V3
    if query is None:
        return False

    try:
        await asyncio.wait_for(
            query.answer(
                text=text,
                show_alert=show_alert,
            )
            if text
            else query.answer(),
            timeout=2.5,
        )
        return True
    except Exception:
        return False


async def unified_callback_router(update: Any, context: Any) -> None:
    query = getattr(update, "callback_query", None)
    callback_data = str(getattr(query, "data", "") or "")
    # AADIL_SCORECARD_CALLBACK_REPLAY_GUARD_V2
    scorecard_replay = _scorecard_replay_claim(update, callback_data)
    if scorecard_replay["suppress"]:
        replay_acknowledged = await _safe_answer(
            query,
            "Already processing this scorecard request.",
        )
        _append_log({
            "timestamp": _utc_now(),
            "event": "callback_replay_suppressed",
            "callback_data": callback_data,
            "reason": scorecard_replay["reason"],
            "callback_identity": scorecard_replay["identity"],
            "acknowledged": replay_acknowledged,
        })
        return

    application = getattr(context, "application", None)
    state = application.bot_data.get(_STATE_KEY, {}) if application is not None else {}
    entries = list(state.get("entries") or [])

    selected = None
    check_result = None
    for entry in entries:
        handler = entry["handler"]
        try:
            result = handler.check_update(update)
        except Exception as error:
            _append_log({
                "timestamp": _utc_now(),
                "event": "handler_check_error",
                "callback_data": callback_data,
                "handler": _summary(entry),
                "error": repr(error),
            })
            continue
        if result is False or result is None:
            continue
        selected = entry
        check_result = result
        break

    if selected is None:
        await _safe_answer(
            query,
            "This button is not connected to an active handler.",
            show_alert=True,
        )
        _append_log({
            "timestamp": _utc_now(),
            "event": "unmatched_callback",
            "callback_data": callback_data,
        })
        return

    handler = selected["handler"]
    info = _summary(selected)
    _append_log({
        "timestamp": _utc_now(),
        "event": "callback_received",
        "callback_data": callback_data,
        "selected_handler": info,
    })

    # AADIL_CALLBACK_ACK_BEFORE_WORK_V3
    # End Telegram's spinner before database, rendering, or n8n work.
    acknowledged = await _safe_answer(
        query,
        "Working…",
    )
    _append_log({
        "timestamp": _utc_now(),
        "event": "callback_acknowledged",
        "callback_data": callback_data,
        "selected_handler": info,
        "acknowledged": acknowledged,
    })

    try:
        collector = getattr(handler, "collect_additional_context", None)
        if callable(collector):
            collector(context, update, application, check_result)

        result = handler.callback(update, context)
        if inspect.isawaitable(result):
            await result

        _append_log({
            "timestamp": _utc_now(),
            "event": "callback_completed",
            "callback_data": callback_data,
            "selected_handler": info,
        })
    except BadRequest as error:
        if "message is not modified" in str(error).lower():
            await _safe_answer(query, "Already up to date.")
            _append_log({
                "timestamp": _utc_now(),
                "event": "callback_already_current",
                "callback_data": callback_data,
                "selected_handler": info,
                "error": str(error),
            })
            return
        await _safe_answer(
            query,
            "Telegram rejected this action. The exact error was logged.",
            show_alert=True,
        )
        _append_log({
            "timestamp": _utc_now(),
            "event": "callback_bad_request",
            "callback_data": callback_data,
            "selected_handler": info,
            "error": str(error),
            "traceback": traceback.format_exc(),
        })
    except Exception as error:
        await _safe_answer(
            query,
            "Button action failed. The exact error was logged.",
            show_alert=True,
        )
        _append_log({
            "timestamp": _utc_now(),
            "event": "callback_failed",
            "callback_data": callback_data,
            "selected_handler": info,
            "error": repr(error),
            "traceback": traceback.format_exc(),
        })


def install_unified_button_router(application: Any) -> dict[str, Any]:
    """Preserve all existing callbacks but route explicit patterns before generic handlers."""
    if application.bot_data.get(_STATE_KEY):
        return application.bot_data[_STATE_KEY]["report"]

    entries: list[dict[str, Any]] = []
    handlers_map = getattr(application, "handlers", {})
    for group in sorted(handlers_map):
        for index, handler in enumerate(list(handlers_map.get(group) or [])):
            if isinstance(handler, CallbackQueryHandler):
                entries.append({"group": group, "index": index, "handler": handler})

    if not entries:
        report = {
            "marker": _MARKER,
            "installed": False,
            "reason": "no_callback_handlers_found",
            "created_at": _utc_now(),
        }
        application.bot_data[_STATE_KEY] = {"entries": [], "report": report}
        return report

    ordered = sorted(
        entries,
        key=lambda entry: (
            0 if getattr(entry["handler"], "pattern", None) is not None else 1,
            entry["group"],
            entry["index"],
        ),
    )

    for entry in entries:
        application.remove_handler(entry["handler"], entry["group"])

    router_group = min(entry["group"] for entry in entries)
    application.add_handler(
        CallbackQueryHandler(unified_callback_router),
        group=router_group,
    )

    report = {
        "marker": _MARKER,
        "installed": True,
        "created_at": _utc_now(),
        "router_group": router_group,
        "original_handler_count": len(entries),
        "specific_handler_count": sum(
            1 for entry in entries
            if getattr(entry["handler"], "pattern", None) is not None
        ),
        "generic_handler_count": sum(
            1 for entry in entries
            if getattr(entry["handler"], "pattern", None) is None
        ),
        "evaluation_order": [_summary(entry) for entry in ordered],
    }
    application.bot_data[_STATE_KEY] = {"entries": ordered, "report": report}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STARTUP_REPORT.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    _append_log({"timestamp": _utc_now(), "event": "router_installed", **report})
    return report
