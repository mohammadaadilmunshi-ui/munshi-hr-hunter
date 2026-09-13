"""Hunter-owned ingestion for verified production submission receipts."""
from __future__ import annotations
import hashlib, hmac, json, os, sqlite3
from typing import Any, Mapping
from app import application_submit_authorization_v1 as submit_auth
from app.database import get_connection

RECEIPT_VERSION = "munshi-production-submission-receipt-v1"
SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS production_submission_receipts_v1 (
      receipt_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
      application_id TEXT NOT NULL, authorization_id TEXT NOT NULL,
      authority_digest TEXT NOT NULL CHECK(length(authority_digest)=64),
      claim_digest TEXT NOT NULL CHECK(length(claim_digest)=64), plan_id TEXT NOT NULL,
      session_id TEXT NOT NULL, review_id TEXT NOT NULL, approval_id TEXT NOT NULL,
      provider TEXT NOT NULL, provider_application_id TEXT NOT NULL, target_url TEXT NOT NULL,
      verification_method TEXT NOT NULL, verification_evidence_digest TEXT NOT NULL CHECK(length(verification_evidence_digest)=64),
      receipt_digest TEXT NOT NULL CHECK(length(receipt_digest)=64), receipt_json TEXT NOT NULL,
      verified_at TEXT NOT NULL, ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(receipt_digest), FOREIGN KEY(authorization_id) REFERENCES application_submit_authorizations_v1(authorization_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_production_receipt_application
       ON production_submission_receipts_v1(tenant_id,user_id,application_id,ingested_at DESC);""",
)

def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

def _secret() -> bytes:
    value = str(os.getenv("MUNSHI_PRODUCTION_RECEIPT_HMAC_SECRET") or "")
    if len(value) < 32:
        raise RuntimeError("Production receipt HMAC secret is not configured.")
    return value.encode()

def _digest(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest.")
    return text

def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        submit_auth.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns: connection.commit()
    finally:
        if owns: connection.close()

def verify_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(receipt)
    signature = str(value.pop("signature", ""))
    receipt_digest = str(value.pop("receipt_digest", "")).lower()
    if value.get("version") != RECEIPT_VERSION or value.get("synthetic") is not False or value.get("verification_status") != "VERIFIED":
        raise PermissionError("Only non-synthetic VERIFIED production receipts may be ingested.")
    expected = hashlib.sha256(_canonical(value)).hexdigest()
    if not hmac.compare_digest(expected, receipt_digest):
        raise PermissionError("Production receipt digest is invalid.")
    expected_sig = hmac.new(_secret(), receipt_digest.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, f"sha256={expected_sig}"):
        raise PermissionError("Production receipt signature is invalid.")
    return {**value, "receipt_digest": receipt_digest, "signature": signature}

def ingest_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    value = verify_receipt(receipt)
    required = ("receipt_id","tenant_id","user_id","application_id","authorization_id","authority_digest","claim_digest","plan_id","session_id","review_id","approval_id","provider","provider_application_id","target_url","verification_method","verification_evidence_digest","verified_at")
    for key in required:
        if not str(value.get(key) or "").strip(): raise ValueError(f"Production receipt requires {key}.")
    _digest(value["authority_digest"], "Authority digest"); _digest(value["claim_digest"], "Claim digest"); _digest(value["verification_evidence_digest"], "Verification evidence digest")
    connection = get_connection()
    try:
        ensure_schema(connection)
        if connection.in_transaction: connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute("SELECT * FROM production_submission_receipts_v1 WHERE receipt_id=?", (str(value["receipt_id"]),)).fetchone()
        if prior is not None:
            if str(prior["receipt_digest"]) != str(value["receipt_digest"]): raise PermissionError("Conflicting production-receipt replay.")
            connection.commit(); return {"receipt_id": str(value["receipt_id"]), "status":"INGESTED", "replayed":True, "verification_status":"VERIFIED"}
        authority = connection.execute("SELECT * FROM application_submit_authorizations_v1 WHERE authorization_id=?", (str(value["authorization_id"]),)).fetchone()
        if authority is None or str(authority["status"]) != "CLAIMED": raise PermissionError("Production receipt requires a currently CLAIMED submit authorization.")
        for key in ("tenant_id","user_id","application_id","authority_digest","claim_digest","plan_id","session_id","review_id","approval_id","provider","target_url"):
            if str(authority[key]) != str(value[key]): raise PermissionError(f"Production receipt binding mismatch: {key}.")
        connection.execute("""INSERT INTO production_submission_receipts_v1(receipt_id,tenant_id,user_id,application_id,authorization_id,authority_digest,claim_digest,plan_id,session_id,review_id,approval_id,provider,provider_application_id,target_url,verification_method,verification_evidence_digest,receipt_digest,receipt_json,verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (str(value["receipt_id"]),str(value["tenant_id"]),str(value["user_id"]),str(value["application_id"]),str(value["authorization_id"]),str(value["authority_digest"]),str(value["claim_digest"]),str(value["plan_id"]),str(value["session_id"]),str(value["review_id"]),str(value["approval_id"]),str(value["provider"]),str(value["provider_application_id"]),str(value["target_url"]),str(value["verification_method"]),str(value["verification_evidence_digest"]),str(value["receipt_digest"]),json.dumps(dict(receipt),sort_keys=True,separators=(",",":"),ensure_ascii=True),str(value["verified_at"])))
        if connection.execute("UPDATE application_submit_authorizations_v1 SET status='CONSUMED',consumed_at=CURRENT_TIMESTAMP WHERE authorization_id=? AND status='CLAIMED' AND claim_digest=?", (str(value["authorization_id"]),str(value["claim_digest"]))).rowcount != 1:
            raise PermissionError("Production receipt lost the atomic authorization-consumption race.")
        connection.commit(); return {"receipt_id":str(value["receipt_id"]),"status":"INGESTED","replayed":False,"verification_status":"VERIFIED"}
    except Exception:
        connection.rollback(); raise
    finally:
        connection.close()
