from __future__ import annotations

import hashlib
import importlib
from datetime import UTC, datetime, timedelta

import pytest

from app import application_provider_router_v1 as router
from app import database
from app import product_application_flow_v2 as product_flow


def _job(*, fingerprint: str, url: str) -> int:
    connection = database.get_connection()
    try:
        job_id = connection.execute(
            """INSERT INTO jobs(
                   job_fingerprint,source,company_name,title,apply_url,status,
                   primary_decision
               ) VALUES (?,?,?,?,?,'found','ELIGIBLE')""",
            (fingerprint, "manual", "Example Employer", "People Analyst", url),
        ).lastrowid
        connection.commit()
        return int(job_id)
    finally:
        connection.close()


def _binding(job_id: int, package_id: str) -> dict:
    return {
        "package": {
            "preflight_package_id": package_id,
            "application_id": f"application-{job_id}",
            "job_id": job_id,
        },
        "package_digest": "a" * 64,
        "candidate_profile_digest": "b" * 64,
        "resume_sha256": "c" * 64,
    }


def test_one_provider_owns_duplicate_rows_for_the_exact_opening(hunter_db, monkeypatch):
    del hunter_db
    url = "https://jobs.ashbyhq.com/example/role-123/application"
    first_job = _job(fingerprint="provider-route-one", url=url)
    second_job = _job(fingerprint="provider-route-two", url=url)
    bindings = {
        "package-one": _binding(first_job, "package-one"),
        "package-two": _binding(second_job, "package-two"),
    }
    monkeypatch.setattr(router, "_preflight_binding", lambda key: bindings[key])

    selected = router.select_provider(
        job_id=first_job,
        provider="NATIVE",
        verified_employer_url=url,
        preflight_package_id="package-one",
        idempotency_key="route-one",
    )
    replayed = router.select_provider(
        job_id=first_job,
        provider="NATIVE",
        verified_employer_url=url,
        preflight_package_id="package-one",
        idempotency_key="route-one",
    )

    assert selected["replayed"] is False
    assert replayed["route_id"] == selected["route_id"]
    assert replayed["replayed"] is True
    with pytest.raises(FileExistsError, match="another execution provider"):
        router.select_provider(
            job_id=second_job,
            provider="TSENTA",
            verified_employer_url=url,
            preflight_package_id="package-two",
            idempotency_key="route-two",
        )


def test_provider_completion_and_employer_confirmation_are_distinct(
    hunter_db, monkeypatch
):
    del hunter_db
    url = "https://jobs.lever.co/example/role-456"
    job_id = _job(fingerprint="provider-state", url=url)
    monkeypatch.setattr(
        router,
        "_preflight_binding",
        lambda package_id: _binding(job_id, package_id),
    )
    route = router.select_provider(
        job_id=job_id,
        provider="TSENTA",
        verified_employer_url=url,
        preflight_package_id="package-state",
        idempotency_key="route-state",
    )

    with pytest.raises(RuntimeError, match="cannot precede"):
        router.confirm_employer_receipt(
            route_id=route["route_id"],
            verification_method="CORRELATED_EMPLOYER_EMAIL",
            evidence_digest=hashlib.sha256(b"email").hexdigest(),
        )
    filling = router.record_provider_status(
        route_id=route["route_id"],
        provider_status="RUNNING",
        provider_application_id="private-provider-id",
    )
    submitted = router.record_provider_status(
        route_id=route["route_id"], provider_status="COMPLETED"
    )
    confirmed = router.confirm_employer_receipt(
        route_id=route["route_id"],
        verification_method="CORRELATED_EMPLOYER_EMAIL",
        evidence_digest=hashlib.sha256(b"email").hexdigest(),
    )

    assert filling["state"] == "FILLING"
    assert submitted["state"] == "PROVIDER_SUBMITTED"
    assert submitted["employer_confirmed_at"] is None
    assert confirmed["state"] == "EMPLOYER_CONFIRMED"
    assert (
        confirmed["provider_application_id_sha256"]
        == hashlib.sha256(b"private-provider-id").hexdigest()
    )


def test_dispatch_is_inert_by_default(hunter_db, monkeypatch):
    del hunter_db
    monkeypatch.delenv(router.EXECUTION_ENV, raising=False)
    url = "https://jobs.smartrecruiters.com/example/role-789"
    job_id = _job(fingerprint="provider-envelope", url=url)
    monkeypatch.setattr(
        router,
        "_preflight_binding",
        lambda package_id: _binding(job_id, package_id),
    )
    route = router.select_provider(
        job_id=job_id,
        provider="NATIVE",
        verified_employer_url=url,
        preflight_package_id="package-envelope",
        idempotency_key="route-envelope",
    )

    envelope = router.dispatch_envelope(route["route_id"])
    assert envelope["execution_enabled"] is False
    assert envelope["submit_authorized"] is False
    assert envelope["contains_candidate_personal_data"] is False


def test_failed_route_has_bounded_durable_retry_and_shared_projection(
    hunter_db, monkeypatch
):
    del hunter_db
    url = "https://job-boards.greenhouse.io/example/jobs/999"
    job_id = _job(fingerprint="provider-retry", url=url)
    monkeypatch.setattr(
        router,
        "_preflight_binding",
        lambda package_id: _binding(job_id, package_id),
    )
    route = router.select_provider(
        job_id=job_id,
        provider="TSENTA",
        verified_employer_url=url,
        preflight_package_id="package-retry",
        idempotency_key="route-retry",
    )
    router.record_provider_status(route_id=route["route_id"], provider_status="RUNNING")
    failed = router.record_provider_status(
        route_id=route["route_id"],
        provider_status="FAILED",
        error_code="FIXTURE_FAILURE",
    )

    assert failed["state"] == "FAILED"
    assert failed["next_attempt_at"] is not None
    with pytest.raises(RuntimeError, match="backoff"):
        router.claim_due_retry(route["route_id"], now=datetime.now(UTC))
    retried = router.claim_due_retry(
        route["route_id"], now=datetime.now(UTC) + timedelta(minutes=10)
    )
    projection = product_flow.flow_for_job(job_id, summary=True)["provider_route"]

    assert retried["state"] == "PREPARATION"
    assert retried["attempt_count"] == 1
    assert projection["provider"] == "TSENTA"
    assert projection["state"] == "PREPARATION"
    assert "provider_application_id_sha256" not in projection


def test_provider_completion_may_be_first_observed_state(hunter_db, monkeypatch):
    del hunter_db
    url = "https://jobs.ashbyhq.com/example/role-direct-complete"
    job_id = _job(fingerprint="provider-direct-complete", url=url)
    monkeypatch.setattr(
        router,
        "_preflight_binding",
        lambda package_id: _binding(job_id, package_id),
    )
    route = router.select_provider(
        job_id=job_id,
        provider="TSENTA",
        verified_employer_url=url,
        preflight_package_id="package-direct-complete",
        idempotency_key="route-direct-complete",
    )

    result = router.record_provider_status(
        route_id=route["route_id"], provider_status="COMPLETED"
    )

    assert result["state"] == "PROVIDER_SUBMITTED"
    assert result["attempt_count"] == 1


def test_migration_058_is_additive_and_idempotent(hunter_db):
    del hunter_db
    migration = importlib.import_module(
        "migrations.058_tsenta_provider_learning_foundation_v1"
    )
    connection = database.get_connection()
    try:
        migration.apply(connection)
        migration.apply(connection)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        connection.close()

    assert {
        "application_provider_routes_v1",
        "application_provider_route_events_v1",
        "tsenta_observations_v1",
    } <= tables



def test_status_codes_fail_closed_and_error_details_are_redacted(hunter_db, monkeypatch):
    del hunter_db
    url = "https://jobs.lever.co/example/safe-errors"
    job_id = _job(fingerprint="provider-safe-errors", url=url)
    monkeypatch.setattr(
        router, "_preflight_binding",
        lambda package_id: _binding(job_id, package_id),
    )
    route = router.select_provider(
        job_id=job_id,
        provider="TSENTA",
        verified_employer_url=url,
        preflight_package_id="package-safe-errors",
        idempotency_key="route-safe-errors",
    )
    with pytest.raises(ValueError, match="machine code"):
        router.record_provider_status(
            route_id=route["route_id"],
            provider_status="Failed: candidate@example.com",
        )
    stored = router.record_provider_status(
        route_id=route["route_id"],
        provider_status="FAILED",
        error_code="Candidate candidate@example.com denied at https://private.example",
    )
    assert stored["last_error_code"] == "PROVIDER_FAILURE_UNCLASSIFIED"
    assert "candidate@example.com" not in str(stored)
    assert "private.example" not in str(stored)
