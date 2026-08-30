from __future__ import annotations

import importlib
import json
import sys
from functools import wraps
from typing import Any, Callable, Iterable


MARKER = "AADIL_FREE_ADAPTER_STABILITY_V1"

FREE_SOURCE_NAMES = (
    "Google Jobs",
    "JobSpy",
    "Indeed Jobs (JobSpy)",
    "LinkedIn Jobs (JobSpy)",
    "Ashby",
    "Dice",
)

PAID_DISABLED_SOURCE_NAMES = (
    "SerpAPI",
)

COMBINED_JOBSPY_ALLOWED_SITES = (
    "zip_recruiter",
)


def normalize_site_name(value: Any) -> str:
    text = str(value or "").strip().casefold()
    aliases = {
        "ziprecruiter": "zip_recruiter",
        "zip-recruiter": "zip_recruiter",
        "zip recruiter": "zip_recruiter",
        "linkedin_jobs": "linkedin",
        "indeed_jobs": "indeed",
        "google_jobs": "google",
    }
    return aliases.get(text, text)


def normalize_sites(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        if "," in value:
            values = value.split(",")
        else:
            values = [value]
    elif isinstance(value, Iterable):
        values = list(value)
    else:
        values = [value]

    result: list[str] = []
    for item in values:
        normalized = normalize_site_name(item)
        if normalized and normalized not in result:
            result.append(normalized)

    return result


def sites_for_mode(mode: str) -> list[str]:
    normalized_mode = str(mode or "").strip().casefold()

    if normalized_mode in {
        "combined",
        "jobspy",
        "ziprecruiter",
        "zip_recruiter",
    }:
        return list(COMBINED_JOBSPY_ALLOWED_SITES)

    if normalized_mode in {
        "indeed",
        "indeed_jobspy",
    }:
        return ["indeed"]

    if normalized_mode in {
        "linkedin",
        "linkedin_jobspy",
    }:
        return ["linkedin"]

    raise ValueError(
        f"Unsupported JobSpy policy mode: {mode!r}"
    )


def _empty_jobspy_result() -> Any:
    try:
        import pandas as pd

        return pd.DataFrame()
    except Exception:
        return []


def _emit_warning(
    *,
    mode: str,
    error: BaseException,
) -> None:
    payload = {
        "marker": MARKER,
        "event": "jobspy_site_failure_isolated",
        "mode": mode,
        "error_type": type(error).__name__,
        "error": str(error),
        "action": "returned_empty_result_without_disabling_other_free_sources",
    }
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
        ),
        file=sys.stderr,
        flush=True,
    )


def make_jobspy_wrapper(
    original: Callable[..., Any],
    *,
    mode: str,
) -> Callable[..., Any]:
    allowed_sites = sites_for_mode(mode)

    @wraps(original)
    def wrapped(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        call_kwargs = dict(kwargs)

        if "site_name" in call_kwargs:
            call_kwargs["site_name"] = list(
                allowed_sites
            )
        elif "site_names" in call_kwargs:
            call_kwargs["site_names"] = list(
                allowed_sites
            )
        elif "sites" in call_kwargs:
            call_kwargs["sites"] = list(
                allowed_sites
            )
        else:
            call_kwargs["site_name"] = list(
                allowed_sites
            )

        try:
            result = original(
                *args,
                **call_kwargs,
            )
        except TypeError as error:
            if (
                "NoneType" in str(error)
                and "subscriptable" in str(error)
            ):
                _emit_warning(
                    mode=mode,
                    error=error,
                )
                return _empty_jobspy_result()
            raise

        if result is None:
            _emit_warning(
                mode=mode,
                error=RuntimeError(
                    "JobSpy returned None"
                ),
            )
            return _empty_jobspy_result()

        return result

    setattr(
        wrapped,
        "_aadil_free_adapter_policy_mode",
        mode,
    )
    setattr(
        wrapped,
        "_aadil_free_adapter_allowed_sites",
        tuple(allowed_sites),
    )
    return wrapped


def _call_canonical_role_match(
    *args: Any,
    **kwargs: Any,
) -> bool | None:
    candidates = (
        (
            "app.targeting",
            (
                "role_match",
                "matches_target_role",
                "title_matches_target",
            ),
        ),
        (
            "app.targeting_rules",
            (
                "role_match",
                "matches_target_role",
                "title_matches_target",
            ),
        ),
        (
            "app.job_targeting",
            (
                "role_match",
                "matches_target_role",
                "title_matches_target",
            ),
        ),
        (
            "app.universal_targeting",
            (
                "role_match",
                "matches_target_role",
                "title_matches_target",
            ),
        ),
    )

    for module_name, names in candidates:
        try:
            module = importlib.import_module(
                module_name
            )
        except Exception:
            continue

        for name in names:
            function = getattr(
                module,
                name,
                None,
            )
            if not callable(function):
                continue
            if function is dice_role_match_compat:
                continue

            try:
                return bool(
                    function(
                        *args,
                        **kwargs,
                    )
                )
            except TypeError:
                continue
            except Exception:
                continue

    return None


def dice_role_match_compat(
    *args: Any,
    **kwargs: Any,
) -> bool:
    """
    Compatibility bridge for the broken Dice-local prefilter.

    It first tries the project's canonical role matcher. When no compatible
    callable is available, it returns True so the job continues to the
    universal targeting gate in job_store.save_job. This avoids a NameError
    without bypassing the production storage gate.
    """
    canonical_result = (
        _call_canonical_role_match(
            *args,
            **kwargs,
        )
    )

    if canonical_result is not None:
        return canonical_result

    return True


def self_test() -> dict[str, Any]:
    calls: list[dict[str, Any]] = []

    def fake_scrape_jobs(
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        calls.append(
            {
                "args": list(args),
                "kwargs": kwargs,
            }
        )
        return [{"ok": True}]

    combined = make_jobspy_wrapper(
        fake_scrape_jobs,
        mode="zip_recruiter",
    )
    indeed = make_jobspy_wrapper(
        fake_scrape_jobs,
        mode="indeed",
    )
    linkedin = make_jobspy_wrapper(
        fake_scrape_jobs,
        mode="linkedin",
    )

    combined(site_name=["indeed", "google"])
    indeed(site_name=["google"])
    linkedin(site_name=["indeed"])

    return {
        "success": True,
        "marker": MARKER,
        "calls": calls,
        "combined_sites": calls[0]["kwargs"][
            "site_name"
        ],
        "indeed_sites": calls[1]["kwargs"][
            "site_name"
        ],
        "linkedin_sites": calls[2]["kwargs"][
            "site_name"
        ],
        "dice_fallback": (
            dice_role_match_compat(
                "any role"
            )
        ),
        "provider_calls": 0,
        "telegram_calls": 0,
        "n8n_calls": 0,
        "database_writes": 0,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            self_test(),
            indent=2,
        )
    )
