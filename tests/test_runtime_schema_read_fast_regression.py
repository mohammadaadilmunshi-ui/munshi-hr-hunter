from __future__ import annotations

from app import database, product_state, tenant_foundation
from app.native_resume_service import ensure_schema as ensure_native_resume_schema


def test_ready_runtime_schema_guards_are_read_only(hunter_db) -> None:
    connection = database.get_connection()
    try:
        # database.initialize_database() installs the tenant/product schemas,
        # but native_resume_service remains an additive service-local schema.
        # Install that service schema once, then prove every high-frequency
        # guard is read-only once its schema is actually ready.
        ensure_native_resume_schema(connection)
        connection.commit()
        assert connection.in_transaction is False

        connection.execute("PRAGMA query_only = ON")

        tenant_foundation.ensure_schema(connection)
        assert connection.in_transaction is False

        product_state.ensure_schema(connection)
        assert connection.in_transaction is False

        ensure_native_resume_schema(connection)
        assert connection.in_transaction is False
    finally:
        connection.close()
