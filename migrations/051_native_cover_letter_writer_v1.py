"""Add Native Cover Letter Writer V1 audit authority."""
from __future__ import annotations

import sqlite3

from app import native_cover_letter_writer_v1


def apply(connection: sqlite3.Connection) -> None:
    native_cover_letter_writer_v1.ensure_schema(connection)
