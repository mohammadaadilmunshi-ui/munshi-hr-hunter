"""Immutable job/input bindings for strengthened Phase 4 resume versions.

This module is additive and preparation-only. It binds a generated resume to the
exact owned Hunter job snapshot and deterministic generation inputs used to create
it. It grants no browser, ATS, n8n, Gmail, outreach, or submission authority.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from app.database import get_connection
from app.phase67_common import safe_mapping_digest
from app.tenant_foundation import current_owner

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS native_resume_job_bindings (
        version_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
        generation_input_sha256 TEXT NOT NULL CHECK(length(generation_input_sha256)=64),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(version_id) REFERENCES native_resume_versions(version_id) ON DELETE RESTRICT,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_native_resume_job_bindings_owner_job
       ON native_resume_job_bindings(tenant_id,user_id,job_id,created_at DESC);""",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        from app import native_resume_service_v2

        native_resume_service_v2.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def generation_input_digest(values: Mapping[str, Any]) -> str:
    """Digest only deterministic, non-secret generation inputs."""
    return safe_mapping_digest(values, label="Resume generation inputs")


def save_resume_job_binding(
    connection: sqlite3.Connection,
    *,
    version_id: str,
    job_id: int,
    job_snapshot_sha256: str,
    generation_input_sha256: str,
) -> None:
    ensure_schema(connection)
    owner = current_owner(connection)
    connection.execute(
        """INSERT INTO native_resume_job_bindings(
               version_id,tenant_id,user_id,job_id,job_snapshot_sha256,generation_input_sha256
           ) VALUES (?,?,?,?,?,?)""",
        (
            str(version_id),
            owner.tenant_id,
            owner.user_id,
            int(job_id),
            str(job_snapshot_sha256),
            str(generation_input_sha256),
        ),
    )


def resume_job_binding(
    version_id: str,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM native_resume_job_bindings
               WHERE version_id=? AND tenant_id=? AND user_id=?""",
            (str(version_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        if owns:
            connection.close()


def assert_parent_job_current(parent_version_id: str, current_job_snapshot_sha256: str) -> None:
    binding = resume_job_binding(parent_version_id)
    if not binding:
        raise ValueError(
            "This older resume version is not job-snapshot-bound. Start a fresh strengthened resume before revising it."
        )
    if str(binding["job_snapshot_sha256"]) != str(current_job_snapshot_sha256):
        raise ValueError(
            "The stored job changed after this resume was created. Start a fresh resume from the current job snapshot."
        )
