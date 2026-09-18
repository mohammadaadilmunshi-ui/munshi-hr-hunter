"""Add canonical lifecycle history/read-model authority."""
from __future__ import annotations

import sqlite3

from app import application_lifecycle_authority_v1


def apply(connection: sqlite3.Connection) -> None:
    application_lifecycle_authority_v1.ensure_schema(connection)
