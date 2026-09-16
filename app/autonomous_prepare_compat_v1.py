"""Compatibility routing for autonomous preparation rollout.

Native Resume can own automatic durable Prepare immediately because its immutable
Candidate Truth/job/artifact bindings are already complete. The legacy n8n auto
lane must not be silently replaced until the n8n producer emits the equivalent
resume binding required by durable Phase 9 preparation.

The historical automatic/preparation default is n8n. An account without an
explicit saved ``prepare_engine`` therefore remains on that proven path. Native
automatic durable preparation activates only after an explicit NATIVE preference.
The experimental durable N8N bridge is separately opt-in and default-off.
"""
from __future__ import annotations

import os
from typing import Any

from app.database import get_connection
from app.n8n_dispatch import load_setting

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
    """Resolve the saved automatic engine without silently migrating users.

    Older product policy rows predate ``prepare_engine`` entirely and the
    existing chooser historically defaults to n8n. Missing therefore means
    N8N for backward compatibility. Only an explicit NATIVE value changes
    automatic ownership to durable Native Prepare.
    """
    connection = get_connection()
    try:
        policy = load_setting(
            connection,
            "product_automation_policy_v1",
            {},
        )
    finally:
        connection.close()

    engine = str(policy.get("prepare_engine") or "").strip().upper()
    if not engine:
        return "N8N"
    if engine not in {"NATIVE", "N8N"}:
        raise RuntimeError("Product automation policy has an invalid prepare engine.")
    return engine


def routing_mode() -> dict[str, Any]:
    """Choose automatic ownership without changing eligibility or quotas.

    Explicit NATIVE uses durable Prepare. N8N, including the historical missing
    preference, remains on the legacy queue unless the experimental durable flag
    is explicitly enabled. This prevents a source rollout from converting a
    working n8n auto lane into ISSUE.
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
