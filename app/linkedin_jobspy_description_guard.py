from __future__ import annotations

import functools
import sys
import types
from typing import Any, Callable

VERSION = "linkedin-jobspy-description-guard-v1.1.0"
MARKER = "AADIL_LINKEDIN_JOBSPY_RUNTIME_DESCRIPTION_GUARD_V1_1"


def _jobspy_module() -> Any:
    module = sys.modules.get("jobspy")
    if module is not None:
        return module
    import jobspy  # type: ignore
    return jobspy


def install() -> dict[str, Any]:
    """Force JobSpy LinkedIn detail fetching without depending on call location.

    The LinkedIn worker may delegate to a shared JobSpy adapter, so patch both
    ``jobspy.scrape_jobs`` and already-imported module globals that reference the
    original function.
    """
    module = _jobspy_module()
    original = getattr(module, "scrape_jobs", None)
    if not callable(original):
        raise RuntimeError("jobspy.scrape_jobs is unavailable")

    if getattr(original, "_aadil_linkedin_description_guard", False):
        return {
            "success": True,
            "already_installed": True,
            "patched_module_globals": 0,
            "version": VERSION,
        }

    @functools.wraps(original)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        kwargs["linkedin_fetch_description"] = True
        return original(*args, **kwargs)

    guarded._aadil_linkedin_description_guard = True  # type: ignore[attr-defined]
    guarded._aadil_original_scrape_jobs = original  # type: ignore[attr-defined]
    setattr(module, "scrape_jobs", guarded)

    patched = 0
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

    return {
        "success": True,
        "already_installed": False,
        "patched_module_globals": patched,
        "version": VERSION,
    }


def self_test() -> dict[str, Any]:
    original_jobspy = sys.modules.get("jobspy")
    holder_name = "_aadil_linkedin_guard_test_holder"
    original_holder = sys.modules.get(holder_name)
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
        success = (
            result.get("success") is True
            and direct["kwargs"].get("linkedin_fetch_description") is True
            and delegated["kwargs"].get("linkedin_fetch_description") is True
            and len(calls) == 2
        )
        return {
            "success": success,
            "direct_forced": direct["kwargs"].get("linkedin_fetch_description") is True,
            "delegated_forced": delegated["kwargs"].get("linkedin_fetch_description") is True,
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
