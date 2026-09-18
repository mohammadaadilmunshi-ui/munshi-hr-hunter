"""Add sanitized application-mail evidence and one-time verification broker V2."""
from __future__ import annotations

import sqlite3

from app import mail_intelligence_v2


def apply(connection: sqlite3.Connection) -> None:
    mail_intelligence_v2.ensure_schema(connection)
