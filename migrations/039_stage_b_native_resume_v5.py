"""Add Stage B-native resume V5 atomic binding schema."""
from __future__ import annotations

import sqlite3

from app.stage_b_resume_binding_v1 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
