"""Backend-only Tsenta connection truth.

A Mac's Codex MCP OAuth grant is not a Netcup application grant. The server
must never infer authorization from a browser/UI flag or environment boolean.
Until user-owned, tenant-scoped OAuth credentials can be verified through an
authenticated Tsenta MCP profile read, no backend execution is available.
"""

from __future__ import annotations

from typing import Any


def connection_status() -> dict[str, Any]:
    """Fail closed until a real secure backend OAuth lifecycle is implemented."""
    return {
        "provider": "TSENTA",
        "authenticated_backend": False,
        "profile_read_verified": False,
        "execution_ready": False,
        "reason_code": "BACKEND_OAUTH_NOT_CONFIGURED",
        "local_codex_session_reused": False,
    }
