"""Add Phase 11 tenant-bound Gmail OAuth and local email-evidence tables."""
from app.gmail_oauth_tenant import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
