"""Disabled-by-default, local application-readiness planning ledger.

This is a preparation contract, not an executor.  It creates an immutable,
tenant-owned snapshot for one defensively owned Hunter job and contains no
transport, credential, browser, messaging, callback, or authority surface.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Mapping, Sequence
from uuid import uuid4

from app.database import get_connection
from app.tenant_foundation import OwnerContext, current_owner, ensure_schema as ensure_tenant_schema


NATIVE_APPLICATION_PREPARATION_VERSION = "native-application-preparation-v1"
PREPARATION_STATUSES = frozenset({"NEEDS_INPUT", "BLOCKED_EXTERNAL", "READY_TO_APPLY"})
_ACCOUNT_STATES = frozenset({"READY", "CREATABLE", "NEEDS_INPUT", "BLOCKED_EXTERNAL"})
_BINARY_STATES = frozenset({"PASS", "FAIL", "NEEDS_INPUT", "BLOCKED_EXTERNAL"})
_YES_NO_STATES = frozenset({"YES", "NO", "NEEDS_INPUT", "BLOCKED_EXTERNAL"})

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS native_application_preparations (
        preparation_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL, application_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('NEEDS_INPUT','BLOCKED_EXTERNAL','READY_TO_APPLY')),
        readiness_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        UNIQUE(tenant_id,user_id,idempotency_key),
        UNIQUE(tenant_id,user_id,job_id,application_id)
    );""",
    """CREATE INDEX IF NOT EXISTS idx_native_application_preparations_owner_job
        ON native_application_preparations(tenant_id,user_id,job_id,created_at DESC);""",
)


def native_application_preparation_enabled() -> bool:
    return str(os.getenv("MUNSHI_NATIVE_APPLICATION_PREPARATION_ENABLED") or "").strip().casefold() in {"1", "true", "yes", "on"}


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def _enabled_owner(connection: sqlite3.Connection) -> OwnerContext:
    if not native_application_preparation_enabled():
        raise RuntimeError("Native application preparation is disabled.")
    return current_owner(connection)


def _text(value: Any, label: str, maximum: int = 240) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return result


def _choice(value: Any, choices: frozenset[str], label: str) -> str:
    result = str(value or "").strip().upper()
    if result not in choices:
        raise ValueError(f"Unsupported {label}.")
    return result


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["readiness"] = json.loads(result.pop("readiness_json"))
    result["snapshot"] = json.loads(result.pop("snapshot_json"))
    return result


def _owned_job(connection: sqlite3.Connection, owner: OwnerContext, job_id: int) -> dict[str, Any]:
    row = connection.execute(
        """SELECT j.id,j.company_name,j.title,j.job_url,j.apply_url,j.status
             FROM jobs AS j JOIN owned_record_owners AS o
               ON o.record_domain='job' AND o.record_key=CAST(j.id AS TEXT)
            WHERE j.id=? AND o.tenant_id=? AND o.user_id=?""",
        (job_id, owner.tenant_id, owner.user_id),
    ).fetchone()
    if row is None:
        raise LookupError("Job is not defensively associated with the current user.")
    return dict(row)


def _answer_requests(values: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    from app.answer_brain import NON_PLAINTEXT_FAMILIES, resolve_answer

    if values is None:
        return []
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError("Answer requests must be a list.")
    result: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, Mapping) or set(item) - {"question_family", "conditions", "profile_fact_key"}:
            raise ValueError("Answer request fields are unsupported.")
        family = _text(item.get("question_family"), "Question family", 100).casefold()
        if family in NON_PLAINTEXT_FAMILIES:
            raise ValueError("Sensitive or credential questions are outside this preparation contract.")
        conditions = item.get("conditions")
        if conditions is not None and not isinstance(conditions, dict):
            raise ValueError("Answer conditions must be an object.")
        resolved = resolve_answer(question_family=family, conditions=conditions, profile_fact_key=item.get("profile_fact_key"))
        # Do not copy free-form answers into this ledger.  The vault remains
        # authoritative; the preparation snapshot retains resolution metadata.
        result.append({"question_family": family, "conditions": conditions or {}, "status": resolved["status"], "resolution": resolved.get("resolution"), "reason": resolved.get("reason")})
    return result


def _master_resume(connection: sqlite3.Connection, owner: OwnerContext) -> dict[str, Any]:
    from app.candidate_artifacts import master_resume

    record = master_resume(connection=connection, owner=owner)
    if not record:
        return {"status": "NEEDS_INPUT", "artifact_id": None}
    return {"status": "READY", "artifact_id": record["artifact_id"], "source_job_id": record["job_id"]}


def _contacts(connection: sqlite3.Connection, owner: OwnerContext, job_id: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT c.contact_id,c.display_name,c.title,c.source,c.email_provenance
             FROM relationship_contacts AS c JOIN relationship_contact_job_links AS l
               ON l.contact_id=c.contact_id AND l.tenant_id=c.tenant_id AND l.user_id=c.user_id
            WHERE l.job_id=? AND c.tenant_id=? AND c.user_id=? ORDER BY c.created_at DESC""",
        (job_id, owner.tenant_id, owner.user_id),
    ).fetchall()
    # Explicitly exclude contact email values and inferred patterns.
    return [dict(row) for row in rows]


def prepare_application(*, job_id: int, application_id: str, idempotency_key: str,
                        opportunity: Mapping[str, Any], answer_requests: Sequence[Mapping[str, Any]] | None = None,
                        eligibility: str = "NEEDS_INPUT", account_state: str = "NEEDS_INPUT",
                        permissions: str = "NEEDS_INPUT", duplicate_application: str = "NEEDS_INPUT",
                        job_open: str = "NEEDS_INPUT") -> dict[str, Any]:
    """Assemble a deterministic readiness snapshot; it never executes an action."""
    if not isinstance(opportunity, Mapping):
        raise ValueError("Opportunity must be an object.")
    try:
        job_id = int(job_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Job id must be an integer.") from error
    if job_id <= 0:
        raise ValueError("Job id must be positive.")
    application_id = _text(application_id, "Application id")
    idempotency_key = _text(idempotency_key, "Idempotency key")
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = _enabled_owner(connection)
        # `ensure_schema`/owner resolution may install the additive tenant
        # defaults.  Finish that local schema transaction before the advisory
        # Career Policy service opens its own owner-scoped SQLite connection.
        # Keeping it open creates a writer-lock cycle during first use.
        connection.commit()
        job = _owned_job(connection, owner, job_id)
        prior = connection.execute("SELECT * FROM native_application_preparations WHERE tenant_id=? AND user_id=? AND idempotency_key=?", (owner.tenant_id, owner.user_id, idempotency_key)).fetchone()
        if prior is not None:
            prior_payload = _payload(prior)
            if prior_payload["job_id"] != job_id or prior_payload["application_id"] != application_id:
                raise ValueError("Idempotency key belongs to a different job or application.")
            return prior_payload
        duplicate = connection.execute("SELECT 1 FROM native_application_preparations WHERE tenant_id=? AND user_id=? AND job_id=? AND application_id=?", (owner.tenant_id, owner.user_id, job_id, application_id)).fetchone()
        if duplicate is not None:
            raise ValueError("Application identity already has a preparation snapshot.")
        from app.career_policy import evaluate_opportunity
        policy = evaluate_opportunity(opportunity)
        answers = _answer_requests(answer_requests)
        resume = _master_resume(connection, owner)
        eligibility = _choice(eligibility, _BINARY_STATES, "eligibility")
        account_state = _choice(account_state, _ACCOUNT_STATES, "account state")
        permissions = _choice(permissions, _BINARY_STATES, "permissions")
        duplicate_application = _choice(duplicate_application, _YES_NO_STATES, "duplicate application")
        job_open = _choice(job_open, _YES_NO_STATES, "job open")
        checks = {
            "eligibility": eligibility, "opportunity_policy": policy["status"], "resume": resume["status"],
            # An absent or empty request list cannot establish that the target
            # application has no required questions.  Keep that uncertainty
            # explicit until the caller supplies resolved question evidence.
            "answers": "READY" if answers and all(item["status"] == "ANSWERED" for item in answers) else "NEEDS_INPUT",
            "required_files": resume["status"], "account": account_state, "permissions": permissions,
            "duplicate_application": duplicate_application, "job_open": job_open,
        }
        external = any(value == "BLOCKED_EXTERNAL" for value in checks.values())
        ready = (checks["eligibility"] == "PASS" and checks["opportunity_policy"] == "PASS" and checks["resume"] == "READY" and checks["answers"] == "READY" and checks["required_files"] == "READY" and checks["account"] in {"READY", "CREATABLE"} and checks["permissions"] == "PASS" and checks["duplicate_application"] == "NO" and checks["job_open"] == "YES")
        status = "READY_TO_APPLY" if ready else ("BLOCKED_EXTERNAL" if external else "NEEDS_INPUT")
        readiness = {"version": NATIVE_APPLICATION_PREPARATION_VERSION, "status": status, "checks": checks, "ready": ready}
        snapshot = {"job": job, "application_id": application_id, "resume": resume, "answers": answers, "contacts": _contacts(connection, owner, job_id), "policy": {key: policy[key] for key in ("status", "pursuit_state", "hard_failures", "unknowns")}}
        preparation_id = str(uuid4())
        connection.execute("INSERT INTO native_application_preparations(preparation_id,tenant_id,user_id,job_id,application_id,idempotency_key,status,readiness_json,snapshot_json) VALUES (?,?,?,?,?,?,?,?,?)", (preparation_id, owner.tenant_id, owner.user_id, job_id, application_id, idempotency_key, status, _json(readiness), _json(snapshot)))
        connection.commit()
        row = connection.execute("SELECT * FROM native_application_preparations WHERE preparation_id=? AND tenant_id=? AND user_id=?", (preparation_id, owner.tenant_id, owner.user_id)).fetchone()
        assert row is not None
        return _payload(row)
    finally:
        connection.close()
