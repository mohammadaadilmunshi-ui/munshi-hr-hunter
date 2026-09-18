"""Add the immutable Phase 9 application preflight package ledger."""
from __future__ import annotations

import sqlite3

from app.application_preflight_package_v1 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
