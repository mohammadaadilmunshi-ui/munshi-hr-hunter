from __future__ import annotations

from pathlib import Path
from typing import Any

from app.database import get_setting
from app.platform_config import (
    database_path,
    endpoint_url,
    fastapi_endpoint,
    is_macos,
    logs_directory,
    n8n_database_path as portable_n8n_database_path,
    n8n_endpoint,
    n8n_user_directory,
    ollama_endpoint,
    ollama_enabled,
    ollama_required,
    platform_mode,
    project_root,
    runtime_directory,
    scheduler_backend,
    streamlit_endpoint,
)


def integration_health() -> dict[str, Any]:
    return dict(get_setting("integration_health", {}) or {})


def downstream_contract() -> dict[str, Any]:
    return dict(get_setting("downstream_contract", {}) or {})


def provider_runtime() -> dict[str, Any]:
    return dict(get_setting("provider_runtime", {}) or {})


def provider_int(key: str, *, minimum: int = 1) -> int:
    try:
        value = int(provider_runtime()[key])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(f"Required provider runtime integer is missing: {key}") from None
    return max(minimum, value)


def telegram_batch_limit(requested: int | None = None) -> int:
    contract = downstream_contract()
    try:
        configured_default = int(contract["telegram_default_batch_limit"])
        configured_maximum = int(contract["telegram_max_batch_size"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Canonical Telegram batch limits are incomplete.") from None
    return max(
        1,
        min(
            int(requested) if requested is not None else configured_default,
            configured_maximum,
        ),
    )


def n8n_database_path() -> Path:
    configured = str(integration_health().get("n8n_database_path") or "").strip()
    if not configured:
        return portable_n8n_database_path()
    path = Path(configured).expanduser()
    return path if path.is_absolute() else project_root() / path


def n8n_workflow_id() -> str:
    snapshot = integration_health().get("n8n_read_only_snapshot") or {}
    configured = str(snapshot.get("workflow_id") or "").strip()
    if not configured:
        raise RuntimeError("The controlled n8n workflow identity is not configured.")
    return configured


def downstream_int(key: str, *, minimum: int = 0) -> int:
    try:
        value = int(downstream_contract().get(key))
    except (TypeError, ValueError):
        raise RuntimeError(f"Required downstream integer setting is missing: {key}") from None
    return max(minimum, value)


def service_endpoint(name: str) -> tuple[str, int]:
    portable = {"fastapi": fastapi_endpoint, "streamlit": streamlit_endpoint, "n8n": n8n_endpoint}
    if name in portable:
        endpoint = portable[name]()
        return endpoint.host, endpoint.port
    services = integration_health().get("services") or {}
    service = services.get(name) if isinstance(services, dict) else None
    if not isinstance(service, dict):
        raise RuntimeError(f"Service endpoint is not configured: {name}")
    host = str(service.get("host") or "").strip()
    try:
        port = int(service.get("port"))
    except (TypeError, ValueError):
        raise RuntimeError(f"Service port is not configured: {name}") from None
    if not host or port <= 0:
        raise RuntimeError(f"Service endpoint is incomplete: {name}")
    return host, port


def launch_agent_plist(label: str) -> Path:
    directory = str(integration_health().get("launch_agents_directory") or "").strip()
    if not is_macos():
        raise RuntimeError("LaunchAgents are only available on macOS; use an external process manager.")
    if not directory:
        directory = str(Path.home() / "Library" / "LaunchAgents")
    return Path(directory).expanduser() / f"{label}.plist"


__all__ = [
    "database_path", "endpoint_url", "logs_directory", "n8n_database_path",
    "n8n_endpoint", "n8n_user_directory", "ollama_endpoint", "ollama_enabled",
    "ollama_required", "platform_mode", "project_root", "runtime_directory",
    "scheduler_backend", "service_endpoint", "streamlit_endpoint", "fastapi_endpoint",
    "launch_agent_plist",
]
