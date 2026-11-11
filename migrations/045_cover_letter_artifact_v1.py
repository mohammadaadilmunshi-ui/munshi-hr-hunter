"""Install the immutable Hunter Cover Letter Artifact V1 schema."""

from __future__ import annotations

import sqlite3

from app.cover_letter_artifact_v1 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
