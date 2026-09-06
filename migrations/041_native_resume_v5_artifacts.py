"""Register immutable Native Resume V5 DOCX/PDF artifact storage."""
from __future__ import annotations

import sqlite3

from app.native_resume_artifact_v5 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    """Apply the additive artifact schema to the migration-managed database."""
    ensure_schema(connection)
