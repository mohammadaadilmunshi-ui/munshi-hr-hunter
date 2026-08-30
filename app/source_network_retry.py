from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from typing import Any

VERSION = "source-network-retry-v1.0.0"
MIN_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 2


def _is_timeout(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    if isinstance(error, urllib.error.URLError):
        reason = getattr(error, "reason", None)
        return isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).casefold()
    return "timed out" in str(error).casefold() or "read operation timed out" in str(error).casefold()


def _with_min_timeout(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[tuple[Any, ...], dict[str, Any]]:
    mutable_args = list(args)
    output_kwargs = dict(kwargs)
    if "timeout" in output_kwargs:
        try:
            output_kwargs["timeout"] = max(float(output_kwargs["timeout"]), MIN_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            output_kwargs["timeout"] = MIN_TIMEOUT_SECONDS
    elif len(mutable_args) >= 3:
        try:
            mutable_args[2] = max(float(mutable_args[2]), MIN_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            mutable_args[2] = MIN_TIMEOUT_SECONDS
    else:
        output_kwargs["timeout"] = MIN_TIMEOUT_SECONDS
    return tuple(mutable_args), output_kwargs


def google_urlopen_with_retry(*args: Any, **kwargs: Any) -> Any:
    adjusted_args, adjusted_kwargs = _with_min_timeout(args, kwargs)
    last_error: BaseException | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return urllib.request.urlopen(*adjusted_args, **adjusted_kwargs)
        except Exception as error:
            if not _is_timeout(error) or attempt + 1 >= MAX_ATTEMPTS:
                raise
            last_error = error
            time.sleep(1.25)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Google provider retry wrapper reached an invalid state.")


def google_requests_get_with_retry(*args: Any, **kwargs: Any) -> Any:
    try:
        import requests
    except ImportError as error:
        raise RuntimeError("requests is unavailable for the Google provider.") from error

    output_kwargs = dict(kwargs)
    timeout = output_kwargs.get("timeout", MIN_TIMEOUT_SECONDS)
    try:
        output_kwargs["timeout"] = max(float(timeout), MIN_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        output_kwargs["timeout"] = MIN_TIMEOUT_SECONDS

    last_error: BaseException | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return requests.get(*args, **output_kwargs)
        except Exception as error:
            if not _is_timeout(error) or attempt + 1 >= MAX_ATTEMPTS:
                raise
            last_error = error
            time.sleep(1.25)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Google requests retry wrapper reached an invalid state.")


def self_test() -> dict[str, Any]:
    class FakeTimeout(TimeoutError):
        pass

    attempts = {"count": 0}
    original = urllib.request.urlopen

    def fake_open(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise FakeTimeout("read operation timed out")
        return "ok"

    urllib.request.urlopen = fake_open
    try:
        result = google_urlopen_with_retry("https://example.invalid")
    finally:
        urllib.request.urlopen = original

    return {
        "success": result == "ok" and attempts["count"] == 2,
        "attempts": attempts["count"],
        "network_request_made": False,
    }
