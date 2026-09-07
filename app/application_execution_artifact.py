"""Shared, preparation-only resume artifact contract for Apply execution.

Hunter owns this projection.  It deliberately accepts n8n only when its
authoritative callback supplies an immutable, job-bound artifact; it never
infers Native provenance or turns preparation into submission authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Mapping
from urllib.parse import urlsplit

from app import native_resume_artifact_v5
from app import native_resume_service_v5 as resume_v5
from app.database import get_connection
from app.tenant_foundation import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    current_owner,
    ensure_schema as ensure_tenant_schema,
    tenant_foundation_enabled,
)


SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS application_execution_artifacts (
        execution_artifact_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        engine TEXT NOT NULL CHECK(engine IN ('native','n8n')),
        artifact_id TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256)=64),
        artifact_reference TEXT NOT NULL,
        version_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,application_id),
        UNIQUE(tenant_id,user_id,engine,artifact_id),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_application_execution_artifacts_owner_job
       ON application_execution_artifacts(tenant_id,user_id,job_id,created_at DESC);""",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _required(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"Execution artifact requires {name}.")
    return result


def native(*, application_id: str, version_id: str) -> dict[str, Any]:
    """Normalize one already-validated immutable Native V5 artifact pair."""
    materialized = native_resume_artifact_v5.materialize(version_id)
    version = resume_v5.get_version(version_id)
    pdf = materialized["pdf"]
    return persist({
        "application_id": _required(application_id, "application_id"),
        "job_id": int(materialized.get("job_id") or version.get("job_id") or 0),
        "engine": "native",
        "artifact_id": pdf["artifact_id"],
        "version_id": str(version_id),
        "version_number": int(version["version_number"]),
        "filename": pdf["filename"],
        "mime_type": pdf["mime_type"],
        "artifact_reference": pdf["object_reference"],
        "artifact_sha256": pdf["sha256"],
        "page_count": int(pdf["page_count"]),
        "candidate_truth_binding": materialized["candidate_truth_binding"],
        "job_snapshot_binding": materialized["job_snapshot_binding"],
        # Native version creation is immutable; a wall-clock projection time
        # would make a retry look like a different artifact binding.
        "created_at": str(version.get("created_at") or materialized.get("created_at") or "native-v5"),
        "submission_authority": False,
    })


def n8n(*, application_id: str, job_id: int, callback: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize an authoritative n8n callback without copying plaintext bytes."""
    if str(callback.get("engine") or "n8n").casefold() != "n8n":
        raise ValueError("n8n execution artifact callback has invalid engine provenance.")
    if int(callback.get("job_id") or 0) != int(job_id):
        raise ValueError("n8n execution artifact is bound to another job.")
    result = {
        "application_id": _required(application_id, "application_id"),
        "job_id": int(job_id),
        "engine": "n8n",
        "artifact_id": _required(callback.get("artifact_id"), "artifact_id"),
        "version_id": _required(callback.get("version_id"), "version_id"),
        "version_number": int(callback.get("version_number") or 0),
        "filename": _required(callback.get("filename"), "filename"),
        "mime_type": _required(callback.get("mime_type"), "mime_type"),
        "artifact_reference": _required(callback.get("artifact_reference"), "artifact_reference"),
        "artifact_sha256": _required(callback.get("artifact_sha256"), "artifact_sha256"),
        "page_count": int(callback.get("page_count") or 0),
        "candidate_truth_binding": callback.get("candidate_truth_binding"),
        "job_snapshot_binding": callback.get("job_snapshot_binding"),
        "created_at": _required(callback.get("created_at"), "created_at"),
        "submission_authority": False,
    }
    reference = str(result["artifact_reference"])
    parsed_reference = urlsplit(reference)
    if (
        not _SHA256_RE.fullmatch(result["artifact_sha256"])
        or result["mime_type"] != "application/pdf"
        or parsed_reference.scheme != "hunter-n8n"
        or not parsed_reference.netloc
        or result["page_count"] != 1
    ):
        raise ValueError("n8n execution artifact must provide an exact one-page immutable PDF.")
    if not isinstance(result["candidate_truth_binding"], Mapping) or not isinstance(
        result["job_snapshot_binding"], Mapping
    ):
        raise ValueError("n8n execution artifact lacks Candidate Truth or job binding.")
    return result


def _canonical_payload(artifact: Mapping[str, Any]) -> str:
    return json.dumps(dict(artifact), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _owned_job(connection: sqlite3.Connection, *, job_id: int, tenant_id: str, user_id: str) -> None:
    job = connection.execute("SELECT 1 FROM jobs WHERE id=?", (int(job_id),)).fetchone()
    if job is None:
        raise LookupError("Execution artifact job is unavailable.")
    row = connection.execute(
        """SELECT tenant_id,user_id FROM owned_record_owners
           WHERE record_domain='job' AND record_key=CAST(? AS TEXT)
        """,
        (int(job_id),),
    ).fetchone()
    if row is not None and (str(row["tenant_id"]) != tenant_id or str(row["user_id"]) != user_id):
        raise PermissionError("Execution artifact job is not owned by the current tenant user.")
    if row is None and (
        tenant_foundation_enabled()
        or tenant_id != DEFAULT_TENANT_ID
        or user_id != DEFAULT_USER_ID
    ):
        raise PermissionError("Execution artifact job is not owned by the current tenant user.")


def persist(
    artifact: Mapping[str, Any], *, connection: sqlite3.Connection | None = None
) -> dict[str, Any]:
    """Durably bind one immutable artifact to one owned application identity.

    Replays must be byte-for-byte equivalent; an application cannot be silently
    rebound to a different artifact, engine, job, or Candidate Truth snapshot.
    """
    normalized = dict(artifact)
    application_id = _required(normalized.get("application_id"), "application_id")
    job_id = int(normalized.get("job_id") or 0)
    if job_id <= 0:
        raise ValueError("Execution artifact requires a positive job_id.")
    engine = str(normalized.get("engine") or "").casefold()
    if engine not in {"native", "n8n"}:
        raise ValueError("Execution artifact has invalid engine provenance.")
    if normalized.get("submission_authority") is not False:
        raise ValueError("Execution artifacts never grant submission authority.")

    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        if owns:
            if connection.in_transaction:
                connection.commit()
            connection.execute("BEGIN IMMEDIATE")
        owner = current_owner(connection)
        _owned_job(connection, job_id=job_id, tenant_id=owner.tenant_id, user_id=owner.user_id)
        payload_json = _canonical_payload(normalized)
        existing = connection.execute(
            """SELECT execution_artifact_id,payload_json FROM application_execution_artifacts
               WHERE tenant_id=? AND user_id=? AND application_id=?""",
            (owner.tenant_id, owner.user_id, application_id),
        ).fetchone()
        if existing is not None:
            if str(existing["payload_json"]) != payload_json:
                raise ValueError("Application identity is already bound to a different execution artifact.")
            result = dict(normalized)
            result["execution_artifact_id"] = str(existing["execution_artifact_id"])
            return result

        execution_artifact_id = (
            "execution-artifact-"
            + hashlib.sha256(
                f"{owner.tenant_id}\0{owner.user_id}\0{application_id}".encode("utf-8")
            ).hexdigest()[:32]
        )
        connection.execute(
            """INSERT INTO application_execution_artifacts(
                   execution_artifact_id,tenant_id,user_id,application_id,job_id,engine,
                   artifact_id,artifact_sha256,artifact_reference,version_id,payload_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                execution_artifact_id, owner.tenant_id, owner.user_id, application_id, job_id, engine,
                _required(normalized.get("artifact_id"), "artifact_id"),
                _required(normalized.get("artifact_sha256"), "artifact_sha256"),
                _required(normalized.get("artifact_reference"), "artifact_reference"),
                _required(normalized.get("version_id"), "version_id"), payload_json,
            ),
        )
        if owns:
            connection.commit()
        result = dict(normalized)
        result["execution_artifact_id"] = execution_artifact_id
        return result
    except Exception:
        if owns:
            connection.rollback()
        raise
    finally:
        if owns:
            connection.close()
