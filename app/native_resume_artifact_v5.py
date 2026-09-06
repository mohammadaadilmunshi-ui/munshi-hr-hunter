"""Immutable DOCX/PDF artifact materialization for Stage B-bound Native Resume V5.

This module closes the gap between a validated resume document and the exact file
that an execution-ready Application Plan may reference. It performs no network,
browser, n8n, ATS, Gmail, or submission action. PDF materialization fails closed
unless the rendered file is exactly one page.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from app import native_resume_service_v5 as resume_v5
from app.database import get_connection
from app.tenant_foundation import current_owner

ARTIFACT_VERSION = "native-resume-v5-artifact-v1"

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS native_resume_v5_artifacts (
        artifact_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        version_id TEXT NOT NULL,
        artifact_version TEXT NOT NULL,
        artifact_kind TEXT NOT NULL CHECK(artifact_kind IN ('DOCX','PDF')),
        filename TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        sha256 TEXT NOT NULL CHECK(length(sha256)=64),
        byte_count INTEGER NOT NULL CHECK(byte_count > 0),
        page_count INTEGER,
        object_reference TEXT NOT NULL,
        content BLOB NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,version_id,artifact_kind),
        UNIQUE(tenant_id,user_id,object_reference),
        FOREIGN KEY(version_id) REFERENCES native_resume_versions(version_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_native_resume_v5_artifacts_owner_version
       ON native_resume_v5_artifacts(tenant_id,user_id,version_id,artifact_kind);""",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        resume_v5.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _artifact_id(tenant_id: str, user_id: str, version_id: str, kind: str) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}\0{user_id}\0{version_id}\0{kind}\0{ARTIFACT_VERSION}".encode("utf-8")
    ).hexdigest()
    return f"native-resume-artifact-{digest[:32]}"


def _current_resume(version_id: str) -> dict[str, Any]:
    record = resume_v5.get_version(version_id)
    if record.get("status") != "VALIDATED":
        raise RuntimeError("Native Resume V5 is not validated.")
    if record.get("candidate_truth_bound") is not True:
        raise RuntimeError("Native Resume V5 is not bound to Candidate Truth.")
    if record.get("job_snapshot_bound") is not True:
        raise RuntimeError("Native Resume V5 is not bound to the exact job snapshot.")
    if record.get("stage_b_bound") is not True:
        raise RuntimeError("Native Resume V5 is not bound to the Stage B tailoring plan.")

    snapshot = resume_v5.v4.truth_binding.current_candidate_profile_snapshot()
    binding = record.get("candidate_truth_binding") or {}
    if not resume_v5.v4.truth_binding.binding_matches_snapshot(binding, snapshot):
        raise RuntimeError("Candidate Truth changed after this resume was generated.")

    current_job = resume_v5.v4.safe_owned_job_snapshot(int(record["job_id"]))
    job_binding = record.get("job_snapshot_binding") or {}
    if str(job_binding.get("job_snapshot_sha256") or "") != str(
        current_job["job_snapshot_sha256"]
    ):
        raise RuntimeError("The stored job changed after this resume was generated.")

    stage_b = record.get("stage_b_binding") or {}
    plan_id = str(stage_b.get("plan_id") or "")
    if not plan_id or resume_v5.planner.plan_freshness(plan_id).get("fresh") is not True:
        raise RuntimeError("The Stage B tailoring plan is stale.")
    return record


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "artifact_id": str(row["artifact_id"]),
        "version_id": str(row["version_id"]),
        "artifact_version": str(row["artifact_version"]),
        "artifact_kind": str(row["artifact_kind"]),
        "filename": str(row["filename"]),
        "mime_type": str(row["mime_type"]),
        "sha256": str(row["sha256"]),
        "byte_count": int(row["byte_count"]),
        "page_count": int(row["page_count"]) if row["page_count"] is not None else None,
        "object_reference": str(row["object_reference"]),
        "immutable": True,
    }


def list_artifacts(version_id: str) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        rows = connection.execute(
            """SELECT * FROM native_resume_v5_artifacts
               WHERE tenant_id=? AND user_id=? AND version_id=?
               ORDER BY CASE artifact_kind WHEN 'DOCX' THEN 1 ELSE 2 END""",
            (owner.tenant_id, owner.user_id, str(version_id)),
        ).fetchall()
        return [_row_payload(row) for row in rows]
    finally:
        connection.close()


def materialize(version_id: str) -> dict[str, Any]:
    """Render and persist the exact immutable DOCX and one-page PDF for V5."""
    record = _current_resume(str(version_id))
    context = resume_v5.v4.v1.job_context(int(record["job_id"]))
    docx = resume_v5.v4.v1.version_docx(str(version_id))
    pdf, page_count = resume_v5.v4.v1.version_pdf(str(version_id))
    if int(page_count) != 1:
        raise ValueError(
            f"Native Resume V5 PDF must be exactly one page; renderer returned {int(page_count)} pages."
        )
    if not docx.startswith(b"PK"):
        raise RuntimeError("Native Resume V5 DOCX renderer returned an invalid document.")
    if not pdf.startswith(b"%PDF"):
        raise RuntimeError("Native Resume V5 PDF renderer returned an invalid document.")

    # Rendering may take time. Revalidate truth/job/Stage B before persistence so
    # an artifact cannot become current merely because generation started earlier.
    _current_resume(str(version_id))

    definitions = (
        (
            "DOCX",
            resume_v5.v4.v1.safe_filename(record, context, "docx"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            docx,
            None,
        ),
        (
            "PDF",
            resume_v5.v4.v1.safe_filename(record, context, "pdf"),
            "application/pdf",
            pdf,
            1,
        ),
    )

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        for kind, filename, mime_type, content, pages in definitions:
            digest = _sha(content)
            object_reference = f"hunter-native-resume://{version_id}/{kind.casefold()}"
            existing = connection.execute(
                """SELECT * FROM native_resume_v5_artifacts
                   WHERE tenant_id=? AND user_id=? AND version_id=? AND artifact_kind=?""",
                (owner.tenant_id, owner.user_id, str(version_id), kind),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["sha256"]) != digest
                    or int(existing["byte_count"]) != len(content)
                    or bytes(existing["content"]) != content
                    or (
                        (existing["page_count"] is None) != (pages is None)
                        or (
                            pages is not None
                            and int(existing["page_count"]) != int(pages)
                        )
                    )
                ):
                    raise RuntimeError(
                        "Immutable Native Resume V5 artifact content changed for the same version."
                    )
                continue
            connection.execute(
                """INSERT INTO native_resume_v5_artifacts(
                       artifact_id,tenant_id,user_id,version_id,artifact_version,
                       artifact_kind,filename,mime_type,sha256,byte_count,page_count,
                       object_reference,content
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _artifact_id(owner.tenant_id, owner.user_id, str(version_id), kind),
                    owner.tenant_id,
                    owner.user_id,
                    str(version_id),
                    ARTIFACT_VERSION,
                    kind,
                    filename,
                    mime_type,
                    digest,
                    len(content),
                    pages,
                    object_reference,
                    content,
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    artifacts = list_artifacts(str(version_id))
    by_kind = {item["artifact_kind"]: item for item in artifacts}
    if set(by_kind) != {"DOCX", "PDF"}:
        raise RuntimeError("Native Resume V5 artifact pair is incomplete.")
    return {
        "version": ARTIFACT_VERSION,
        "resume_version_id": str(version_id),
        "job_id": int(record["job_id"]),
        "candidate_truth_binding": record["candidate_truth_binding"],
        "job_snapshot_binding": record["job_snapshot_binding"],
        "stage_b_binding": record["stage_b_binding"],
        "docx": by_kind["DOCX"],
        "pdf": by_kind["PDF"],
        "page_verification": {"status": "PASS", "pdf_pages": 1},
        "submission_authority": False,
    }


def artifact_bytes(artifact_id: str) -> bytes:
    """Owner-scoped resolver for a later authenticated transport boundary."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT content FROM native_resume_v5_artifacts
               WHERE artifact_id=? AND tenant_id=? AND user_id=?""",
            (str(artifact_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Native Resume V5 artifact was not found for this owner.")
        return bytes(row["content"])
    finally:
        connection.close()
