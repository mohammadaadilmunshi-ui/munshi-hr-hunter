"""Evidence-only, first-party sender match; no actual inbox or email sends."""
from __future__ import annotations

import hashlib
import json

import pytest

from app import verified_employer_domains_v1 as registry
from app import relay_inbox_receipt_v1 as receipts

ATS="https://job-boards.greenhouse.io/ansabiotechnologies/jobs/4753487005"
APP_ID="provider-linked-ansa-app-1"
CLAIM_AT="2026-11-16T19:18:56+00:00"
LISTING={
    "id":"inbox-msg-ansa-1",
    "source":"SES",
    "direction":"INBOUND",
    "category":"ACKNOWLEDGMENT",
    "from":"careers@ansabio.com",
    "receivedAt":"2026-11-16T19:20:05+00:00",
    "threadId":"thread-ansa",
    "applicationId":APP_ID,
}
DETAIL={
    "id":"inbox-msg-ansa-1",
    "from":"careers@ansabio.com",
    "subject":"Thank you for applying to Ansa Biotechnologies!",
    "body":"Mohammad, thanks for your interest in working with us at Ansa Biotechnologies! Your application has been received and we look forward to reviewing it shortly.",
}


def _proof(listing=LISTING,detail=DETAIL,**other):
    args=dict(
        listing=listing,detail=detail,
        company="Ansa Biotechnologies",role="HR Coordinator",
        claim_at=CLAIM_AT,
        expected_application_ref_sha256=hashlib.sha256(APP_ID.encode()).hexdigest(),
        same_company_routes=1,
        employer_application_url=ATS,
    )
    args.update(other)
    return receipts._message_proof(**args)


def test_reviewed_domain_plus_exact_provider_id_allows_ansa_confirmation():
    evidence=_proof()
    assert isinstance(evidence,str) and len(evidence)==64
    assert evidence==_proof()
    assert registry.reviewed_sender_matches(
        company="Ansa Biotechnologies",sender_domain="ansabio.com",
        employer_application_url=ATS,source="SES",
        linked_application_id=APP_ID,
    )


@pytest.mark.parametrize("changed",[
    {"source":"GMAIL"},
    {"source":"UNVERIFIED"},
    {"direction":"OUTBOUND"},
    {"applicationId":None},
    {"applicationId":"wrong-application"},
    {"from":"careers@ansabio.com.evil.test"},
    {"from":"careers@ansabio-careers.com"},
    {"receivedAt":"2026-11-16T16:06:35Z"},
    {"receivedAt":"2026-12-01T19:20:05Z"},
    {"category":"REMINDER"},
    {"id":"message-other"},
])
def test_ansa_untrusted_or_unbound_inbox_metadata_never_verifies(changed):
    assert _proof(listing=LISTING|changed) is None


@pytest.mark.parametrize("overrides",[
    {"employer_application_url":"https://job-boards.greenhouse.io/differentco/jobs/4753487005"},
    {"employer_application_url":"https://job-boards.greenhouse.io.evil.test/ansabiotechnologies/jobs/4753487005"},
    {"employer_application_url":"http://job-boards.greenhouse.io/ansabiotechnologies/jobs/4753487005"},
    {"employer_application_url":"https://other-greenhouse.io/ansabiotechnologies/jobs/4753487005"},
    {"company":"Other Employer"},
    {"expected_application_ref_sha256":"f"*64},
    {"claim_at":"2026-11-01T19:18:56Z"},
])
def test_ansa_confirmation_rejects_wrong_ats_identity_and_time(overrides):
    assert _proof(**overrides) is None


def test_sender_domain_needs_explicit_ack_and_consistent_from_headers():
    assert _proof(detail=DETAIL|{"body":"Your application was not submitted."}) is None
    assert _proof(detail=DETAIL|{"from":"fake@example.com"}) is None
    assert _proof(detail=DETAIL|{"subject":"Company newsletter","body":"New roles at Ansa."}) is None


def test_domain_registry_malformed_data_fails_closed(tmp_path,monkeypatch):
    data=tmp_path/"verified.json"
    monkeypatch.setattr(registry,"_MANIFEST",data)
    registry._reviewed.cache_clear()
    try:
        data.write_text('{"schema_version":2,"reviewed_employers":[]}')
        assert registry._reviewed()==()
        data.write_text(json.dumps({"schema_version":1,"reviewed_employers":[
            {"company_name":"Ansa Biotechnologies","sender_domain":"ansabio.com",
             "ats_board_origin":"https://job-boards.greenhouse.io.evil.test/ansabiotechnologies"}
        ]}))
        registry._reviewed.cache_clear()
        assert registry._reviewed()==()
        data.write_text('not-json')
        registry._reviewed.cache_clear()
        assert registry._reviewed()==()
    finally:
        registry._reviewed.cache_clear()
