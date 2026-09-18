"""Add Hunter-only synthetic submit command ledger."""
from app.application_submit_command_v1 import ensure_schema

def apply(connection):
    ensure_schema(connection)
