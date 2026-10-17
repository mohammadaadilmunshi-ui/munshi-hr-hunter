from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from app import native_standing_consent_v1 as consent

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
JD = {
    "id":42, "company_name":"Acme HR", "hunter_score":97,
    "primary_decision":"ELIGIBLE", "status":"found",
    "hard_rejection_reason":None,"already_applied":0,"cpt_trapdoor":0,
}
DIGEST = "a"*64


def _enable(monkeypatch, *, cap=1):
    monkeypatch.setattr(consent, "_utc", lambda:NOW)
    return consent.grant(
        company_names=["Acme HR"], score=95, daily_cap=cap,
        hours=24, confirmation=consent.PHRASE,
    )


def test_disabled_by_default_cannot_authorize(hunter_db, monkeypatch):
    monkeypatch.setattr(consent, "_utc", lambda:NOW)
    assert consent.status()["enabled"] is False
    assert consent.reserve_fresh_package(
        job=JD,review_digest=DIGEST,
        operation_created_at=NOW.isoformat(),
    )["reserved"] is False


def test_requires_explicit_confirmed_exact_employer_scope(hunter_db, monkeypatch):
    monkeypatch.setattr(consent, "_utc", lambda:NOW)
    with pytest.raises(PermissionError, match="confirmation"):
        consent.grant(
            company_names=["Acme HR"],score=95,daily_cap=1,hours=24,
            confirmation="yes",
        )
    with pytest.raises(ValueError, match="exact"):
        consent.grant(
            company_names=["any"],score=95,daily_cap=1,hours=24,
            confirmation=consent.PHRASE,
        )
    assert consent.status()["enabled"] is False


def test_fresh_scoped_job_consumes_at_most_daily_limit(hunter_db, monkeypatch):
    _enable(monkeypatch)
    digest=DIGEST
    early=(NOW-timedelta(minutes=1)).isoformat()
    late=(NOW+timedelta(minutes=1)).isoformat()
    assert consent.reserve_fresh_package(
        job=JD,review_digest=digest,operation_created_at=early,
    )["reserved"] is False
    first=consent.reserve_fresh_package(
        job=JD,review_digest=digest,operation_created_at=late,
    )
    assert first["reserved"] is True
    assert consent.reserve_fresh_package(
        job=JD,review_digest=digest,operation_created_at=late,
    )["reason"] == "already_reserved"
    next_job={**JD,"id":43}
    assert consent.reserve_fresh_package(
        job=next_job,review_digest="b"*64,operation_created_at=late,
    )["reason"] == "daily_limit_reached"


def test_unknown_or_ineligible_jobs_and_revoke_fail_closed(hunter_db, monkeypatch):
    _enable(monkeypatch)
    late=(NOW+timedelta(minutes=1)).isoformat()
    for variation in (
        {"company_name":"Other HR"},
        {"hunter_score":94},
        {"already_applied":1},
        {"primary_decision":"REJECT"},
        {"status":"archived"},
        {"cpt_trapdoor":1},
        {"hard_rejection_reason":"work_auth"},
    ):
        assert consent.reserve_fresh_package(
            job={**JD,**variation},review_digest=DIGEST,
            operation_created_at=late,
        )["reserved"] is False
    consent.revoke()
    assert consent.status()["enabled"] is False
    assert consent.reserve_fresh_package(
        job=JD,review_digest=DIGEST,operation_created_at=late,
    )["reserved"] is False


def test_expiration_does_not_regrant_historical_job(hunter_db,monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(consent, "_utc", lambda:NOW+timedelta(days=2))
    assert consent.status()["enabled"] is False
    assert consent.reserve_fresh_package(
        job=JD,review_digest=DIGEST,
        operation_created_at=(NOW+timedelta(minutes=1)).isoformat(),
    )["reserved"] is False


def test_concurrent_reservations_single_slot(hunter_db, monkeypatch):
    _enable(monkeypatch)
    late=(NOW+timedelta(minutes=1)).isoformat()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(
            lambda _:consent.reserve_fresh_package(
                job=JD,review_digest=DIGEST,
                operation_created_at=late,
            ), range(2),
        ))
    assert [x["reserved"] for x in results].count(True)==1


def _seed_job(monkeypatch, hunter_db):
    from app import database
    c=database.get_connection()
    try:
        fields=[r[1] for r in c.execute("PRAGMA table_info(jobs)")]
        mandatory={"job_fingerprint":"bounded-test-42","source":"manual",
                   "company_name":"Acme HR","title":"HR Analyst",
                   "hunter_score":97,"primary_decision":"ELIGIBLE","status":"found"}
        included={k:v for k,v in mandatory.items() if k in fields}
        columns=",".join(included)
        sql=f"INSERT INTO jobs({columns}) VALUES({','.join('?' for _ in included)})"
        jid=int(c.execute(sql,tuple(included.values())).lastrowid)
        c.commit()
        return jid
    finally:
        c.close()


def test_scoped_policy_can_mint_exact_package_mandate_once(hunter_db,monkeypatch):
    from app import prepare_orchestrator_v1 as orchestrator
    from app import product_application_flow_v2 as flow
    jid=_seed_job(monkeypatch,hunter_db)
    _enable(monkeypatch)
    late=(NOW+timedelta(minutes=1)).isoformat()
    captured=[]
    monkeypatch.setattr(
        orchestrator,"prepare_operation",
        lambda operation_id:{"operation_id":operation_id,"job_id":jid,
                             "created_at":late,"engine":"NATIVE"},
    )
    monkeypatch.setattr(
        flow,"flow_for_job",
        lambda job_id:{
            "state":"READY_FOR_APPROVAL",
            "prepare":{"operation_id":"prepare-op-42"},
            "package_review":{"review_digest":DIGEST,
                              "preflight_package_id":"preflight-42"},
        },
    )
    def fake_approve(job_id,**kwargs):
        captured.append((job_id,kwargs))
        assert kwargs["approval_source"] == "STANDING_POLICY"
        assert consent.validate_reserved_slot(
            slot=kwargs["consent_slot"],
            policy_id=kwargs["consent_policy_id"],
            job_id=jid,review_digest=DIGEST,
        )
        return {"mandate_id":"execution-mandate-42"}
    monkeypatch.setattr(flow,"approve_and_submit_for_job",fake_approve)
    result=consent.maybe_authorize_fresh_operation("prepare-op-42")
    assert result["authorized"] is True
    assert captured[0][0] == jid
    assert result["submission_authority"] is False
    retry=consent.maybe_authorize_fresh_operation("prepare-op-42")
    assert retry["authorized"] is False
    assert retry["reason"] == "already_reserved"
    assert len(captured)==1


def test_stale_operation_never_mints_mandate(hunter_db,monkeypatch):
    from app import prepare_orchestrator_v1 as orchestrator
    from app import product_application_flow_v2 as flow
    jid=_seed_job(monkeypatch,hunter_db)
    _enable(monkeypatch)
    monkeypatch.setattr(
        orchestrator,"prepare_operation",
        lambda oid:{"operation_id":oid,"job_id":jid,
                    "created_at":(NOW-timedelta(days=1)).isoformat(),"engine":"NATIVE"},
    )
    monkeypatch.setattr(flow,"flow_for_job",lambda jid:{
        "state":"READY_FOR_APPROVAL",
        "prepare":{"operation_id":"fresh-op"},
        "package_review":{"review_digest":DIGEST},
    })
    monkeypatch.setattr(
        flow,"approve_and_submit_for_job",
        lambda *a,**k:pytest.fail("No old preparation may be approved"),
    )
    result=consent.maybe_authorize_fresh_operation("old-op")
    assert result["authorized"] is False


def test_missing_or_revoked_slot_blocks_standing_mandate(hunter_db,monkeypatch):
    from app import application_execution_mandate_v1 as mandate
    from app import product_application_flow_v2 as flow
    _enable(monkeypatch)
    monkeypatch.setattr(flow,"flow_for_job",lambda jid:{
        "state":"READY_FOR_APPROVAL",
        "package_review":{"review_digest":DIGEST,
                          "preflight_package_id":"preflight-42"},
        "artifacts":{"preflight":{
            "preflight_package_id":"preflight-42",
            "cover_letter_decision":"NOT_REQUIRED",
        }},
    })
    monkeypatch.setattr(
        mandate,"approve_package",
        lambda **k:pytest.fail("No mandate without valid consent proof"),
    )
    with pytest.raises(PermissionError,match="consent slot"):
        flow.approve_and_submit_for_job(
            42,approval_source="STANDING_POLICY",
            consent_policy_id="unknown",consent_slot="invalid",
        )
    consent.revoke()
    assert consent.status()["enabled"] is False
