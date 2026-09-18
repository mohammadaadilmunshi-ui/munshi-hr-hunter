"""Additive, internal-only native resume shadow planning ledger."""
from app.native_resume_shadow import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
