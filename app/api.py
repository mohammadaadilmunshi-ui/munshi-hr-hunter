from __future__ import annotations

import json
import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field

from app.actions import (
    VALID_ACTIONS,
    apply_job_action,
)
from app.application_runs_v1 import normalize_callback_status

from app.database import (
    DB_PATH,
    ROOT_DIR,
    get_connection,
    initialize_database,
)
from app.gmail_integration import begin_authorization, complete_authorization


load_dotenv(ROOT_DIR / ".env")

API_SECRET = os.getenv(
    "HUNTER_API_SECRET",
    "",
).strip()

if not API_SECRET:
    raise RuntimeError(
        "HUNTER_API_SECRET is missing from .env"
    )


def production_callbacks_explicitly_disabled() -> bool:
    value = os.getenv("PRODUCTION_CALLBACKS_ENABLED")
    if value is None:
        return False
    return value.strip().lower() not in {"1", "true", "yes", "on"}


@asynccontextmanager
async def lifespan(_application: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Aadil HR Hunter Local API",
    description=(
        "Local action, status, and n8n callback bridge "
        "for Aadil HR Hunter Command Center."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


class JobActionRequest(BaseModel):
    action: str
    actor: str = "Aadil"


class N8nStatusUpdate(BaseModel):
    row_id: int | None = None
    job_fingerprint: str = Field(min_length=8)
    n8n_status: str = Field(min_length=1)

    final_ats_score: float | None = None

    resume_doc_url: str | None = None
    resume_pdf_url: str | None = None
    cover_letter_doc_url: str | None = None

    google_sheet_row_url: str | None = None
    google_sheet_url: str | None = None

    recruiter_found: bool | None = None
    outreach_draft_created: bool | None = None

    # AADIL_EXTENDED_N8N_CALLBACK_FIELDS_V1
    resume_docx_url: str | None = None
    resume_word_url: str | None = None
    cover_letter_pdf_url: str | None = None
    contacts_sheet_url: str | None = None
    outreach_sheet_url: str | None = None
    recruiter_names: list[str] | str | None = None
    recruiter_linkedin_urls: list[str] | str | None = None
    recruiter_contacts: list[dict[str, Any]] | dict[str, Any] | None = None
    execution_id: int | None = None
    n8n_execution_id: int | None = None
    extra_outputs: dict[str, Any] | None = None

    send_mode: str = "manual"
    error_message: str | None = None
    completed_at: str | None = None

    # AADIL_UNIFIED_ATS_CALLBACK_GATE_V1
    queue_id: int | None = None
    request_id: str | None = None
    entry_path: str | None = None
    source_adapter: str | None = None
    full_job_description: str | None = None
    manual_job_text: str | None = None
    ats_engine_version: str | None = None
    ats_gate_status: str | None = None
    evidence_integrity: float | None = None
    missing_verified_terms: Any = None
    placement_gaps: Any = None
    unsupported_market_gaps: Any = None
    outreach_drafts: list[Any] | None = None

    # Complete Application Loop artifact evidence.  This remains optional for
    # legacy callbacks, but if application_id is present all immutable fields
    # below are required and persisted atomically with the callback receipt.
    application_id: str | None = Field(default=None, min_length=1, max_length=240)
    preflight_package_id: str | None = Field(default=None, min_length=1, max_length=240)
    artifact_id: str | None = None
    version_id: str | None = None
    version_number: int | None = None
    filename: str | None = None
    mime_type: str | None = None
    artifact_reference: str | None = None
    artifact_sha256: str | None = None
    page_count: int | None = None
    candidate_truth_binding: dict[str, Any] | None = None
    job_snapshot_binding: dict[str, Any] | None = None
    artifact_created_at: str | None = None


def callback_receipt_key(job_id: int, payload: N8nStatusUpdate, callback_status: str) -> str:
    if payload.application_id is not None and callback_status == "completed":
        if payload.queue_id is None or not payload.request_id:
            raise ValueError("Application artifact callbacks require queue_id and request_id.")
        material = ["application", str(payload.queue_id), payload.request_id]
    elif payload.request_id:
        material = ["request", payload.request_id, callback_status]
    elif payload.queue_id is not None:
        material = ["queue", str(payload.queue_id), callback_status]
    elif payload.execution_id is not None or payload.n8n_execution_id is not None:
        material = [
            "execution",
            str(payload.execution_id or payload.n8n_execution_id),
            callback_status,
        ]
    else:
        material = [
            "legacy",
            str(job_id),
            callback_status,
            str(payload.send_mode or ""),
            str(payload.resume_doc_url or ""),
            str(payload.resume_pdf_url or ""),
            str(payload.google_sheet_row_url or payload.google_sheet_url or ""),
            str(payload.error_message or ""),
        ]
    return hashlib.sha256("\x1f".join(material).encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _artifact_callback_binding(
    connection: Any, *, row: Any, payload: N8nStatusUpdate,
) -> dict[str, Any]:
    """Resolve application evidence only from the stored queue contract."""
    if payload.queue_id is None or not payload.request_id or not payload.preflight_package_id:
        raise ValueError("Application artifact callbacks require queue, request, and preflight identities.")
    queue = connection.execute(
        """SELECT * FROM n8n_dispatch_queue WHERE id=? AND request_id=?""",
        (int(payload.queue_id), payload.request_id),
    ).fetchone()
    if queue is None:
        raise LookupError("Callback queue and request identities do not match.")
    queue = dict(queue)
    if str(queue["queue_status"]) not in {"dispatching", "accepted", "completed"}:
        raise ValueError("Application artifact callback targets a queue that was not dispatched.")
    required = ("tenant_id", "user_id", "preflight_package_id", "preflight_package_digest",
                "application_id", "expected_job_snapshot_sha256", "expected_candidate_truth_json")
    if any(queue.get(name) in (None, "") for name in required):
        raise ValueError("Application artifact callback targets an unbound legacy queue.")
    if (
        int(queue["job_id"]) != int(row["id"])
        or str(queue["job_fingerprint"]) != str(row["job_fingerprint"])
        or str(queue["application_id"]) != payload.application_id
        or str(queue["preflight_package_id"]) != payload.preflight_package_id
    ):
        raise ValueError("Application artifact callback identity conflicts with its queue binding.")
    package = connection.execute(
        """SELECT * FROM application_preflight_packages
           WHERE package_id=? AND tenant_id=? AND user_id=?""",
        (queue["preflight_package_id"], queue["tenant_id"], queue["user_id"]),
    ).fetchone()
    if package is None or (
        int(package["job_id"]) != int(row["id"])
        or str(package["application_id"]) != payload.application_id
        or str(package["package_digest"]) != queue["preflight_package_digest"]
    ):
        raise ValueError("Application artifact callback conflicts with its stored preflight package.")
    snapshot = json.loads(str(package["snapshot_json"]))
    from app.phase67_common import sha256_json

    if (
        str(package["status"]) != "READY_TO_APPLY"
        or str(snapshot.get("status") or "") != "READY_TO_APPLY"
        or str(snapshot.get("application_id") or "") != str(payload.application_id)
        or str(snapshot.get("job_snapshot_sha256") or "")
            != str(queue["expected_job_snapshot_sha256"])
        or sha256_json(snapshot) != str(package["package_digest"])
    ):
        raise ValueError("Application artifact callback preflight package integrity is invalid.")
    truth = dict(snapshot.get("application_truth") or {})
    expected_candidate = json.loads(str(queue["expected_candidate_truth_json"]))
    callback_candidate = dict(payload.candidate_truth_binding or {})
    required_candidate = ("source_extraction_id", "profile_revision", "profile_digest")
    if any(expected_candidate.get(key) in (None, "") for key in required_candidate):
        raise ValueError("Application artifact callback queue lacks an exact Candidate Truth binding.")
    if (
        str(package["job_snapshot_sha256"]) != queue["expected_job_snapshot_sha256"]
        or str((payload.job_snapshot_binding or {}).get("job_snapshot_sha256") or "")
            != queue["expected_job_snapshot_sha256"]
        or any(str(callback_candidate.get(key)) != str(expected_candidate.get(key))
               for key in required_candidate)
        or any(str(dict(truth.get("candidate_profile_binding") or {}).get(key)) != str(expected_candidate.get(key))
               for key in required_candidate)
    ):
        raise ValueError("Application artifact callback evidence does not match Candidate Truth or job snapshot.")

    # Recompute the mutable job evidence inside the callback's BEGIN IMMEDIATE
    # transaction, using the exact owner stored on the queue.  This closes the
    # gap between the pre-lock full-preflight freshness check and persistence.
    from app.phase67_common import safe_owned_job_snapshot

    current_job = safe_owned_job_snapshot(
        int(row["id"]),
        connection=connection,
        tenant_id=str(queue["tenant_id"]),
        user_id=str(queue["user_id"]),
    )
    if str(current_job["job_snapshot_sha256"]) != str(queue["expected_job_snapshot_sha256"]):
        raise ValueError("Application artifact callback job snapshot is stale.")
    return queue


def _callback_job_row(connection: Any, payload: N8nStatusUpdate) -> Any:
    if payload.row_id is not None:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?",
            (payload.row_id,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM jobs WHERE job_fingerprint = ?",
            (payload.job_fingerprint,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No matching local job was found.",
        )
    if payload.job_fingerprint != row["job_fingerprint"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="row_id and job_fingerprint refer to different jobs.",
        )
    return row


def _callback_payload_sha256(payload: N8nStatusUpdate) -> str:
    return hashlib.sha256(
        json.dumps(
            payload.model_dump(exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _preflight_freshness_guard_before_lock(
    connection: Any, *, payload: N8nStatusUpdate,
) -> int | None:
    """Check exact owner-scoped preflight freshness before taking the write lock.

    ``preflight_package_freshness`` currently traverses Candidate Truth vault
    readers that open their own SQLite connections and update vault
    ``last_used_at`` metadata. Running that traversal while this callback owns a
    BEGIN IMMEDIATE lock deadlocks on SQLite.  Instead, run the complete
    freshness capture immediately before the lock, then use PRAGMA data_version
    to prove that no other connection committed between that capture and lock
    acquisition. The exact queue/package/job binding is revalidated again under
    the write lock by ``_artifact_callback_binding``.
    """
    if payload.application_id is None or payload.n8n_status != "completed":
        return None
    if payload.queue_id is None or not payload.request_id or not payload.preflight_package_id:
        raise ValueError("Application artifact callbacks require queue, request, and preflight identities.")
    queue = connection.execute(
        "SELECT * FROM n8n_dispatch_queue WHERE id=? AND request_id=?",
        (int(payload.queue_id), payload.request_id),
    ).fetchone()
    if queue is None:
        raise LookupError("Callback queue and request identities do not match.")
    queue = dict(queue)
    if any(queue.get(name) in (None, "") for name in ("tenant_id", "user_id", "preflight_package_id")):
        raise ValueError("Application artifact callback targets an unbound legacy queue.")
    if (
        str(queue["preflight_package_id"]) != payload.preflight_package_id
        or str(queue.get("application_id") or "") != str(payload.application_id)
    ):
        raise ValueError("Application artifact callback identity conflicts with its queue binding.")

    from app.application_preflight_package_v1 import preflight_package_freshness
    from app.tenant_foundation import owner_context

    with owner_context(
        tenant_id=str(queue["tenant_id"]),
        user_id=str(queue["user_id"]),
    ):
        freshness = preflight_package_freshness(str(queue["preflight_package_id"]))
    if not freshness.get("fresh"):
        raise ValueError("Application artifact callback preflight inputs are stale.")
    return int(connection.execute("PRAGMA data_version").fetchone()[0])


def require_api_secret(
    x_hunter_secret: str | None = Header(
        default=None,
    ),
) -> None:
    if (
        not x_hunter_secret
        or not secrets.compare_digest(
            x_hunter_secret,
            API_SECRET,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid local API secret.",
        )


def serialize_row(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "success": True,
        "service": "Aadil HR Hunter Local API",
        "version": "0.1.0",
        "database": DB_PATH.name,
        "timestamp": utc_now(),
    }


@app.get(
    "/api/gmail/oauth/start",
    dependencies=[Depends(require_api_secret)],
)
def gmail_oauth_start() -> dict[str, str]:
    """Authenticated initiation endpoint; it only returns a one-time Google URL."""
    try:
        return {"authorization_url": begin_authorization()}
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None


@app.get("/api/gmail/oauth/callback")
def gmail_oauth_callback(
    code: str = Query(min_length=1, max_length=4096),
    state_value: str = Query(alias="state", min_length=1, max_length=512),
) -> dict[str, bool]:
    """Google callback is state+PKCE protected because Google cannot send API auth."""
    try:
        complete_authorization(code, state_value)
    except Exception:
        # Do not reveal callback codes, state, token provider responses, or vault details.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Gmail authorization could not be completed.") from None
    return {"success": True}


@app.get(
    "/api/status",
    dependencies=[Depends(require_api_secret)],
)
def system_status() -> dict[str, Any]:
    connection = get_connection()

    try:
        counts = {
            "jobs": connection.execute(
                "SELECT COUNT(*) FROM jobs"
            ).fetchone()[0],
            "events": connection.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0],
            "approved_for_n8n": connection.execute(
                """
                SELECT COUNT(*)
                FROM jobs
                WHERE status = 'approved_for_n8n'
                """
            ).fetchone()[0],
            "sent_to_n8n": connection.execute(
                """
                SELECT COUNT(*)
                FROM jobs
                WHERE sent_to_n8n = 1
                """
            ).fetchone()[0],
            "enabled_sources": connection.execute(
                """
                SELECT COUNT(*)
                FROM source_health
                WHERE enabled = 1
                """
            ).fetchone()[0],
        }
    finally:
        connection.close()

    return {
        "success": True,
        "environment": "development",
        "n8n_connected": False,
        "telegram_connected": False,
        "real_scrapers_enabled": False,
        "counts": counts,
    }


@app.get(
    "/api/jobs/{job_id}",
    dependencies=[Depends(require_api_secret)],
)
def get_job(job_id: int) -> dict[str, Any]:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} was not found.",
        )

    job = serialize_row(row)

    try:
        job["score_breakdown"] = json.loads(
            job.get("score_breakdown_json")
            or "{}"
        )
    except json.JSONDecodeError:
        job["score_breakdown"] = {}

    return {
        "success": True,
        "job": job,
    }


@app.post(
    "/api/jobs/{job_id}/action",
    dependencies=[Depends(require_api_secret)],
)
def job_action(
    job_id: int,
    request: JobActionRequest,
) -> dict[str, Any]:
    if request.action not in VALID_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Unsupported job action.",
                "allowed_actions": sorted(
                    VALID_ACTIONS
                ),
            },
        )

    success, message = apply_job_action(
        job_id=job_id,
        action=request.action,
        actor=request.actor,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )

    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT
                id,
                company_name,
                title,
                status,
                hunter_score,
                match_label,
                sent_to_n8n
            FROM jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    finally:
        connection.close()

    return {
        "success": True,
        "message": message,
        "job": serialize_row(row),
        "external_call_made": False,
    }


@app.post(
    "/api/n8n/status-update",
    dependencies=[Depends(require_api_secret)],
)
def n8n_status_update(
    payload: N8nStatusUpdate,
) -> dict[str, Any]:
    if production_callbacks_explicitly_disabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Production callbacks are disabled on this runtime.",
        )

    connection = get_connection()

    # Callback processing must be able to recover cleanly after restoring a
    # minimal Hunter database.  These are local Hunter tables only; this does
    # not open or mutate n8n's database.
    from app.n8n_dispatch import ensure_schema as ensure_dispatch_schema
    from app.universal_n8n_progress import ensure_schema as ensure_progress_schema

    ensure_dispatch_schema(connection)
    ensure_progress_schema(connection)
    from app.application_preflight_package_v1 import ensure_schema as ensure_preflight_schema
    from app.application_execution_artifact import ensure_schema as ensure_artifact_schema
    ensure_preflight_schema(connection)
    ensure_artifact_schema(connection)
    receipt_columns = {column[1] for column in connection.execute("PRAGMA table_info(n8n_callback_receipts)")}
    if "payload_sha256" not in receipt_columns:
        connection.execute("ALTER TABLE n8n_callback_receipts ADD COLUMN payload_sha256 TEXT")
    if "execution_artifact_id" not in receipt_columns:
        connection.execute("ALTER TABLE n8n_callback_receipts ADD COLUMN execution_artifact_id TEXT")

    callback_status = normalize_callback_status(
        payload.n8n_status,
        payload.final_ats_score,
        gate_status=payload.ats_gate_status,
        evidence_integrity=payload.evidence_integrity,
        missing_verified_terms=payload.missing_verified_terms,
        placement_gaps=payload.placement_gaps,
        unsupported_market_gaps=payload.unsupported_market_gaps,
    )
    payload.n8n_status = callback_status
    # Schema setup is intentionally outside the receipt transaction; sqlite
    # DDL may begin a transaction on restored databases.
    connection.commit()
    payload_sha256 = _callback_payload_sha256(payload)

    try:
        # Exact successful application-artifact replays are acknowledged from
        # the immutable receipt before re-running mutable freshness checks.
        # Otherwise a legitimate retry could begin failing merely because the
        # candidate edited Candidate Truth after the original callback had
        # already committed. A changed payload under the same callback identity
        # still conflicts.
        if payload.application_id is not None and callback_status == "completed":
            replay_row = _callback_job_row(connection, payload)
            replay_key = callback_receipt_key(int(replay_row["id"]), payload, callback_status)
            prior_receipt = connection.execute(
                """SELECT job_id,payload_sha256,execution_artifact_id
                   FROM n8n_callback_receipts WHERE receipt_key=?""",
                (replay_key,),
            ).fetchone()
            if prior_receipt is not None:
                if int(prior_receipt["job_id"]) != int(replay_row["id"]):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Callback receipt identity conflicts with the requested job.",
                    )
                if str(prior_receipt["payload_sha256"] or "") != payload_sha256:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Callback identity was replayed with different evidence.",
                    )
                return {
                    "success": True,
                    "duplicate_callback": True,
                    "job_id": int(replay_row["id"]),
                    "job_fingerprint": replay_row["job_fingerprint"],
                    "n8n_status": callback_status,
                    "queue_id": payload.queue_id,
                    "execution_artifact_id": prior_receipt["execution_artifact_id"],
                    "message": "Duplicate n8n callback acknowledged without replaying side effects.",
                }

        freshness_data_version = _preflight_freshness_guard_before_lock(
            connection,
            payload=payload,
        )
        connection.execute("BEGIN IMMEDIATE")
        if freshness_data_version is not None:
            locked_data_version = int(connection.execute("PRAGMA data_version").fetchone()[0])
            if locked_data_version != freshness_data_version:
                raise ValueError(
                    "Application artifact callback inputs changed while the callback lock was acquired."
                )

        row = _callback_job_row(connection, payload)

        completed_at = (
            payload.completed_at
            or utc_now()
        )

        queue_binding: dict[str, Any] | None = None
        if payload.application_id is not None and callback_status == "completed":
            queue_binding = _artifact_callback_binding(connection, row=row, payload=payload)
        receipt_key = callback_receipt_key(int(row["id"]), payload, callback_status)
        prior_receipt = connection.execute(
            """SELECT job_id,payload_sha256,execution_artifact_id
               FROM n8n_callback_receipts WHERE receipt_key=?""",
            (receipt_key,),
        ).fetchone()
        if prior_receipt is not None:
            if int(prior_receipt["job_id"]) != int(row["id"]):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Callback receipt identity conflicts with the requested job.",
                )
            if payload.application_id is not None and str(prior_receipt["payload_sha256"] or "") != payload_sha256:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="Callback identity was replayed with different evidence.")
            connection.commit()
            return {
                "success": True, "duplicate_callback": True, "job_id": int(row["id"]),
                "job_fingerprint": row["job_fingerprint"], "n8n_status": callback_status,
                "queue_id": payload.queue_id,
                "execution_artifact_id": prior_receipt["execution_artifact_id"],
                "message": "Duplicate n8n callback acknowledged without replaying side effects.",
            }
        claim = connection.execute(
            """
            INSERT OR IGNORE INTO n8n_callback_receipts (
              receipt_key, job_id, request_id, queue_id, callback_status, payload_sha256
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_key,
                int(row["id"]),
                payload.request_id,
                payload.queue_id,
                callback_status,
                payload_sha256,
            ),
        )
        if int(claim.rowcount or 0) == 0:
            raise RuntimeError("Callback receipt identity claim failed unexpectedly.")

        google_sheet_url = (
            payload.google_sheet_row_url
            or payload.google_sheet_url
        )

        connection.execute(
            """
            INSERT OR IGNORE INTO n8n_results (
                job_id,
                job_fingerprint,
                send_mode,
                n8n_status,
                final_ats_score,
                resume_doc_url,
                resume_pdf_url,
                cover_letter_doc_url,
                google_sheet_url,
                recruiter_found,
                outreach_draft_created,
                error_message,
                completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["job_fingerprint"],
                payload.send_mode,
                callback_status,
                payload.final_ats_score,
                payload.resume_doc_url,
                payload.resume_pdf_url,
                payload.cover_letter_doc_url,
                google_sheet_url,
                (
                    None
                    if payload.recruiter_found is None
                    else int(payload.recruiter_found)
                ),
                (
                    None
                    if payload.outreach_draft_created
                    is None
                    else int(
                        payload.outreach_draft_created
                    )
                ),
                payload.error_message,
                completed_at,
            ),
        )

        # AADIL_CALLBACK_IDENTITY_AND_PROGRESS_REPAIR_V1
        # n8n historically emitted n8n_execution_id while the API stored
        # execution_id. Normalize both and, when available, recover the
        # execution ID already discovered by the local progress tracker.
        normalized_payload = payload.model_dump()
        normalized_execution_id = (
            payload.execution_id
            or payload.n8n_execution_id
        )
        if normalized_execution_id is None and payload.queue_id is not None:
            progress_row = connection.execute(
                """
                SELECT execution_id
                FROM telegram_n8n_progress
                WHERE queue_id = ?
                LIMIT 1
                """,
                (int(payload.queue_id),),
            ).fetchone()
            if progress_row is not None:
                normalized_execution_id = progress_row["execution_id"]
        normalized_payload["execution_id"] = normalized_execution_id

        execution_artifact_id: str | None = None
        # An artifact is execution evidence only once n8n has completed its
        # preparation run.  Intermediate/error callbacks may carry partial
        # fields, but must never claim the immutable application binding.
        if payload.application_id is not None and callback_status == "completed":
            from app import application_execution_artifact

            artifact = application_execution_artifact.n8n(
                application_id=payload.application_id,
                job_id=int(row["id"]),
                callback={
                    **payload.model_dump(exclude_none=True),
                    "engine": "n8n",
                    "job_id": int(row["id"]),
                    "created_at": payload.artifact_created_at,
                },
            )
            if queue_binding is None:
                raise ValueError("Application artifact callback lacks an exact queue binding.")
            persisted_artifact = application_execution_artifact.persist_verified_n8n(
                artifact, connection=connection,
                tenant_id=str(queue_binding["tenant_id"]), user_id=str(queue_binding["user_id"]),
            )
            execution_artifact_id = str(persisted_artifact["execution_artifact_id"])
            connection.execute(
                "UPDATE n8n_callback_receipts SET execution_artifact_id=? WHERE receipt_key=?",
                (execution_artifact_id, receipt_key),
            )

        from app.universal_n8n_progress import persist_extended_result

        persist_extended_result(
            connection,
            int(row["id"]),
            normalized_payload,
            completed_at,
        )

        connection.execute(
            """
            UPDATE jobs
            SET
                status = ?,
                sent_to_n8n = 1,
                n8n_send_mode = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                callback_status,
                payload.send_mode,
                row["id"],
            ),
        )

        # AADIL_CALLBACK_QUEUE_PROGRESS_TERMINAL_V1
        # Prefer the exact queue identity supplied by the webhook. Falling
        # back to job_id is retained only for old callback payloads.
        if payload.queue_id is not None:
            connection.execute(
                """
                UPDATE n8n_dispatch_queue
                SET
                    queue_status = 'completed',
                    completed_at = COALESCE(completed_at, ?),
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND job_id = ?
                  AND queue_status != 'completed'
                """,
                (
                    completed_at,
                    int(payload.queue_id),
                    int(row["id"]),
                ),
            )
            connection.execute(
                """
                UPDATE telegram_n8n_progress
                SET
                    execution_id = COALESCE(?, execution_id),
                    run_status = 'completed',
                    error_message = NULL,
                    completed_at = COALESCE(completed_at, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE queue_id = ?
                  AND job_id = ?
                """,
                (
                    normalized_execution_id,
                    completed_at,
                    int(payload.queue_id),
                    int(row["id"]),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE n8n_dispatch_queue
                SET
                    queue_status = 'completed',
                    completed_at = COALESCE(completed_at, ?),
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE
                    job_id = ?
                    AND queue_status IN (
                        'pending',
                        'dispatching',
                        'accepted',
                        'failed'
                    )
                """,
                (
                    completed_at,
                    row["id"],
                ),
            )

        connection.execute(
            """
            INSERT INTO events (
                job_id,
                event_type,
                actor,
                event_status,
                payload_json
            )
            VALUES (
                ?,
                'n8n_status_callback',
                'n8n',
                'recorded',
                ?
            )
            """,
            (
                row["id"],
                json.dumps(
                    payload.model_dump(),
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()

    except HTTPException:
        connection.rollback()
        raise
    except (ValueError, LookupError, PermissionError) as error:
        connection.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except Exception as error:
        connection.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error
    finally:
        connection.close()

    telegram_sync_result: dict[str, Any]

    try:
        # Import lazily after the database transaction has completed.
        # This avoids making Telegram part of the critical callback write.
        from app.telegram_sync import sync_latest_job_card

        telegram_sync_result = sync_latest_job_card(
            int(row["id"]),
            notice=(
                "n8n result received and "
                "automation details refreshed."
            ),
            actor="n8n_callback",
        )

    except Exception as error:
        # A Telegram failure must never make the successful database
        # callback return an HTTP 500 response.
        telegram_sync_result = {
            "success": False,
            "reason": "telegram_sync_exception",
            "job_id": int(row["id"]),
            "error": str(error),
        }

    return {
        "success": True,
        "job_id": row["id"],
        "job_fingerprint": row["job_fingerprint"],
        "n8n_status": callback_status,
        "queue_id": payload.queue_id,
        "execution_id": normalized_execution_id,
        "execution_artifact_id": execution_artifact_id,
        "message": "n8n status stored successfully.",
        "telegram_sync": telegram_sync_result,
    }

# MUNSHI_INBOUND_MAIL_ROUTER_V2
# Signed Cloudflare Email Routing ingress. This endpoint stores/classifies mail
# only; it never carries review or submission authority.
from app.inbound_mail import router as inbound_mail_router

app.include_router(inbound_mail_router)

# LOCAL_HR_AGENT_PROXY_ROUTER_V1
from app.hr_agent_proxy import router as hr_agent_proxy_router

app.include_router(hr_agent_proxy_router)

# AADIL_LIVE_API_ATS_GATE_V2
# FastAPI must be restarted after this marker is installed.


# ---------------------------------------------------------------------------
# Hunter-owned Application Review V2 authority
# ---------------------------------------------------------------------------



class ApplicationReviewCreateRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=240)
    idempotency_key: str = Field(min_length=1, max_length=240)
    prepared_package: dict[str, Any]


class ApplicationReviewApprovalRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=240)
    expected_review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_prepared_package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_browser_form_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApplicationReviewRevalidateRequest(BaseModel):
    prepared_package: dict[str, Any] | None = None


class ApplicationApproveAndSubmitRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=240)
    approval_idempotency_key: str = Field(min_length=1, max_length=240)
    authorization_idempotency_key: str = Field(min_length=1, max_length=240)
    expected_review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_prepared_package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_browser_form_digest: str = Field(pattern=r"^[0-9a-f]{64}$")



@app.post(
    "/api/application-reviews",
    dependencies=[Depends(require_api_secret)],
)
def create_application_review(
    payload: ApplicationReviewCreateRequest,
) -> dict[str, Any]:
    from app import application_review_v2

    try:
        return application_review_v2.create_review(
            plan_id=payload.plan_id,
            prepared_package=payload.prepared_package,
            idempotency_key=payload.idempotency_key,
        )
    except (LookupError, PermissionError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@app.get(
    "/api/application-reviews/{review_id}",
    dependencies=[Depends(require_api_secret)],
)
def get_application_review(review_id: str) -> dict[str, Any]:
    from app import application_review_v2

    try:
        return application_review_v2.get_review(review_id)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (PermissionError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@app.post(
    "/api/application-reviews/{review_id}/revalidate",
    dependencies=[Depends(require_api_secret)],
)
def revalidate_application_review(
    review_id: str,
    payload: ApplicationReviewRevalidateRequest,
) -> dict[str, Any]:
    from app import application_review_v2

    try:
        return application_review_v2.revalidate_review(
            review_id,
            prepared_package=payload.prepared_package,
        )
    except (LookupError, PermissionError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@app.post(
    "/api/application-reviews/{review_id}/approve",
    dependencies=[Depends(require_api_secret)],
)
def approve_application_review(review_id: str) -> dict[str, Any]:
    """Retired late browser-review approval surface."""
    del review_id
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Late browser-review approval is retired. The only customer approval "
            "is POST /api/jobs/{job_id}/approve-and-submit before ATS execution."
        ),
    )


@app.post(
    "/api/application-reviews/{review_id}/approve-and-submit",
    dependencies=[Depends(require_api_secret)],
)
def approve_and_submit_application_review(
    review_id: str,
    payload: ApplicationApproveAndSubmitRequest,
) -> dict[str, Any]:
    """Retired late approval route kept only as an explicit tombstone."""
    del review_id, payload
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Browser-bound approve-and-submit is retired. Use the pre-browser "
            "POST /api/jobs/{job_id}/approve-and-submit package mandate."
        ),
    )


@app.post(
    "/api/jobs/{job_id}/approve-and-submit",
    dependencies=[Depends(require_api_secret)],
)
def approve_and_submit_job_package(job_id: int) -> dict[str, Any]:
    """The sole customer approval boundary, before unattended ATS execution."""
    from app import product_application_flow_v2

    try:
        return product_application_flow_v2.approve_and_submit_for_job(int(job_id))
    except (LookupError, PermissionError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

# Hunter-owned private execution bridge for hosted preparation

@app.post("/api/application-execution/plan-current")
async def application_execution_plan_current(request: Request) -> Response:
    from app import application_artifact_transport_v2
    try:
        body, headers = application_artifact_transport_v2.current_plan_response(await request.body(), request.headers)
        return Response(content=body, media_type="application/json", headers=headers)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except (LookupError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

@app.post("/api/application-execution/artifact")
async def application_execution_artifact(request: Request) -> Response:
    from app import application_artifact_transport_v2
    try:
        body, headers = application_artifact_transport_v2.artifact_response(await request.body(), request.headers)
        return Response(content=body, media_type="application/octet-stream", headers=headers)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except (LookupError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@app.post("/api/application-execution/cover-letter")
async def application_execution_cover_letter(request: Request) -> Response:
    from app import application_artifact_transport_v2
    try:
        body, headers = application_artifact_transport_v2.cover_letter_response(await request.body(), request.headers)
        return Response(content=body, media_type="application/pdf", headers=headers)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except (LookupError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@app.post("/api/application-execution/autoapply-config")
async def application_execution_autoapply_config(request: Request) -> Response:
    from app import application_artifact_transport_v2
    try:
        body, headers = application_artifact_transport_v2.autoapply_config_response(
            await request.body(), request.headers
        )
        return Response(content=body, media_type="application/json", headers=headers)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except (LookupError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@app.post("/api/application-execution/autoapply-credential")
async def application_execution_autoapply_credential(request: Request) -> Response:
    from app import application_artifact_transport_v2
    try:
        body, headers = application_artifact_transport_v2.autoapply_credential_response(
            await request.body(), request.headers
        )
        # Secret bytes are returned only across the authenticated execution bridge
        # to the Apply service process. They are never represented as JSON.
        return Response(content=body, media_type="application/octet-stream", headers=headers)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except (LookupError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


def _account_execution_response_error(error: Exception) -> HTTPException:
    if isinstance(error, PermissionError):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


@app.post("/api/application-execution/account/prepare")
async def application_execution_account_prepare(request: Request) -> Response:
    from app import application_account_transport_v1 as transport
    try:
        body, headers = transport.account_prepare_response(await request.body(), request.headers)
        return Response(content=body, media_type="application/json", headers=headers)
    except (LookupError, PermissionError, RuntimeError, ValueError) as error:
        raise _account_execution_response_error(error) from error


@app.post("/api/application-execution/account/credential")
async def application_execution_account_credential(request: Request) -> Response:
    from app import application_account_transport_v1 as transport
    try:
        body, headers = transport.account_credential_response(await request.body(), request.headers)
        return Response(content=body, media_type="application/json", headers=headers)
    except (LookupError, PermissionError, RuntimeError, ValueError) as error:
        raise _account_execution_response_error(error) from error


@app.post("/api/application-execution/mailbox/health")
async def application_execution_mailbox_health(request: Request) -> Response:
    from app import application_account_transport_v1 as transport
    try:
        body, headers = transport.mailbox_health_response(await request.body(), request.headers)
        return Response(content=body, media_type="application/json", headers=headers)
    except (LookupError, PermissionError, RuntimeError, ValueError) as error:
        raise _account_execution_response_error(error) from error


@app.post("/api/application-execution/mailbox/begin")
async def application_execution_mailbox_begin(request: Request) -> Response:
    from app import application_account_transport_v1 as transport
    try:
        body, headers = transport.mailbox_begin_response(await request.body(), request.headers)
        return Response(content=body, media_type="application/json", headers=headers)
    except (LookupError, PermissionError, RuntimeError, ValueError) as error:
        raise _account_execution_response_error(error) from error


@app.post("/api/application-execution/mailbox/claim")
async def application_execution_mailbox_claim(request: Request) -> Response:
    from app import application_account_transport_v1 as transport
    try:
        body, headers = transport.mailbox_claim_response(await request.body(), request.headers)
        return Response(content=body, media_type="application/json", headers=headers)
    except (LookupError, PermissionError, RuntimeError, ValueError) as error:
        raise _account_execution_response_error(error) from error


@app.post("/api/application-execution/mailbox/consume")
async def application_execution_mailbox_consume(request: Request) -> Response:
    from app import application_account_transport_v1 as transport
    try:
        body, headers = transport.mailbox_consume_response(await request.body(), request.headers)
        return Response(content=body, media_type="application/json", headers=headers)
    except (LookupError, PermissionError, RuntimeError, ValueError) as error:
        raise _account_execution_response_error(error) from error


@app.post("/api/application-execution/mailbox/cancel")
async def application_execution_mailbox_cancel(request: Request) -> Response:
    from app import application_account_transport_v1 as transport
    try:
        body, headers = transport.mailbox_cancel_response(await request.body(), request.headers)
        return Response(content=body, media_type="application/json", headers=headers)
    except (LookupError, PermissionError, RuntimeError, ValueError) as error:
        raise _account_execution_response_error(error) from error
