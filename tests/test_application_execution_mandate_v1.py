from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import application_execution_mandate_v1 as mandate
from app import database


PACKAGE_DIGEST = "a" * 64
RESUME_DIGEST = "b" * 64
PROFILE_DIGEST = "c" * 64
ARTIFACT_DIGEST = "e" * 64


@pytest.fixture
def mandate_authority(hunter_db, monkeypatch: pytest.MonkeyPatch):
    connection = database.get_connection()
    try:
        connection.execute(
            "INSERT OR IGNORE INTO tenants(tenant_id,display_name) VALUES('default','Default')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO app_users(user_id,display_name) VALUES('owner','Owner')"
        )
        connection.execute(
            """INSERT OR IGNORE INTO tenant_memberships(tenant_id,user_id,role)
               VALUES('default','owner','owner')"""
        )
        connection.commit()
    finally:
        connection.close()

    package = {
        "package_id": "preflight-1",
        "package_digest": PACKAGE_DIGEST,
        "application_id": "application-1",
        "job_id": 42,
        "resume_version_id": "resume-1",
        "resume_sha256": RESUME_DIGEST,
        "status": "READY_TO_APPLY",
        "snapshot": {
            "job": {
                "id": 42,
                "company_name": "Example Co",
                "title": "People Analyst",
                "location_raw": "Example, NJ",
                "job_url": "https://jobs.example.test/42",
                "apply_url": "https://jobs.example.test/42",
            },
            "resume": {
                "version_id": "resume-1",
                "rendered_resume_sha256": RESUME_DIGEST,
            },
            "application_truth": {
                "projection_digest": "d" * 64,
                "protected_fact_keys": ["application_defaults.authorization_status"],
                "unresolved_fact_keys": [],
            },
            "answer_inventory": {
                "candidate_truth_binding": {
                    "profile_digest": PROFILE_DIGEST,
                },
                "answers": [
                    {
                        "answer_id": "answer-1",
                        "question_key": "contact.email",
                        "question_family": "candidate_fact",
                        "planning_use": "autofill_ready",
                    }
                ],
                "excluded_answers": [],
            },
        },
    }
    monkeypatch.setattr(
        mandate.preflight_v1,
        "get_preflight_package",
        lambda package_id: dict(package),
    )
    monkeypatch.setattr(
        mandate.preflight_v1,
        "preflight_package_freshness",
        lambda package_id: {"fresh": True},
    )
    monkeypatch.setattr(
        mandate,
        "current_owner",
        lambda connection: SimpleNamespace(tenant_id="default", user_id="owner"),
    )
    monkeypatch.setattr(
        mandate,
        "current_owner_readonly",
        lambda connection: SimpleNamespace(tenant_id="default", user_id="owner"),
    )
    return package


def test_package_review_is_prebrowser_and_explicitly_forbids_second_prompt(
    mandate_authority,
) -> None:
    review = mandate.package_review(
        preflight_package_id="preflight-1",
        cover_letter_decision="NOT_REQUIRED",
    )

    assert review["resume"]["sha256"] == RESUME_DIGEST
    assert review["job"]["apply_url"] == "https://jobs.example.test/42"
    assert review["execution_policy"]["post_approval_user_review"] is False
    assert review["execution_policy"]["post_approval_user_submit_prompt"] is False
    assert review["execution_policy"]["normal_runtime_questions"].startswith(
        "AUTONOMOUS"
    )
    assert review["submission_authority"] is False
    assert len(review["review_digest"]) == 64


def test_approve_package_is_one_idempotent_customer_mandate(
    mandate_authority,
) -> None:
    review = mandate.package_review(
        preflight_package_id="preflight-1",
        cover_letter_decision="NOT_REQUIRED",
    )
    first = mandate.approve_package(
        preflight_package_id="preflight-1",
        cover_letter_decision="NOT_REQUIRED",
        expected_review_digest=review["review_digest"],
        idempotency_key="customer-package-approval-1",
    )
    second = mandate.approve_package(
        preflight_package_id="preflight-1",
        cover_letter_decision="NOT_REQUIRED",
        expected_review_digest=review["review_digest"],
        idempotency_key="customer-package-approval-1",
    )

    assert first["mandate_id"] == second["mandate_id"]
    assert first["approved"] is True
    assert first["submission_authority"] is False
    current = mandate.current_for_preflight("preflight-1")
    assert current is not None
    assert current["mandate_id"] == first["mandate_id"]
    summary = mandate.current_summary_for_preflight("preflight-1")
    assert summary is not None
    assert summary["mandate_id"] == first["mandate_id"]
    assert summary["summary_projection"] is True
    assert summary["submission_authority"] is False


def test_approval_refuses_changed_displayed_package(
    mandate_authority,
) -> None:
    with pytest.raises(ValueError, match="Displayed package review changed"):
        mandate.approve_package(
            preflight_package_id="preflight-1",
            cover_letter_decision="NOT_REQUIRED",
            expected_review_digest="f" * 64,
            idempotency_key="customer-package-approval-wrong",
        )


def test_mandate_binds_rendered_resume_and_browser_review_binds_pdf_artifact(
    mandate_authority,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_review = mandate.package_review(
        preflight_package_id="preflight-1",
        cover_letter_decision="NOT_REQUIRED",
    )
    approved = mandate.approve_package(
        preflight_package_id="preflight-1",
        cover_letter_decision="NOT_REQUIRED",
        expected_review_digest=package_review["review_digest"],
        idempotency_key="customer-package-approval-digest-domains",
    )
    monkeypatch.setattr(
        mandate.application_plan_v2,
        "get_application_plan",
        lambda plan_id: {
            "plan_id": plan_id,
            "job_id": 42,
            "snapshot": {
                "job": {"id": 42},
                "resume": {
                    "version_id": "resume-1",
                    "rendered_output_sha256": RESUME_DIGEST,
                    "artifact_sha256": ARTIFACT_DIGEST,
                },
                "provider_policy": {"allowed_hosts": ["jobs.example.test"]},
            },
        },
    )

    authorized = mandate.mandate_authorizes_internal_review(
        mandate_id=approved["mandate_id"],
        review_record={
            "application_id": "application-1",
            "plan_id": "application-plan-1",
            "resume_artifact_sha256": ARTIFACT_DIGEST,
            "review": {
                "destination_url": "https://jobs.example.test/42/apply",
                "cover_letter": None,
            },
        },
    )

    assert authorized["resume_version_id"] == "resume-1"
    assert authorized["resume_sha256"] == RESUME_DIGEST


def test_legacy_package_review_does_not_gain_legal_attestation_scope(
    mandate_authority,
) -> None:
    review = mandate.package_review(
        preflight_package_id="preflight-1",
        cover_letter_decision="NOT_REQUIRED",
    )
    assert "legal_attestation" not in review["execution_policy"]


def test_new_preflight_capability_is_visible_in_single_approval_scope(
    mandate_authority,
) -> None:
    mandate_authority["snapshot"]["execution_capabilities"] = {
        "legal_attestation_execution": True,
    }
    review = mandate.package_review(
        preflight_package_id="preflight-1",
        cover_letter_decision="NOT_REQUIRED",
    )
    assert review["execution_policy"]["legal_attestation"] == (
        "AUTONOMOUS_ONLY_WHEN_EXACT_PROVIDER_TERMS_IDENTITY_IS_BOUND"
    )


def _attestation_identity() -> dict[str, str]:
    return {
        "semantic_type": "LEGAL_ATTESTATION",
        "logical_field_fingerprint": "1" * 64,
        "label_digest": "2" * 64,
        "terms_identity_digest": "3" * 64,
        "provider_host": "jobs.example.test",
    }


def test_legal_attestation_fails_closed_without_explicit_mandate_scope(
    mandate_authority,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mandate.application_plan_v2,
        "executable_plan",
        lambda plan_id: {
            "plan_id": plan_id,
            "plan_digest": "9" * 64,
            "application_id": "application-1",
            "job_id": 42,
            "inputs": {"preflight_package_id": "preflight-1"},
            "snapshot": {
                "job": {
                    "id": 42,
                    "apply_url": "https://jobs.example.test/42",
                },
                "provider_policy": {
                    "allowed_hosts": ["jobs.example.test"],
                },
            },
        },
    )
    monkeypatch.setattr(
        mandate,
        "current_for_preflight",
        lambda _package_id: {
            "mandate_id": "mandate-1",
            "review_digest": "8" * 64,
            "application_id": "application-1",
            "job_id": 42,
            "review": {"execution_policy": {}},
        },
    )
    with pytest.raises(PermissionError, match="does not authorize"):
        mandate.authorize_legal_attestation(
            plan_id="application-plan-1",
            plan_digest="9" * 64,
            attestation_identity=_attestation_identity(),
        )


def test_legal_attestation_proof_binds_exact_identity_without_submit_authority(
    mandate_authority,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {
        "plan_id": "application-plan-1",
        "plan_digest": "9" * 64,
        "application_id": "application-1",
        "job_id": 42,
        "inputs": {"preflight_package_id": "preflight-1"},
        "snapshot": {
            "job": {
                "id": 42,
                "apply_url": "https://jobs.example.test/42",
            },
            "provider_policy": {
                "allowed_hosts": ["jobs.example.test"],
            },
        },
    }
    monkeypatch.setattr(
        mandate.application_plan_v2,
        "executable_plan",
        lambda _plan_id: plan,
    )
    monkeypatch.setattr(
        mandate,
        "current_for_preflight",
        lambda _package_id: {
            "mandate_id": "mandate-1",
            "review_digest": "8" * 64,
            "application_id": "application-1",
            "job_id": 42,
            "review": {
                "execution_policy": {
                    "legal_attestation": (
                        "AUTONOMOUS_ONLY_WHEN_EXACT_PROVIDER_TERMS_IDENTITY_IS_BOUND"
                    )
                }
            },
        },
    )
    identity = _attestation_identity()
    proof = mandate.authorize_legal_attestation(
        plan_id="application-plan-1",
        plan_digest="9" * 64,
        attestation_identity=identity,
    )
    assert proof["authorized"] is True
    assert proof["submission_authority"] is False
    assert proof["attestation_identity"] == identity
    assert len(proof["authorization_digest"]) == 64
