from __future__ import annotations

import pytest

from app.tsenta_profile_bridge_v1 import (
    build_inert_tsenta_handoff,
    compare_authorized_profile,
    compare_candidate_truth_snapshot,
)


def test_unapproved_profile_observation_never_compares():
    with pytest.raises(PermissionError):
        compare_authorized_profile(
            {"email": "test@example.com"},
            {"email": "test@example.com"},
            source="authorized_tsenta_mcp",
        )


def test_authorized_comparison_only_returns_conflicts_not_private_values():
    report = compare_authorized_profile(
        {
            "given_name": "Example",
            "email": "confirmed@example.com",
            "work_authorization": "OPT",
        },
        {
            "given_name": " example ",
            "email": "other@example.com",
            "work_authorization": "OPT",
            "unsupported_tool_field": "do not copy",
        },
        source="user_exported_tsenta_profile",
        explicit_profile_read_approval=True,
    )
    assert report["matched_fields"] == 2
    assert report["conflicts"] == 1
    assert report["unmapped_external_fields"] == 1
    assert report["field_statuses"]["email"] == "CONFLICT_REQUIRES_REVIEW"
    assert "confirmed@example.com" not in str(report)
    assert "other@example.com" not in str(report)
    assert "do not copy" not in str(report)
    assert report["candidate_truth_modified"] is False


def test_sensitive_token_and_passport_rejected():
    for name in ("oauth_token", "password", "passport_number", "ssn"):
        with pytest.raises(ValueError, match="Sensitive"):
            compare_authorized_profile(
                {},
                {name: "do-not-leak"},
                source="authorized_tsenta_mcp",
                explicit_profile_read_approval=True,
            )


def test_candidate_truth_snapshot_comparison_resolves_protected_values_in_memory():
    snapshot = {
        "profile_revision": 7,
        "facts": [
            {"key": "contact.first_name", "value": "Example", "protected": False},
            {"key": "contact.country", "value": "US", "protected": False},
            {
                "key": "application_defaults.authorized_to_work",
                "value_reference": "hunter-vault://private",
                "protected": True,
            },
        ],
    }
    report = compare_candidate_truth_snapshot(
        snapshot,
        {
            "authorized_to_work": True,
            "sponsorship_required": False,
            "work_authorization_country": "United States",
        },
        {
            "data": {
                "personalInformation": {"firstName": " example ", "country": "USA"},
                "workAuthorization": {
                    "isAuthorizedToWork": True,
                    "needsSponsorship": True,
                    "workAuthorizations": [{"countryCode": "US"}],
                },
            }
        },
        source="authorized_tsenta_mcp",
        explicit_profile_read_approval=True,
    )

    assert report["field_statuses"]["given_name"] == "MATCH"
    assert report["field_statuses"]["country"] == "MATCH"
    assert report["field_statuses"]["authorized_to_work"] == "MATCH"
    assert report["field_statuses"]["work_authorization_country"] == "MATCH"
    assert (
        report["field_statuses"]["sponsorship_required"] == "CONFLICT_REQUIRES_REVIEW"
    )
    assert report["candidate_truth_modified"] is False
    assert "hunter-vault" not in str(report)
    assert "Example" not in str(report)


def test_inert_handoff_contains_digests_no_credentials_or_submit():
    handoff = build_inert_tsenta_handoff(
        job_id=101,
        original_linkedin_url="https://www.linkedin.com/jobs/view/4455550001",
        verified_employer_url="https://jobs.ashbyhq.com/example/abc/apply",
        candidate_truth_sha256="a" * 64,
        resume_sha256="b" * 64,
        prepare_package_sha256="c" * 64,
    )
    assert handoff["submit_authorized"] is False
    assert handoff["capability_inventory_confirmed"] is False
    assert handoff["contains_candidate_personal_data"] is False
    assert handoff["network_requests"] == 0
    assert handoff["verified_employer_url"].startswith("https://jobs.ashbyhq.com")
    assert len(handoff["handoff_idempotency_sha256"]) == 64


def test_duplicate_preparation_produces_same_idempotency():
    params = {
        "job_id": 101,
        "original_linkedin_url": None,
        "verified_employer_url": "https://job-boards.greenhouse.io/company/jobs/100",
        "candidate_truth_sha256": "1" * 64,
        "resume_sha256": "2" * 64,
        "prepare_package_sha256": "3" * 64,
    }
    assert (
        build_inert_tsenta_handoff(**params)["handoff_idempotency_sha256"]
        == build_inert_tsenta_handoff(**params)["handoff_idempotency_sha256"]
    )


@pytest.mark.parametrize(
    "update",
    [
        {"job_id": -1},
        {"verified_employer_url": "https://www.linkedin.com/jobs/view/123"},
        {"verified_employer_url": "http://localhost/apply"},
        {"candidate_truth_sha256": "bad"},
        {"original_linkedin_url": "https://attacker.example.com/jobs/view/123"},
    ],
)
def test_bad_handoff_data_fails_closed(update):
    defaults = {
        "job_id": 101,
        "original_linkedin_url": "https://www.linkedin.com/jobs/view/4455550001",
        "verified_employer_url": "https://job-boards.greenhouse.io/company/jobs/123",
        "candidate_truth_sha256": "a" * 64,
        "resume_sha256": "b" * 64,
        "prepare_package_sha256": "c" * 64,
    }
    defaults.update(update)
    with pytest.raises(ValueError):
        build_inert_tsenta_handoff(**defaults)
