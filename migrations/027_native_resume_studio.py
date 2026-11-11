"""Additive native Resume Studio source and version persistence."""
from app.native_resume_service import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
