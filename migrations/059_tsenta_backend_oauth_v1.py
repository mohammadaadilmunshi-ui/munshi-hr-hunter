"""Add tenant-scoped encrypted Tsenta backend OAuth and one-time PKCE intent tables."""

from __future__ import annotations

import sqlite3

from app.tsenta_oauth_v1 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
