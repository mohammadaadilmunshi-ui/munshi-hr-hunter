"""Repair LinkedIn posting URLs for jobs first seen in the previous 48 hours."""
from __future__ import annotations

import sqlite3

from app.linkedin_recent_backfill import apply_recent_linkedin_backfill


def apply(connection: sqlite3.Connection) -> None:
    apply_recent_linkedin_backfill(
        connection,
        lookback_hours=48,
        actor="deployment-migration:linkedin-48h-backfill-v1",
    )
