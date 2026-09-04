"""Additive Phase 6 career-preferences and local policy schema."""
from app.career_policy import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
