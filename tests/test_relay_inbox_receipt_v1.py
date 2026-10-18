"""Read-only employer inbox correlation; never invent or submit a receipt."""

import pytest

from app import relay_inbox_receipt_v1 as receipt

REF="f"*64
CLAIM="2026-10-18T01:21:52+00:00"
LISTING={
    "id":"provider-message-1","source":"SES","direction":"INBOUND",
    "category":"ACKNOWLEDGMENT",
    "from":"notification@galileo-learning.com",
    "receivedAt":"2026-10-18T01:23:21+00:00",
    "threadId":"thread-1","applicationId":None,
}
DETAIL={
    "id":"provider-message-1","from":"notification@galileo-learning.com",
    "subject":"Thank you for your Galileo Learning application",
    "body":"Greetings from Galileo. Thank you for your application to Galileo Learning! We will review your application.",
}


def proof(listing=None,detail=None,**overrides):
    kw={
        "listing":LISTING if listing is None else listing,
        "detail":DETAIL if detail is None else detail,
        "company":"Galileo Learning",
        "role":"Regional Talent Acquisition Partner",
        "claim_at":CLAIM,
        "expected_application_ref_sha256":REF,
        "same_company_routes":1,
    }
    kw.update(overrides)
    return receipt._message_proof(**kw)


def test_authenticated_employer_acknowledgment_is_a_stable_digest():
    first=proof()
    assert isinstance(first,str) and len(first)==64
    assert first==proof()


@pytest.mark.parametrize("mutated",[
    {"id":"different-message"},
    {"source":"UNTRUSTED"},
    {"direction":"OUTBOUND"},
    {"category":"NEWSLETTER"},
    {"from":"phishing@example.net"},
    {"receivedAt":"2025-01-01T00:00:00Z"},
    {"receivedAt":"2026-11-01T00:00:00Z"},
    {"applicationId":"wrong-application-id"},
])
def test_unmatched_inbox_messages_are_never_receipts(mutated):
    assert proof(listing=LISTING | mutated) is None


def test_ambiguous_employer_is_never_promoted_from_unlinked_email():
    assert proof(same_company_routes=2) is None


def test_message_without_acknowledgment_does_not_prove_submission():
    assert proof(detail=DETAIL | {
        "body":"Learn about open roles at Galileo Learning",
        "subject":"Galileo Learning newsletter",
    }) is None


def test_detail_sender_must_match_authenticated_list_metadata():
    assert proof(detail=DETAIL | {"from":"attacker@example.net"}) is None


def test_known_ats_sender_must_include_exact_job_title():
    listed=LISTING | {"from":"notification@smartrecruiters.com"}
    detail=DETAIL | {"from":"notification@smartrecruiters.com"}
    assert proof(listing=listed,detail=detail) is None
    detail["body"] += " Regional Talent Acquisition Partner."
    assert proof(listing=listed,detail=detail) is not None


def test_signed_external_account_id_is_required_if_present():
    import hashlib
    app_id="provider-application-42"
    assert proof(
        listing=LISTING | {"applicationId":app_id},
        expected_application_ref_sha256=hashlib.sha256(app_id.encode()).hexdigest(),
    ) is not None


def test_untrusted_email_never_submits_or_sets_receipt(monkeypatch):
    assert not hasattr(receipt,"apply_to_job")
    assert not hasattr(receipt,"send_email")


def test_inbox_sync_requires_provider_submission_and_matches_only_exact_owner(
    tmp_path,monkeypatch,
):
    import sqlite3
    from types import SimpleNamespace
    dbfile=tmp_path/"managed-receipts.sqlite"
    with sqlite3.connect(dbfile) as db:
        db.executescript("""
        CREATE TABLE application_provider_routes_v1(
           route_id TEXT,tenant_id TEXT,user_id TEXT,job_id INTEGER,
           provider TEXT,state TEXT,provider_application_id_sha256 TEXT);
        CREATE TABLE jobs(id INTEGER PRIMARY KEY,company_name TEXT,title TEXT);
        CREATE TABLE tsenta_external_dispatch_claims_v1(
           tenant_id TEXT,user_id TEXT,job_id INTEGER,
           state TEXT,created_at TEXT,tsenta_application_id_sha256 TEXT);
        """)
        db.execute(
            "INSERT INTO jobs VALUES(6605,?,?)",
            ("Galileo Learning","Regional Talent Acquisition Partner"),
        )
        db.execute(
            "INSERT INTO application_provider_routes_v1 VALUES(?,?,?,?,?,?,?)",
            ("r1","t","u",6605,"TSENTA","PROVIDER_SUBMITTED",REF),
        )
        db.execute(
            "INSERT INTO tsenta_external_dispatch_claims_v1 VALUES(?,?,?,?,?,?)",
            ("t","u",6605,"ACCEPTED",CLAIM,REF),
        )

    def connection():
        db=sqlite3.connect(dbfile)
        db.row_factory=sqlite3.Row
        return db
    monkeypatch.setattr(receipt,"get_connection",connection)
    monkeypatch.setattr(
        receipt,"current_owner",
        lambda _db:SimpleNamespace(tenant_id="t",user_id="u"),
    )
    actions=[]
    monkeypatch.setattr(
        receipt.router,"confirm_employer_receipt",
        lambda **kwargs:actions.append(kwargs) or {"state":"EMPLOYER_CONFIRMED"},
    )

    class Inbox:
        def __init__(self):self.reads=[]
        def call_read(self,method,options):
            self.reads.append(method)
            if method=="list_inbox_messages":
                assert options["query"]=="Galileo Learning"
                assert options["limit"] == receipt.MAX_PROVIDER_INBOX_PAGE == 20
                return {"structuredContent":{"result":{"messages":[LISTING]}}}
            if method=="get_inbox_message":
                assert options["messageId"]==LISTING["id"]
                return {"structuredContent":{"result":DETAIL}}
            raise AssertionError("No reply or submission is permitted")

    provider=Inbox()
    result=receipt.reconcile_employer_inbox(job_id=6605,mcp=provider)
    assert result["status"]=="EMPLOYER_CONFIRMED"
    assert provider.reads==["list_inbox_messages","get_inbox_message"]
    assert len(actions)==1
    assert actions[0]["verification_method"]=="CORRELATED_EMPLOYER_EMAIL"
    assert actions[0]["evidence_digest"]==proof()

    # A route which is still merely PREPARING must not be promoted by email.
    with sqlite3.connect(dbfile) as c:
        c.execute("UPDATE application_provider_routes_v1 SET state='PREPARATION'")
    actions.clear()
    provider.reads.clear()
    result=receipt.reconcile_employer_inbox(job_id=6605,mcp=provider)
    assert result["status"]=="NOT_PROVIDER_SUBMITTED"
    assert provider.reads==[]
    assert actions==[]
