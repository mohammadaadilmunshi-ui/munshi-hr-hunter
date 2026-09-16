"""Compatibility routing for autonomous preparation rollout.

Native Resume can own automatic durable Prepare immediately because its immutable
Candidate Truth/job/artifact bindings are already complete. The legacy n8n auto
lane must not be silently replaced until the n8n producer emits the equivalent
resume binding required by durable Phase 9 preparation.

Default behavior therefore preserves the proven legacy n8n queue whenever the
saved product engine is N8N. The experimental durable N8N path is opt-in only and
does not weaken any truth or submission boundary.
"""
from __future__ import annotations

import os
from typing import Any

from app import autonomous_prepare_v1 as runtime
from app.database import get_connection

N8N_DURABLE_PREPARE_ENV = "MUNSHI_N8N_DURABLE_PREPARE_ENABLED"
SUBMISSION_AUTHORITY = False


def _enabled(value: Any) -> bool:
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def n8n_durable_prepare_enabled() -> bool:
    """Return the explicit opt-in for the still-incomplete n8n durable bridge."""
    return _enabled(os.getenv(N8N_DURABLE_PREPARE_ENV))


def configured_engine() -> str:
    """Read the same saved engine authority used by durable admission."""
    connection = get_connection()
    try:
        return runtime._configured_engine(connection)
    finally:
        connection.close()


def routing_mode() -> dict[str, Any]:
    """Choose automatic ownership without changing eligibility or quotas.

    NATIVE is durable by default. N8N remains on the legacy queue unless the
    explicit experimental durable flag is enabled. This avoids turning a
    previously working n8n auto lane into ISSUE merely because the durable
    callback contract is not yet producer-complete.
    """
    engine = configured_engine()
    durable_n8n = n8n_durable_prepare_enabled()

    if engine == "N8N" and not durable_n8n:
        return {
            "engine": "N8N",
            "mode": "LEGACY_N8N_COMPAT",
            "durable_prepare_is_auto_owner": False,
            "legacy_auto_n8n_queue_enabled": True,
            "reason": "n8n_durable_resume_binding_not_enabled",
            "submission_authority": False,
        }

    return {
        "engine": engine,
        "mode": "DURABLE_PREPARE",
        "durable_prepare_is_auto_owner": True,
        "legacy_auto_n8n_queue_enabled": False,
        "reason": (
            "native_durable_prepare"
            if engine == "NATIVE"
            else "explicit_n8n_durable_prepare_opt_in"
        ),
        "submission_authority": False,
    }
