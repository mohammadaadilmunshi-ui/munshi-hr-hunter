"""Backend-only MUNSHI Relay connection truth.

A Mac's Codex MCP OAuth grant is not a Netcup application grant. The server
must never infer authorization from a browser/UI flag or environment boolean.
Until user-owned, tenant-scoped OAuth credentials can be verified through an
authenticated MUNSHI Relay MCP profile read, no backend execution is available.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def connection_status() -> dict[str, Any]:
    """Read verified tenant OAuth state without implying execution readiness."""
    from app import tsenta_oauth_v1

    try:
        state = tsenta_oauth_v1.status()
    except (sqlite3.OperationalError, RuntimeError, ValueError, OSError):
        state = {"authorized": False, "profile_read_verified": False,
                 "reason_code": "BACKEND_OAUTH_NOT_CONFIGURED"}
    authenticated = state.get("authorized") is True
    profile_verified = state.get("profile_read_verified") is True
    # Backend readiness requires a verified owner plus the installed
    # single-use dispatcher. OAuth never grants submission consent by itself.
    import importlib.util

    integrated = importlib.util.find_spec(
        "app.relay_application_dispatch_v1"
    ) is not None
    ready = authenticated and profile_verified and integrated
    return {
        "provider": "TSENTA",
        "authenticated_backend": authenticated,
        "profile_read_verified": profile_verified,
        "execution_ready": ready,
        "reason_code": (
            "READY" if ready else
            "DISPATCH_WORKER_NOT_VALIDATED"
            if authenticated and profile_verified
            else str(state.get("reason_code") or "BACKEND_OAUTH_NOT_CONFIGURED")
        ),
        "local_codex_session_reused": False,
    }
