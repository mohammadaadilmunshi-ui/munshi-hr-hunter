"""Avoid unnecessary SQLite DDL on established Resume Studio read paths.

Existing schemas are owned by migrations and verified at startup. An uninitialized
test or new installation still uses the original schema initializer.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable


def ensure_existing_read_table(
    connection: sqlite3.Connection,
    table_name: str,
    initialize: Callable[[sqlite3.Connection], None],
) -> None:
    """Use sqlite_master read first, never CREATE TABLE on ordinary reads."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", table_name) is None:
        raise ValueError("Unexpected Resume Studio table identifier.")
    ready = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    if ready is None:
        initialize(connection)
