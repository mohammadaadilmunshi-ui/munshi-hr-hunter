"""Additive Phase 7 relationship-intelligence schema."""
from app.relationship_intelligence import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
