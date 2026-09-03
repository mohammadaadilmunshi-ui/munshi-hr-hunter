"""Additive product UI state schema.

The application calls ``app.product_state.ensure_schema`` during database
initialization so this migration is safe on existing SQLite databases.
"""
from app.product_state import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
