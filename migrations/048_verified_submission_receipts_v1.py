"""Add verified Apply receipt evidence and Hunter CRM lifecycle authority."""
from __future__ import annotations

import sqlite3

from app import application_submission_receipt_v1


def apply(connection: sqlite3.Connection) -> None:
    application_submission_receipt_v1.ensure_schema(connection)
