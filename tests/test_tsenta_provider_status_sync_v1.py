from __future__ import annotations

from app import application_provider_router_v1 as router
from app import database
from app import tsenta_provider_status_sync_v1 as sync


def create_route(monkeypatch, provider="TSENTA"):
    connection = database.get_connection()
    try:
        job_id = connection.execute(
            """INSERT INTO jobs(
                job_fingerprint,source,company_name,title,apply_url,status,
                primary_decision
            ) VALUES (?,?,?,?,?,'found','ELIGIBLE')""",
            (f"sync-{provider}", "manual", "Example", "Analyst",
             "https://jobs.lever.co/example/sync"),
        ).lastrowid
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(
        router,
        "_preflight_binding",
        lambda package_id: {
            "package": {
                "preflight_package_id": package_id,
                "application_id": f"synthetic-{job_id}",
                "job_id": job_id,
            },
            "package_digest": "a" * 64,
            "candidate_profile_digest": "b" * 64,
            "resume_sha256": "c" * 64,
        },
    )
    return router.select_provider(
        job_id=job_id,
        provider=provider,
        verified_employer_url="https://jobs.lever.co/example/sync",
        preflight_package_id=f"synthetic-package-{job_id}",
        idempotency_key=f"synthetic-idempotency-{job_id}",
    )


class FakeReadOnlyAdapter:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def list_applications(self, *, page, limit):
        self.calls.append((page, limit))
        return {"applications": self.records}


def test_sync_matches_only_hashed_existing_provider_id(hunter_db, monkeypatch):
    del hunter_db
    route = create_route(monkeypatch)
    router.record_provider_status(
        route_id=route["route_id"],
        provider_status="RUNNING",
        provider_application_id="tsenta-private-existing-app",
    )
    adapter = FakeReadOnlyAdapter([
        {"applicationId": "wrong-id", "status": "COMPLETED"},
        {
            "applicationId": "tsenta-private-existing-app",
            "status": "COMPLETED",
            "email": "private@example.com",
        },
    ])
    result = sync.sync_existing_application_status(
        route_id=route["route_id"], adapter=adapter
    )
    assert result["state"] == "PROVIDER_SUBMITTED"
    assert result["independent_receipt_verified"] is False
    assert result["matched"] is True
    assert adapter.calls == [(1, 25)]
    assert "tsenta-private" not in str(result)
    assert "private@example.com" not in str(result)
    assert router.get_route(route["route_id"])["employer_confirmed_at"] is None


def test_sync_does_not_infer_identity_from_job_metadata(hunter_db, monkeypatch):
    del hunter_db
    route = create_route(monkeypatch)
    adapter = FakeReadOnlyAdapter([
        {"applicationId": "unknown", "status": "COMPLETED"},
    ])
    import pytest
    with pytest.raises(RuntimeError, match="not securely bound"):
        sync.sync_existing_application_status(route_id=route["route_id"], adapter=adapter)
    assert adapter.calls == []


def test_sync_denies_native_provider_even_when_id_is_bound(hunter_db, monkeypatch):
    del hunter_db
    route = create_route(monkeypatch, provider="NATIVE")
    adapter = FakeReadOnlyAdapter([])
    import pytest
    with pytest.raises(PermissionError, match="Tsenta-owned"):
        sync.sync_existing_application_status(route_id=route["route_id"], adapter=adapter)
    assert adapter.calls == []


def test_missing_provider_identity_never_advances_to_submitted(hunter_db, monkeypatch):
    del hunter_db
    route = create_route(monkeypatch)
    router.record_provider_status(
        route_id=route["route_id"],
        provider_status="RUNNING",
        provider_application_id="known-existing-id",
    )
    adapter = FakeReadOnlyAdapter([
        {"applicationId": "different-id", "status": "COMPLETED"}
    ])
    result = sync.sync_existing_application_status(
        route_id=route["route_id"], adapter=adapter,
    )
    assert result["matched"] is False
    assert result["state"] == "FILLING"
    assert router.get_route(route["route_id"])["state"] == "FILLING"
