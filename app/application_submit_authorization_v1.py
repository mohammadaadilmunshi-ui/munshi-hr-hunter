"""Canonical production submit authorization for autonomous MUNSHI Apply.

This module is the sole Hunter-side authority for crossing the irreversible
browser-submit boundary.  A worker request and local final-submit flag are only
intent/configuration; they are never sufficient authority by themselves.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping
from uuid import uuid4

from app import application_plan_v2, application_review_v2
from app.database import get_connection

AUTH_VERSION = "munshi-submit-authorization-v1"
AUTH_TTL_SECONDS = 300
ACTIVE = ("ISSUED", "CLAIMED")

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS application_submit_authorizations_v1 (
        authorization_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        review_id TEXT NOT NULL,
        approval_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        target_url TEXT NOT NULL,
        checkpoint_id TEXT NOT NULL,
        generation INTEGER NOT NULL,
        plan_digest TEXT NOT NULL CHECK(length(plan_digest)=64),
        review_digest TEXT NOT NULL CHECK(length(review_digest)=64),
        approval_digest TEXT NOT NULL CHECK(length(approval_digest)=64),
        prepared_package_digest TEXT NOT NULL CHECK(length(prepared_package_digest)=64),
        browser_form_digest TEXT NOT NULL CHECK(length(browser_form_digest)=64),
        resume_sha256 TEXT NOT NULL CHECK(length(resume_sha256)=64),
        cover_letter_sha256 TEXT,
        authority_digest TEXT NOT NULL CHECK(length(authority_digest)=64),
        authority_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('ISSUED','CLAIMED','CONSUMED','REVOKED','EXPIRED')),
        issued_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        claimed_at TEXT,
        claimed_by TEXT,
        claim_digest TEXT,
        consumed_at TEXT,
        revoked_at TEXT,
        revocation_reason TEXT,
        UNIQUE(tenant_id,user_id,application_id,session_id,generation),
        UNIQUE(authority_digest),
        FOREIGN KEY(plan_id) REFERENCES application_plans_v2(plan_id) ON DELETE RESTRICT,
        FOREIGN KEY(review_id) REFERENCES application_reviews_v2(review_id) ON DELETE RESTRICT,
        FOREIGN KEY(approval_id) REFERENCES application_review_approvals_v2(approval_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_submit_auth_current
       ON application_submit_authorizations_v1(
          tenant_id,user_id,application_id,session_id,status,generation DESC
       );""",
)


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest.")
    return text


def _text(value: Any, label: str, maximum: int = 2000) -> str:
    text = " ".join(str(value or "").split())
    if not text or len(text) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters.")
    return text


def _secret() -> bytes:
    value = str(os.getenv("MUNSHI_PRODUCTION_SUBMIT_AUTH_HMAC_SECRET") or "")
    if len(value) < 32:
        raise RuntimeError("Production submit-authorization HMAC secret is not configured.")
    return value.encode()


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        application_review_v2.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _bound_rows(connection: sqlite3.Connection, p: Mapping[str, Any]):
    row = connection.execute(
        """SELECT
             p.plan_id,p.tenant_id,p.user_id,p.application_id,p.provider,p.status AS plan_status,
             p.executable,p.plan_digest,p.snapshot_json,
             r.review_id,r.review_digest,r.prepared_package_digest,r.browser_form_digest,
             r.resume_artifact_sha256,r.invalidated_at,
             a.approval_id,a.approval_digest,a.consumed_at,a.consumption_key
           FROM application_plans_v2 AS p
           JOIN application_reviews_v2 AS r ON r.plan_id=p.plan_id
           JOIN application_review_approvals_v2 AS a ON a.review_id=r.review_id
           WHERE p.plan_id=? AND p.tenant_id=? AND p.user_id=? AND p.application_id=?
             AND r.review_id=? AND a.approval_id=?""",
        (
            str(p["plan_id"]), str(p["tenant_id"]), str(p["user_id"]),
            str(p["application_id"]), str(p["review_id"]), str(p["approval_id"]),
        ),
    ).fetchone()
    if row is None:
        raise PermissionError("Submit authorization owner/plan/review/approval binding is invalid.")
    checks = (
        str(row["plan_status"]) == "READY_TO_APPLY",
        int(row["executable"]) == 1,
        row["invalidated_at"] is None,
        str(row["provider"]).upper() == str(p["provider"]).upper(),
        str(row["plan_digest"]) == _digest(p["plan_digest"], "Plan digest"),
        str(row["review_digest"]) == _digest(p["review_digest"], "Review digest"),
        str(row["approval_digest"]) == _digest(p["approval_digest"], "Approval digest"),
        str(row["prepared_package_digest"]) == _digest(p["prepared_package_digest"], "Prepared-package digest"),
        str(row["browser_form_digest"]) == _digest(p["browser_form_digest"], "Browser-form digest"),
        str(row["resume_artifact_sha256"]) == _digest(p["resume_sha256"], "Resume digest"),
    )
    if not all(checks):
        raise PermissionError("Submit authorization conflicts with current immutable review bindings.")
    superseded = connection.execute(
        "SELECT 1 FROM application_plan_revisions_v2 WHERE prior_plan_id=? AND replacement_plan_id IS NOT NULL",
        (str(p["plan_id"]),),
    ).fetchone()
    if superseded is not None:
        raise PermissionError("Submit authorization cannot use a superseded Application Plan.")
    return row


def _material(p: Mapping[str, Any], *, authorization_id: str, generation: int, issued_at: str, expires_at: str) -> dict[str, Any]:
    cover = p.get("cover_letter_sha256")
    if cover is not None:
        cover = _digest(cover, "Cover-letter digest")
    return {
        "version": AUTH_VERSION,
        "authorization_id": authorization_id,
        "tenant_id": _text(p["tenant_id"], "Tenant id", 240),
        "user_id": _text(p["user_id"], "User id", 240),
        "application_id": _text(p["application_id"], "Application id", 240),
        "plan_id": _text(p["plan_id"], "Plan id", 240),
        "session_id": _text(p["session_id"], "Session id", 240),
        "review_id": _text(p["review_id"], "Review id", 240),
        "approval_id": _text(p["approval_id"], "Approval id", 240),
        "provider": _text(p["provider"], "Provider", 64).upper(),
        "target_url": _text(p["target_url"], "Target URL", 4000),
        "checkpoint_id": _text(p["checkpoint_id"], "Checkpoint id", 240),
        "generation": int(generation),
        "plan_digest": _digest(p["plan_digest"], "Plan digest"),
        "review_digest": _digest(p["review_digest"], "Review digest"),
        "approval_digest": _digest(p["approval_digest"], "Approval digest"),
        "prepared_package_digest": _digest(p["prepared_package_digest"], "Prepared-package digest"),
        "browser_form_digest": _digest(p["browser_form_digest"], "Browser-form digest"),
        "resume_sha256": _digest(p["resume_sha256"], "Resume digest"),
        "cover_letter_sha256": cover,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "synthetic": False,
        "submission_authority": True,
    }


def issue_authorization(payload: Mapping[str, Any], *, ttl_seconds: int = AUTH_TTL_SECONDS) -> dict[str, Any]:
    ttl = max(30, min(int(ttl_seconds), AUTH_TTL_SECONDS))
    connection = get_connection()
    try:
        ensure_schema(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = _bound_rows(connection, payload)
        session_id = _text(payload["session_id"], "Session id", 240)

        # A material review/plan rollover invalidates every still-unused authority
        # for this same logical application/session before a new generation exists.
        connection.execute(
            """UPDATE application_submit_authorizations_v1
               SET status='REVOKED',revoked_at=CURRENT_TIMESTAMP,
                   revocation_reason='superseded_by_new_authorization_generation'
               WHERE tenant_id=? AND user_id=? AND application_id=? AND session_id=?
                 AND status='ISSUED'""",
            (str(payload["tenant_id"]), str(payload["user_id"]), str(payload["application_id"]), session_id),
        )
        generation = int(connection.execute(
            """SELECT COALESCE(MAX(generation),0)+1 AS n
               FROM application_submit_authorizations_v1
               WHERE tenant_id=? AND user_id=? AND application_id=? AND session_id=?""",
            (str(payload["tenant_id"]), str(payload["user_id"]), str(payload["application_id"]), session_id),
        ).fetchone()["n"])
        authorization_id = f"submit-auth-{uuid4()}"
        if row["consumed_at"] is not None and str(row["consumption_key"] or "") != authorization_id:
            raise PermissionError("Review approval has already been consumed by another submit authority.")
        now = _now()
        issued_at = _iso(now)
        expires_at = _iso(now + timedelta(seconds=ttl))
        material = _material(payload, authorization_id=authorization_id, generation=generation, issued_at=issued_at, expires_at=expires_at)
        authority_digest = hashlib.sha256(_canonical(material)).hexdigest()
        signature = hmac.new(_secret(), authority_digest.encode(), hashlib.sha256).hexdigest()
        envelope = {**material, "authority_digest": authority_digest, "signature": f"sha256={signature}"}

        connection.execute(
            """UPDATE application_review_approvals_v2
               SET consumed_at=COALESCE(consumed_at,CURRENT_TIMESTAMP),consumption_key=COALESCE(consumption_key,?)
               WHERE approval_id=? AND consumed_at IS NULL""",
            (authorization_id, str(payload["approval_id"])),
        )
        connection.execute(
            """INSERT INTO application_submit_authorizations_v1(
                 authorization_id,tenant_id,user_id,application_id,plan_id,session_id,review_id,approval_id,
                 provider,target_url,checkpoint_id,generation,plan_digest,review_digest,approval_digest,
                 prepared_package_digest,browser_form_digest,resume_sha256,cover_letter_sha256,
                 authority_digest,authority_json,status,issued_at,expires_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ISSUED',?,?)""",
            (
                authorization_id, material["tenant_id"], material["user_id"], material["application_id"],
                material["plan_id"], session_id, material["review_id"], material["approval_id"],
                material["provider"], material["target_url"], material["checkpoint_id"], generation,
                material["plan_digest"], material["review_digest"], material["approval_digest"],
                material["prepared_package_digest"], material["browser_form_digest"], material["resume_sha256"],
                material["cover_letter_sha256"], authority_digest,
                json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True), issued_at, expires_at,
            ),
        )
        connection.commit()
        return envelope
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def verify_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(envelope)
    signature = str(value.pop("signature", ""))
    authority_digest = str(value.pop("authority_digest", "")).lower()
    if value.get("version") != AUTH_VERSION or value.get("synthetic") is not False or value.get("submission_authority") is not True:
        raise PermissionError("Submit authorization envelope version/authority flags are invalid.")
    expected_digest = hashlib.sha256(_canonical(value)).hexdigest()
    if not hmac.compare_digest(expected_digest, authority_digest):
        raise PermissionError("Submit authorization digest is invalid.")
    expected_sig = hmac.new(_secret(), authority_digest.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, f"sha256={expected_sig}"):
        raise PermissionError("Submit authorization signature is invalid.")
    if _parse_time(str(value["expires_at"])) <= _now():
        raise PermissionError("Submit authorization is expired.")
    return {**value, "authority_digest": authority_digest, "signature": signature}


def claim_authorization(envelope: Mapping[str, Any], *, claimant_id: str) -> dict[str, Any]:
    verified = verify_envelope(envelope)
    claimant = _text(claimant_id, "Claimant id", 240)
    connection = get_connection()
    try:
        ensure_schema(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM application_submit_authorizations_v1 WHERE authorization_id=?",
            (str(verified["authorization_id"]),),
        ).fetchone()
        if row is None:
            raise PermissionError("Submit authorization does not exist.")
        if str(row["status"]) != "ISSUED":
            raise PermissionError("Submit authorization has already been claimed, revoked, consumed, or expired.")
        if _parse_time(str(row["expires_at"])) <= _now():
            connection.execute(
                "UPDATE application_submit_authorizations_v1 SET status='EXPIRED' WHERE authorization_id=? AND status='ISSUED'",
                (str(verified["authorization_id"]),),
            )
            connection.commit()
            raise PermissionError("Submit authorization expired before claim.")
        for key in ("tenant_id","user_id","application_id","plan_id","session_id","review_id","approval_id","provider","target_url","checkpoint_id","plan_digest","review_digest","approval_digest","prepared_package_digest","browser_form_digest","resume_sha256","authority_digest"):
            if str(row[key]) != str(verified[key]):
                raise PermissionError(f"Submit authorization persisted binding mismatch: {key}.")
        _bound_rows(connection, verified)
        claim_material = {
            "authorization_id": verified["authorization_id"],
            "authority_digest": verified["authority_digest"],
            "claimant_id": claimant,
            "generation": int(verified["generation"]),
        }
        claim_digest = hashlib.sha256(_canonical(claim_material)).hexdigest()
        changed = connection.execute(
            """UPDATE application_submit_authorizations_v1
               SET status='CLAIMED',claimed_at=CURRENT_TIMESTAMP,claimed_by=?,claim_digest=?
               WHERE authorization_id=? AND status='ISSUED'""",
            (claimant, claim_digest, str(verified["authorization_id"])),
        ).rowcount
        if changed != 1:
            raise PermissionError("Submit authorization one-time claim lost an atomic race.")
        connection.commit()
        return {
            "version": "munshi-submit-authorization-claim-v1",
            "authorization_id": verified["authorization_id"],
            "authority_digest": verified["authority_digest"],
            "claim_digest": claim_digest,
            "generation": int(verified["generation"]),
            "status": "CLAIMED",
            "submission_authority": True,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
