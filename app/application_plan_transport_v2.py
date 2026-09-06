"""Signed, replay-identifiable transport envelope for Application Plan V2.

This is a source-only transport boundary. It creates canonical bytes and HMAC
headers for a later Apply receiver but intentionally contains no HTTP client and
performs no browser, ATS, n8n, Gmail, credential, or submission action.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Any
from uuid import uuid4

from app import application_plan_v2
from app import apply_handoff
from app.database import get_connection
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema

TRANSPORT_VERSION = "munshi-application-plan-handoff-v2"
LIVE_HANDOFF_ENV = "MUNSHI_APPLY_LIVE_HANDOFF_ENABLED"

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS apply_plan_handoffs_v2 (
        handoff_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        transport_version TEXT NOT NULL,
        plan_digest TEXT NOT NULL CHECK(length(plan_digest)=64),
        body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64),
        envelope_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('READY_TO_APPLY','PLAN_ACCEPTED')),
        accepted_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,idempotency_key),
        UNIQUE(tenant_id,user_id,plan_id),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(plan_id) REFERENCES application_plans_v2(plan_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_apply_plan_handoffs_v2_owner_application
       ON apply_plan_handoffs_v2(tenant_id,user_id,application_id,created_at DESC);""",
)


def live_handoff_enabled() -> bool:
    return str(os.getenv(LIVE_HANDOFF_ENV) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        application_plan_v2.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _envelope(plan: dict[str, Any], handoff_id: str) -> dict[str, Any]:
    snapshot = dict(plan["snapshot"])
    provider = str(plan["provider"]).upper()
    return {
        "version": TRANSPORT_VERSION,
        "handoff_id": handoff_id,
        "tenant_id": str(plan["tenant_id"]),
        "user_id": str(plan["user_id"]),
        "application_id": str(plan["application_id"]),
        "plan_id": str(plan["plan_id"]),
        "plan_digest": str(plan["plan_digest"]),
        "provider": provider,
        "state": "READY_TO_APPLY",
        "content_contract": {
            "application_plan_version": application_plan_v2.PLAN_VERSION,
            "receiver_min_version": 2,
            "receiver_max_version": 2,
        },
        "plan": snapshot,
        "submission_authority": False,
    }


def create_signed_plan_transport(
    *,
    plan_id: str,
    idempotency_key: str,
    timestamp: int | None = None,
) -> apply_handoff.SignedHandoffTransport:
    """Create/replay the exact signed transport for one executable current plan."""
    if not live_handoff_enabled():
        raise RuntimeError("Application Plan live handoff is disabled.")
    # Fail closed before any ledger write if the shared signing secret is absent.
    apply_handoff._secret()
    key = str(idempotency_key or "").strip()
    if not key or len(key) > 240:
        raise ValueError("Application Plan handoff idempotency key is required.")
    plan = application_plan_v2.executable_plan(str(plan_id))

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if str(plan["tenant_id"]) != owner.tenant_id or str(plan["user_id"]) != owner.user_id:
            raise PermissionError("Application Plan owner does not match current tenant user.")
        prior = connection.execute(
            """SELECT * FROM apply_plan_handoffs_v2
               WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if prior is not None:
            if str(prior["plan_id"]) != str(plan_id):
                raise ValueError("Idempotency key belongs to another Application Plan handoff.")
            payload = json.loads(str(prior["envelope_json"]))
            expected_body = _canonical(payload)
            if hashlib.sha256(expected_body).hexdigest() != str(prior["body_sha256"]):
                raise RuntimeError("Stored Application Plan handoff body digest is invalid.")
            return apply_handoff.sign_transport(payload, timestamp=timestamp)

        handoff_id = f"plan-handoff-{uuid4()}"
        payload = _envelope(plan, handoff_id)
        body = _canonical(payload)
        digest = hashlib.sha256(body).hexdigest()
        connection.execute(
            """INSERT INTO apply_plan_handoffs_v2(
                   handoff_id,tenant_id,user_id,plan_id,application_id,provider,
                   idempotency_key,transport_version,plan_digest,body_sha256,
                   envelope_json,state
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'READY_TO_APPLY')""",
            (
                handoff_id,
                owner.tenant_id,
                owner.user_id,
                str(plan_id),
                str(plan["application_id"]),
                str(plan["provider"]).upper(),
                key,
                TRANSPORT_VERSION,
                str(plan["plan_digest"]),
                digest,
                body.decode("utf-8"),
            ),
        )
        connection.commit()
        return apply_handoff.sign_transport(payload, timestamp=timestamp)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record_plan_accepted(
    *,
    handoff_id: str,
    plan_id: str,
    plan_digest: str,
) -> dict[str, Any]:
    """Record only a correlated Apply ACK. It is expressly not submission."""
    if not live_handoff_enabled():
        raise RuntimeError("Application Plan live handoff is disabled.")
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM apply_plan_handoffs_v2
               WHERE handoff_id=? AND tenant_id=? AND user_id=?""",
            (str(handoff_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Application Plan handoff was not found for this owner.")
        if str(row["plan_id"]) != str(plan_id) or str(row["plan_digest"]) != str(plan_digest):
            raise PermissionError("Application Plan ACK does not match the immutable handoff.")
        if str(row["state"]) != "PLAN_ACCEPTED":
            connection.execute(
                """UPDATE apply_plan_handoffs_v2
                   SET state='PLAN_ACCEPTED',accepted_at=CURRENT_TIMESTAMP
                   WHERE handoff_id=?""",
                (str(handoff_id),),
            )
            connection.commit()
        updated = connection.execute(
            "SELECT * FROM apply_plan_handoffs_v2 WHERE handoff_id=?",
            (str(handoff_id),),
        ).fetchone()
        assert updated is not None
        return {
            "handoff_id": str(updated["handoff_id"]),
            "plan_id": str(updated["plan_id"]),
            "state": str(updated["state"]),
            "submission_authority": False,
        }
    finally:
        connection.close()
