"""Cross-platform runtime paths and service endpoints.

This module is intentionally independent of the settings database.  It is
safe to use from launchers, tests, and code paths that run before the Hunter
database has been initialized.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _path(value: str, *, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "required", "enabled"}


def project_root() -> Path:
    return _path(_env("AADIL_HR_HUNTER_PROJECT", str(REPOSITORY_ROOT)), base=REPOSITORY_ROOT)


def database_path() -> Path:
    return _path(_env("DATABASE_PATH", "data/hunter.db"), base=project_root())


def runtime_directory() -> Path:
    return _path(_env("AADIL_HR_HUNTER_RUNTIME", str(Path.home() / ".aadil_hr_hunter_runtime")), base=project_root())


def logs_directory() -> Path:
    configured = os.getenv("AADIL_HR_HUNTER_LOGS", "").strip()
    return _path(configured, base=project_root()) if configured else runtime_directory() / "logs"


def n8n_user_directory() -> Path:
    return _path(_env("N8N_USER_FOLDER", str(Path.home() / ".n8n")), base=project_root())


def n8n_database_path() -> Path:
    configured = os.getenv("N8N_DATABASE_PATH", "").strip()
    return _path(configured, base=project_root()) if configured else n8n_user_directory() / "database.sqlite"


def _port(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return value


@dataclass(frozen=True)
class ServiceEndpoint:
    host: str
    port: int
    base_url: str


def _endpoint(host_name: str, port_name: str, default_host: str, default_port: int, url_name: str | None = None) -> ServiceEndpoint:
    host = _env(host_name, default_host)
    port = _port(port_name, default_port)
    configured_url = os.getenv(url_name, "").strip() if url_name else ""
    if configured_url:
        parsed = urlsplit(configured_url)
        if parsed.scheme and parsed.hostname:
            return ServiceEndpoint(parsed.hostname, parsed.port or port, configured_url.rstrip("/"))
    return ServiceEndpoint(host, port, f"http://{host}:{port}")


def fastapi_endpoint() -> ServiceEndpoint:
    return _endpoint("FASTAPI_HOST", "FASTAPI_PORT", "127.0.0.1", 8000)


def streamlit_endpoint() -> ServiceEndpoint:
    return _endpoint("STREAMLIT_HOST", "STREAMLIT_PORT", "127.0.0.1", 8501)


def n8n_endpoint() -> ServiceEndpoint:
    return _endpoint("N8N_HOST", "N8N_PORT", "127.0.0.1", 5678, "N8N_BASE_URL")


def ollama_endpoint() -> ServiceEndpoint:
    return _endpoint("OLLAMA_HOST", "OLLAMA_PORT", "127.0.0.1", 11434, "OLLAMA_BASE_URL")


def ollama_enabled() -> bool:
    return _bool("OLLAMA_ENABLED") or _bool("OLLAMA_REQUIRED")


def ollama_required() -> bool:
    return _bool("OLLAMA_REQUIRED")


def platform_mode() -> str:
    configured = os.getenv("AADIL_HR_HUNTER_PLATFORM", "").strip().lower()
    return configured or ("macos" if platform.system() == "Darwin" else "portable")


def scheduler_backend() -> str:
    configured = os.getenv("SCHEDULER_BACKEND", "").strip().lower()
    return configured or ("launchd" if platform_mode() == "macos" else "external")


def is_macos() -> bool:
    return platform.system() == "Darwin" or platform_mode() == "macos"


def endpoint_url(name: str, path: str = "") -> str:
    endpoints = {
        "fastapi": fastapi_endpoint,
        "streamlit": streamlit_endpoint,
        "n8n": n8n_endpoint,
        "ollama": ollama_endpoint,
    }
    try:
        base = endpoints[name]().base_url
    except KeyError:
        raise ValueError(f"Unknown service: {name}") from None
    return f"{base}/{path.lstrip('/')}" if path else base
