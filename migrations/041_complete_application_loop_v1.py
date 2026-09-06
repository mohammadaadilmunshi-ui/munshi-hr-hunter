"""Add immutable Native V5 artifacts and Application Plan V2 ledgers."""
from __future__ import annotations

import sqlite3

from app import application_plan_v2
from app import native_resume_artifact_v5


def apply(connection: sqlite3.Connection) -> None:
    native_resume_artifact_v5.ensure_schema(connection)
    application_plan_v2.ensure_schema(connection)
