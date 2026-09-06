"""Add the immutable signed Application Plan V2 transport ledger."""
from __future__ import annotations

import sqlite3

from app.application_plan_transport_v2 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
