"""Persist immutable, owner-scoped Complete Application Loop artifacts."""
from __future__ import annotations

import sqlite3

from app.application_execution_artifact import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
