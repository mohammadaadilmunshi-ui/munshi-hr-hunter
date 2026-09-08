"""Hunter-only, synthetic-only submit command issuer (no execution path)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from app import application_plan_v2, application_review_v2
from app.database import get_connection
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema

COMMAND_VERSION = "munshi-synthetic-submit-command-v1"
COMMAND_PURPOSE = "SYNTHETIC_SUBMIT_COMMAND"
COMMAND_ENV = "MUNSHI_SYNTHETIC_SUBMIT_COMMAND_ENABLED"
COMMAND_SECRET_ENV = "MUNSHI_APPLICATION_SUBMIT_HMAC_SECRET"
SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS application_submit_commands_v1 (
        command_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        approval_id TEXT NOT NULL, review_id TEXT NOT NULL, plan_id TEXT NOT NULL,
        application_id TEXT NOT NULL, provider TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
        fixture_job_id INTEGER NOT NULL, target_url TEXT NOT NULL, idempotency_key TEXT NOT NULL,
        review_digest TEXT NOT NULL CHECK(length(review_digest)=64), approval_digest TEXT NOT NULL CHECK(length(approval_digest)=64),
        plan_digest TEXT NOT NULL CHECK(length(plan_digest)=64), prepared_package_digest TEXT NOT NULL CHECK(length(prepared_package_digest)=64),
        browser_form_digest TEXT NOT NULL CHECK(length(browser_form_digest)=64), resume_sha256 TEXT NOT NULL CHECK(length(resume_sha256)=64),
        cover_letter_sha256 TEXT CHECK(cover_letter_sha256 IS NULL OR length(cover_letter_sha256)=64),
        expires_at INTEGER NOT NULL, envelope_json TEXT NOT NULL, body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64), signature TEXT NOT NULL CHECK(length(signature)=64),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,idempotency_key), UNIQUE(tenant_id,user_id,approval_id),
        FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(review_id) REFERENCES application_reviews_v2(review_id) ON DELETE RESTRICT,
        FOREIGN KEY(approval_id) REFERENCES application_review_approvals_v2(approval_id) ON DELETE RESTRICT
    );""",
)

def enabled() -> bool:
    return str(os.getenv(COMMAND_ENV) or "").strip().casefold() in {"1", "true", "yes", "on"}

def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None; connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection); application_review_v2.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS: connection.execute(statement)
        if owns: connection.commit()
    finally:
        if owns: connection.close()

def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

def _digest(value: Any, label: str) -> str:
    value = str(value or "").strip().lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value): raise ValueError(f"{label} must be a lowercase SHA-256 digest.")
    return value

def _text(value: Any, label: str, maximum: int = 240) -> str:
    value = " ".join(str(value or "").split())
    if not value or len(value) > maximum: raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return value

def _secret() -> bytes:
    value = str(os.getenv(COMMAND_SECRET_ENV) or "")
    if len(value) < 32: raise RuntimeError("Synthetic submit command HMAC secret is not configured.")
    return value.encode()

def _target(value: Any) -> str:
    value = _text(value, "Synthetic target URL", 4000); parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.casefold().endswith(".invalid"): raise ValueError("Synthetic submit target must be HTTPS on a .invalid hostname.")
    return value

def _stored(row: sqlite3.Row) -> dict[str, Any]:
    body = str(row["envelope_json"]).encode()
    if hashlib.sha256(body).hexdigest() != str(row["body_sha256"]): raise RuntimeError("Stored synthetic submit command body digest is invalid.")
    signature = hmac.new(_secret(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, str(row["signature"])): raise RuntimeError("Stored synthetic submit command signature is invalid.")
    return {"command_id": str(row["command_id"]), "envelope": json.loads(body), "body_sha256": str(row["body_sha256"]), "signature": signature, "replayed": True}

def issue_submit_command(*, review_id: str, approval_id: str, idempotency_key: str, expected_review_digest: str, expected_approval_digest: str, expected_plan_digest: str, expected_prepared_package_digest: str, expected_browser_form_digest: str, now: int | None = None, ttl_seconds: int = 300) -> dict[str, Any]:
    """Issue/replay one signed command; it never submits or exposes a route."""
    if not enabled() or not application_review_v2.application_review_v2_enabled(): raise RuntimeError("Synthetic submit commands are disabled.")
    _secret(); key = _text(idempotency_key, "Submit command idempotency key")
    expected = {"review_digest": _digest(expected_review_digest, "Expected review digest"), "approval_digest": _digest(expected_approval_digest, "Expected approval digest"), "plan_digest": _digest(expected_plan_digest, "Expected plan digest"), "prepared_package_digest": _digest(expected_prepared_package_digest, "Expected prepared package digest"), "browser_form_digest": _digest(expected_browser_form_digest, "Expected browser form digest")}
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= 600: raise ValueError("Synthetic submit command expiry must be 1..600 seconds.")
    command_id = "synthetic-submit-command-" + str(uuid4()); issued = int(time.time()) if now is None else int(now); expires = issued + ttl_seconds
    connection = get_connection()
    try:
        ensure_schema(connection); owner = current_owner(connection)
        if connection.in_transaction: connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute("SELECT * FROM application_submit_commands_v1 WHERE tenant_id=? AND user_id=? AND idempotency_key=?", (owner.tenant_id, owner.user_id, key)).fetchone()
        if prior is not None:
            if str(prior["approval_id"]) != str(approval_id) or any(str(prior[name]) != expected[name] for name in expected): raise ValueError("Submit command idempotency key belongs to different bindings.")
            connection.commit(); return _stored(prior)
        locked_review = connection.execute("SELECT * FROM application_reviews_v2 WHERE review_id=? AND tenant_id=? AND user_id=?", (str(review_id), owner.tenant_id, owner.user_id)).fetchone()
        if locked_review is None: raise PermissionError("Application review is not owned by the current tenant user.")
        if locked_review["invalidated_at"] is not None or any(str(locked_review[name]) != expected[name] for name in ("review_digest", "plan_digest", "prepared_package_digest", "browser_form_digest")): raise ValueError("Application review changed or is stale.")
        snapshot = json.loads(str(locked_review["review_json"]))
        if application_review_v2.sha256_json(snapshot) != str(locked_review["review_digest"]): raise RuntimeError("Stored application review digest is invalid.")
        bindings = dict(snapshot.get("bindings") or {})
        target = _target(snapshot.get("destination_url")); fixture_job_id = int(dict(snapshot.get("job") or {}).get("id"))
        approval = connection.execute("""SELECT a.*,r.invalidated_at FROM application_review_approvals_v2 a JOIN application_reviews_v2 r ON r.review_id=a.review_id WHERE a.approval_id=? AND a.tenant_id=? AND a.user_id=?""", (str(approval_id), owner.tenant_id, owner.user_id)).fetchone()
        if approval is None or str(approval["review_id"]) != str(review_id): raise PermissionError("Submit approval is not owned by the current review owner.")
        if approval["invalidated_at"] is not None or approval["consumed_at"] is not None: raise ValueError("Submit approval is unavailable.")
        if any(str(approval[name]) != expected[name] for name in ("review_digest", "approval_digest", "plan_digest", "prepared_package_digest", "browser_form_digest")): raise ValueError("Submit approval bindings changed.")
        if application_plan_v2.runtime_plan_is_superseded(str(locked_review["plan_id"])) or application_plan_v2.application_plan_freshness(str(locked_review["plan_id"])).get("fresh") is not True: raise ValueError("Application Plan is stale or superseded.")
        fixture = connection.execute("""SELECT 1 FROM staging_synthetic_jobs s JOIN jobs j ON j.id=s.job_id
            WHERE s.job_id=? AND s.environment='staging' AND s.synthetic=1 AND s.is_test_data=1
              AND s.external_actions_disabled=1 AND s.source='staging_fixture' AND j.apply_url=?""", (fixture_job_id, target)).fetchone()
        if fixture is None: raise PermissionError("Submit command requires an owned staging synthetic fixture.")
        envelope = {"version": COMMAND_VERSION, "purpose": COMMAND_PURPOSE, "command_id": command_id, "tenant_id": owner.tenant_id, "user_id": owner.user_id, "application_id": str(locked_review["application_id"]), "plan_id": str(locked_review["plan_id"]), "provider": str(snapshot["provider"]), "review_id": str(review_id), "approval_id": str(approval_id), "synthetic": True, "submission_authority": True, "fixture_job_id": fixture_job_id, "target_url": target, "checkpoint_id": str(bindings["checkpoint_id"]), "review_digest": expected["review_digest"], "approval_digest": expected["approval_digest"], "plan_digest": expected["plan_digest"], "prepared_package_digest": expected["prepared_package_digest"], "browser_form_digest": expected["browser_form_digest"], "resume_sha256": _digest(bindings["resume_artifact_sha256"], "Resume digest"), "cover_letter_sha256": bindings.get("cover_letter_artifact_sha256"), "issued_at": issued, "expires_at": expires}
        if envelope["cover_letter_sha256"] is not None: envelope["cover_letter_sha256"] = _digest(envelope["cover_letter_sha256"], "Cover letter digest")
        body = _canonical(envelope); digest = hashlib.sha256(body).hexdigest(); signature = hmac.new(_secret(), body, hashlib.sha256).hexdigest()
        if connection.execute("UPDATE application_review_approvals_v2 SET consumed_at=CURRENT_TIMESTAMP,consumption_key=? WHERE approval_id=? AND consumed_at IS NULL", (command_id, str(approval_id))).rowcount != 1: raise ValueError("Submit approval was consumed before command issuance.")
        connection.execute("""INSERT INTO application_submit_commands_v1(command_id,tenant_id,user_id,approval_id,review_id,plan_id,application_id,provider,checkpoint_id,fixture_job_id,target_url,idempotency_key,review_digest,approval_digest,plan_digest,prepared_package_digest,browser_form_digest,resume_sha256,cover_letter_sha256,expires_at,envelope_json,body_sha256,signature) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (command_id,owner.tenant_id,owner.user_id,str(approval_id),str(review_id),str(locked_review["plan_id"]),str(locked_review["application_id"]),str(snapshot["provider"]),str(bindings["checkpoint_id"]),fixture_job_id,target,key,expected["review_digest"],expected["approval_digest"],expected["plan_digest"],expected["prepared_package_digest"],expected["browser_form_digest"],envelope["resume_sha256"],envelope["cover_letter_sha256"],expires,body.decode(),digest,signature))
        connection.commit(); return {"command_id": command_id, "envelope": envelope, "body_sha256": digest, "signature": signature, "replayed": False}
    except Exception:
        connection.rollback(); raise
    finally: connection.close()
