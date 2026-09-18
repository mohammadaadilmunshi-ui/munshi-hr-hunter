from app.cross_repo_contract import validate_profile_snapshot
from app.phase8_application_truth import (
    APPLICATION_TRUTH_AUTHORITY,
    APPLICATION_TRUTH_VERSION,
    build_application_truth_projection,
    projection_binding_matches,
    projection_digest_payload,
)


def _profile_snapshot() -> dict:
    return validate_profile_snapshot(
        {
            "contract_version": "munshi-candidate-profile-snapshot-v1",
            "authority": "munshi-hr-hunter",
            "projection_mode": "READ_ONLY",
            "revision_scope": "SOURCE_EXTRACTION",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "profile_id": "candidate-truth:tenant-1:user-1",
            "profile_revision": 4,
            "override_revision": 2,
            "candidate_details_revision": 0,
            "source_extraction_id": "extract-1",
            "source_profile_sha256": "a" * 64,
            "source_resume_sha256": "b" * 64,
            "generated_at": "2026-09-05T20:30:00-04:00",
            "facts": [
                {
                    "fact_id": "fact-name",
                    "key": "contact.full_name",
                    "category": "IDENTITY",
                    "trust_level": "DOCUMENT_CONFIRMED",
                    "protected": False,
                    "source": "master-resume-extraction:extract-1",
                    "value": "Example Candidate",
                },
                {
                    "fact_id": "fact-email",
                    "key": "contact.email",
                    "category": "CONTACT",
                    "trust_level": "DOCUMENT_CONFIRMED",
                    "protected": False,
                    "source": "master-resume-extraction:extract-1",
                    "value": "candidate@example.com",
                },
                {
                    "fact_id": "fact-auth",
                    "key": "application_defaults.authorized_to_work",
                    "category": "WORK_AUTHORIZATION",
                    "trust_level": "USER_CONFIRMED",
                    "protected": True,
                    "source": "candidate-profile-details-v31:r1",
                    "value_reference": "hunter-vault://candidate-profile-details-v31/authorized_to_work",
                },
            ],
        }
    )


def test_phase8_projection_keeps_hunter_authoritative_and_read_only() -> None:
    profile = _profile_snapshot()
    projection = build_application_truth_projection(profile)

    assert projection["contract_version"] == APPLICATION_TRUTH_VERSION
    assert projection["authority"] == APPLICATION_TRUTH_AUTHORITY
    assert projection["projection_mode"] == "READ_ONLY"
    assert projection["mutation_authority"] is False
    assert projection["submission_authority"] is False
    assert projection["candidate_profile_binding"]["profile_digest"] == profile["profile_digest"]


def test_phase8_never_embeds_protected_plaintext_and_reports_missing_truth() -> None:
    projection = build_application_truth_projection(_profile_snapshot())
    auth = next(
        fact
        for fact in projection["facts"]
        if fact["key"] == "application_defaults.authorized_to_work"
    )

    assert auth["protected"] is True
    assert "value" not in auth
    assert auth["value_reference"].startswith("hunter-vault://")
    assert "application_defaults.sponsorship_required" in projection["unresolved_fact_keys"]
    assert "application_defaults.authorized_to_work" in projection["protected_fact_keys"]


def test_phase8_can_bind_exact_owned_job_snapshot_without_changing_truth() -> None:
    profile = _profile_snapshot()
    projection = build_application_truth_projection(
        profile,
        job_snapshot={
            "job": {"id": 42, "title": "People Analyst"},
            "job_snapshot_sha256": "c" * 64,
        },
    )

    assert projection["job_context"] == {
        "job_id": "42",
        "job_snapshot_sha256": "c" * 64,
    }
    assert projection_binding_matches(
        projection,
        source_extraction_id=profile["source_extraction_id"],
        profile_revision=profile["profile_revision"],
        profile_digest=profile["profile_digest"],
    )
    assert not projection_binding_matches(
        projection,
        source_extraction_id=profile["source_extraction_id"],
        profile_revision=profile["profile_revision"] + 1,
        profile_digest=profile["profile_digest"],
    )


def test_phase8_projection_digest_ignores_observation_time_but_not_truth() -> None:
    first = build_application_truth_projection(_profile_snapshot())
    stable = projection_digest_payload(first)

    changed_time = {**first, "generated_at": "2026-09-06T01:00:00+00:00"}
    assert projection_digest_payload(changed_time) == stable

    changed_truth = {
        **first,
        "facts": [
            {**fact, "value": "Different Candidate"}
            if fact.get("key") == "contact.full_name"
            else fact
            for fact in first["facts"]
        ],
    }
    assert projection_digest_payload(changed_truth) != stable
