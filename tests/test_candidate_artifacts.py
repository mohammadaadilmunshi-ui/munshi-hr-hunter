from __future__ import annotations

import importlib
import sqlite3

import pytest

from app.candidate_artifacts import clear_master_resume, ensure_schema, master_resume
from app.product_state import save_master_resume


def _result(connection, *, url: str = "https://docs.example.test/resume.pdf") -> int:
    job_id = connection.execute(
        """INSERT INTO jobs(job_fingerprint,source,company_name,title,location_raw,description_raw)
           VALUES ('artifact-job','fixture','Artifact Co','HR Analyst','NY','Evidence')"""
    ).lastrowid
    connection.execute(
        """INSERT INTO n8n_results(job_id,job_fingerprint,send_mode,n8n_status,resume_pdf_url)
           VALUES (?,?,?,?,?)""",
        (job_id, "artifact-result", "manual", "completed", url),
    )
    connection.commit()
    return int(job_id)


def test_master_designation_requires_a_stored_n8n_resume_and_keeps_source_unchanged(hunter_db) -> None:
    from app.database import get_connection

    connection = get_connection()
    try:
        job_id = _result(connection)
    finally:
        connection.close()
    with pytest.raises(ValueError, match="recorded in n8n results"):
        save_master_resume(job_id, "https://docs.example.test/not-recorded.pdf", "Unrecorded")
    save_master_resume(job_id, "https://docs.example.test/resume.pdf", "Candidate choice")
    record = master_resume()
    assert record["job_id"] == job_id
    assert record["url"] == "https://docs.example.test/resume.pdf"
    connection = get_connection()
    try:
        assert connection.execute("SELECT resume_pdf_url FROM n8n_results").fetchone()[0] == record["url"]
        assert connection.execute("SELECT COUNT(*) FROM candidate_artifacts").fetchone()[0] == 1
    finally:
        connection.close()
    clear_master_resume()
    assert master_resume() == {}
    connection = get_connection()
    try:
        assert connection.execute("SELECT COUNT(*) FROM candidate_artifacts").fetchone()[0] == 1
    finally:
        connection.close()


def test_legacy_n8n_artifacts_are_not_exposed_to_another_tenant(hunter_db, monkeypatch) -> None:
    from app.database import get_connection
    from app.tenant_foundation import owner_context

    connection = get_connection()
    try:
        job_id = _result(connection)
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES ('team-a','Team A')")
        connection.execute("INSERT INTO app_users(user_id,display_name) VALUES ('member-a','Member A')")
        connection.execute("INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES ('team-a','member-a','member')")
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")
    with owner_context(tenant_id="team-a", user_id="member-a"):
        with pytest.raises(ValueError, match="recorded in n8n results"):
            save_master_resume(job_id, "https://docs.example.test/resume.pdf", "Cross-tenant")
        assert master_resume() == {}


def test_legacy_explicit_designation_is_adopted_only_when_its_url_is_stored(hunter_db) -> None:
    from app.database import get_connection, save_setting

    connection = get_connection()
    try:
        job_id = _result(connection)
    finally:
        connection.close()
    save_setting(
        "candidate_master_resume_v1",
        {"job_id": job_id, "url": "https://docs.example.test/resume.pdf", "label": "Previous choice"},
        changed_by="legacy-test",
    )
    assert master_resume()["label"] == "Previous choice"
    clear_master_resume()
    assert master_resume() == {}


def test_legacy_explicit_designation_with_unstored_url_is_not_adopted(hunter_db) -> None:
    from app.database import get_connection, save_setting

    connection = get_connection()
    try:
        job_id = _result(connection)
    finally:
        connection.close()
    save_setting(
        "candidate_master_resume_v1",
        {"job_id": job_id, "url": "https://docs.example.test/not-stored.pdf", "label": "Invalid legacy choice"},
        changed_by="legacy-test",
    )
    assert master_resume() == {}


def test_migration_is_additive(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "upgraded.sqlite")
    try:
        importlib.import_module("migrations.017_candidate_artifacts").apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert {"candidate_artifacts", "candidate_artifact_designations"} <= tables


def _add_owner(connection, tenant_id: str, user_id: str) -> None:
    connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES (?,?)", (tenant_id, tenant_id))
    connection.execute("INSERT INTO app_users(user_id,display_name) VALUES (?,?)", (user_id, user_id))
    connection.execute(
        "INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES (?,?, 'member')",
        (tenant_id, user_id),
    )


def _add_artifact(connection, tenant_id: str, user_id: str, artifact_id: str) -> None:
    connection.execute(
        """INSERT INTO candidate_artifacts(
               artifact_id,tenant_id,user_id,artifact_kind,display_label,object_reference,
               source_system,source_result_id,source_field
           ) VALUES (?, ?, ?, 'resume_pdf', 'Resume', 'https://docs.example.test/resume.pdf',
                     'n8n_results', ?, 'resume_pdf_url')""",
        (artifact_id, tenant_id, user_id, 100 + len(artifact_id)),
    )


def test_designations_reject_cross_owner_artifact_insert_and_update(hunter_db) -> None:
    from app.database import get_connection

    connection = get_connection()
    try:
        _add_owner(connection, "team-a", "member-a")
        _add_owner(connection, "team-b", "member-b")
        _add_artifact(connection, "team-a", "member-a", "artifact-a")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO candidate_artifact_designations(
                       tenant_id,user_id,artifact_id,designation_kind,label,source_label
                   ) VALUES ('team-b','member-b','artifact-a','master_resume','Bad','test')"""
            )
        connection.execute(
            """INSERT INTO candidate_artifact_designations(
                   tenant_id,user_id,artifact_id,designation_kind,label,source_label
               ) VALUES ('team-a','member-a','artifact-a','master_resume','Good','test')"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """UPDATE candidate_artifact_designations
                      SET tenant_id='team-b', user_id='member-b'
                    WHERE artifact_id='artifact-a'"""
            )
    finally:
        connection.close()


def test_master_resume_owner_match_hides_fk_disabled_cross_owner_row(hunter_db) -> None:
    from app.database import get_connection
    from app.tenant_foundation import OwnerContext

    connection = get_connection()
    try:
        job_id = _result(connection)
        _add_owner(connection, "team-a", "member-a")
        _add_owner(connection, "team-b", "member-b")
        _add_artifact(connection, "team-a", "member-a", "artifact-a")
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """INSERT INTO candidate_artifact_designations(
                   tenant_id,user_id,artifact_id,designation_kind,label,source_label
               ) VALUES ('team-b','member-b','artifact-a','master_resume','Forged','test')"""
        )
        connection.commit()
        assert master_resume(
            connection=connection, owner=OwnerContext("team-b", "member-b")
        ) == {}
        assert job_id > 0
    finally:
        connection.close()


def test_legacy_designation_upgrade_preserves_history_and_rejects_mismatch_atomically(tmp_path) -> None:
    from app.tenant_foundation import ensure_schema as ensure_tenant_schema

    connection = sqlite3.connect(tmp_path / "legacy-artifacts.sqlite")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        ensure_tenant_schema(connection)
        connection.commit()
        connection.execute(
            """CREATE TABLE candidate_artifacts (
                artifact_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
                artifact_kind TEXT NOT NULL, display_label TEXT NOT NULL, object_reference TEXT NOT NULL,
                source_system TEXT NOT NULL, source_result_id INTEGER NOT NULL, source_field TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tenant_id,user_id,source_result_id,source_field),
                FOREIGN KEY (tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id)
            )"""
        )
        connection.execute(
            """CREATE TABLE candidate_artifact_designations (
                designation_id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL, designation_kind TEXT NOT NULL, label TEXT NOT NULL,
                source_label TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, designated_at TEXT NOT NULL,
                cleared_at TEXT, FOREIGN KEY (tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id),
                FOREIGN KEY (artifact_id) REFERENCES candidate_artifacts(artifact_id)
            )"""
        )
        _add_artifact(connection, "default", "local-owner", "legacy-artifact")
        _add_owner(connection, "team-b", "member-b")
        connection.execute(
            """INSERT INTO candidate_artifact_designations(
                   designation_id,tenant_id,user_id,artifact_id,designation_kind,label,source_label,
                   active,designated_at,cleared_at
               ) VALUES (41,'default','local-owner','legacy-artifact','master_resume','Legacy','old',0,
                         '2025-01-02T03:04:05Z','2025-01-03T03:04:05Z')"""
        )
        connection.commit()
        before = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='candidate_artifact_designations'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO candidate_artifact_designations(
                   tenant_id,user_id,artifact_id,designation_kind,label,source_label,active,designated_at
               ) VALUES ('team-b','member-b','legacy-artifact','master_resume','Bad','old',0,'2025-01-04')"""
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="cross-owner"):
            ensure_schema(connection)
        after = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='candidate_artifact_designations'"
        ).fetchone()[0]
        assert after == before
        assert connection.execute("SELECT COUNT(*) FROM candidate_artifact_designations").fetchone()[0] == 2
        connection.execute("DELETE FROM candidate_artifact_designations WHERE label='Bad'")
        connection.commit()
        ensure_schema(connection)
        preserved = connection.execute(
            "SELECT designation_id,label,designated_at,cleared_at FROM candidate_artifact_designations"
        ).fetchone()
        assert preserved == (41, "Legacy", "2025-01-02T03:04:05Z", "2025-01-03T03:04:05Z")

    finally:
        connection.close()
