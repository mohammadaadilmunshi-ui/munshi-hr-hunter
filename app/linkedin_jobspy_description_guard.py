from __future__ import annotations

import functools
import sys
import types
from typing import Any
from urllib.parse import urlsplit

VERSION = "linkedin-jobspy-direct-url-guard-v1.2.0"
MARKER = "AADIL_LINKEDIN_JOBSPY_DIRECT_URL_GUARD_V1_2"

_AGGREGATOR_HOSTS = frozenset(
    {
        "linkedin.com",
        "indeed.com",
        "glassdoor.com",
        "ziprecruiter.com",
    }
)


def _jobspy_module() -> Any:
    module = sys.modules.get("jobspy")
    if module is not None:
        return module
    import jobspy  # type: ignore
    return jobspy


def _linkedin_requested(value: Any) -> bool:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
    else:
        return False
    return any(str(item or "").strip().casefold() == "linkedin" for item in values)


def _direct_application_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme.casefold() not in {"http", "https"} or not host:
        return None
    if any(host == item or host.endswith("." + item) for item in _AGGREGATOR_HOSTS):
        return None
    return text


def _install_board_normalizer_guard() -> bool:
    """Prevent the shared outer normalizer from restoring LinkedIn as apply_url.

    The dedicated LinkedIn worker imports jobspy_board_common before calling this
    installer. We replace only that module's normalize_jobspy_row global; all
    other dedicated JobSpy worker processes remain untouched.
    """
    try:
        from app import jobspy_board_common as board_common
    except Exception:
        return False

    original = getattr(board_common, "normalize_jobspy_row", None)
    if not callable(original):
        return False
    if getattr(original, "_aadil_linkedin_direct_url_guard", False):
        return True

    @functools.wraps(original)
    def guarded_normalize(record: dict[str, Any], site_name: str) -> dict[str, Any]:
        result = original(record, site_name)
        if str(site_name or "").strip().casefold() != "linkedin":
            return result
        safe = _direct_application_url(result.get("apply_url"))
        if safe == result.get("apply_url"):
            return result
        protected = dict(result)
        protected["apply_url"] = safe
        return protected

    guarded_normalize._aadil_linkedin_direct_url_guard = True  # type: ignore[attr-defined]
    guarded_normalize._aadil_original_normalizer = original  # type: ignore[attr-defined]
    board_common.normalize_jobspy_row = guarded_normalize
    return True


def install() -> dict[str, Any]:
    """Protect LinkedIn detail fetching and direct-application URL semantics.

    This installer runs only in the dedicated LinkedIn worker. It still checks
    site_name before forcing LinkedIn detail retrieval so shared JobSpy behavior
    cannot be altered accidentally. It also guards the outer Hunter normalizer so
    an unresolved LinkedIn listing URL is never restored as ``apply_url``.
    """
    module = _jobspy_module()
    # Test doubles intentionally expose scrape_jobs only.
    if getattr(module, "__path__", None) is not None:
        from app.linkedin_apply_button_v7 import install_jobspy_apply_parser
        install_jobspy_apply_parser()
    original = getattr(module, "scrape_jobs", None)
    if not callable(original):
        raise RuntimeError("jobspy.scrape_jobs is unavailable")

    already_installed = bool(getattr(original, "_aadil_linkedin_description_guard", False))
    patched = 0

    if not already_installed:
        @functools.wraps(original)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            if _linkedin_requested(kwargs.get("site_name")):
                kwargs["linkedin_fetch_description"] = True
            return original(*args, **kwargs)

        guarded._aadil_linkedin_description_guard = True  # type: ignore[attr-defined]
        guarded._aadil_original_scrape_jobs = original  # type: ignore[attr-defined]
        setattr(module, "scrape_jobs", guarded)

        current_module = sys.modules.get(__name__)
        for loaded in list(sys.modules.values()):
            if loaded is None or loaded is current_module:
                continue
            namespace = getattr(loaded, "__dict__", None)
            if not isinstance(namespace, dict):
                continue
            for name, value in list(namespace.items()):
                if value is original:
                    try:
                        namespace[name] = guarded
                        patched += 1
                    except Exception:
                        pass

    normalizer_guarded = _install_board_normalizer_guard()

    return {
        "success": True,
        "already_installed": already_installed,
        "patched_module_globals": patched,
        "board_normalizer_guarded": normalizer_guarded,
        "version": VERSION,
    }


def self_test() -> dict[str, Any]:
    original_jobspy = sys.modules.get("jobspy")
    holder_name = "_aadil_linkedin_guard_test_holder"
    original_holder = sys.modules.get(holder_name)
    board_common = None
    original_normalizer = None
    try:
        from app import jobspy_board_common as board_common_module
        board_common = board_common_module
        original_normalizer = getattr(board_common, "normalize_jobspy_row", None)
    except Exception:
        board_common = None
        original_normalizer = None

    calls: list[dict[str, Any]] = []

    def fake_scrape_jobs(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(dict(kwargs))
        return {"kwargs": dict(kwargs)}

    fake_jobspy = types.ModuleType("jobspy")
    fake_jobspy.scrape_jobs = fake_scrape_jobs  # type: ignore[attr-defined]
    holder = types.ModuleType(holder_name)
    holder.delegated_scrape = fake_scrape_jobs  # type: ignore[attr-defined]

    try:
        sys.modules["jobspy"] = fake_jobspy
        sys.modules[holder_name] = holder
        result = install()
        direct = fake_jobspy.scrape_jobs(site_name=["linkedin"], search_term="HR")  # type: ignore[attr-defined]
        delegated = holder.delegated_scrape(site_name=["linkedin"], search_term="TA")  # type: ignore[attr-defined]
        non_linkedin = fake_jobspy.scrape_jobs(site_name=["indeed"], search_term="HR")  # type: ignore[attr-defined]
        success = (
            result.get("success") is True
            and direct["kwargs"].get("linkedin_fetch_description") is True
            and delegated["kwargs"].get("linkedin_fetch_description") is True
            and "linkedin_fetch_description" not in non_linkedin["kwargs"]
            and len(calls) == 3
        )
        return {
            "success": success,
            "direct_forced": direct["kwargs"].get("linkedin_fetch_description") is True,
            "delegated_forced": delegated["kwargs"].get("linkedin_fetch_description") is True,
            "non_linkedin_unchanged": "linkedin_fetch_description" not in non_linkedin["kwargs"],
            "calls": len(calls),
            "install": result,
            "version": VERSION,
        }
    finally:
        if original_jobspy is None:
            sys.modules.pop("jobspy", None)
        else:
            sys.modules["jobspy"] = original_jobspy
        if original_holder is None:
            sys.modules.pop(holder_name, None)
        else:
            sys.modules[holder_name] = original_holder
        if board_common is not None and callable(original_normalizer):
            board_common.normalize_jobspy_row = original_normalizer
