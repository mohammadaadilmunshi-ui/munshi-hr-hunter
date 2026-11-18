"""Resume Studio should not attempt schema writes on every library read."""
import sqlite3

import pytest

from app.resume_read_schema_v1 import ensure_existing_read_table


def test_existing_resume_table_never_calls_schema_initializer():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE native_resume_versions(version_id TEXT PRIMARY KEY)")
    con.execute("PRAGMA query_only=ON")

    def forbidden(_connection):
        pytest.fail("Read-only Resume Studio view must not trigger DDL")

    ensure_existing_read_table(con, "native_resume_versions", forbidden)
    assert con.execute("PRAGMA query_only").fetchone()[0] == 1
    con.close()


def test_new_schema_still_uses_original_initializer():
    con = sqlite3.connect(":memory:")
    called = []

    def initialize(connection):
        called.append(True)
        connection.execute("CREATE TABLE native_resume_versions(version_id TEXT PRIMARY KEY)")

    ensure_existing_read_table(con, "native_resume_versions", initialize)
    ensure_existing_read_table(con, "native_resume_versions", initialize)
    assert called == [True]
    con.close()


def test_schema_identifier_must_be_literal_table():
    con = sqlite3.connect(":memory:")
    with pytest.raises(ValueError):
        ensure_existing_read_table(con, "versions; DROP TABLE jobs", lambda c: None)
    con.close()
