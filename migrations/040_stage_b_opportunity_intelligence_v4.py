"""Add Stage B-aware Opportunity Intelligence V4 ledger."""
from __future__ import annotations

import sqlite3

from app.opportunity_intelligence_v4 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
