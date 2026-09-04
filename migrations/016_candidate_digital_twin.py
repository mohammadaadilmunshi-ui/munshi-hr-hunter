"""Additive tenant-scoped candidate digital-twin storage."""
from app.candidate_digital_twin import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
