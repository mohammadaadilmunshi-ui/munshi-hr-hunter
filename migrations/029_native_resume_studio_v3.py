"""Additive Resume Studio V3 profile-extraction storage."""
from app.native_resume_service_v3 import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
