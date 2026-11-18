"""Opt-in, disposable UI test boundary. Never imported by the production app."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys

DATABASE = Path("/app/hunter/data/uiux-synthetic.db")


def assert_stage() -> None:
    from app.staging_feature_policy import isolated_staging

    if os.getenv("MUNSHI_UIUX_STAGE") != "synthetic-only" or not isolated_staging():
        raise RuntimeError("UI test entrypoint requires the isolated synthetic staging contract")
    if Path(os.environ.get("DATABASE_PATH", "")).resolve() != DATABASE:
        raise RuntimeError("UI staging refuses any database outside its disposable synthetic path")
    if Path("/app/hunter/.env").exists():
        raise RuntimeError("UI staging refuses an application environment file")


def install() -> None:
    assert_stage()
    from app import database

    if getattr(database.get_connection, "_uiux_guard", False):
        return
    original = database.get_connection

    def authorizer(action, table, column, db, trigger):
        if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
            if any(word in str(table).casefold() for word in ("dispatch", "outbox", "submit_command", "prepare_task", "prepare_operation", "oauth", "lifecycle")):
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def connection():
        assert_stage()
        result = original()
        result.set_authorizer(authorizer)
        return result

    connection._uiux_guard = True
    database.get_connection = connection

    def audit(event, args):
        if event in {"socket.connect", "subprocess.Popen", "os.system", "smtplib.connect"}:
            raise PermissionError("External operations and worker execution are disabled in synthetic UI staging")

    sys.addaudithook(audit)
