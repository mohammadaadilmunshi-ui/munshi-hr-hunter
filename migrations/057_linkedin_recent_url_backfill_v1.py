"""Repair LinkedIn posting URLs for jobs first seen in the previous 48 hours."""
from __future__ import annotations

import sqlite3

from app.linkedin_recent_backfill import (
    apply_recent_linkedin_backfill,
    plan_recent_linkedin_backfill,
)


def apply(connection: sqlite3.Connection) -> None:
    result = apply_recent_linkedin_backfill(
        connection,
        lookback_hours=48,
        actor="deployment-migration:linkedin-48h-backfill-v1",
    )
    verification = plan_recent_linkedin_backfill(
        connection,
        lookback_hours=48,
    )
    if int(verification["counts"]["changed"]) != 0:
        raise RuntimeError(
            "LinkedIn 48h deployment backfill did not converge to a stable state."
        )
    if int(result["counts"]["scanned"]) != int(verification["counts"]["scanned"]):
        raise RuntimeError(
            "LinkedIn 48h deployment backfill scope changed while Hunter was offline."
        )
