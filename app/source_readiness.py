"""Inspect shipped workers without importing them or changing runtime state.

SQLite owns worker selection; a stale mapping is not executable evidence.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from app.discovery_policy import DiscoveryPolicyError, require_free_provider

ROOT = Path(__file__).resolve().parent.parent


def source_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def worker_available(module: str) -> bool:
    return bool(re.fullmatch(r"app\.[a-z][a-z0-9_]*", module)) and (
        ROOT / "app" / (module.removeprefix("app.") + ".py")
    ).is_file()


def source_readiness(source: Mapping[str, Any], registry: Mapping[str, Any]) -> dict[str, Any]:
    name = str(source.get("source_name") or "")
    try:
        require_free_provider(name, cost_mode=str(source.get("cost_mode") or "unknown"))
    except DiscoveryPolicyError:
        return {"ready": False, "reason": "disabled_by_policy", "module": None}
    if source_key(name) in {source_key(x) for x in registry.get("retired_source_keys", [])}:
        return {"ready": False, "reason": "retired_source", "module": None}
    module = next((str(m or "").strip() for n, m in dict(registry.get("workers") or {}).items()
                   if source_key(n) == source_key(name)), "")
    if not module:
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        module = f"app.{slug}_worker"
    available = worker_available(module)
    return {
        "ready": available,
        "reason": "worker_available" if available else "worker_missing",
        "module": module if available else None,
    }
