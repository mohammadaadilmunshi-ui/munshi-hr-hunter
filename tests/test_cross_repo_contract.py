from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.cross_repo_contract import (
    EXECUTION_RECEIPT_VERSION,
    PROFILE_SNAPSHOT_VERSION,
    crm_projection_for_receipt,
    event_can_assert_submission,
    validate_execution_receipt,
    validate_profile_snapshot,
    validate_receipt_correlation,
    validate_resume_artifact,
)


def _profile_snapshot() -> dict[str, object]:
    return {
        "contract_version": PROFILE_SNAPSHOT_VERSION,
        "authority": "munshi-hr-hunter",
        "projection_mode": "READ_ONLY",
        "revision_scope": "SOURCE_EXTRACTION",
        "tenant_id": "owner",
        "user_id": "owner-user",
        "profile_id": "profile-1",
        "profile_revision": 7,
        "override_revision": 3,
        "candidate_details_revision": 2,
        "source_extraction_id": "extract-3",
        "source_profile_sha256": "a" * 64,
        "source_resume_sha256": "b" * 64,
        "generated_at": "2026-09-05T20:30:00+00:00",
        "facts": [
            {
                "fact_id": "fact-name",
                "key": "identity.full_name",
                "category": "IDENTITY",
                "trust_level": "USER_CONFIRMED",
                "protected": False,
                "source": "candidate-truth-profile",
                "value": "Example Candidate",
            },
            {
                "fact_id": "fact-auth",
                "key": "work_authorization.detail",
                "category": "WORK_AUTHORIZATION",
                "trust_level": "USER_CONFIRMED",
                "protected": True,
                "source": "candidate-override-vault",
                "value_reference": "vault://profile/profile-1/fact-auth",
            },
        ],
    }


def _receipt(event_type: str, **payload: object) -> dict[str, object]:
    return {
        "contract_version": EXECUTION_RECEIPT_VERSION,
        "source": "munshi-apply",
        "event_id": f"event-{event_type.lower()}",
        "correlation_id": "correlation-1",
        "tenant_id": "owner",
        "user_id": "owner-user",
        "handoff_id": "handoff-1",
        "handoff_body_sha256": "c" * 64,
        "preparation_id": "prep-1",
        "application_id": "application-1",
        "runtime_application_id": "apply-runtime-1",
        "provider": "GREENHOUSE",
        "event_type": event_type,
        "occurred_at": "2026-09-05T20:31:00+00:00",
        "payload": payload,
    }


def test_profile_projection_is_hunter_owned_read_only_and_evidence_bound() -> None:
    validated = validate_profile_snapshot(_profile_snapshot())
    assert validated["authority"] == "munshi-hr-hunter"
    assert validated["projection_mode"] == "READ_ONLY"
    assert validated["revision_scope"] == "SOURCE_EXTRACTION"
    assert validated["source_profile_sha256"] == "a" * 64
    assert validated["source_resume_sha256"] == "b" * 64
    assert len(validated["profile_digest"]) == 64
    assert "value" not in validated["facts"][1]
    assert validated["facts"][1]["value_reference"].startswith("vault://")

    wrong_authority = _profile_snapshot()
    wrong_authority["authority"] = "munshi-apply"
    with pytest.raises(ValueError, match="authority"):
        validate_profile_snapshot(wrong_authority)

    writable = _profile_snapshot()
    writable["projection_mode"] = "READ_WRITE"
    with pytest.raises(ValueError, match="READ_ONLY"):
        validate_profile_snapshot(writable)

    wrong_scope = _profile_snapshot()
    wrong_scope["revision_scope"] = "GLOBAL"
    with pytest.raises(ValueError, match="SOURCE_EXTRACTION"):
        validate_profile_snapshot(wrong_scope)


def test_profile_digest_is_stable_across_export_time_and_fact_input_order() -> None:
    first = validate_profile_snapshot(_profile_snapshot())

    repeated = _profile_snapshot()
    repeated["generated_at"] = "2026-09-05T21:30:00+00:00"
    facts = repeated["facts"]
    assert isinstance(facts, list)
    repeated["facts"] = list(reversed(facts))
    second = validate_profile_snapshot(repeated)

    assert first["profile_digest"] == second["profile_digest"]
    assert first["facts"] == second["facts"]


def test_profile_digest_changes_when_truth_or_evidence_binding_changes() -> None:
    first = validate_profile_snapshot(_profile_snapshot())

    changed_truth = _profile_snapshot()
    facts = changed_truth["facts"]
    assert isinstance(facts, list)
    facts[0] = {**facts[0], "value": "Different Candidate"}
    second = validate_profile_snapshot(changed_truth)
    assert first["profile_digest"] != second["profile_digest"]

    changed_source = _profile_snapshot()
    changed_source["source_resume_sha256"] = "d" * 64
    third = validate_profile_snapshot(changed_source)
    assert first["profile_digest"] != third["profile_digest"]


def test_protected_plaintext_duplicate_ids_and_duplicate_keys_fail_closed() -> None:
    snapshot = _profile_snapshot()
    facts = snapshot["facts"]
    assert isinstance(facts, list)
    protected = dict(facts[1])
    protected["value"] = "must-not-cross-generic-contract"
    snapshot["facts"] = [facts[0], protected]
    with pytest.raises(ValueError, match="must not embed plaintext"):
        validate_profile_snapshot(snapshot)

    duplicate_id = _profile_snapshot()
    duplicate_facts = duplicate_id["facts"]
    assert isinstance(duplicate_facts, list)
    duplicate_id["facts"] = [duplicate_facts[0], dict(duplicate_facts[0])]
    with pytest.raises(ValueError, match="duplicate fact_id"):
        validate_profile_snapshot(duplicate_id)

    duplicate_key = _profile_snapshot()
    key_facts = duplicate_key["facts"]
    assert isinstance(key_facts, list)
    duplicate_key["facts"] = [
        key_facts[0],
        {**key_facts[1], "key": key_facts[0]["key"]},
    ]
    with pytest.raises(ValueError, match="duplicate profile fact key"):
        validate_profile_snapshot(duplicate_key)


def test_resume_artifact_requires_exact_profile_and_sha_binding() -> None:
    artifact = validate_resume_artifact(
        {
            "artifact_id": "resume-1",
            "kind": "resume_pdf",
            "sha256": "a" * 64,
            "mime_type": "application/pdf",
            "size_bytes": 12345,
            "source_preparation_id": "prep-1",
            "source_extraction_id": "extract-3",
            "profile_revision": 7,
            "profile_digest": "b" * 64,
            "job_id": "42",
        }
    )
    assert artifact["sha256"] == "a" * 64
    assert artifact["profile_digest"] == "b" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        validate_resume_artifact({**artifact, "sha256": "not-a-digest"})
    with pytest.raises(ValueError, match="profile_digest"):
        validate_resume_artifact({**artifact, "profile_digest": "bad"})


def test_handoff_or_email_cannot_assert_submission() -> None:
    with pytest.raises(ValueError, match="source"):
        validate_execution_receipt({**_receipt("APPLICATION_READY"), "source": "gmail"})
    with pytest.raises(ValueError, match="unsupported Apply execution event"):
        validate_execution_receipt({**_receipt("APPLICATION_READY"), "event_type": "HANDOFF_ACCEPTED"})

    ready = _receipt("APPLICATION_READY")
    assert crm_projection_for_receipt(ready) == "READY_FOR_REVIEW"
    assert event_can_assert_submission(ready) is False


def test_non_submission_event_cannot_smuggle_submission_or_confirmation_claims() -> None:
    with pytest.raises(ValueError, match="non-submission"):
        validate_execution_receipt(_receipt("APPLICATION_READY", submit_succeeded=True))
    with pytest.raises(ValueError, match="non-confirmation"):
        validate_execution_receipt(_receipt("APPLICATION_READY", confirmation_observed=True))


def test_submission_requires_verified_successful_apply_evidence() -> None:
    with pytest.raises(ValueError, match="verified successful submit evidence"):
        validate_execution_receipt(_receipt("APPLICATION_SUBMITTED"))
    with pytest.raises(ValueError, match="verified successful submit evidence"):
        validate_execution_receipt(
            _receipt("APPLICATION_SUBMITTED", submit_attempted=True, submit_succeeded=False)
        )

    submitted = _receipt(
        "APPLICATION_SUBMITTED", submit_attempted=True, submit_succeeded=True
    )
    assert crm_projection_for_receipt(submitted) == "SUBMITTED"
    assert event_can_assert_submission(submitted) is True


def test_confirmation_requires_confirmation_evidence() -> None:
    with pytest.raises(ValueError, match="confirmation evidence"):
        validate_execution_receipt(_receipt("APPLICATION_CONFIRMED"))
    confirmed = _receipt("APPLICATION_CONFIRMED", confirmation_observed=True)
    assert crm_projection_for_receipt(confirmed) == "SUBMITTED_CONFIRMED"
    assert event_can_assert_submission(confirmed) is True


def test_receipt_must_correlate_to_exact_hunter_handoff_context() -> None:
    ready = _receipt("APPLICATION_READY")
    validated = validate_receipt_correlation(
        ready,
        tenant_id="owner",
        user_id="owner-user",
        handoff_id="handoff-1",
        handoff_body_sha256="c" * 64,
        preparation_id="prep-1",
        application_id="application-1",
    )
    assert validated["handoff_id"] == "handoff-1"

    with pytest.raises(ValueError, match="handoff_body_sha256"):
        validate_receipt_correlation(
            ready,
            tenant_id="owner",
            user_id="owner-user",
            handoff_id="handoff-1",
            handoff_body_sha256="d" * 64,
            preparation_id="prep-1",
            application_id="application-1",
        )
    with pytest.raises(ValueError, match="application_id"):
        validate_receipt_correlation(
            ready,
            tenant_id="owner",
            user_id="owner-user",
            handoff_id="handoff-1",
            handoff_body_sha256="c" * 64,
            preparation_id="prep-1",
            application_id="application-OTHER",
        )


def test_checkpoint_and_failure_stay_non_submitted() -> None:
    checkpoint = _receipt("SECURITY_CHECKPOINT")
    assert crm_projection_for_receipt(checkpoint) == "NEEDS_INPUT"
    assert event_can_assert_submission(checkpoint) is False
    failed = _receipt("INTERACTION_FAILED")
    assert crm_projection_for_receipt(failed) == "APPLY_RUNTIME_FAILED"
    assert event_can_assert_submission(failed) is False


def test_contract_module_has_no_runtime_authority_imports() -> None:
    source = (
        Path(__file__).resolve().parent.parent / "app" / "cross_repo_contract.py"
    ).read_text(encoding="utf-8")
    parsed = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    banned_prefixes = (
        "requests",
        "httpx",
        "urllib",
        "selenium",
        "playwright",
        "app.n8n",
        "app.gmail",
        "app.applyd",
        "app.database",
    )
    assert not any(name.startswith(banned_prefixes) for name in imported)
