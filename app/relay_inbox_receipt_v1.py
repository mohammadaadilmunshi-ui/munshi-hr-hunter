"""Authenticate, correlate and digest employer confirmation from managed inbox.

No email body or personal data is stored. Only unique recent employer matches
for an already provider-submitted application qualify. Unmatched messages stay
unassigned; inbox tool calls never send, delete or mark messages.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any
from urllib.parse import urlsplit

from app import application_provider_router_v1 as router
from app.database import get_connection
from app.tenant_foundation import current_owner
from app.tsenta_mcp_adapter_v1 import _unwrap

ALLOWED_GENERIC_ATS_DOMAINS = frozenset({
    "smartrecruiters.com", "greenhouse.io", "lever.co",
    "myworkday.com", "workday.com", "ashbyhq.com",
    "mail.paylocity.com",
})
ACKNOWLEDGEMENTS = (
    "thank you for your application",
    "thank you for applying",
    "we have received your application",
    "we received your application",
    "application has been received",
    "thank you for submitting your application",
    "application is queued for review",
    "we have your application",
    "we've received your application",
    "your application has been received",
    "your application was successfully submitted",
    "your application has been successfully submitted",
)
FAILED_ACKNOWLEDGEMENTS = (
    "application was not submitted",
    "application has not been submitted",
    "application not received",
    "we did not receive your application",
    "unable to submit your application",
    "could not submit your application",
    "your application is incomplete",
    "please complete your application",
)
MAX_HOURS_AFTER_CLAIM=96
MAX_PROVIDER_INBOX_PAGE=20


def _ascii_words(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _time(text: object) -> datetime | None:
    try:
        t=datetime.fromisoformat(str(text).replace("Z","+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except (TypeError, ValueError):
        return None


def _registrable_host(hostname: str | None) -> str | None:
    """Conservative offline shared-site identity, not arbitrary substring matching.

    Multi-level country-code suffixes (e.g. co.uk) require an extra label.
    Refuse IP addresses, invalid hostnames, and bare shared public suffixes.
    """
    host=str(hostname or "").strip().casefold().rstrip(".")
    if not host or len(host)>253:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    labels=host.split(".")
    if len(labels)<2 or any(
        len(part)>63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?",part)
        for part in labels
    ):
        return None
    if len(labels[-1])==2 and labels[-2] in {
        "ac","co","com","edu","gov","mil","net","org"
    }:
        if len(labels)<3:
            return None
        return ".".join(labels[-3:])
    common=".".join(labels[-2:])
    # Shared hosting suffixes let unrelated tenants create lookalike senders.
    # This list contains unsafe PUBLIC HOSTING boundaries, not ATS vendors.
    if common in {
        "github.io","pages.dev","vercel.app","netlify.app","workers.dev",
        "appspot.com","herokuapp.com","cloudfront.net","firebaseapp.com",
        "azurewebsites.net","blogspot.com",
    }:
        return None
    return common


def _verified_destination_host(value: str | None) -> str | None:
    try:
        parsed=urlsplit(str(value or ""))
        if parsed.scheme.casefold()!="https" or parsed.username or parsed.password:
            return None
        return _registrable_host(parsed.hostname)
    except ValueError:
        return None


def _message_proof(
    *, listing: Mapping[str,Any], detail: Mapping[str,Any],
    company: str, role: str, claim_at: str,
    expected_application_ref_sha256: str,
    same_company_routes: int,
    employer_application_url: str | None = None,
) -> str | None:
    """Fail closed for unlinked or contradictory employer evidence."""
    ident=str(listing.get("id") or "")
    if not ident or ident != str(detail.get("id") or ""):
        return None
    if str(listing.get("source") or "").upper() not in {"SES", "INBOX"}:
        return None
    if str(listing.get("direction") or "").upper()!="INBOUND":
        return None
    if str(listing.get("category") or "").upper()!="ACKNOWLEDGMENT":
        return None
    sender=parseaddr(str(detail.get("from") or listing.get("from") or ""))[1].casefold()
    if "@" not in sender or sender.startswith("@"):
        return None
    host=sender.rsplit("@",1)[1]
    company_slug=_ascii_words(company)
    is_employer=_ascii_words((_registrable_host(host) or "").split(".")[0])==company_slug
    is_known_ats=host in ALLOWED_GENERIC_ATS_DOMAINS
    # A verified employer apply URL identifies the ATS web domain.
    # New ATS domains qualify without changing a hardcoded sender allowlist,
    # but only when the message also names the exact bound application.
    is_destination_ats=(
        _registrable_host(host) is not None
        and _registrable_host(host)==_verified_destination_host(employer_application_url)
    )
    if not (is_employer or is_known_ats or is_destination_ats):
        return None
    received=_time(listing.get("receivedAt"))
    issued=_time(claim_at)
    if received is None or issued is None or not (
        issued-timedelta(minutes=2) <= received
        <= issued+timedelta(hours=MAX_HOURS_AFTER_CLAIM)
    ):
        return None
    linked=listing.get("applicationId")
    if (
        linked is not None
        and hashlib.sha256(str(linked).encode()).hexdigest()
        != expected_application_ref_sha256
    ):
        return None
    # An unlinked confirmation may be correlated only if a single employer
    # application is present. Otherwise require a provider application ID.
    if linked is None and same_company_routes!=1:
        return None
    # Any sender accepted through application-destination inference must have
    # a matching provider application ID. Never infer a receipt from matching
    # company text alone, even when a provider assigns COMPANY_UNIQUE metadata.
    if (is_destination_ats or host=="mail.paylocity.com") and linked is None:
        return None
    # Validate provider list/detail agreement and explicit employer intent.
    if str(listing.get("from") or "").casefold()!=str(detail.get("from") or "").casefold():
        return None
    subject=" ".join(str(detail.get("subject") or "").casefold().split())
    body=" ".join(str(detail.get("body") or "").casefold().split())
    combined=subject+" "+body
    normalized=_ascii_words(combined)
    if company_slug not in normalized:
        return None
    if not any(term in combined for term in ACKNOWLEDGEMENTS):
        return None
    if any(term in combined for term in FAILED_ACKNOWLEDGEMENTS):
        return None
    # Shared ATS senders must name the role, including newly inferred ATS
    # domains. Employer-owned sender with no title needs unique company route.
    if (is_known_ats or is_destination_ats) and _ascii_words(role) not in normalized:
        return None
    if not (is_known_ats or is_destination_ats) and linked is None and same_company_routes!=1:
        return None
    envelope={
        "provider":"AUTHENTICATED_MANAGED_INBOX","message_id":ident,
        "thread_id":str(listing.get("threadId") or ""),
        "sender_sha256":hashlib.sha256(sender.encode()).hexdigest(),
        "received_at":received.isoformat(),
        "body_sha256":hashlib.sha256(str(detail.get("body") or "").encode()).hexdigest(),
        "subject_sha256":hashlib.sha256(str(detail.get("subject") or "").encode()).hexdigest(),
        "employer":company_slug,
        "application_ref_sha256":expected_application_ref_sha256,
    }
    return hashlib.sha256(json.dumps(envelope,sort_keys=True).encode()).hexdigest()


def reconcile_employer_inbox(*, job_id:int, mcp:Any) -> dict[str,Any]:
    """Read the provider-managed inbox and promote independently verified email."""
    db=get_connection()
    try:
        owner=current_owner(db)
        route=db.execute(
            """SELECT * FROM application_provider_routes_v1
               WHERE tenant_id=? AND user_id=? AND job_id=?""",
            (owner.tenant_id,owner.user_id,int(job_id)),
        ).fetchone()
        job=db.execute("SELECT * FROM jobs WHERE id=?",(int(job_id),)).fetchone()
        claim=db.execute(
            """SELECT * FROM tsenta_external_dispatch_claims_v1
               WHERE tenant_id=? AND user_id=? AND job_id=?""",
            (owner.tenant_id,owner.user_id,int(job_id)),
        ).fetchone()
        if route is None or job is None or claim is None:
            raise LookupError("Opening has no owned managed-inbox receipt binding.")
        if route["state"]=="EMPLOYER_CONFIRMED":
            return {"status":"EMPLOYER_CONFIRMED","matched":True}
        if route["provider"]!="TSENTA" or route["state"]!="PROVIDER_SUBMITTED":
            return {"status":"NOT_PROVIDER_SUBMITTED","matched":False}
        ref=route["provider_application_id_sha256"]
        if not ref or ref!=claim["tsenta_application_id_sha256"] or claim["state"]!="ACCEPTED":
            return {"status":"PROVIDER_IDENTITY_UNVERIFIED","matched":False}
        company=str(job["company_name"] or "")
        role=str(job["title"] or "")
        routes=db.execute(
            """SELECT count(*) FROM application_provider_routes_v1 r
               JOIN jobs j ON r.job_id=j.id
               WHERE r.tenant_id=? AND r.user_id=?
                 AND lower(j.company_name)=lower(?)
                 AND r.provider='TSENTA'
                 AND r.state IN ('PROVIDER_SUBMITTED','EMPLOYER_CONFIRMED')""",
            (owner.tenant_id,owner.user_id,company),
        ).fetchone()[0]
        route_id=route["route_id"]
        created=claim["created_at"]
    finally:
        db.close()
    listing=_unwrap(mcp.call_read("list_inbox_messages",{
        "query":company,"page":1,"limit":MAX_PROVIDER_INBOX_PAGE,
    }),tool_name="list_inbox_messages")
    entries=listing.get("messages") if isinstance(listing,Mapping) else None
    if not isinstance(entries,list) or len(entries)>MAX_PROVIDER_INBOX_PAGE:
        return {"status":"INBOX_UNAVAILABLE","matched":False}
    for item in entries:
        if not isinstance(item,Mapping) or not item.get("id"):
            continue
        if str(item.get("category") or "").upper()!="ACKNOWLEDGMENT":
            continue
        detail=_unwrap(mcp.call_read("get_inbox_message",{
            "messageId":str(item["id"]),"bodyMaxChars":4000,
        }),tool_name="get_inbox_message")
        if not isinstance(detail,Mapping):
            continue
        digest=_message_proof(
            listing=item,detail=detail,company=company,role=role,
            claim_at=created,expected_application_ref_sha256=ref,
            same_company_routes=int(routes),
            employer_application_url=str(job["apply_url"] or ""),
        )
        if digest:
            updated=router.confirm_employer_receipt(
                route_id=route_id,
                verification_method="CORRELATED_EMPLOYER_EMAIL",
                evidence_digest=digest,
            )
            return {
                "status":str(updated["state"]),
                "matched":True,
                "receipt_method":"CORRELATED_EMPLOYER_EMAIL",
            }
    return {"status":"NO_CORRELATED_EMPLOYER_EMAIL","matched":False}
