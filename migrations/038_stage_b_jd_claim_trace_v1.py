"""Add the immutable Stage B JD ↔ resume claim-trace ledger."""
from __future__ import annotations

import sqlite3

from app.jd_claim_trace_v1 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
