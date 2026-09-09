"""Phase 9's local, signed preparation handoff boundary.

This module only creates and verifies inert handoff envelopes.  It deliberately
has no HTTP client, provider adapter, n8n, email, browser, or credential path.
An Apply-side receiver must independently decide whether it has authority to
act; accepting a package is never a submission.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4

from app.database import get_connection
from app.tenant_foundation import OwnerContext, current_owner, ensure_schema as ensure_tenant_schema


HANDOFF_VERSION = "munshi-apply-preparation-handoff-v1"
HANDOFF_STATES = frozenset({"PREPARED", "NEEDS_INPUT", "READY_TO_APPLY", "HANDOFF_ACCEPTED"})
_PROVIDERS = {"GREENHOUSE", "LEVER", "ASHBY", "SMARTRECRUITERS", "WORKDAY"}
SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS apply_preparation_handoffs (
        handoff_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        preparation_id TEXT NOT NULL, package_version TEXT NOT NULL, idempotency_key TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('PREPARED','NEEDS_INPUT','READY_TO_APPLY','HANDOFF_ACCEPTED')),
        provider TEXT NOT NULL, payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL,
        accepted_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        UNIQUE(tenant_id,user_id,idempotency_key),
        UNIQUE(tenant_id,user_id,preparation_id)
    );""",
    """CREATE INDEX IF NOT EXISTS idx_apply_preparation_handoffs_owner
       ON apply_preparation_handoffs(tenant_id,user_id,created_at DESC);""",
)


@dataclass(frozen=True)
class SignedHandoffTransport:
    """Canonical bytes/headers an authenticated Apply receiver can verify.

    This is intentionally a formatting primitive, not a transport client.
    """
    body: bytes
    headers: dict[str, str]


def apply_handoff_enabled() -> bool:
    return str(os.getenv("MUNSHI_APPLY_HANDOFF_ENABLED") or "").strip().casefold() in {"1", "true", "yes", "on"}


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


def _secret() -> bytes:
    secret = os.getenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET")
    if not secret or len(secret) < 16:
        raise RuntimeError("Apply handoff HMAC secret is not configured.")
    return secret.encode("utf-8")


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sign_payload(payload: Mapping[str, Any]) -> str:
    """Return an HMAC for a canonical package; never log it or the secret."""
    return hmac.new(_secret(), _canonical(payload), hashlib.sha256).hexdigest()


def sign_transport(payload: Mapping[str, Any], *, timestamp: int | None = None) -> SignedHandoffTransport:
    """Produce Apply-compatible freshness-bound HMAC headers without sending.

    The exact payload remains the shared Phase 9 package: ``handoff_id`` is the
    receiver's replay identity, and no generic event wrapper is introduced.
    """
    handoff_id = payload.get("handoff_id")
    if not isinstance(handoff_id, str) or not handoff_id:
        raise ValueError("Canonical handoff_id is required for signed transport.")
    body = _canonical(payload)
    body_hash = hashlib.sha256(body).hexdigest()
    created_at = str(int(time.time()) if timestamp is None else timestamp)
    signed = f"{handoff_id}.{created_at}.{body_hash}".encode("utf-8")
    signature = hmac.new(_secret(), signed, hashlib.sha256).hexdigest()
    return SignedHandoffTransport(
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-Munshi-Event-Id": handoff_id,
            "X-Munshi-Timestamp": created_at,
            "X-Munshi-Content-SHA256": body_hash,
            "X-Munshi-Signature": f"sha256={signature}",
        },
    )


def _provider(job: Mapping[str, Any]) -> str:
    url = " ".join(str(job.get(key) or "").casefold() for key in ("job_url", "apply_url"))
    for provider, markers in {
        "GREENHOUSE": ("greenhouse.io",), "LEVER": ("lever.co",), "ASHBY": ("ashbyhq.com",),
        "SMARTRECRUITERS": ("smartrecruiters.com",), "WORKDAY": ("myworkdayjobs.com", "workday.com"),
    }.items():
        if any(marker in url for marker in markers):
            return provider
    return "UNSUPPORTED_SAFE"


def _package_state(preparation: Mapping[str, Any]) -> str:
    status = str(preparation["status"])
    if status == "READY_TO_APPLY":
        return "READY_TO_APPLY"
    return "NEEDS_INPUT"


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def create_handoff(*, preparation_id: str, idempotency_key: str) -> dict[str, Any]:
    """Create an immutable tenant-bound package from a Phase 8 snapshot."""
    if not apply_handoff_enabled():
        raise RuntimeError("Apply handoff is disabled.")
    # Fail closed before creating any bridge record: an unsigned package is
    # never a valid handoff candidate.
    _secret()
    if not isinstance(preparation_id, str) or not preparation_id or len(preparation_id) > 120:
        raise ValueError("Preparation id is required.")
    if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 240:
        raise ValueError("Idempotency key is required.")
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        prior = connection.execute("SELECT * FROM apply_preparation_handoffs WHERE tenant_id=? AND user_id=? AND idempotency_key=?", (owner.tenant_id, owner.user_id, idempotency_key)).fetchone()
        if prior:
            result = _row_payload(prior)
            if result["preparation_id"] != preparation_id:
                raise ValueError("Idempotency key belongs to another preparation.")
            result["signature"] = sign_payload(result["payload"])
            return result
        preparation = connection.execute("SELECT * FROM native_application_preparations WHERE preparation_id=? AND tenant_id=? AND user_id=?", (preparation_id, owner.tenant_id, owner.user_id)).fetchone()
        if not preparation:
            raise LookupError("Preparation is not owned by the current tenant user.")
        snapshot = json.loads(preparation["snapshot_json"])
        job = snapshot.get("job")
        if not isinstance(job, dict):
            raise ValueError("Preparation snapshot is malformed.")
        provider = _provider(job)
        state = _package_state(preparation)
        payload = {"version": HANDOFF_VERSION, "handoff_id": str(uuid4()), "tenant_id": owner.tenant_id,
                   "user_id": owner.user_id, "preparation_id": preparation_id,
                   "application_id": preparation["application_id"], "job": {key: job.get(key) for key in ("id", "company_name", "title", "job_url", "apply_url")},
                   "provider": provider, "state": state,
                   "artifact_references": snapshot.get("resume"), "answers": snapshot.get("answers", []),
                   "provenance": {"preparation_version": json.loads(preparation["readiness_json"]).get("version"), "preparation_created_at": preparation["created_at"]}}
        encoded = _canonical(payload).decode("utf-8")
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        connection.execute("INSERT INTO apply_preparation_handoffs(handoff_id,tenant_id,user_id,preparation_id,package_version,idempotency_key,state,provider,payload_json,payload_digest) VALUES (?,?,?,?,?,?,?,?,?,?)", (payload["handoff_id"], owner.tenant_id, owner.user_id, preparation_id, HANDOFF_VERSION, idempotency_key, state, provider, encoded, digest))
        connection.commit()
        row = connection.execute("SELECT * FROM apply_preparation_handoffs WHERE handoff_id=?", (payload["handoff_id"],)).fetchone()
        assert row
        result = _row_payload(row)
        result["signature"] = sign_payload(payload)
        return result
    finally:
        connection.close()


def accept_handoff(*, payload: Mapping[str, Any], signature: str) -> dict[str, Any]:
    """Verify an inbound loopback handoff and record receipt, never execution."""
    if not apply_handoff_enabled():
        raise RuntimeError("Apply handoff is disabled.")
    if not isinstance(payload, Mapping) or not isinstance(signature, str):
        raise ValueError("Handoff payload and signature are required.")
    expected = sign_payload(payload)
    if not hmac.compare_digest(expected, signature):
        raise PermissionError("Handoff signature is invalid.")
    required = {"version", "handoff_id", "tenant_id", "user_id", "preparation_id", "application_id", "job", "provider", "state", "artifact_references", "answers", "provenance"}
    if set(payload) != required or payload.get("version") != HANDOFF_VERSION or payload.get("provider") not in _PROVIDERS | {"UNSUPPORTED_SAFE"} or payload.get("state") not in {"NEEDS_INPUT", "READY_TO_APPLY"}:
        raise ValueError("Handoff payload is unsupported or malformed.")
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if payload["tenant_id"] != owner.tenant_id or payload["user_id"] != owner.user_id:
            raise PermissionError("Handoff owner does not match current tenant user.")
        row = connection.execute("SELECT * FROM apply_preparation_handoffs WHERE handoff_id=? AND tenant_id=? AND user_id=?", (payload["handoff_id"], owner.tenant_id, owner.user_id)).fetchone()
        if not row:
            raise LookupError("Unknown handoff package.")
        stored = _row_payload(row)
        if not hmac.compare_digest(stored["payload_digest"], hashlib.sha256(_canonical(payload)).hexdigest()):
            raise PermissionError("Handoff payload does not match its immutable record.")
        if stored["state"] != "HANDOFF_ACCEPTED":
            connection.execute("UPDATE apply_preparation_handoffs SET state='HANDOFF_ACCEPTED', accepted_at=CURRENT_TIMESTAMP WHERE handoff_id=?", (payload["handoff_id"],))
            connection.commit()
        updated = connection.execute("SELECT * FROM apply_preparation_handoffs WHERE handoff_id=?", (payload["handoff_id"],)).fetchone()
        assert updated
        return _row_payload(updated)
    finally:
        connection.close()
