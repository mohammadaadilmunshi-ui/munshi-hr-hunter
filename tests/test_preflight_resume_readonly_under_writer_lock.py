"""Regression: read-only preflight must not install schema while a writer exists."""
from __future__ import annotations

import json
import sqlite3

from app import (
    application_preflight_package_v1 as preflight,
    native_resume_service as v1,
    native_resume_service_v4 as v4,
    phase4_job_binding as job_binding,
    phase45_truth_binding as truth_binding,
    tenant_foundation,
)


def test_resume_preflight_reads_during_concurrent_writer_without_schema_writes(
    monkeypatch, tmp_path
):
    path = tmp_path / "hunter-locked.sqlite"
    tenant = tenant_foundation.DEFAULT_TENANT_ID
    user = tenant_foundation.DEFAULT_USER_ID
    setup = sqlite3.connect(path)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.executescript("""
        CREATE TABLE native_resume_versions (
          version_id TEXT,tenant_id TEXT,user_id TEXT,job_id INTEGER,
          version_number INTEGER,html_sha256 TEXT,status TEXT,
          document_json TEXT,diagnostics_json TEXT,locked_sections_json TEXT
        );
        CREATE TABLE native_resume_truth_bindings (
          version_id TEXT,tenant_id TEXT,user_id TEXT,
          source_extraction_id TEXT,profile_revision INTEGER,profile_digest TEXT,
          source_profile_sha256 TEXT,source_resume_sha256 TEXT
        );
        CREATE TABLE native_resume_job_bindings (
          version_id TEXT,tenant_id TEXT,user_id TEXT,
          job_id INTEGER,job_snapshot_sha256 TEXT,generation_input_sha256 TEXT
        );
    """)
    setup.execute(
        "INSERT INTO native_resume_versions VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("version-1", tenant, user, 4465, 2, "a" * 64, "VALIDATED",
         json.dumps({}), json.dumps({}), json.dumps([])),
    )
    setup.execute(
        "INSERT INTO native_resume_truth_bindings VALUES (?,?,?,?,?,?,?,?)",
        ("version-1", tenant, user, "extract-1", 55, "b" * 64,
         "c" * 64, "d" * 64),
    )
    setup.execute(
        "INSERT INTO native_resume_job_bindings VALUES (?,?,?,?,?,?)",
        ("version-1", tenant, user, 4465, "e" * 64, "f" * 64),
    )
    setup.commit()

    def readonly_connection():
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    # Every getter must succeed even with no possibility of implicit DDL/INSERT.
    monkeypatch.setattr(tenant_foundation, "tenant_foundation_enabled", lambda: False)
    for module in (v1, truth_binding, job_binding):
        monkeypatch.setattr(module, "get_connection", readonly_connection)
        monkeypatch.setattr(
            module, "ensure_schema",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("Preflight attempted to install schema")
            ),
        )

    writer = sqlite3.connect(path, timeout=1)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE native_resume_versions SET status='VALIDATED'")
    try:
        reference = preflight._resume_reference("version-1")
        assert reference["version_id"] == "version-1"
        assert reference["version_number"] == 2
        assert reference["rendered_resume_sha256"] == "a" * 64
        assert reference["candidate_truth_binding"]["profile_revision"] == 55
        assert reference["job_snapshot_binding"]["job_snapshot_sha256"] == "e" * 64

        # Legacy callers retain their existing lazy-migration behavior; they
        # are not silently switched to a weaker or different persistence model.
        try:
            v4.get_version("version-1")
        except AssertionError as exc:
            assert "install schema" in str(exc)
        else:
            raise AssertionError("Legacy path should still call ensure_schema")
    finally:
        writer.rollback()
        writer.close()
        setup.close()
