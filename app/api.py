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


def callback_receipt_key(job_id: int, payload: N8nStatusUpdate, callback_status: str) -> str:
    if payload.request_id:
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

    try:
        if payload.row_id is not None:
            row = connection.execute(
                """
                SELECT *
                FROM jobs
                WHERE id = ?
                """,
                (payload.row_id,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT *
                FROM jobs
                WHERE job_fingerprint = ?
                """,
                (payload.job_fingerprint,),
            ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No matching local job was found.",
            )

        if (
            payload.job_fingerprint
            != row["job_fingerprint"]
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "row_id and job_fingerprint "
                    "refer to different jobs."
                ),
            )

        completed_at = (
            payload.completed_at
            or utc_now()
        )

        receipt_key = callback_receipt_key(int(row["id"]), payload, callback_status)
        claim = connection.execute(
            """
            INSERT OR IGNORE INTO n8n_callback_receipts (
              receipt_key, job_id, request_id, queue_id, callback_status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                receipt_key,
                int(row["id"]),
                payload.request_id,
                payload.queue_id,
                callback_status,
            ),
        )
        if int(claim.rowcount or 0) == 0:
            connection.commit()
            return {
                "success": True,
                "duplicate_callback": True,
                "job_id": int(row["id"]),
                "job_fingerprint": row["job_fingerprint"],
                "n8n_status": callback_status,
                "queue_id": payload.queue_id,
                "message": "Duplicate n8n callback acknowledged without replaying side effects.",
            }

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
        "message": "n8n status stored successfully.",
        "telegram_sync": telegram_sync_result,
    }

# LOCAL_HR_AGENT_PROXY_ROUTER_V1
from app.hr_agent_proxy import router as hr_agent_proxy_router

app.include_router(hr_agent_proxy_router)

# AADIL_LIVE_API_ATS_GATE_V2
# FastAPI must be restarted after this marker is installed.
