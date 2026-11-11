"""Add the fail-closed preflight binding columns to Hunter's local n8n queue."""
from __future__ import annotations

import sqlite3

from app.n8n_dispatch import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(n8n_callback_receipts)")}
    for name in ("payload_sha256", "execution_artifact_id"):
        if name not in columns:
            connection.execute(f"ALTER TABLE n8n_callback_receipts ADD COLUMN {name} TEXT")
