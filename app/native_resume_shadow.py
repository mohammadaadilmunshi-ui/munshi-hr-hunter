"""Internal-only planning ledger for the future native resume shadow engine.

This module deliberately has no renderer, model client, queue, callback, or
n8n integration.  It records a tenant-owned *plan* only when shadow mode is
explicitly enabled.  In particular, it cannot create an artifact or become an
authority path.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any
from uuid import uuid4

from app.database import get_connection
from app.tenant_foundation import OwnerContext, current_owner, ensure_schema as ensure_tenant_schema


_SENSITIVE_FACT_TOKENS = frozenset({
    "age", "birth", "citizen", "disability", "ethnicity", "gender", "marital",
    "race", "religion", "sex", "veteran",
})
_STOP_TERMS = frozenset({
    "about", "and", "are", "for", "from", "has", "have", "into", "job", "our",
    "role", "that", "the", "this", "with", "you", "your",
})
_TERM_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+/#.-]{2,}")

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS native_resume_shadow_runs (
        shadow_run_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        artifact_id TEXT NOT NULL,
        baseline_n8n_result_id INTEGER NOT NULL,
        idempotency_key TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('NEEDS_INPUT', 'BLOCKED_EXTERNAL')),
        exact_terms_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        prompt_plan_json TEXT NOT NULL,
        missing_fields_json TEXT NOT NULL,
        comparison_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, user_id, idempotency_key),
        FOREIGN KEY (tenant_id, user_id)
            REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE RESTRICT,
        FOREIGN KEY (tenant_id, user_id, artifact_id)
            REFERENCES candidate_artifacts(tenant_id, user_id, artifact_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_native_resume_shadow_owner_job
        ON native_resume_shadow_runs(tenant_id, user_id, job_id, created_at DESC);""",
)


def native_resume_shadow_enabled() -> bool:
    return str(os.getenv("MUNSHI_NATIVE_RESUME_SHADOW_ENABLED") or "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def native_resume_authority_enabled() -> bool:
    """Phase 4 has no authority branch; this remains false even if configured."""
    return False


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        # Candidate artifacts are the only legal reference boundary here.
        from app.candidate_artifacts import ensure_schema as ensure_candidate_artifact_schema
        from app.candidate_digital_twin import ensure_schema as ensure_candidate_digital_twin_schema
        ensure_candidate_artifact_schema(connection)
        ensure_candidate_digital_twin_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _terms(description: str) -> list[str]:
    """A deterministic, audit-friendly exact-term ledger; no semantic inference."""
    found: dict[str, str] = {}
    for match in _TERM_PATTERN.finditer(str(description or "")):
        term = match.group(0)
        key = term.casefold()
        if key not in _STOP_TERMS:
            found.setdefault(key, term)
    return [found[key] for key in sorted(found)]


def _text(value: str, label: str, maximum: int = 20_000) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return result


def _artifact_baseline(connection: sqlite3.Connection, owner: OwnerContext, artifact_id: str, job_id: int) -> dict[str, Any]:
    # The join repeats owner predicates defensively, so a forged FK-disabled row
    # is never readable as another candidate's artifact/baseline.
    row = connection.execute(
        """SELECT a.artifact_id, a.source_result_id, r.id AS baseline_n8n_result_id,
                  r.final_ats_score, r.n8n_status
             FROM candidate_artifacts AS a
             JOIN n8n_results AS r ON r.id=a.source_result_id AND r.job_id=?
            WHERE a.tenant_id=? AND a.user_id=? AND a.artifact_id=?
              AND a.source_system='n8n_results'""",
        (int(job_id), owner.tenant_id, owner.user_id, artifact_id),
    ).fetchone()
    if row is None:
        raise LookupError("Artifact does not belong to the current candidate or has no known n8n baseline.")
    return dict(row)


def _evidence(connection: sqlite3.Connection, owner: OwnerContext) -> list[dict[str, str]]:
    rows = connection.execute(
        """SELECT f.fact_key, f.value_json, f.provenance, e.source_reference, e.excerpt
             FROM candidate_digital_twin_facts AS f
             JOIN candidate_digital_twin_evidence AS e
               ON e.fact_id=f.fact_id AND e.tenant_id=f.tenant_id AND e.user_id=f.user_id
            WHERE f.tenant_id=? AND f.user_id=? AND f.user_confirmed=1
            ORDER BY f.fact_key, e.evidence_id""",
        (owner.tenant_id, owner.user_id),
    ).fetchall()
    evidence: list[dict[str, str]] = []
    for row in rows:
        key = str(row["fact_key"])
        if any(token in key.casefold() for token in _SENSITIVE_FACT_TOKENS):
            continue
        evidence.append({
            "fact_key": key,
            "value_json": str(row["value_json"]),
            "provenance": str(row["provenance"]),
            "source_reference": str(row["source_reference"]),
            "excerpt": str(row["excerpt"] or ""),
        })
    return evidence


def _run_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    for field in ("exact_terms_json", "evidence_json", "prompt_plan_json", "missing_fields_json", "comparison_json"):
        payload[field.removesuffix("_json")] = json.loads(payload.pop(field))
    return payload


def plan_shadow_resume(*, job_id: int, artifact_id: str, job_description: str, idempotency_key: str) -> dict[str, Any]:
    """Persist an honest planning/telemetry record, never a generated resume.

    Disabled mode rejects before schema work or writes.  The implementation has
    no model or physical renderer yet, therefore no call can report success.
    """
    if not native_resume_shadow_enabled():
        raise RuntimeError("Native resume shadow mode is disabled.")
    key = _text(idempotency_key, "Idempotency key", 200)
    description = _text(job_description, "Job description")
    reference = _text(artifact_id, "Artifact ID", 200)
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        prior = connection.execute(
            """SELECT s.* FROM native_resume_shadow_runs AS s
               JOIN candidate_artifacts AS a
                 ON a.tenant_id=s.tenant_id AND a.user_id=s.user_id AND a.artifact_id=s.artifact_id
              WHERE s.tenant_id=? AND s.user_id=? AND s.idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if prior is not None:
            return _run_payload(prior)
        baseline = _artifact_baseline(connection, owner, reference, int(job_id))
        evidence = _evidence(connection, owner)
        terms = _terms(description)
        missing = [] if evidence else ["confirmed_non_sensitive_evidence"]
        status = "NEEDS_INPUT" if missing else "BLOCKED_EXTERNAL"
        prompt_plan = {
            "kind": "native_resume_shadow_prompt_plan_v1",
            "exact_terms": terms,
            "evidence_count": len(evidence),
            "model_status": "BLOCKED_EXTERNAL",
            "renderer_status": "BLOCKED_EXTERNAL",
        }
        comparison = {
            "baseline_n8n_result_id": baseline["baseline_n8n_result_id"],
            "baseline_ats_score": baseline["final_ats_score"],
            "baseline_status": baseline["n8n_status"],
            "native_artifact": None,
            "native_ats_score": None,
            "page_count": None,
            "integrity": "not_generated",
        }
        run_id = str(uuid4())
        connection.execute(
            """INSERT INTO native_resume_shadow_runs(
                   shadow_run_id,tenant_id,user_id,job_id,artifact_id,baseline_n8n_result_id,idempotency_key,
                   status,exact_terms_json,evidence_json,prompt_plan_json,missing_fields_json,comparison_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, owner.tenant_id, owner.user_id, int(job_id), reference,
             baseline["baseline_n8n_result_id"], key, status, _json(terms), _json(evidence),
             _json(prompt_plan), _json(missing), _json(comparison)),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM native_resume_shadow_runs WHERE shadow_run_id=? AND tenant_id=? AND user_id=?",
            (run_id, owner.tenant_id, owner.user_id),
        ).fetchone()
        assert row is not None
        return _run_payload(row)
    finally:
        connection.close()
