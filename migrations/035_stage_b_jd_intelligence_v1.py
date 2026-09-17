"""Add the immutable Stage B JD Intelligence snapshot ledger."""
from __future__ import annotations

import sqlite3

from app.jd_intelligence_v1 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
