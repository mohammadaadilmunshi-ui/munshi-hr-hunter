"""MUNSHI Relay: exact-job, one-use external application dispatch.

A UI click grants application-specific consent. SQLite records the exclusive
claim *before* an external application call. A timeout is always UNKNOWN and
never causes a blind retry. Provider submission != employer receipt.
"""
from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app import application_provider_router_v1 as router
from app.database import get_connection
from app.mailbox_verification_service import runtime_health
from app.tenant_foundation import current_owner
from app.tsenta_mcp_adapter_v1 import TsentaMcpAdapter, _unwrap
from app.tsenta_mcp_http_v1 import TsentaMcpApplicationClient
from app.tsenta_oauth_v1 import _token_for_current_owner

CLAIM_TABLE = "tsenta_external_dispatch_claims_v1"
CLAIM_SCHEMA = f"""CREATE TABLE IF NOT EXISTS {CLAIM_TABLE} (
 claim_key TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
 route_id TEXT NOT NULL, job_id INTEGER NOT NULL, application_id TEXT NOT NULL,
 employer_url_sha256 TEXT NOT NULL, pdf_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('CLAIMED','ACCEPTED','UNKNOWN')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 tsenta_application_id_sha256 TEXT, response_status TEXT,
 UNIQUE(tenant_id,user_id,application_id),
 FOREIGN KEY(route_id) REFERENCES application_provider_routes_v1(route_id)
)"""
MAX_PDF_BYTES = 8_000_000


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _load_bound_artifact(route: Mapping[str, Any]) -> tuple[bytes, str, str]:
    """Two independent cryptographic bindings, not a mistaken single digest."""
    db = get_connection()
    try:
        owner = current_owner(db)
        assert_route_owner = (
            route.get("tenant_id") == owner.tenant_id
            and route.get("user_id") == owner.user_id
        )
        if not assert_route_owner:
            raise PermissionError("Application route ownership mismatch.")
        pkg = db.execute(
            """SELECT * FROM application_preflight_packages
               WHERE package_id=? AND tenant_id=? AND user_id=? AND job_id=?""",
            (route["preflight_package_id"], owner.tenant_id, owner.user_id,
             route["job_id"]),
        ).fetchone()
        if pkg is None or pkg["status"] != "READY_TO_APPLY":
            raise PermissionError("The current application package is not ready.")
        if (pkg["package_digest"] != route["package_digest"]
                or pkg["resume_sha256"] != route["resume_sha256"]
                or pkg["application_id"] != route["application_id"]):
            raise PermissionError("Immutable package binding changed.")
        result = db.execute(
            """SELECT a.* FROM native_resume_v5_artifacts a
               WHERE a.tenant_id=? AND a.user_id=? AND a.version_id=?
                 AND a.artifact_kind='PDF' AND a.mime_type='application/pdf'
                 AND a.page_count=1
               ORDER BY a.created_at DESC LIMIT 1""",
            (owner.tenant_id,owner.user_id,pkg["resume_version_id"]),
        ).fetchone()
        alias = db.execute(
            """SELECT email_address FROM ats_application_identities
               WHERE tenant_id=? AND user_id=? AND relay_state='ACTIVE'
               ORDER BY updated_at DESC LIMIT 1""",
            (owner.tenant_id,owner.user_id),
        ).fetchone()
        if result is None or alias is None:
            raise PermissionError("Verified PDF or managed application inbox missing.")
        pdf = bytes(result["content"] or b"")
        sha = hashlib.sha256(pdf).hexdigest()
        if (
            not pdf.startswith(b"%PDF-") or not 100 <= len(pdf) <= MAX_PDF_BYTES
            or len(pdf) != result["byte_count"] or result["sha256"] != sha
        ):
            raise PermissionError("The bound one-page PDF could not be verified.")
        return pdf, sha, str(alias["email_address"])
    finally:
        db.close()


def _authorize_claim(
    *, route: Mapping[str, Any], pdf_sha256: str,
    reviewed_digest: str,
) -> str:
    """The UI approval and one exclusive dispatch claim in one transaction.

    Existing claims remain exclusive after timeout; never create a second key.
    """
    if route["provider"] != "TSENTA" or route["state"] != "PREPARATION":
        raise PermissionError("Opening is not reserved for this engine.")
    key = "relay-" + _digest(
        f'{route["application_id"]}:{route["route_id"]}:'
        f'{route["package_digest"]}:{reviewed_digest}'
    )[:40]
    db = get_connection()
    try:
        db.execute("BEGIN IMMEDIATE")
        owner = current_owner(db)
        if (owner.tenant_id, owner.user_id) != (
            route["tenant_id"],route["user_id"]
        ):
            raise PermissionError("Candidate identity scope changed.")
        current = db.execute(
            """SELECT * FROM application_provider_routes_v1
               WHERE route_id=? AND tenant_id=? AND user_id=?""",
            (route["route_id"],owner.tenant_id,owner.user_id),
        ).fetchone()
        if current is None or (
            current["provider"] != "TSENTA"
            or current["state"] != "PREPARATION"
            or current["package_digest"] != route["package_digest"]
            or current["provider_application_id_sha256"]
            or current["provider_submitted_at"]
            or current["employer_confirmed_at"]
            or current["receipt_method"]
        ):
            raise PermissionError("Application route changed before approval.")
        job = db.execute(
            "SELECT already_applied,primary_decision,status FROM jobs WHERE id=?",
            (route["job_id"],),
        ).fetchone()
        if (
            job is None or job["already_applied"]
            or job["primary_decision"] != "ELIGIBLE" or job["status"] != "found"
        ):
            raise PermissionError("Job is no longer eligible to submit.")
        # Ensure no Native mandate is active for this application.
        active = db.execute(
            """SELECT count(*) FROM application_execution_mandates_v1
               WHERE application_id=? AND revoked_at IS NULL""",
            (route["application_id"],),
        ).fetchone()[0]
        if active:
            raise PermissionError("Existing Native authorization must be resolved.")
        db.execute(CLAIM_SCHEMA)
        found = db.execute(
            f"""SELECT state FROM {CLAIM_TABLE}
                WHERE tenant_id=? AND user_id=? AND application_id=?""",
            (owner.tenant_id,owner.user_id,route["application_id"]),
        ).fetchone()
        if found is not None:
            raise FileExistsError("This employer application has already been dispatched.")
        moment = _now()
        db.execute(
            f"""INSERT INTO {CLAIM_TABLE}(
                claim_key,tenant_id,user_id,route_id,job_id,application_id,
                employer_url_sha256,pdf_sha256,state,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (key,owner.tenant_id,owner.user_id,route["route_id"],
             route["job_id"],route["application_id"],
             route["employer_url_sha256"],pdf_sha256,
             "CLAIMED",moment,moment),
        )
        db.commit()
        return key
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _mark_claim(claim_key: str, status: str, app_id: str | None = None) -> None:
    if status not in {"ACCEPTED","UNKNOWN"}:
        raise ValueError("Unsupported claim outcome.")
    db = get_connection()
    try:
        db.execute("BEGIN IMMEDIATE")
        owner=current_owner(db)
        count=db.execute(
            f"""UPDATE {CLAIM_TABLE}
                SET state=?,response_status=?,tsenta_application_id_sha256=?,
                    updated_at=?
                WHERE claim_key=? AND tenant_id=? AND user_id=?
                  AND state IN ('CLAIMED','UNKNOWN')""",
            (status,status,_digest(app_id) if app_id else None,_now(),
             claim_key,owner.tenant_id,owner.user_id),
        ).rowcount
        if count != 1:
            raise RuntimeError("The one-use claim changed during execution.")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _confirmed_application(
    mcp: TsentaMcpApplicationClient,
    employer_url: str,
) -> tuple[str,str] | None:
    """Independent readback from provider listing plus detailed terminal state."""
    matches=[]
    query=employer_url.rsplit("/",1)[-1].split("-",1)[0]
    for page in range(1,5):
        result=_unwrap(
            mcp.call_read("list_applications",{
                "query":query,"page":page,"limit":50,
            }),tool_name="list_applications",
        )
        rows=result.get("applications") if isinstance(result,Mapping) else None
        if not isinstance(rows,list):
            return None
        matches.extend(
            r for r in rows if isinstance(r,Mapping)
            and r.get("url")==employer_url
        )
        if len(matches)>1 or len(rows)<50:
            break
    if len(matches)!=1:
        return None
    record=matches[0]
    ident=record.get("applicationId")
    if not isinstance(ident,str) or not ident or record.get("status")!="COMPLETED":
        return None
    detail=_unwrap(mcp.call_read(
        "get_application_status",{"applicationId":ident},
    ),tool_name="get_application_status")
    if not isinstance(detail,Mapping) or (
        detail.get("status")!="COMPLETED"
        or detail.get("statusLabel")!="sent"
        or detail.get("isTerminal") is not True
        or detail.get("needsAction") is not False
        or detail.get("errorCode") is not None
    ):
        return None
    return ident,"COMPLETED"


def _prior_provider_application(
    mcp: TsentaMcpApplicationClient, url: str
) -> bool:
    """Read-before-write duplicate check against the connected account."""
    query=url.rsplit("/",1)[-1].split("-",1)[0]
    for page in range(1,5):
        payload=_unwrap(
            mcp.call_read("list_applications",{
                "query":query,"page":page,"limit":50,
            }),tool_name="list_applications",
        )
        rows=payload.get("applications") if isinstance(payload,Mapping) else None
        if not isinstance(rows,list):
            raise RuntimeError("Provider duplicate-check response unavailable.")
        if any(isinstance(row,Mapping) and row.get("url")==url for row in rows):
            return True
        if len(rows)<50:
            return False
    # Never assume no duplicate when the bounded pagination is exhausted.
    raise RuntimeError("Unable to exhaust provider duplicate-check inventory.")


def submit_from_dashboard(
    *, job_id: int, review_digest: str, preflight_package_id: str,
    recovering_failed_native: bool = False,
) -> dict[str, Any]:
    """One user click -> one job -> one outbound apply call, no second attempt."""
    from app import product_application_flow_v2 as flow_v2
    from app import tsenta_backend_readiness_v1
    from app.tsenta_submission_preflight_v1 import (
        submission_readiness,
        tsenta_profile_with_managed_email,
    )

    if tsenta_backend_readiness_v1.connection_status()["execution_ready"] is not True:
        raise PermissionError("MUNSHI Relay dispatch is not enabled.")
    flow=flow_v2.flow_for_job(int(job_id))
    review=flow.get("package_review") or {}
    preflight=(flow.get("artifacts") or {}).get("preflight") or {}
    allowed_flow_states = (
        {"READY_FOR_APPROVAL","FAILED_SAFELY","BLOCKED"}
        if recovering_failed_native else {"READY_FOR_APPROVAL"}
    )
    if (
        flow.get("state") not in allowed_flow_states
        or not review or review.get("review_digest")!=review_digest
        or preflight.get("preflight_package_id")!=preflight_package_id
    ):
        raise PermissionError("The exact pre-browser package approval has changed.")
    from app.application_provider_ui_v1 import verified_apply_destination
    db=get_connection()
    try:
        source=db.execute("SELECT * FROM jobs WHERE id=?",(int(job_id),)).fetchone()
        if source is None:
            raise LookupError("Opening is no longer available.")
        employer_url=verified_apply_destination(dict(source),db)
        if not employer_url:
            raise PermissionError("Employer application URL is not verified.")
    finally:
        db.close()
    if recovering_failed_native:
        # Native still owns the opening here. Delay irreversible handoff
        # until the account, identity, PDF, duplicate check, and upload
        # all pass. No other engine may submit before the atomic transfer.
        db=get_connection()
        try:
            owner=current_owner(db)
            existing=db.execute(
                """SELECT * FROM application_provider_routes_v1
                   WHERE tenant_id=? AND user_id=? AND job_id=?""",
                (owner.tenant_id,owner.user_id,int(job_id)),
            ).fetchone()
            if existing is None or existing["provider"]!="NATIVE":
                raise PermissionError("The stopped Native route was not found.")
            route=dict(existing)
        finally:
            db.close()
        # The readiness probe needs a hypothetical future owner, without
        # mutating the actual durable Native lock before the upload succeeds.
        candidate_route={**route,"provider":"TSENTA","state":"PREPARATION"}
    else:
        route=router.select_provider(
        job_id=int(job_id),provider="TSENTA",
        verified_employer_url=employer_url,
        preflight_package_id=preflight_package_id,
        idempotency_key="relay-route-"+_digest(
            f"{job_id}:{preflight_package_id}:{review_digest}"
        )[:40],
        )
        candidate_route=route
    if candidate_route["provider"]!="TSENTA" or candidate_route["state"]!="PREPARATION":
        raise PermissionError("Submission engine selection changed.")
    pdf,pdf_sha,email=_load_bound_artifact(candidate_route)
    if runtime_health().get("ready") is not True:
        raise RuntimeError("MUNSHI verification inbox is not healthy.")
    token=_token_for_current_owner()
    with TsentaMcpApplicationClient(token) as mcp:
        mcp.initialize()
        snap=TsentaMcpAdapter(
            lambda name,args: mcp.call_read(name,args),
            available_tools=list(mcp._tools),
        ).read_account_snapshot()
        raw_profile=snap.selected_resume_profile
        catalog=mcp._send("tools/list",{})
        tools=frozenset(
            str(t.get("name") or "").replace("-","_")
            for t in catalog.get("tools",[]) if isinstance(t,Mapping)
        )
        readiness=submission_readiness(
            route=candidate_route,profile=raw_profile,resume_pdf=pdf,
            accepted_resume_binding={
                "rendered_output_sha256":route["resume_sha256"],
                "artifact_sha256":pdf_sha,
            },
            managed_email=email,relay_ready=True,
            available_tools=tools,
            profile_verified=snap.privacy_safe_summary().get("onboarding_complete") is True,
        )
        if not readiness["ready_for_review"]:
            raise PermissionError(
                "Relay preflight blocked: "+",".join(readiness["blockers"])
            )
        profile=tsenta_profile_with_managed_email(
            profile=raw_profile,managed_email=email
        )
        # Explicit user instruction: sponsorship now or in the future = Yes.
        auth=profile.get("workAuthorization")
        if not isinstance(auth,dict):
            raise PermissionError("Work authorization profile is incomplete.")
        auth["needsSponsorship"]=True
        if str(auth.get("optStatus") or "").casefold()=="pending":
            auth["visaType"]="F-1"
        if isinstance(profile.get("workPreferences"),dict) and (
            str(auth.get("optStatus") or "").casefold()=="pending"
        ):
            profile["workPreferences"]["canStartImmediately"]=False

        # Do not submit a second application already present in the
        # connected account, even if the MUNSHI local ledger is incomplete.
        if _prior_provider_application(mcp,candidate_route["employer_url"]):
            raise FileExistsError(
                "Connected engine already tracks this exact employer opening."
            )
        # Upload is NOT an employer application. Verify id/size before claim.
        uploaded=_unwrap(mcp.call_application(
            "upload_custom_resume",{
                "fileBase64":base64.b64encode(pdf).decode("ascii"),
            },claimed_for_single_use=True,
        ),tool_name="upload_custom_resume")
        identifier=uploaded.get("uploadId") if isinstance(uploaded,Mapping) else None
        if not identifier or uploaded.get("byteSize")!=len(pdf):
            raise RuntimeError("Provider did not verify the uploaded PDF size.")
        checked=_unwrap(mcp.call_application(
            "check_custom_resume",{"uploadId":identifier},
            claimed_for_single_use=True,
        ),tool_name="check_custom_resume")
        if not isinstance(checked,Mapping) or not TsentaMcpAdapter._verified_custom_resume(
            checked,upload_id=identifier,expected_byte_size=len(pdf)
        ):
            raise RuntimeError("Provider did not verify the selected PDF.")
        # From this point forward, only one outbound application attempt is
        # ever permitted. On unknown outcomes, reconcile, NEVER retry.
        if recovering_failed_native:
            handoff_failed_native_route(int(job_id),flow=flow)
            route={**candidate_route,"provider":"TSENTA","state":"PREPARATION"}
        claim=_authorize_claim(route=route,pdf_sha256=pdf_sha,reviewed_digest=review_digest)
        try:
            response=mcp.call_application(
                "apply_with_custom_resume",{
                    "url":route["employer_url"],
                    "uploadId":identifier,"profile":profile,
                },claimed_for_single_use=True,
            )
            # Response may contain nested results instead of a top-level ID.
            _unwrap(response,tool_name="apply_with_custom_resume")
        except (RuntimeError,TimeoutError,OSError,ValueError,TypeError):
            _mark_claim(claim,"UNKNOWN")
            return {"status":"OUTCOME_UNKNOWN","engine":"MUNSHI Relay",
                    "retry_allowed":False}
        try:
            found=_confirmed_application(mcp,route["employer_url"])
        except (RuntimeError,TimeoutError,OSError,ValueError,TypeError):
            found=None
        if found is None:
            _mark_claim(claim,"UNKNOWN")
            return {"status":"PROVIDER_STATUS_PENDING","engine":"MUNSHI Relay",
                    "retry_allowed":False}
        app_id, status=found
        # Record submission separately from an independent employer receipt.
        confirmed=router.record_provider_status(
            route_id=route["route_id"],provider_status=status,
            provider_application_id=app_id,
        )
        if confirmed["state"]!="PROVIDER_SUBMITTED":
            raise RuntimeError("Provider submitted but route reconciliation failed.")
        _mark_claim(claim,"ACCEPTED",app_id)
        db=get_connection()
        try:
            db.execute(
                "UPDATE jobs SET already_applied=1 WHERE id=? AND already_applied=0",
                (job_id,),
            )
            db.commit()
        finally:
            db.close()
        return {"status":"PROVIDER_SUBMITTED","engine":"MUNSHI Relay",
                "employer_receipt_verified":False,
                "retry_allowed":False}


def handoff_failed_native_route(
    job_id: int, *, flow: Mapping[str,Any],
) -> dict[str,Any]:
    """Atomically revoke the old Native approval before Relay can dispatch.

    Never switch a ready, running, submitting or completed application.
    This is deliberately unavailable for ambiguous Native browser states.
    """
    if flow.get("state") not in {"FAILED_SAFELY","BLOCKED"}:
        raise PermissionError("Native execution has not stopped safely.")
    if flow.get("production_receipt"):
        raise PermissionError("Existing employer receipt forbids engine switching.")
    prepare = flow.get("prepare") or {}
    if prepare.get("status") not in {"FAILED_SAFELY","BLOCKED","FAILED"}:
        raise PermissionError("Native preparation is not confirmed stopped.")
    # The original Candidate Truth and resume bindings must still be fresh,
    # even when recovering a failed Native route rather than creating a route.
    preflight=(flow.get("artifacts") or {}).get("preflight") or {}
    if not preflight.get("preflight_package_id"):
        raise PermissionError("The original package is unavailable.")
    router._preflight_binding(str(preflight["preflight_package_id"]))

    db=get_connection()
    try:
        db.execute("BEGIN IMMEDIATE")
        owner=current_owner(db)
        rows=db.execute(
            """SELECT * FROM application_provider_routes_v1
               WHERE job_id=? AND tenant_id=? AND user_id=?""",
            (job_id,owner.tenant_id,owner.user_id),
        ).fetchall()
        if len(rows)!=1:
            raise PermissionError("Exact application route identity is ambiguous.")
        route=dict(rows[0])
        if route["provider"]!="NATIVE" or route["state"] not in (
            "PREPARATION","FAILED","PAUSED"
        ):
            raise PermissionError("Native route is not recoverable.")
        if any(route.get(k) for k in (
            "provider_application_id_sha256","provider_submitted_at",
            "employer_confirmed_at","receipt_method","receipt_evidence_digest",
        )):
            raise PermissionError("Possible prior submission prevents handoff.")
        mandate=db.execute(
            """SELECT mandate_id,revoked_at FROM application_execution_mandates_v1
               WHERE application_id=? AND tenant_id=? AND user_id=?
                 AND revoked_at IS NULL""",
            (route["application_id"],owner.tenant_id,owner.user_id),
        ).fetchall()
        if len(mandate)>1:
            raise PermissionError("Multiple Native approvals prevent handoff.")
        existing=db.execute(
            f"SELECT 1 FROM {CLAIM_TABLE} WHERE application_id=?",
            (route["application_id"],),
        ).fetchone() if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (CLAIM_TABLE,),
        ).fetchone() else None
        if existing:
            raise FileExistsError("A provider dispatch claim already exists.")
        if mandate:
            change=db.execute(
                """UPDATE application_execution_mandates_v1
                   SET revoked_at=?,revocation_reason=?
                   WHERE mandate_id=? AND revoked_at IS NULL""",
                (_now(),"RELAY_ENGINE_HANDOFF",mandate[0]["mandate_id"]),
            ).rowcount
            if change!=1:raise RuntimeError("Native mandate revocation failed.")
        changed=db.execute(
            """UPDATE application_provider_routes_v1
               SET provider='TSENTA',state='PREPARATION',updated_at=?,
                   provider_status='ENGINE_HANDOFF'
               WHERE route_id=? AND tenant_id=? AND user_id=? AND provider='NATIVE'
                 AND state IN ('PREPARATION','FAILED','PAUSED')
                 AND provider_application_id_sha256 IS NULL
                 AND employer_confirmed_at IS NULL""",
            (_now(),route["route_id"],owner.tenant_id,owner.user_id),
        ).rowcount
        if changed!=1:raise RuntimeError("Exclusive route transfer failed.")
        event=dict(route)
        from app.application_provider_router_v1 import _event
        _event(db,route=event,event_type="NATIVE_FAILED_ROUTE_HANDOFF",
               from_state=route["state"],to_state="PREPARATION")
        db.commit()
        return {"provider":"TSENTA","state":"PREPARATION","route_id":route["route_id"]}
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def recover_failed_native_from_dashboard(job_id: int) -> dict[str,Any]:
    """User-triggered switch-and-submit for a verified failed Native route."""
    from app import application_execution_mandate_v1 as mandate_v1
    from app import product_application_flow_v2 as flow_v2

    flow=flow_v2.flow_for_job(int(job_id))
    if flow.get("state") not in {"FAILED_SAFELY","BLOCKED"}:
        raise PermissionError("Only safely stopped Native attempts can switch.")
    package=((flow.get("artifacts") or {}).get("preflight") or {})
    package_id=str(package.get("preflight_package_id") or "")
    if not package_id:
        raise PermissionError("Original immutable package is unavailable.")
    review=flow.get("package_review") or {}
    if not review.get("review_digest"):
        review=mandate_v1.package_review(
            preflight_package_id=package_id,
            cover_letter_decision=str(package.get("cover_letter_decision") or "UNDETERMINED"),
            cover_letter=None,
        )
    if not review.get("review_digest"):
        raise PermissionError("Original package review cannot be verified.")
    return submit_from_dashboard(
        job_id=int(job_id),review_digest=str(review["review_digest"]),
        preflight_package_id=package_id,
        recovering_failed_native=True,
    )


def reconcile_from_dashboard(job_id: int) -> dict[str, Any]:
    """Read-only provider check; update only local status and claimed receipt.

    This never calls upload/application tools. A provider-sent result remains
    unconfirmed by the employer until independent evidence arrives.
    """
    db=get_connection()
    try:
        owner=current_owner(db)
        row=db.execute(
            """SELECT * FROM application_provider_routes_v1
               WHERE tenant_id=? AND user_id=? AND job_id=?""",
            (owner.tenant_id,owner.user_id,int(job_id)),
        ).fetchone()
        if row is None or row["provider"]!="TSENTA":
            raise PermissionError("Opening is not owned by MUNSHI Relay.")
        route=dict(row)
        if route["state"]=="EMPLOYER_CONFIRMED" and route["receipt_evidence_digest"]:
            return {"status":"EMPLOYER_CONFIRMED",
                    "employer_receipt_verified":True,"retry_allowed":False}
        claim_exists=db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (CLAIM_TABLE,),
        ).fetchone()
        claimed=db.execute(
            f"SELECT * FROM {CLAIM_TABLE} WHERE route_id=?",
            (route["route_id"],),
        ).fetchone() if claim_exists else None
        if not claimed:
            return {"status":"NO_DISPATCH_CLAIM","retry_allowed":False}
    finally:
        db.close()
    with TsentaMcpApplicationClient(_token_for_current_owner()) as client:
        client.initialize()
        found=_confirmed_application(client,route["employer_url"])
    if found is None:
        return {"status":"PROVIDER_STATUS_PENDING","retry_allowed":False}
    identifier,status=found
    if route["state"] not in {"PROVIDER_SUBMITTED","EMPLOYER_CONFIRMED"}:
        router.record_provider_status(
            route_id=route["route_id"],provider_status=status,
            provider_application_id=identifier,
        )
    if claimed["state"]!="ACCEPTED":
        _mark_claim(str(claimed["claim_key"]),"ACCEPTED",identifier)
    db=get_connection()
    try:
        db.execute(
            "UPDATE jobs SET already_applied=1 WHERE id=? AND already_applied=0",
            (int(job_id),),
        )
        db.commit()
    finally:
        db.close()
    # A provider-managed employer acknowledgment is independent of the
    # provider's own sent status. It is read-only with no email reply.
    try:
        from app.relay_inbox_receipt_v1 import reconcile_employer_inbox

        with TsentaMcpApplicationClient(_token_for_current_owner()) as mcp:
            mcp.initialize()
            receipt=reconcile_employer_inbox(job_id=int(job_id),mcp=mcp)
        if receipt.get("status")=="EMPLOYER_CONFIRMED":
            return {
                "status":"EMPLOYER_CONFIRMED",
                "employer_receipt_verified":True,
                "retry_allowed":False,
            }
    except (RuntimeError,LookupError,ValueError,TypeError,PermissionError,OSError):
        pass
    return {
        "status":"PROVIDER_SUBMITTED",
        "employer_receipt_verified":False,
        "retry_allowed":False,
    }



def reconcile_owner_pending(*,limit:int=10) -> dict[str,int]:
    """Scheduled local status/email synchronization, never an application send.

    Iterates only route IDs already claimed and eligible for readback. A single
    failing provider read cannot trigger an external apply or block other jobs.
    """
    if not 1 <= int(limit) <= 20:
        raise ValueError("Reconciliation batch limit is outside its safe bounds.")
    db=get_connection()
    try:
        owner=current_owner(db)
        jobs=[int(row["job_id"]) for row in db.execute(
            """SELECT DISTINCT r.job_id
               FROM application_provider_routes_v1 r
               JOIN tsenta_external_dispatch_claims_v1 d
                 ON d.route_id=r.route_id
                AND d.tenant_id=r.tenant_id AND d.user_id=r.user_id
               WHERE r.tenant_id=? AND r.user_id=? AND r.provider='TSENTA'
                 AND r.state IN ('PREPARATION','FILLING','PAUSED',
                                 'PROVIDER_SUBMITTED')
               ORDER BY r.updated_at DESC LIMIT ?""",
            (owner.tenant_id,owner.user_id,int(limit)),
        )]
    finally:
        db.close()
    counts={"checked":0,"provider_submitted":0,"employer_confirmed":0,
            "pending":0,"errors":0,"applications_sent":0}
    for job in jobs:
        try:
            result=reconcile_from_dashboard(job)
            counts["checked"]+=1
            status=result.get("status")
            if status=="EMPLOYER_CONFIRMED":
                counts["employer_confirmed"]+=1
            elif status=="PROVIDER_SUBMITTED":
                counts["provider_submitted"]+=1
            else:counts["pending"]+=1
        except (RuntimeError,LookupError,ValueError,TypeError,
                PermissionError,OSError):
            counts["errors"]+=1
    return counts
