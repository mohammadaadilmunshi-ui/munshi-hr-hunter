"""Deterministic one-shot Relay submission boundary tests.

Zero real network/SQLite writes; mocks assert exact operations and ordering.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from app import relay_application_dispatch_v1 as relay

URL="https://jobs.smartrecruiters.com/DohlerGmbH/744000154149608-human-resources-coordinator"
PDF=b"%PDF-1.4\n"+(b"synthetic proof\n"*25)+b"%%EOF"


class FakeDB:
    def __init__(self,log):self.log=log
    def execute(self,query,params=()):
        if "SELECT * FROM jobs WHERE id" in query:
            return SimpleNamespace(fetchone=lambda: {
                "id":6198,"source":"Indeed","company_name":"Dohler",
                "title":"Human Resources Coordinator","apply_url":URL,
            })
        if "UPDATE jobs SET already_applied" in query:
            self.log.append("mark_already_applied")
            return SimpleNamespace(rowcount=1)
        raise AssertionError("Unexpected SQL for mocked dispatcher")

    def commit(self): self.log.append("commit")
    def close(self): pass


class FakeClient:
    def __init__(self,token):
        assert token=="synthetic"
        self.calls=[]
        self._tools={}
        self.raise_on_apply=False
        self._catalog={
            "tools":[{"name":n} for n in (
                "upload-custom-resume","check-custom-resume",
                "apply-with-custom-resume",
            )],
        }

    def __enter__(self):return self
    def __exit__(self,*_args):pass
    def initialize(self):pass
    def _send(self,name,args):
        assert name=="tools/list"
        return self._catalog

    def call_read(self,name,args=None):
        raise AssertionError("Unexpected remote read")
    def call_application(self,name,args,claimed_for_single_use=False):
        assert claimed_for_single_use
        self.calls.append((name,args))
        if name=="upload_custom_resume":
            assert set(args)=={"fileBase64"}
            return {"result":{"uploadId":"synthetic-upload","byteSize":len(PDF)}}
        if name=="check_custom_resume":
            assert args=={"uploadId":"synthetic-upload"}
            return {"result":{"uploadId":"synthetic-upload","byteSize":len(PDF),
                              "viewUrl":"https://example.net/document"}}
        if name=="apply_with_custom_resume":
            assert args["url"]==URL
            assert args["uploadId"]=="synthetic-upload"
            assert args["profile"]["personalInformation"]["email"]=="alias@example.org"
            assert args["profile"]["workAuthorization"]["needsSponsorship"] is True
            assert args["profile"]["workAuthorization"]["visaType"]=="F-1"
            if self.raise_on_apply:
                raise TimeoutError("Remote request outcome unknown")
            return {"result":{"summary":{"total":1},"results":[{}]}}
        raise AssertionError(name)


class FakeAdapter:
    def __init__(self,*args,**kwargs):pass
    def read_account_snapshot(self):
        return SimpleNamespace(
            selected_resume_profile={"data":{
                "personalInformation":{"firstName":"Test","lastName":"Applicant",
                                       "email":"other@example.org"},
                "workAuthorization":{
                    "visaType":"OPT","optStatus":"Pending",
                    "needsSponsorship":False,
                },
                "workPreferences":{"canStartImmediately":True},
            }},
            privacy_safe_summary=lambda: {"onboarding_complete":True},
        )
    @staticmethod
    def _verified_custom_resume(*args,**kwargs):
        return True


def setup(monkeypatch,*,unknown=False):
    from app import tsenta_backend_readiness_v1
    from app import product_application_flow_v2 as flows

    log=[]
    client=FakeClient("synthetic")
    client.raise_on_apply=unknown
    pkg={
        "provider":"TSENTA","state":"PREPARATION",
        "route_id":"route-test","application_id":"app-test",
        "job_id":6198,"employer_url":URL,
        "resume_sha256":"f"*64,"package_digest":"p"*64,
        "preflight_package_id":"package-test",
    }
    monkeypatch.setattr(
        tsenta_backend_readiness_v1,"connection_status",
        lambda:{"execution_ready":True},
    )
    monkeypatch.setattr(flows,"flow_for_job",lambda _: {
        "state":"READY_FOR_APPROVAL",
        "package_review":{"review_digest":"a"*64},
        "artifacts":{"preflight":{"preflight_package_id":"package-test"}},
        "provider_route":None,
    })
    monkeypatch.setattr(relay.router,"select_provider",lambda **kwargs: pkg)
    monkeypatch.setattr(relay,"get_connection",lambda: FakeDB(log))
    monkeypatch.setattr(relay,"_load_bound_artifact",lambda route:(
        PDF,hashlib.sha256(PDF).hexdigest(),"alias@example.org",
    ))
    monkeypatch.setattr(relay,"runtime_health",lambda:{"ready":True})
    monkeypatch.setattr(relay,"_token_for_current_owner",lambda: "synthetic")
    monkeypatch.setattr(relay,"TsentaMcpApplicationClient",lambda _:client)
    monkeypatch.setattr(relay,"TsentaMcpAdapter",FakeAdapter)
    from app import tsenta_submission_preflight_v1 as preflight
    monkeypatch.setattr(preflight,"submission_readiness",
                        lambda **kwargs:{"ready_for_review":True,"blockers":[]})
    monkeypatch.setattr(relay,"_authorize_claim",
                        lambda **kwargs:log.append("claim") or "claim-1")
    monkeypatch.setattr(relay,"_mark_claim",
                        lambda key,state,app_id=None:log.append(f"mark_{state}"))
    monkeypatch.setattr(relay,"_confirmed_application",
                        lambda *args:("provider-app-id","COMPLETED"))
    monkeypatch.setattr(relay,"_prior_provider_application",
                        lambda *args:False)
    monkeypatch.setattr(relay.router,"record_provider_status",
                        lambda **kwargs:{"state":"PROVIDER_SUBMITTED"})
    return client,log


def test_one_click_stages_document_claims_before_applying_and_marks_submitted(
    monkeypatch,
):
    client,log=setup(monkeypatch)
    result=relay.submit_from_dashboard(job_id=6198,review_digest="a"*64,
                                       preflight_package_id="package-test")
    assert result["status"]=="PROVIDER_SUBMITTED"
    assert result["employer_receipt_verified"] is False
    assert [n for n,_ in client.calls]==[
        "upload_custom_resume","check_custom_resume",
        "apply_with_custom_resume",
    ]
    assert log==["claim","mark_ACCEPTED","mark_already_applied","commit"]


def test_uncertain_external_result_never_reissues_or_marks_applied(monkeypatch):
    client,log=setup(monkeypatch,unknown=True)
    result=relay.submit_from_dashboard(job_id=6198,review_digest="a"*64,
                                       preflight_package_id="package-test")
    assert result["status"]=="OUTCOME_UNKNOWN"
    assert result["retry_allowed"] is False
    assert log==["claim","mark_UNKNOWN"]
    assert len([n for n,_ in client.calls if n=="apply_with_custom_resume"])==1


def test_missing_verified_destination_never_contacts_engine(monkeypatch):
    client,log=setup(monkeypatch)
    monkeypatch.setattr(relay,"get_connection",
                        lambda: SimpleNamespace(
                            execute=lambda *a,**kw: SimpleNamespace(
                                fetchone=lambda:{"id":6198,"source":"LinkedIn",
                                                 "apply_url":None},
                            ),
                            close=lambda:None,
                        ))
    with pytest.raises((PermissionError,ValueError)):
        relay.submit_from_dashboard(
            job_id=6198,review_digest="a"*64,
            preflight_package_id="package-test",
        )
    assert client.calls==[]
    assert log==[]


def test_provider_duplicate_blocks_before_upload_or_claim(monkeypatch):
    client,log=setup(monkeypatch)
    monkeypatch.setattr(relay,"_prior_provider_application",lambda *a:True)
    with pytest.raises(FileExistsError,match="already tracks"):
        relay.submit_from_dashboard(job_id=6198,review_digest="a"*64,
                                   preflight_package_id="package-test")
    assert client.calls==[]
    assert log==[]


def _ledger_db(tmp_path,monkeypatch,*,native=False,received=False):
    import sqlite3
    path=tmp_path/"exclusive-app.sqlite"
    connection=sqlite3.connect(path)
    connection.executescript("""
      CREATE TABLE application_provider_routes_v1(
        route_id TEXT PRIMARY KEY,tenant_id TEXT,user_id TEXT,
        application_id TEXT,job_id INTEGER,provider TEXT,state TEXT,
        package_digest TEXT,preflight_package_id TEXT,
        employer_url TEXT,employer_url_sha256 TEXT,
        provider_application_id_sha256 TEXT,provider_submitted_at TEXT,
        employer_confirmed_at TEXT,receipt_method TEXT,
        receipt_evidence_digest TEXT,updated_at TEXT,provider_status TEXT
      );
      CREATE TABLE application_execution_mandates_v1(
        mandate_id TEXT PRIMARY KEY,tenant_id TEXT,user_id TEXT,
        application_id TEXT,revoked_at TEXT,revocation_reason TEXT
      );
      CREATE TABLE application_provider_route_events_v1(
        event_id TEXT PRIMARY KEY,route_id TEXT,tenant_id TEXT,user_id TEXT,
        event_type TEXT,from_state TEXT,to_state TEXT,
        evidence_json TEXT,created_at TEXT
      );
      CREATE TABLE jobs(id INTEGER PRIMARY KEY,already_applied INTEGER,
        primary_decision TEXT,status TEXT);
    """)
    connection.execute(
        """INSERT INTO application_provider_routes_v1(
           route_id,tenant_id,user_id,application_id,job_id,provider,state,
           package_digest,preflight_package_id,employer_url,employer_url_sha256,
           employer_confirmed_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("route-test","t","u","app-test",6198,
         "NATIVE" if native else "TSENTA","PREPARATION",
         "p"*64,"package-test",URL,hashlib.sha256(URL.encode()).hexdigest(),
         "2026-10-01" if received else None),
    )
    connection.execute("INSERT INTO jobs VALUES(6198,0,'ELIGIBLE','found')")
    if native:
        connection.execute(
            """INSERT INTO application_execution_mandates_v1(
               mandate_id,tenant_id,user_id,application_id,revoked_at
            ) VALUES('old-native','t','u','app-test',NULL)"""
        )
    connection.commit()
    connection.close()

    def connect():
        db=sqlite3.connect(path,timeout=5)
        db.row_factory=sqlite3.Row
        return db
    monkeypatch.setattr(relay,"get_connection",connect)
    monkeypatch.setattr(
        relay,"current_owner",lambda db:SimpleNamespace(tenant_id="t",user_id="u"),
    )
    return connect


def test_atomic_one_use_claim_blocks_second_submission(tmp_path,monkeypatch):
    connect=_ledger_db(tmp_path,monkeypatch)
    route={"route_id":"route-test","tenant_id":"t","user_id":"u",
           "application_id":"app-test","job_id":6198,"provider":"TSENTA",
           "state":"PREPARATION","package_digest":"p"*64,
           "employer_url_sha256":hashlib.sha256(URL.encode()).hexdigest()}
    key=relay._authorize_claim(
        route=route,pdf_sha256=hashlib.sha256(PDF).hexdigest(),
        reviewed_digest="a"*64,
    )
    assert key.startswith("relay-")
    with pytest.raises(FileExistsError):
        relay._authorize_claim(
            route=route,pdf_sha256=hashlib.sha256(PDF).hexdigest(),
            reviewed_digest="a"*64,
        )
    c=connect()
    assert c.execute("SELECT count(*) FROM tsenta_external_dispatch_claims_v1").fetchone()[0]==1
    assert c.execute("SELECT state FROM tsenta_external_dispatch_claims_v1").fetchone()[0]=="CLAIMED"
    c.close()


def test_atomic_native_handoff_revokes_old_approval(tmp_path,monkeypatch):
    connect=_ledger_db(tmp_path,monkeypatch,native=True)
    response=relay.handoff_failed_native_route(6198,flow={
        "state":"FAILED_SAFELY","prepare":{"status":"FAILED_SAFELY"},
        "production_receipt":None,
    })
    assert response["provider"]=="TSENTA"
    c=connect()
    assert c.execute("select provider from application_provider_routes_v1").fetchone()[0]=="TSENTA"
    assert c.execute("select revoked_at from application_execution_mandates_v1").fetchone()[0] is not None
    assert c.execute("select count(*) from application_provider_route_events_v1").fetchone()[0]==1
    c.close()


def test_employer_receipt_prevents_handoff_and_leaves_native_unchanged(
    tmp_path,monkeypatch,
):
    connect=_ledger_db(tmp_path,monkeypatch,native=True,received=True)
    with pytest.raises(PermissionError,match="Possible prior submission"):
        relay.handoff_failed_native_route(6198,flow={
            "state":"FAILED_SAFELY","prepare":{"status":"FAILED_SAFELY"},
            "production_receipt":None,
        })
    c=connect()
    assert c.execute("select provider from application_provider_routes_v1").fetchone()[0]=="NATIVE"
    assert c.execute("select revoked_at from application_execution_mandates_v1").fetchone()[0] is None
    c.close()


def test_only_failed_native_attempts_can_handoff(tmp_path,monkeypatch):
    connect=_ledger_db(tmp_path,monkeypatch,native=True)
    with pytest.raises(PermissionError,match="has not stopped safely"):
        relay.handoff_failed_native_route(6198,flow={
            "state":"SUBMITTING","prepare":{"status":"RUNNING"},
            "production_receipt":None,
        })
    c=connect()
    assert c.execute("select provider from application_provider_routes_v1").fetchone()[0]=="NATIVE"
    c.close()


def test_api_submit_endpoint_routes_optional_engine_without_breaking_native(
    monkeypatch,
):
    monkeypatch.setenv("HUNTER_API_SECRET","test-only-not-a-real-secret")
    from app import api
    from app import product_application_flow_v2 as flow

    calls=[]
    monkeypatch.setattr(
        flow,"approve_and_submit_for_job",
        lambda job_id,execution_provider="NATIVE": (
            calls.append((job_id,execution_provider))
            or {"status":"PROVIDER_SUBMITTED"}
        ),
    )
    assert api.approve_and_submit_job_package(12)["status"]=="PROVIDER_SUBMITTED"
    p=api.JobSubmissionEngineRequest(execution_provider="TSENTA")
    assert api.approve_and_submit_job_package(12,p)["status"]=="PROVIDER_SUBMITTED"
    assert calls==[(12,"NATIVE"),(12,"TSENTA")]
