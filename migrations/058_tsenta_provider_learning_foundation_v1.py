"""Add provider ownership and privacy-minimized Tsenta observation ledgers."""

from __future__ import annotations

import sqlite3

from app import application_provider_router_v1, tsenta_observation_v1


def apply(connection: sqlite3.Connection) -> None:
    application_provider_router_v1.ensure_schema(connection)
    tsenta_observation_v1.ensure_schema(connection)
