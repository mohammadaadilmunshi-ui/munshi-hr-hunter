"""Add authenticated inbound mail storage and correlation authority."""
from __future__ import annotations

import sqlite3

from app import inbound_mail


def apply(connection: sqlite3.Connection) -> None:
    inbound_mail.ensure_schema(connection)
