"""Tenant-scoped, evidence-backed index of generated candidate artifacts.

Legacy ``n8n_results`` rows remain the source of truth.  This module only
normalizes URL values that are already stored in those rows; it never uploads,
creates, probes, or deletes an artifact.  Because historic n8n rows predate
tenant ownership, they are available solely to the singleton default owner.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.database import get_connection, get_setting, save_setting
from app.tenant_foundation import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    OwnerContext,
    current_owner,
    ensure_schema as ensure_tenant_schema,
)


_N8N_ARTIFACT_COLUMNS = {
    "resume_doc_url": ("resume_doc", "Resume document"),
    "resume_pdf_url": ("resume_pdf", "Resume PDF"),
    "resume_docx_url": ("resume_docx", "Resume DOCX"),
    "cover_letter_doc_url": ("cover_letter_doc", "Cover letter document"),
    "cover_letter_pdf_url": ("cover_letter_pdf", "Cover letter PDF"),
    "google_sheet_url": ("supporting_sheet", "Generated worksheet"),
    "google_sheet_row_url": ("supporting_sheet", "Generated worksheet row"),
}

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS candidate_artifacts (
        artifact_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        artifact_kind TEXT NOT NULL,
        display_label TEXT NOT NULL,
        object_reference TEXT NOT NULL,
        source_system TEXT NOT NULL CHECK(source_system = 'n8n_results'),
        source_result_id INTEGER NOT NULL,
        source_field TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, user_id, source_result_id, source_field),
        UNIQUE(tenant_id, user_id, artifact_id),
        FOREIGN KEY (tenant_id, user_id)
            REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_candidate_artifacts_owner_kind
        ON candidate_artifacts(tenant_id, user_id, artifact_kind, created_at DESC);""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_artifacts_owner_artifact
        ON candidate_artifacts(tenant_id, user_id, artifact_id);""",
    """CREATE TABLE IF NOT EXISTS candidate_artifact_designations (
        designation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        designation_kind TEXT NOT NULL CHECK(designation_kind = 'master_resume'),
        label TEXT NOT NULL,
        source_label TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
        designated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        cleared_at TEXT,
        FOREIGN KEY (tenant_id, user_id)
            REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE RESTRICT,
        FOREIGN KEY (tenant_id, user_id, artifact_id)
            REFERENCES candidate_artifacts(tenant_id, user_id, artifact_id) ON DELETE RESTRICT
    );""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_master_resume_active
        ON candidate_artifact_designations(tenant_id, user_id, designation_kind)
        WHERE active = 1;""",
)

_DESIGNATION_COLUMNS = """designation_id,tenant_id,user_id,artifact_id,designation_kind,
    label,source_label,active,designated_at,cleared_at"""


def _has_composite_designation_fk(connection: sqlite3.Connection) -> bool:
    """Whether the existing designation table binds its artifact to its owner."""
    foreign_keys = connection.execute(
        "PRAGMA foreign_key_list(candidate_artifact_designations)"
    ).fetchall()
    groups: dict[int, dict[str, str]] = {}
    for foreign_key in foreign_keys:
        if foreign_key[2] != "candidate_artifacts":
            continue
        groups.setdefault(int(foreign_key[0]), {})[str(foreign_key[3])] = str(foreign_key[4])
    return any(
        group == {
            "tenant_id": "tenant_id",
            "user_id": "user_id",
            "artifact_id": "artifact_id",
        }
        for group in groups.values()
    )


def _upgrade_designation_ownership_fk(connection: sqlite3.Connection) -> None:
    """Replace the pre-Phase-3 child FK without losing designation history."""
    mismatch = connection.execute(
        """SELECT 1
             FROM candidate_artifact_designations AS d
             LEFT JOIN candidate_artifacts AS a
               ON a.tenant_id=d.tenant_id
              AND a.user_id=d.user_id
              AND a.artifact_id=d.artifact_id
            WHERE a.artifact_id IS NULL
            LIMIT 1"""
    ).fetchone()
    if mismatch is not None:
        raise sqlite3.IntegrityError(
            "Cannot upgrade candidate artifact designations with cross-owner or missing artifacts."
        )
    connection.execute(
        """CREATE TABLE candidate_artifact_designations_upgrade (
            designation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            designation_kind TEXT NOT NULL CHECK(designation_kind = 'master_resume'),
            label TEXT NOT NULL,
            source_label TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
            designated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            cleared_at TEXT,
            FOREIGN KEY (tenant_id, user_id)
                REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE RESTRICT,
            FOREIGN KEY (tenant_id, user_id, artifact_id)
                REFERENCES candidate_artifacts(tenant_id, user_id, artifact_id) ON DELETE RESTRICT
        );"""
    )
    connection.execute(
        f"""INSERT INTO candidate_artifact_designations_upgrade({_DESIGNATION_COLUMNS})
            SELECT {_DESIGNATION_COLUMNS} FROM candidate_artifact_designations"""
    )
    connection.execute("DROP TABLE candidate_artifact_designations")
    connection.execute(
        "ALTER TABLE candidate_artifact_designations_upgrade "
        "RENAME TO candidate_artifact_designations"
    )
    connection.execute(SCHEMA_STATEMENTS[-1])


def _verify_schema_integrity(connection: sqlite3.Connection) -> None:
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise sqlite3.IntegrityError("Foreign-key check failed after candidate artifact schema upgrade.")
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or str(integrity[0]).casefold() != "ok":
        raise sqlite3.DatabaseError("SQLite integrity check failed after candidate artifact schema upgrade.")


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        # A savepoint works both for startup's surrounding transaction and for
        # direct migration calls.  Unlike executescript, individual statements
        # preserve the caller's transaction and allow an invalid legacy upgrade
        # to roll back without a partial table rebuild.
        connection.execute("SAVEPOINT candidate_artifact_schema")
        try:
            ensure_tenant_schema(connection)
            for statement in SCHEMA_STATEMENTS[:-1]:
                connection.execute(statement)
            table_exists = connection.execute(
                """SELECT 1 FROM sqlite_master
                     WHERE type='table' AND name='candidate_artifact_designations'"""
            ).fetchone()
            upgraded = False
            if table_exists is not None and not _has_composite_designation_fk(connection):
                _upgrade_designation_ownership_fk(connection)
                upgraded = True
            else:
                connection.execute(SCHEMA_STATEMENTS[-1])
            if upgraded:
                _verify_schema_integrity(connection)
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT candidate_artifact_schema")
            connection.execute("RELEASE SAVEPOINT candidate_artifact_schema")
            raise
        connection.execute("RELEASE SAVEPOINT candidate_artifact_schema")
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def _is_stored_reference(value: Any) -> str:
    reference = str(value or "").strip()
    # A stored HTTP(S) reference is usable by the existing product link surface.
    # Do not turn arbitrary callback text or local paths into canonical artifacts.
    return reference if reference.startswith(("https://", "http://")) else ""


def _legacy_owner(owner: OwnerContext) -> bool:
    return owner.tenant_id == DEFAULT_TENANT_ID and owner.user_id == DEFAULT_USER_ID


def normalize_n8n_artifacts(connection: sqlite3.Connection, *, owner: OwnerContext | None = None) -> int:
    """Index actual historic n8n URL fields for the singleton owner only.

    The INSERT is idempotent and makes no changes to ``n8n_results``.  Tenant
    ownership cannot be inferred from legacy result rows, so a future tenant is
    deliberately denied this compatibility path until a trusted bridge exists.
    """
    ensure_schema(connection)
    resolved = owner or current_owner(connection)
    if not _legacy_owner(resolved):
        return 0
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(n8n_results)")}
    available = [(field, *details) for field, details in _N8N_ARTIFACT_COLUMNS.items() if field in columns]
    if not available:
        return 0
    selected = ", ".join(["id", *[f'"{field}"' for field, _, _ in available]])
    rows = connection.execute(f"SELECT {selected} FROM n8n_results ORDER BY id").fetchall()
    inserted = 0
    for row in rows:
        result_id = int(row["id"])
        for field, kind, label in available:
            reference = _is_stored_reference(row[field])
            if not reference:
                continue
            cursor = connection.execute(
                """INSERT OR IGNORE INTO candidate_artifacts(
                    artifact_id,tenant_id,user_id,artifact_kind,display_label,
                    object_reference,source_system,source_result_id,source_field
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (f"n8n-result-{result_id}-{field}", resolved.tenant_id, resolved.user_id,
                 kind, label, reference, "n8n_results", result_id, field),
            )
            inserted += int(cursor.rowcount > 0)
    return inserted


def _owner_artifact(connection: sqlite3.Connection, *, job_id: int, reference: str, owner: OwnerContext) -> dict[str, Any] | None:
    normalize_n8n_artifacts(connection, owner=owner)
    row = connection.execute(
        """SELECT a.* FROM candidate_artifacts a
           JOIN n8n_results r ON r.id=a.source_result_id
           WHERE a.tenant_id=? AND a.user_id=? AND r.job_id=?
             AND a.object_reference=? AND a.artifact_kind IN ('resume_pdf','resume_doc','resume_docx')
           ORDER BY a.source_result_id DESC
           LIMIT 1""",
        (owner.tenant_id, owner.user_id, int(job_id), reference),
    ).fetchone()
    return dict(row) if row else None


def designate_master_resume(*, job_id: int, reference: str, label: str, source_label: str) -> dict[str, Any]:
    """Explicitly designate one indexed resume.  No score or recency promotion occurs."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        artifact = _owner_artifact(connection, job_id=int(job_id), reference=_is_stored_reference(reference), owner=owner)
        if artifact is None:
            raise ValueError("Choose a resume URL recorded in n8n results before designating it as master.")
        connection.execute(
            """UPDATE candidate_artifact_designations
               SET active=0, cleared_at=CURRENT_TIMESTAMP
               WHERE tenant_id=? AND user_id=? AND designation_kind='master_resume' AND active=1""",
            (owner.tenant_id, owner.user_id),
        )
        connection.execute(
            """INSERT INTO candidate_artifact_designations(
                tenant_id,user_id,artifact_id,designation_kind,label,source_label
            ) VALUES (?,?,?,'master_resume',?,?)""",
            (owner.tenant_id, owner.user_id, artifact["artifact_id"],
             str(label or "Master resume").strip()[:240], str(source_label or "Candidate selection").strip()[:160]),
        )
        connection.commit()
        return master_resume(connection=connection, owner=owner)
    finally:
        connection.close()


def master_resume(*, connection: sqlite3.Connection | None = None, owner: OwnerContext | None = None) -> dict[str, Any]:
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        resolved = owner or current_owner(connection)
        row = connection.execute(
            """SELECT d.label,d.source_label,d.designated_at,a.artifact_id,a.object_reference,
                      a.source_result_id,a.source_field,r.job_id
                 FROM candidate_artifact_designations d
                 JOIN candidate_artifacts a ON a.artifact_id=d.artifact_id
                                           AND a.tenant_id=d.tenant_id
                                           AND a.user_id=d.user_id
                 JOIN n8n_results r ON r.id=a.source_result_id
                WHERE d.tenant_id=? AND d.user_id=? AND d.designation_kind='master_resume'
                  AND d.active=1
                LIMIT 1""",
            (resolved.tenant_id, resolved.user_id),
        ).fetchone()
        if row is None:
            # Before the artifact index existed, the singleton UI stored an
            # explicit selection in settings.  Adopt that choice once, but
            # only through the same stored-n8n-evidence gate used by new
            # designations.  Legacy settings have no tenant ownership, so
            # they must never become visible to a future tenant.
            if not _legacy_owner(resolved):
                return {}
            legacy = dict(get_setting("candidate_master_resume_v1", {}) or {})
            try:
                return designate_master_resume(
                    job_id=int(legacy["job_id"]),
                    reference=str(legacy["url"]),
                    label=str(legacy.get("label") or "Master resume"),
                    source_label=str(legacy.get("source") or "Legacy explicit designation"),
                )
            except (KeyError, TypeError, ValueError):
                return {}
        record = dict(row)
        return {"job_id": record["job_id"], "url": record["object_reference"], "label": record["label"],
                "source": record["source_label"], "designated_at": record["designated_at"],
                "artifact_id": record["artifact_id"]}
    finally:
        if owns_connection:
            connection.close()


def clear_master_resume() -> None:
    """Clear only the designation; the immutable artifact index is retained."""
    clear_legacy = False
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        connection.execute(
            """UPDATE candidate_artifact_designations
               SET active=0, cleared_at=CURRENT_TIMESTAMP
               WHERE tenant_id=? AND user_id=? AND designation_kind='master_resume' AND active=1""",
            (owner.tenant_id, owner.user_id),
        )
        connection.commit()
        clear_legacy = _legacy_owner(owner)
    finally:
        connection.close()
    if clear_legacy:
        # A retired singleton setting otherwise would be adopted again by the
        # next read after its indexed designation has been cleared.
        save_setting("candidate_master_resume_v1", {}, changed_by="candidate-artifacts")
