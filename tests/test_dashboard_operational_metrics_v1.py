"""Application dashboard must never reuse historical n8n queue counts."""
from datetime import datetime, timedelta, timezone

from app import dashboard_operational_metrics_v1 as metrics


def _flow(lifecycle_state, **route):
    return {
        "state":lifecycle_state,
        "provider_route":{"provider":"TSENTA",**route},
        "production_receipt":None,
    }


def test_dashboard_tracks_each_live_application_not_old_n8n_rows():
    now=datetime(2026,10,18,4,0,tzinfo=timezone.utc)
    recent=(now-timedelta(minutes=23)).isoformat()
    old=(now-timedelta(days=8)).isoformat()
    applied={
        "state":"EMPLOYER_CONFIRMED",
        "provider_submitted_at":recent,
        "provider_application_id_sha256":"a"*64,
        "receipt_evidence_digest":"b"*64,
        "employer_confirmed_at":recent,
    }
    sent={
        "state":"PROVIDER_SUBMITTED",
        "provider_submitted_at":recent,
        "provider_application_id_sha256":"c"*64,
        "dispatch_claim_exists":True,
    }
    flows=[
        _flow("SUBMITTED",**applied),
        _flow("SUBMISSION_UNVERIFIED",**sent),
        _flow("SUBMITTING",state="PREPARATION",dispatch_claim_exists=True),
        _flow("READY_FOR_APPROVAL"),
        _flow("READY_FOR_REVIEW"),
        _flow("FAILED_SAFELY"),
        _flow("SUBMITTED",**(applied | {"provider_submitted_at":old})),
    ]
    a=metrics.summarize_pipeline(flows,now=now)
    assert a=={
        "tracked":7,"sent_24h":2,"employer_confirmed":2,
        "needs_approval":2,"awaiting_receipt":1,
        "outcome_unknown":1,"actively_processing":0,
        "recovery_needed":1,
    }


def test_no_external_submission_without_actual_provider_id_or_timestamp():
    now=datetime(2026,10,18,4,0,tzinfo=timezone.utc)
    entries=[
        _flow("SUBMITTED",state="EMPLOYER_CONFIRMED",
              receipt_evidence_digest="a"*64,
              provider_submitted_at=now.isoformat()),
        _flow("SUBMITTED",state="EMPLOYER_CONFIRMED",
              provider_application_id_sha256="b"*64,
              provider_submitted_at=now.isoformat()),
        _flow("SUBMISSION_UNVERIFIED",state="PROVIDER_SUBMITTED",
              provider_application_id_sha256="c"*64),
    ]
    a=metrics.summarize_pipeline(entries,now=now)
    # A provider ID plus submission time confirms a send, even without
    # employer confirmation; it does NOT imply an employer receipt.
    assert a["sent_24h"]==1
    assert a["employer_confirmed"]==0
    assert a["awaiting_receipt"]==1


def test_native_receipt_counts_once_from_timestamp():
    now=datetime(2026,10,18,4,0,tzinfo=timezone.utc)
    entry={
        "state":"SUBMITTED","provider_route":None,
        "production_receipt":{
            "verified_receipt_id":"receipt-1",
            "submitted_at":now.isoformat(),
        },
    }
    a=metrics.summarize_pipeline([entry],now=now)
    assert a["sent_24h"]==1
    assert a["employer_confirmed"]==1


def test_unknown_never_counted_as_provider_sent():
    now=datetime(2026,10,18,4,0,tzinfo=timezone.utc)
    f=_flow("SUBMITTING",state="PREPARATION",
            dispatch_claim_exists=True)
    a=metrics.summarize_pipeline([f],now=now)
    assert a["outcome_unknown"]==1
    assert a["sent_24h"]==0
    assert a["needs_approval"]==0


def test_current_dashboard_calls_authoritative_application_center(monkeypatch):
    from app import product_application_flow_v2 as flow

    called=[]
    monkeypatch.setattr(flow,"pipeline_rows",lambda limit:[
        called.append(limit) or
        {"_flow_projection":{"state":"READY_FOR_APPROVAL",
                             "provider_route":None,
                             "production_receipt":None}}
    ])
    assert metrics.operational_activity_summary()["needs_approval"]==1
    assert called==[None]
