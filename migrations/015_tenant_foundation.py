"""Additive default-user and ownership registry foundation."""
from app.tenant_foundation import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
