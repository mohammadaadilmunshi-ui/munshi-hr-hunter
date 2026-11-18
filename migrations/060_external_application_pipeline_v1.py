"""Add the owner-scoped read-only external application tracker projection."""

from __future__ import annotations

import sqlite3

from app.external_application_tracker_v1 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
