"""Add durable user notification preferences and SMTP delivery outbox."""
from __future__ import annotations

import sqlite3

from app import application_notifications_v1


def apply(connection: sqlite3.Connection) -> None:
    application_notifications_v1.ensure_schema(connection)
