"""Add Phase 10 tenant-bound ATS credential tables."""
from app.ats_credentials import ensure_schema
def apply(connection): ensure_schema(connection)
