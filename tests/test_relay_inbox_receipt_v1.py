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
        CREATE TABLE jobs(id INTEGER PRIMARY KEY,company_name TEXT,title TEXT,apply_url TEXT);
        CREATE TABLE tsenta_external_dispatch_claims_v1(
           tenant_id TEXT,user_id TEXT,job_id INTEGER,
           state TEXT,created_at TEXT,tsenta_application_id_sha256 TEXT);
        """)
        db.execute(
            "INSERT INTO jobs VALUES(6605,?,?,?)",
            ("Galileo Learning","Regional Talent Acquisition Partner","https://jobs.smartrecruiters.com/galileo/role"),
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



def test_paylocity_employer_ack_requires_exact_provider_app_link():
    import hashlib

    app_id="paylocity-application-123"
    listing=LISTING | {
        "from":"do-not-reply@mail.paylocity.com",
        "applicationId":app_id,
    }
    detail=DETAIL | {
        "from":"do-not-reply@mail.paylocity.com",
        "subject":"Thank you for applying!",
        "body":(
            "Vets Pets thank you for your application to the "
            "Veterinary Recruiting Sourcing Specialist - Contract role."
        ),
    }
    options={
        "company":"Vets Pets",
        "role":"Veterinary Recruiting Sourcing Specialist - Contract",
        "expected_application_ref_sha256":hashlib.sha256(app_id.encode()).hexdigest(),
    }
    assert proof(listing=listing,detail=detail,**options)
    assert proof(listing=listing | {"applicationId":None},detail=detail,**options) is None
    assert proof(listing=listing | {"applicationId":"wrong"},detail=detail,**options) is None
    assert proof(listing=listing | {"from":"attacker@mail.paylocity.com.evil.net"},
                 detail=detail | {"from":"attacker@mail.paylocity.com.evil.net"},**options) is None



@pytest.mark.parametrize("host",[
    "apply.workable.com",
    "jobs.bamboohr.com",
    "jobs.example-ats.net",
    "submissions.hr-platform.co.uk",
])
def test_unlisted_ats_receipt_uses_verified_employer_apply_domain(host):
    import hashlib

    app_id="independent-provider-reference-789"
    from_host="notify." + receipt._registrable_host(host)
    listed=LISTING | {
        "applicationId":app_id,
        "from":"ack@" + from_host,
    }
    details=DETAIL | {
        "from":"ack@" + from_host,
        "subject":"Your application has been received",
        "body":"Galileo Learning: we have your application for Regional Talent Acquisition Partner.",
    }
    assert receipt._message_proof(
        listing=listed, detail=details,
        company="Galileo Learning",role="Regional Talent Acquisition Partner",
        claim_at=CLAIM,
        expected_application_ref_sha256=hashlib.sha256(app_id.encode()).hexdigest(),
        same_company_routes=1,
        employer_application_url="https://" + host + "/apply/selected-opening",
    )


def test_unlisted_ats_does_not_trust_unrelated_sender_or_unbound_email():
    import hashlib

    ref_id="provider-application-456"
    listed=LISTING | {
        "from":"applications@notifications.hiring-platform.example",
        "applicationId":ref_id,
    }
    details=DETAIL | {
        "from":listed["from"],
        "subject":"Thank you for your application",
        "body":"Galileo Learning Regional Talent Acquisition Partner",
    }
    args={
        "listing":listed,"detail":details,
        "company":"Galileo Learning","role":"Regional Talent Acquisition Partner",
        "claim_at":CLAIM,
        "same_company_routes":1,
        "expected_application_ref_sha256":hashlib.sha256(ref_id.encode()).hexdigest(),
        "employer_application_url":"https://jobs.some-other-ats.com/role",
    }
    assert receipt._message_proof(**args) is None
    correct={**args,"employer_application_url":"https://jobs.hiring-platform.example/role"}
    assert receipt._message_proof(**correct)
    assert receipt._message_proof(**(correct | {
        "listing":listed | {"applicationId":None}
    })) is None
    assert receipt._message_proof(**(correct | {
        "listing":listed | {"applicationId":"different-application"}
    })) is None
    assert receipt._message_proof(**(correct | {
        "employer_application_url":"http://jobs.hiring-platform.example/role"
    })) is None
    assert receipt._message_proof(**(correct | {
        "listing":listed | {"from":"applications@hiring-platform.example.attacker.com"},
        "detail":details | {"from":"applications@hiring-platform.example.attacker.com"}
    })) is None


@pytest.mark.parametrize("body",[
    "Galileo Learning Regional Talent Acquisition Partner: your application is incomplete. Thank you for applying.",
    "Galileo Learning Regional Talent Acquisition Partner: unable to submit your application. Thank you for applying.",
])
def test_negative_email_does_not_count_as_confirmation(body):
    assert proof(detail=DETAIL | {"body":body}) is None


@pytest.mark.parametrize("host,expected",[
    ("secure.jobs.employer.co.uk","employer.co.uk"),
    ("hiring.jobs.site.com","site.com"),
    ("co.uk",None),
    ("127.0.0.1",None),
    ("jobs.evil.com.attacker.com","attacker.com"),
    ("jobs..broken.com",None),
])
def test_registrable_hostname_parser_rejects_invalid_shared_suffix(host,expected):
    assert receipt._registrable_host(host)==expected



def test_unlinked_lookalike_employer_subdomain_does_not_count():
    bad=LISTING | {"from":"notice@galileolearning.com.attacker.org"}
    detail=DETAIL | {"from":bad["from"]}
    assert proof(listing=bad,detail=detail) is None


@pytest.mark.parametrize("shared",["github.io","pages.dev","vercel.app"])
def test_unrelated_shared_hosting_tenants_cannot_cross_confirm(shared):
    import hashlib
    appid="signed-app-reference"
    listing=LISTING | {"applicationId":appid,"from":"no-reply@a." + shared}
    detail=DETAIL | {"from":listing["from"],"body":DETAIL["body"]+" Regional Talent Acquisition Partner"}
    assert receipt._message_proof(
        listing=listing,detail=detail,
        company="Galileo Learning",role="Regional Talent Acquisition Partner",
        claim_at=CLAIM,
        expected_application_ref_sha256=hashlib.sha256(appid.encode()).hexdigest(),
        same_company_routes=1,
        employer_application_url="https://b."+shared+"/apply/role",
    ) is None

def test_unlinked_authenticated_managed_icims_receipt_verified_by_exact_role_company():
    listing=LISTING | {
        "source":"SES", "from":"postholdings+autoreply@talent.icims.com",
        "applicationId":None, "receivedAt":"2026-10-18T19:08:25Z",
    }
    detail=DETAIL | {
        "from":listing["from"],
        "subject":"Thank You for Applying to the role HR Coordinator at Bob Evans Farms",
        "body":("Dear candidate, thank you very much for your recent application "
                "to Bob Evans Farms. Your submission will be reviewed."),
    }
    args={"listing":listing,"detail":detail,"company":"Bob Evans Farms",
          "role":"HR Coordinator","claim_at":"2026-10-18T19:05:14Z",
          "expected_application_ref_sha256":REF,"same_company_routes":1,
          "employer_application_url":"https://bobevanshrjobs-postholdings.icims.com/jobs/6693/"}
    assert receipt._message_proof(**args) is not None
    assert receipt._message_proof(**(args|{"same_company_routes":2})) is None
    assert receipt._message_proof(**(args|{"employer_application_url":"https://not-icims.example/jobs/6693"})) is None
    assert receipt._message_proof(**(args|{"listing":listing|{"source":"INBOX"}})) is None
    assert receipt._message_proof(**(args|{"detail":detail|{"subject":"Thank you for your application"}})) is None
