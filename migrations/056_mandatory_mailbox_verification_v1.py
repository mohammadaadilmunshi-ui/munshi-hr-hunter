"""Add encrypted mailbox verification artifacts, audit, and content columns."""
from __future__ import annotations

import sqlite3

from app import inbound_mail, mailbox_verification_service


def apply(connection: sqlite3.Connection) -> None:
    # This migration makes the mailbox runtime schema explicit in the normal
    # Hunter migration sequence. The helpers are idempotent for older databases.
    inbound_mail.ensure_schema(connection)
    mailbox_verification_service.ensure_schema(connection)
