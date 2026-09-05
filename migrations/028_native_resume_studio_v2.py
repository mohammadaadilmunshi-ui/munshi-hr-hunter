"""Additive candidate-scoped Resume Studio V2 writer settings."""
from app.native_resume_service_v2 import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
