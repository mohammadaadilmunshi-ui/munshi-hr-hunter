"""Add immutable NEEDS_INPUT Application Plan V2 revision links."""

from __future__ import annotations

import sqlite3

from app.application_plan_v2 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
