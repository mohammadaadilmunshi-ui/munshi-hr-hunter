"""Additive evidence-bound snapshot ledgers for strengthened Phases 6 and 7."""
from app.opportunity_intelligence_v2 import ensure_schema as ensure_opportunity_schema
from app.relationship_intelligence_v2 import ensure_schema as ensure_relationship_schema


def apply(connection) -> None:
    ensure_opportunity_schema(connection)
    ensure_relationship_schema(connection)
