"""Atomic Stage B bindings for native resume V5 versions.

Binds one immutable resume version to the exact Stage B tailoring plan and claim
trace used/produced during generation. This sidecar has no browser, ATS, Gmail,
n8n, outreach, or submission authority.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from app import jd_claim_trace_v1 as claim_trace
from app import jd_resume_plan_v1 as planner
from app.database import get_connection
from app.tenant_foundation import current_owner

BINDING_VERSION = "stage-b-native-resume-binding-v1"
SUBMISSION_AUTHORITY = False

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS native_resume_stage_b_bindings (
        version_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
        plan_id TEXT NOT NULL,
        plan_digest TEXT NOT NULL CHECK(length(plan_digest)=64),
        jd_snapshot_id TEXT NOT NULL,
        jd_snapshot_digest TEXT NOT NULL CHECK(length(jd_snapshot_digest)=64),
        match_snapshot_id TEXT NOT NULL,
        match_digest TEXT NOT NULL CHECK(length(match_digest)=64),
        trace_id TEXT NOT NULL,
        trace_digest TEXT NOT NULL CHECK(length(trace_digest)=64),
        writer_context_sha256 TEXT NOT NULL CHECK(length(writer_context_sha256)=64),
        binding_version TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(version_id) REFERENCES native_resume_versions(version_id) ON DELETE RESTRICT,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
        FOREIGN KEY(plan_id) REFERENCES jd_resume_tailoring_plans(plan_id) ON DELETE RESTRICT,
        FOREIGN KEY(trace_id) REFERENCES jd_resume_claim_traces(trace_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_native_resume_stage_b_owner_job
       ON native_resume_stage_b_bindings(tenant_id,user_id,job_id,created_at DESC);""",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        planner.ensure_schema(connection)
        claim_trace.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def save_binding(
    connection: sqlite3.Connection,
    *,
    version_id: str,
    plan: Mapping[str, Any],
    trace: Mapping[str, Any],
    writer_context_sha256: str,
) -> None:
    owner = current_owner(connection)
    if str(plan.get("tenant_id")) != owner.tenant_id or str(plan.get("user_id")) != owner.user_id:
        raise ValueError("Stage B resume plan owner does not match the active candidate.")
    if str(trace.get("tenant_id")) != owner.tenant_id or str(trace.get("user_id")) != owner.user_id:
        raise ValueError("Stage B claim-trace owner does not match the active candidate.")
    if str(trace.get("plan_id")) != str(plan.get("plan_id")):
        raise ValueError("Stage B claim trace belongs to a different tailoring plan.")
    if str(trace.get("resume_version_id")) != str(version_id):
        raise ValueError("Stage B claim trace belongs to a different resume version.")
    if len(str(writer_context_sha256)) != 64:
        raise ValueError("Stage B writer context digest must be a SHA-256.")

    connection.execute(
        """INSERT INTO native_resume_stage_b_bindings(
               version_id,tenant_id,user_id,job_id,job_snapshot_sha256,
               plan_id,plan_digest,jd_snapshot_id,jd_snapshot_digest,
               match_snapshot_id,match_digest,trace_id,trace_digest,
               writer_context_sha256,binding_version
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(version_id),
            owner.tenant_id,
            owner.user_id,
            int(plan["job_id"]),
            str(plan["job_snapshot_sha256"]),
            str(plan["plan_id"]),
            str(plan["plan_digest"]),
            str(plan["jd_snapshot_id"]),
            str(plan["jd_snapshot_digest"]),
            str(plan["match_snapshot_id"]),
            str(plan["match_digest"]),
            str(trace["trace_id"]),
            str(trace["trace_digest"]),
            str(writer_context_sha256),
            BINDING_VERSION,
        ),
    )


def resume_stage_b_binding(
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
            """SELECT * FROM native_resume_stage_b_bindings
               WHERE version_id=? AND tenant_id=? AND user_id=?""",
            (str(version_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        if owns:
            connection.close()


def assert_parent_plan_current(parent_version_id: str, plan: Mapping[str, Any]) -> None:
    binding = resume_stage_b_binding(parent_version_id)
    if not binding:
        raise ValueError(
            "This parent resume is not Stage-B-bound. Start a fresh V5 resume before revising it."
        )
    if str(binding["plan_digest"]) != str(plan.get("plan_digest") or ""):
        raise ValueError(
            "Stage B tailoring intelligence changed after this resume was created. Start a fresh V5 resume."
        )
    if str(binding["job_snapshot_sha256"]) != str(plan.get("job_snapshot_sha256") or ""):
        raise ValueError("Stage B parent resume is bound to a different job snapshot.")
