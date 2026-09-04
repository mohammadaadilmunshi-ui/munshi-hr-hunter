"""Additive Phase 8 native application preparation ledger."""
from app.native_application_preparation import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
