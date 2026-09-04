"""Additive Phase 5 answer-vault schema."""
from app.answer_brain import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
