"""Add opt-in recruiting communication notifications and read-only projection."""
from __future__ import annotations

import sqlite3

from app import application_notifications_runtime_v3


def apply(connection: sqlite3.Connection) -> None:
    application_notifications_runtime_v3.ensure_schema(connection)
