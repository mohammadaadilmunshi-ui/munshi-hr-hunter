"""Add strictly verified, append-only LinkedIn-to-employer ATS URL evidence.

This migration creates no inferred links, grants no submit authority, and
never overwrites the canonical jobs.job_url or jobs.apply_url fields.
"""

from __future__ import annotations

import sqlite3

from app.linkedin_verified_url_evidence_v5 import initialize_schema


def apply(connection: sqlite3.Connection) -> None:
    initialize_schema(connection, allow_migration=True)
