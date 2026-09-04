"""Additive Phase 9 inert Apply preparation handoff ledger."""
from app.apply_handoff import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
