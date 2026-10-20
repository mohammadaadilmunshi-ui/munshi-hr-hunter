from __future__ import annotations

import json
import sys
from functools import wraps
from typing import Any, Callable


MARKER = "AADIL_FREE_ADAPTER_SITE_PRESERVING_V1_1"


def _empty_result() -> Any:
    try:
        import pandas as pd
        return pd.DataFrame()
    except Exception:
        return []


def _warn(error: BaseException, kwargs: dict[str, Any]) -> None:
    payload = {
        "marker": MARKER,
        "event": "jobspy_site_failure_isolated",
        "site_name": kwargs.get("site_name"),
        "error_type": type(error).__name__,
        "error": str(error),
        "action": "fail_this_source_only",
    }
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)


def make_site_preserving_wrapper(
    original: Callable[..., Any],
) -> Callable[..., Any]:
    """
    Preserve the exact site_name selected by each dedicated worker.

    Google stays Google, Indeed stays Indeed, LinkedIn stays LinkedIn, and the
    combined JobSpy worker remains ZipRecruiter-only. A broken scraper must
    propagate a source failure, never masquerade as a genuine empty result.
    """
    @wraps(original)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        call_kwargs = dict(kwargs)

        try:
            result = original(*args, **call_kwargs)
        except TypeError as error:
            text = str(error)
            if "NoneType" in text and "subscriptable" in text:
                _warn(error, call_kwargs)
                raise RuntimeError("Free scraper failed: invalid NoneType response") from error
            raise

        if result is None:
            error = RuntimeError("JobSpy returned None")
            _warn(error, call_kwargs)
            raise error

        return result

    setattr(wrapped, "_aadil_site_preserving_wrapper_v1_1", True)
    setattr(wrapped, "_aadil_original_callable", original)
    return wrapped


def self_test() -> dict[str, Any]:
    calls: list[dict[str, Any]] = []

    def fake(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        calls.append({"args": list(args), "kwargs": kwargs})
        return [{"ok": True}]

    wrapped = make_site_preserving_wrapper(fake)

    for site in ("google", "indeed", "linkedin", "zip_recruiter"):
        wrapped(site_name=[site], search_term="HR")

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise TypeError("'NoneType' object is not subscriptable")

    isolated = False
    try:
        make_site_preserving_wrapper(broken)(site_name=["indeed"], search_term="HR")
    except RuntimeError:
        isolated = True

    return {
        "success": True,
        "marker": MARKER,
        "sites_seen": [
            row["kwargs"]["site_name"][0]
            for row in calls
        ],
        "site_order_preserved": [
            row["kwargs"]["site_name"][0]
            for row in calls
        ] == [
            "google",
            "indeed",
            "linkedin",
            "zip_recruiter",
        ],
        "known_failure_isolated": isolated,
        "provider_calls": 0,
        "telegram_calls": 0,
        "n8n_calls": 0,
        "database_writes": 0,
    }


if __name__ == "__main__":
    print(json.dumps(self_test(), indent=2))
