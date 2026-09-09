"""Add the immutable Stage B Candidate ↔ JD match ledger."""
from __future__ import annotations

import sqlite3

from app.jd_requirement_match_v1 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
