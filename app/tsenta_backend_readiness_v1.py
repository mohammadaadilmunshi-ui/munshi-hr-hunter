"""Backend-only Tsenta connection truth.

A Mac's Codex MCP OAuth grant is not a Netcup application grant. The server
must never infer authorization from a browser/UI flag or environment boolean.
Until user-owned, tenant-scoped OAuth credentials can be verified through an
authenticated Tsenta MCP profile read, no backend execution is available.
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
    # No integrated dispatch worker currently exists.  OAuth never enables an
    # external application submission or reserves the job ownership lock alone.
    return {
        "provider": "TSENTA",
        "authenticated_backend": authenticated,
        "profile_read_verified": profile_verified,
        "execution_ready": False,
        "reason_code": (
            "DISPATCH_WORKER_NOT_VALIDATED"
            if authenticated and profile_verified
            else str(state.get("reason_code") or "BACKEND_OAUTH_NOT_CONFIGURED")
        ),
        "local_codex_session_reused": False,
    }
