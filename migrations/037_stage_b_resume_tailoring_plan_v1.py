"""Add the immutable Stage B Resume Tailoring Plan ledger."""
from __future__ import annotations

import sqlite3

from app.jd_resume_plan_v1 import ensure_schema


def apply(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
