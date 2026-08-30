from __future__ import annotations

import inspect
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from telegram.request import HTTPXRequest


MARKER = "AADIL_TELEGRAM_TRANSPORT_CALLBACK_RESILIENCE_V3"
ROOT_DIR = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT_DIR / "logs" / "telegram_transport_v3.log"

_SYNC_CLIENT: httpx.Client | None = None
_SYNC_CLIENT_LOCK = threading.Lock()

_CONNECT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
)

# These Bot API methods are safe to retry after a read/protocol failure.
# sendMessage is deliberately excluded to prevent duplicate messages.
_IDEMPOTENT_METHODS = {
    "answerCallbackQuery",
    "editMessageText",
    "editMessageReplyMarkup",
    "deleteMessage",
    "getMe",
    "getUpdates",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(payload: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        safe_payload = dict(payload)
        safe_payload.pop("bot_token", None)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    safe_payload,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )
    except Exception:
        pass


def _sync_transport() -> httpx.HTTPTransport:
    parameters = inspect.signature(httpx.HTTPTransport).parameters
    kwargs: dict[str, Any] = {}

    if "local_address" in parameters:
        # Force the stable IPv4 path rather than the failing IPv6/TLS path.
        kwargs["local_address"] = "0.0.0.0"

    if "retries" in parameters:
        # HTTPX retries connection setup failures only.
        kwargs["retries"] = 3

    return httpx.HTTPTransport(**kwargs)


def _new_sync_client() -> httpx.Client:
    return httpx.Client(
        transport=_sync_transport(),
        timeout=httpx.Timeout(
            connect=8.0,
            read=20.0,
            write=20.0,
            pool=5.0,
        ),
        trust_env=False,
        follow_redirects=True,
        http1=True,
        http2=False,
    )


def _get_sync_client() -> httpx.Client:
    global _SYNC_CLIENT

    with _SYNC_CLIENT_LOCK:
        if _SYNC_CLIENT is None:
            _SYNC_CLIENT = _new_sync_client()
        return _SYNC_CLIENT


def _reset_sync_client() -> None:
    global _SYNC_CLIENT

    with _SYNC_CLIENT_LOCK:
        previous = _SYNC_CLIENT
        _SYNC_CLIENT = None

    if previous is not None:
        try:
            previous.close()
        except Exception:
            pass


def telegram_api_request(
    method: str,
    payload: dict[str, Any] | None,
    *,
    bot_token: str,
) -> dict[str, Any]:
    """
    Execute a Telegram Bot API request using an IPv4-bound HTTPX client.

    Connection setup errors are retried. sendMessage is never repeated
    after a possible read/write ambiguity, which prevents duplicate cards.
    """
    normalized_method = str(method or "").strip()

    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")

    if not normalized_method:
        raise ValueError("Telegram method is required.")

    url = (
        "https://api.telegram.org/bot"
        + bot_token
        + "/"
        + normalized_method
    )

    started = time.monotonic()
    last_error: Exception | None = None

    for attempt in (1, 2):
        try:
            response = _get_sync_client().request(
                "POST" if payload is not None else "GET",
                url,
                data=payload,
            )

            body_text = response.text

            if not 200 <= response.status_code < 300:
                raise RuntimeError(
                    "Telegram HTTP "
                    f"{response.status_code}: "
                    f"{body_text[:2000]}"
                )

            try:
                result = response.json()
            except ValueError as error:
                raise RuntimeError(
                    "Telegram returned invalid JSON."
                ) from error

            if not result.get("ok"):
                raise RuntimeError(
                    "Telegram API error: "
                    + json.dumps(
                        result,
                        ensure_ascii=False,
                    )
                )

            _append_log(
                {
                    "timestamp": _utc_now(),
                    "event": "telegram_request_ok",
                    "method": normalized_method,
                    "attempt": attempt,
                    "elapsed_seconds": round(
                        time.monotonic() - started,
                        3,
                    ),
                }
            )

            return result

        except _CONNECT_ERRORS as error:
            last_error = error
            _append_log(
                {
                    "timestamp": _utc_now(),
                    "event": "telegram_connect_retry",
                    "method": normalized_method,
                    "attempt": attempt,
                    "error": repr(error),
                }
            )
            _reset_sync_client()

            if attempt == 1:
                time.sleep(0.35)
                continue
            break

        except (
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.RemoteProtocolError,
        ) as error:
            last_error = error

            if (
                normalized_method in _IDEMPOTENT_METHODS
                and attempt == 1
            ):
                _reset_sync_client()
                time.sleep(0.35)
                continue
            break

        except httpx.HTTPError as error:
            last_error = error
            break

    _append_log(
        {
            "timestamp": _utc_now(),
            "event": "telegram_request_failed",
            "method": normalized_method,
            "elapsed_seconds": round(
                time.monotonic() - started,
                3,
            ),
            "error": repr(last_error),
        }
    )

    raise RuntimeError(
        "Telegram connection failed: "
        + repr(last_error)
    ) from last_error


def _async_transport() -> httpx.AsyncHTTPTransport:
    parameters = inspect.signature(httpx.AsyncHTTPTransport).parameters
    kwargs: dict[str, Any] = {}

    if "local_address" in parameters:
        kwargs["local_address"] = "0.0.0.0"

    if "retries" in parameters:
        kwargs["retries"] = 3

    return httpx.AsyncHTTPTransport(**kwargs)


def _request_kwargs(*, get_updates: bool) -> dict[str, Any]:
    parameters = inspect.signature(HTTPXRequest).parameters

    desired: dict[str, Any] = {
        "connection_pool_size": 2 if get_updates else 12,
        "read_timeout": 45.0 if get_updates else 25.0,
        "write_timeout": 20.0,
        "connect_timeout": 10.0,
        "pool_timeout": 10.0,
        "http_version": "1.1",
    }

    result = {
        key: value
        for key, value in desired.items()
        if key in parameters
    }

    if "httpx_kwargs" in parameters:
        result["httpx_kwargs"] = {
            "transport": _async_transport(),
            "trust_env": False,
            "follow_redirects": True,
        }

    return result


def build_ptb_requests() -> tuple[HTTPXRequest, HTTPXRequest]:
    """
    Build separate clients for ordinary Bot API calls and long polling.
    """
    return (
        HTTPXRequest(**_request_kwargs(get_updates=False)),
        HTTPXRequest(**_request_kwargs(get_updates=True)),
    )


def configure_application_builder(builder: Any) -> Any:
    """
    Configure ApplicationBuilder with resilient Telegram request clients.

    The fallback retains explicit timeouts if a future PTB version rejects
    custom request objects.
    """
    try:
        bot_request, updates_request = build_ptb_requests()
        return (
            builder
            .request(bot_request)
            .get_updates_request(updates_request)
        )
    except Exception as error:
        _append_log(
            {
                "timestamp": _utc_now(),
                "event": "ptb_custom_request_fallback",
                "error": repr(error),
            }
        )

    method_values = {
        "connect_timeout": 10.0,
        "read_timeout": 25.0,
        "write_timeout": 20.0,
        "pool_timeout": 10.0,
        "get_updates_connect_timeout": 10.0,
        "get_updates_read_timeout": 45.0,
        "get_updates_write_timeout": 20.0,
        "get_updates_pool_timeout": 10.0,
    }

    configured = builder

    for method_name, value in method_values.items():
        method = getattr(configured, method_name, None)
        if callable(method):
            configured = method(value)

    return configured


def transport_health() -> dict[str, Any]:
    """
    Perform one safe getMe request without exposing the token or bot details.
    """
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env", override=True)

    token = str(
        os.getenv("TELEGRAM_BOT_TOKEN", "") or ""
    ).strip()

    started = time.monotonic()
    result = telegram_api_request(
        "getMe",
        None,
        bot_token=token,
    )
    bot = result.get("result") or {}

    return {
        "success": True,
        "marker": MARKER,
        "elapsed_seconds": round(
            time.monotonic() - started,
            3,
        ),
        "bot_configured": bool(bot.get("id")),
        "username_present": bool(bot.get("username")),
    }


if __name__ == "__main__":
    print(
        json.dumps(
            transport_health(),
            indent=2,
            ensure_ascii=False,
        )
    )
