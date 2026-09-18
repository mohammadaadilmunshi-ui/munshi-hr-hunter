"""Add durable one-click Prepare orchestration authority."""
from __future__ import annotations

import sqlite3

from app import prepare_orchestrator_v1


def apply(connection: sqlite3.Connection) -> None:
    prepare_orchestrator_v1.ensure_schema(connection)
