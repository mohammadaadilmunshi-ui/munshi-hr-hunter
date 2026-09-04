from __future__ import annotations

import importlib
import sqlite3

from app import database
from app.tenant_foundation import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    OwnerContext,
    associate_owned_record,
    current_owner,
    owner_context,
)


def test_fresh_database_has_stable_singleton_owner(hunter_db) -> None:
    connection = database.get_connection()
    try:
        assert current_owner(connection) == OwnerContext(DEFAULT_TENANT_ID, DEFAULT_USER_ID)
        membership = connection.execute(
            "SELECT role FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
            (DEFAULT_TENANT_ID, DEFAULT_USER_ID),
        ).fetchone()
    finally:
        connection.close()
    assert membership["role"] == "owner"


def test_owned_record_association_is_idempotent_and_never_reassigned(hunter_db) -> None:
    connection = database.get_connection()
    try:
        associate_owned_record(connection, record_domain="candidate_profile_fact", record_key="headline")
        associate_owned_record(connection, record_domain="candidate_profile_fact", record_key="headline")
        rows = connection.execute("SELECT tenant_id,user_id FROM owned_record_owners").fetchall()
    finally:
        connection.close()
    assert [dict(row) for row in rows] == [{"tenant_id": DEFAULT_TENANT_ID, "user_id": DEFAULT_USER_ID}]


def test_owner_context_is_inert_without_feature_flag(hunter_db) -> None:
    with owner_context(tenant_id="another-tenant", user_id="another-user"):
        assert current_owner() == current_owner()
        assert current_owner().user_id == DEFAULT_USER_ID


def test_enabled_owner_context_requires_a_provisioned_membership(hunter_db, monkeypatch) -> None:
    connection = database.get_connection()
    try:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES ('team-a','Team A')")
        connection.execute("INSERT INTO app_users(user_id,display_name) VALUES ('member-a','Member A')")
        connection.execute(
            "INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES ('team-a','member-a','member')"
        )
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")
    with owner_context(tenant_id="team-a", user_id="member-a"):
        assert current_owner() == OwnerContext("team-a", "member-a")


def test_enabled_owner_context_preserves_caller_rollback(hunter_db, monkeypatch) -> None:
    connection = database.get_connection()
    try:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES ('team-a','Team A')")
        connection.execute("INSERT INTO app_users(user_id,display_name) VALUES ('member-a','Member A')")
        connection.execute(
            "INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES ('team-a','member-a','member')"
        )
        connection.commit()
        monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")
        with owner_context(tenant_id="team-a", user_id="member-a"):
            connection.execute("BEGIN")
            associate_owned_record(
                connection, record_domain="candidate_profile_fact", record_key="rollback-check"
            )
            connection.rollback()
        owner = connection.execute(
            "SELECT 1 FROM owned_record_owners WHERE record_domain=? AND record_key=?",
            ("candidate_profile_fact", "rollback-check"),
        ).fetchone()
    finally:
        connection.close()
    assert owner is None


def test_lazy_schema_install_does_not_commit_a_caller_transaction() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("BEGIN")
        associate_owned_record(connection, record_domain="candidate_profile_fact", record_key="lazy")
        assert connection.in_transaction
        connection.rollback()
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='owned_record_owners'"
        ).fetchone()
    finally:
        connection.close()
    assert table is None


def test_migration_is_additive(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "upgraded.sqlite")
    try:
        importlib.import_module("migrations.015_tenant_foundation").apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert {"tenants", "app_users", "tenant_memberships", "owned_record_owners"} <= tables
