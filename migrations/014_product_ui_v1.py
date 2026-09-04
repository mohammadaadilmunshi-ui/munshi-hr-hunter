"""Additive Product UI, Gmail, and encrypted-vault schema.

The repository's initialization convention invokes these same idempotent schema
helpers for fresh and upgraded databases.  Keeping the migration complete
avoids a partial Product UI schema when it is applied independently.
"""
from app.gmail_integration import ensure_schema as ensure_gmail_schema
from app.product_state import ensure_schema as ensure_product_schema
from app.secure_vault import ensure_schema as ensure_vault_schema


def apply(connection) -> None:
    ensure_product_schema(connection)
    ensure_vault_schema(connection)
    ensure_gmail_schema(connection)
