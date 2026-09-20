"""Hunter-owned ingestion for verified production submission receipts."""
from __future__ import annotations
import hashlib, hmac, json, os, sqlite3
from typing import Any, Mapping
from app import application_lifecycle_authority_v1 as lifecycle
from app import application_plan_resolution_v2 as runtime_resolution
from app import application_submit_authorization_v1 as submit_auth
from app.database import get_connection
from app.tenant_foundation import current_owner

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
    receipt_id = str(value.pop("receipt_id", "")).strip()
    if value.get("version") != RECEIPT_VERSION or value.get("synthetic") is not False or value.get("verification_status") != "VERIFIED":
        raise PermissionError("Only non-synthetic VERIFIED production receipts may be ingested.")
    # receipt_id is deterministically derived from this digest, so it cannot be
    # included in its own hash input. Both fields remain cryptographically bound.
    expected = hashlib.sha256(_canonical(value)).hexdigest()
    if not hmac.compare_digest(expected, receipt_digest):
        raise PermissionError("Production receipt digest is invalid.")
    if receipt_id != "production-receipt-" + expected[:32]:
        raise PermissionError("Production receipt id is not bound to its digest.")
    expected_sig = hmac.new(_secret(), receipt_digest.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, f"sha256={expected_sig}"):
        raise PermissionError("Production receipt signature is invalid.")
    return {
        **value,
        "receipt_id": receipt_id,
        "receipt_digest": receipt_digest,
        "signature": signature,
    }


def _project_submitted(
    connection: sqlite3.Connection, value: Mapping[str, Any]
) -> str:
    """Project one VERIFIED production receipt into Hunter's durable CRM."""
    lifecycle.ensure_schema(connection)
    owner = current_owner(connection)
    if (
        str(owner.tenant_id) != str(value["tenant_id"])
        or str(owner.user_id) != str(value["user_id"])
    ):
        raise PermissionError("Production receipt owner does not match Hunter runtime owner.")

    plan = connection.execute(
        """SELECT job_id,provider FROM application_plans_v2
           WHERE plan_id=? AND tenant_id=? AND user_id=? AND application_id=?""",
        (
            str(value["plan_id"]),
            str(value["tenant_id"]),
            str(value["user_id"]),
            str(value["application_id"]),
        ),
    ).fetchone()
    if plan is None:
        raise PermissionError("Production receipt has no owned Application Plan.")
    if str(plan["provider"]).upper() != str(value["provider"]).upper():
        raise PermissionError("Production receipt plan provider binding mismatch.")

    application_id = str(value["application_id"])
    job_id = int(plan["job_id"])
    provider = str(value["provider"]).upper()
    provider_application_id = str(value["provider_application_id"])
    occurred_at = lifecycle._timestamp(value["verified_at"])
    provenance_json = lifecycle._canonical(
        {
            "receipt_id": str(value["receipt_id"]),
            "receipt_digest": str(value["receipt_digest"]),
            "authorization_id": str(value["authorization_id"]),
            "authority_digest": str(value["authority_digest"]),
            "claim_digest": str(value["claim_digest"]),
            "verification_status": "VERIFIED",
            "verification_method": str(value["verification_method"]),
            "submission_authority": False,
        }
    )
    row = lifecycle._owned_record(
        connection, owner=owner, application_id=application_id
    )
    if row is None:
        connection.execute(
            """INSERT INTO application_lifecycle_records(
                   application_id,tenant_id,user_id,job_id,provider,
                   provider_application_id,state,state_version,submitted_at
               ) VALUES (?,?,?,?,?,?,'SUBMITTED',1,?)""",
            (
                application_id,
                owner.tenant_id,
                owner.user_id,
                job_id,
                provider,
                provider_application_id,
                str(value["verified_at"]),
            ),
        )
        lifecycle._append_history(
            connection,
            owner=owner,
            application_id=application_id,
            from_state=None,
            to_state="SUBMITTED",
            state_version=1,
            source="verified_production_receipt",
            reason="Verified production submission receipt received.",
            provenance_json=provenance_json,
            idempotency_key=f"production-receipt:{value['receipt_id']}:SUBMITTED",
            occurred_at=occurred_at,
        )
        state = "SUBMITTED"
    else:
        if int(row["job_id"]) != job_id or str(row["provider"]).upper() != provider:
            raise PermissionError("Production receipt lifecycle binding mismatch.")
        if row["provider_application_id"] not in (None, provider_application_id):
            raise PermissionError("Production receipt provider identity changed.")
        state = str(row["state"])
        preserved = {
            "SUBMITTED",
            "VERIFIED",
            "AWAITING_RESPONSE",
            "ASSESSMENT",
            "INTERVIEW",
            "OFFER",
            "REJECTED",
            "WITHDRAWN",
        }
        paths = {
            "READY_FOR_REVIEW": ("READY_TO_SUBMIT", "SUBMITTING", "SUBMITTED"),
            "READY_TO_SUBMIT": ("SUBMITTING", "SUBMITTED"),
            "SUBMITTING": ("SUBMITTED",),
        }
        if state not in preserved:
            steps = paths.get(state)
            if steps is None:
                raise PermissionError(
                    f"Verified production receipt cannot reconcile lifecycle from {state}."
                )
            version = int(row["state_version"])
            for target in steps:
                transition = lifecycle._transition_with_connection(
                    connection,
                    owner=owner,
                    application_id=application_id,
                    to_state=target,
                    expected_state_version=version,
                    source="verified_production_receipt",
                    idempotency_key=f"production-receipt:{value['receipt_id']}:{target}",
                    reason="Verified production submission receipt reconciliation.",
                    provenance_json=provenance_json,
                    occurred_at=occurred_at,
                )
                version = int(transition["state_version"])
                state = str(transition["state"])
        connection.execute(
            """UPDATE application_lifecycle_records
                  SET provider_application_id=COALESCE(provider_application_id,?),
                      submitted_at=COALESCE(submitted_at,?),
                      updated_at=CURRENT_TIMESTAMP
                WHERE application_id=? AND tenant_id=? AND user_id=?""",
            (
                provider_application_id,
                str(value["verified_at"]),
                application_id,
                owner.tenant_id,
                owner.user_id,
            ),
        )

    if (
        connection.execute(
            "UPDATE jobs SET already_applied=1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,),
        ).rowcount
        != 1
    ):
        raise RuntimeError("Production receipt job disappeared during CRM projection.")
    return state


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
            application_state = _project_submitted(connection, value)
            connection.commit()
            memory = runtime_resolution.promote_runtime_resolutions_after_verified_submission(
                plan_id=str(value["plan_id"])
            )
            return {
                "receipt_id": str(value["receipt_id"]),
                "status": "INGESTED",
                "replayed": True,
                "verification_status": "VERIFIED",
                "application_state": application_state,
                "runtime_answer_memory": memory,
            }
        authority = connection.execute("SELECT * FROM application_submit_authorizations_v1 WHERE authorization_id=?", (str(value["authorization_id"]),)).fetchone()
        if authority is None or str(authority["status"]) != "CLAIMED": raise PermissionError("Production receipt requires a currently CLAIMED submit authorization.")
        for key in ("tenant_id","user_id","application_id","authority_digest","claim_digest","plan_id","session_id","review_id","approval_id","provider","target_url"):
            if str(authority[key]) != str(value[key]): raise PermissionError(f"Production receipt binding mismatch: {key}.")
        connection.execute("""INSERT INTO production_submission_receipts_v1(receipt_id,tenant_id,user_id,application_id,authorization_id,authority_digest,claim_digest,plan_id,session_id,review_id,approval_id,provider,provider_application_id,target_url,verification_method,verification_evidence_digest,receipt_digest,receipt_json,verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (str(value["receipt_id"]),str(value["tenant_id"]),str(value["user_id"]),str(value["application_id"]),str(value["authorization_id"]),str(value["authority_digest"]),str(value["claim_digest"]),str(value["plan_id"]),str(value["session_id"]),str(value["review_id"]),str(value["approval_id"]),str(value["provider"]),str(value["provider_application_id"]),str(value["target_url"]),str(value["verification_method"]),str(value["verification_evidence_digest"]),str(value["receipt_digest"]),json.dumps(dict(receipt),sort_keys=True,separators=(",",":"),ensure_ascii=True),str(value["verified_at"])))
        if connection.execute("UPDATE application_submit_authorizations_v1 SET status='CONSUMED',consumed_at=CURRENT_TIMESTAMP WHERE authorization_id=? AND status='CLAIMED' AND claim_digest=?", (str(value["authorization_id"]),str(value["claim_digest"]))).rowcount != 1:
            raise PermissionError("Production receipt lost the atomic authorization-consumption race.")
        application_state = _project_submitted(connection, value)
        connection.commit()
        memory = runtime_resolution.promote_runtime_resolutions_after_verified_submission(
            plan_id=str(value["plan_id"])
        )
        return {
            "receipt_id": str(value["receipt_id"]),
            "status": "INGESTED",
            "replayed": False,
            "verification_status": "VERIFIED",
            "application_state": application_state,
            "runtime_answer_memory": memory,
        }
    except Exception:
        connection.rollback(); raise
    finally:
        connection.close()
