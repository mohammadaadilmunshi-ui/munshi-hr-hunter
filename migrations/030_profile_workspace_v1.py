"""Additive public brand-asset cache for the permanent Profile workspace."""
from app.profile_brand_resolver import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
