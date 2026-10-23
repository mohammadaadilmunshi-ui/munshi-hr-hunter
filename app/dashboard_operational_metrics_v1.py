"""Dashboard counters derived from the Application Center source of truth.

Legacy n8n job-results are not submission receipts and are not counted as
current approvals, active applications, or confirmed submissions here.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

_ACTIVE_STATES=frozenset({
    "QUEUED","RUNNING","PREPARING","PREPARING_ATS","READY_TO_SUBMIT",
})
_REVIEW_STATES=frozenset({"READY_FOR_APPROVAL","READY_FOR_REVIEW"})
_TERMINAL_SUBMITTED=frozenset({
    "SUBMITTED","VERIFIED","AWAITING_RESPONSE","ASSESSMENT","INTERVIEW",
    "OFFER","REJECTED","WITHDRAWN",
})


def _datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        d=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def summarize_pipeline(
    flows: list[Mapping[str,Any]],
    *,
    now:datetime | None=None,
) -> dict[str,int]:
    """Each application contributes to one current-state counter.

    Submission in the last 24h requires an actual provider sent timestamp or
    independently recorded native receipt; preflight completion is not a send.
    """
    moment=now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment=moment.replace(tzinfo=timezone.utc)
    moment=moment.astimezone(timezone.utc)
    since=moment-timedelta(hours=24)
    out={
        "tracked":0,"sent_24h":0,"employer_confirmed":0,
        "needs_approval":0,"awaiting_receipt":0,
        "outcome_unknown":0,"actively_processing":0,
        "recovery_needed":0,
    }
    for item in flows:
        out["tracked"]+=1
        state=str(item.get("state") or "").upper()
        route=item.get("provider_route") or {}
        receipt=item.get("production_receipt") or {}
        if state in _REVIEW_STATES:
            out["needs_approval"]+=1
        elif route.get("provider")=="TSENTA" and route.get("dispatch_claim_exists") and (
            route.get("state") not in {"EMPLOYER_CONFIRMED","PROVIDER_SUBMITTED"}
        ):
            out["outcome_unknown"]+=1
        elif state=="SUBMISSION_UNVERIFIED":
            out["awaiting_receipt"]+=1
        elif state in _ACTIVE_STATES or state=="SUBMITTING":
            out["actively_processing"]+=1
        elif state in {"FAILED_SAFELY","BLOCKED","NEEDS_INPUT","CANCELLED"}:
            out["recovery_needed"]+=1
        if (
            state in _TERMINAL_SUBMITTED
            and (
                (route.get("state")=="EMPLOYER_CONFIRMED"
                 and bool(route.get("receipt_evidence_digest"))
                 and bool(route.get("provider_application_id_sha256"))
                 and bool(route.get("employer_confirmed_at")))
                or bool(receipt.get("verified_receipt_id"))
                or bool(receipt.get("receipt_evidence_digest"))
            )
        ):
            out["employer_confirmed"]+=1
        evidence_time=_datetime(
            route.get("provider_submitted_at")
            if route.get("provider")=="TSENTA" else
            receipt.get("submitted_at") or receipt.get("verified_at")
        )
        has_sent_evidence=(
            route.get("state") in {"PROVIDER_SUBMITTED","EMPLOYER_CONFIRMED"}
            and bool(route.get("provider_application_id_sha256"))
            if route.get("provider")=="TSENTA" else bool(receipt)
        )
        if has_sent_evidence and evidence_time and since<=evidence_time<=moment:
            out["sent_24h"]+=1
    return out


def operational_activity_summary() -> dict[str,int]:
    from app.product_application_flow_v2 import pipeline_rows

    return summarize_pipeline([
        row["_flow_projection"] for row in pipeline_rows(limit=None)
        if isinstance(row.get("_flow_projection"),dict)
    ])
