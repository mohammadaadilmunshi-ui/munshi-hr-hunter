"""Additive staging-only synthetic fixture ownership ledger."""
from app.staging_fixtures import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
