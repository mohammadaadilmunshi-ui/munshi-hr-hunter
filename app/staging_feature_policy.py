"""Narrow environment policy for safe preparation-only staging features.

Production remains explicit and unchanged. The fallback only activates inside the
existing isolated staging contract: cloud shadow mode, no imported production
state, production callbacks disabled, and all background production-capable lanes
disabled.
"""
from __future__ import annotations

import os


PREPARATION_FEATURE_ENVS = (
    "MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED",
    "MUNSHI_CAREER_POLICY_ENABLED",
    "MUNSHI_RELATIONSHIP_INTELLIGENCE_ENABLED",
)


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


def activate_isolated_staging_preparation_features() -> bool:
    """Turn on only Phase 5–7 preparation gates in the proven isolated staging runtime.

    Compose deliberately keeps these flags false by default. This runtime promotion
    happens only after the existing staging isolation contract is visible inside the
    Hunter process. It never enables tenant impersonation, Gmail, Telegram, discovery,
    coordinator, browser, outreach, callbacks, or submission authority.
    """
    if not isolated_staging():
        return False
    for name in PREPARATION_FEATURE_ENVS:
        os.environ[name] = "true"
    return True
