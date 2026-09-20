"""RETIRED legacy browser-bound customer approval helper.

DO NOT USE from Product, API, workers, or deployment code.

Canonical AutoApply records the sole customer approval before ATS execution in
:mod:`app.application_execution_mandate_v1`.  Later browser/form reviews are
internal integrity state and derive technical submit authority from that earlier
mandate without another user action.

This module remains only as an executable tombstone for old imports.
"""
from __future__ import annotations

from typing import Any

LEGACY_RETIRED = True


def approve_and_authorize(**_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError(
        "Legacy browser-bound approve_and_authorize is retired. "
        "Use the pre-browser application execution mandate."
    )
