"""Narrow environment policy for safe preparation-only staging features.

Production remains explicit and unchanged. The fallback only activates inside the
existing isolated staging contract: cloud shadow mode, no imported production
state, and production callbacks disabled.
"""
from __future__ import annotations

import os


def _truthy_value(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def isolated_staging() -> bool:
    return (
        _truthy_value(os.getenv("CLOUD_SHADOW_MODE"))
        and not _truthy_value(os.getenv("PRODUCTION_STATE_IMPORTED"))
        and not _truthy_value(os.getenv("PRODUCTION_CALLBACKS_ENABLED"))
        and not _truthy_value(os.getenv("HUNTER_ENABLE_TELEGRAM"))
        and not _truthy_value(os.getenv("HUNTER_ENABLE_DISCOVERY_SCHEDULER"))
        and not _truthy_value(os.getenv("HUNTER_ENABLE_COORDINATOR"))
    )


def preparation_feature_enabled(explicit_env: str) -> bool:
    """Enable a safe local preparation feature explicitly or in isolated staging."""
    return _truthy_value(os.getenv(explicit_env)) or isolated_staging()
