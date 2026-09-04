from __future__ import annotations

import importlib
import sqlite3

from app import database


def test_fresh_initialization_has_product_vault_and_gmail_schema(hunter_db) -> None:
    connection = database.get_connection()
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally: connection.close()
    assert {"product_job_state", "auto_prepare_lanes", "gmail_messages", "credential_secret", "gmail_integration_state", "gmail_oauth_state"} <= tables


def test_product_migration_is_complete_and_additive(tmp_path) -> None:
    path = tmp_path / "upgraded.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY)")
        module = importlib.import_module("migrations.014_product_ui_v1")
        module.apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally: connection.close()
    assert {"product_job_state", "auto_prepare_lanes", "gmail_messages", "credential_secret", "gmail_integration_state", "gmail_oauth_state"} <= tables
