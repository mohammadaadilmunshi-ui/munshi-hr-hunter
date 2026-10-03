from __future__ import annotations

import copy

import pytest

from app import application_preflight_package_v1 as phase9
from app import database
from app.phase67_common import sha256_json


def _projection() -> dict:
    return {
        "contract_version": "munshi-application-truth-projection-v1",
        "authority": "munshi-hr-hunter",
        "projection_mode": "READ_ONLY",
        "tenant_id": "default",
        "user_id": "local-owner",
        "profile_id": "candidate-truth:default:local-owner",
        "candidate_profile_binding": {
            "source_extraction_id": "extract-1",
            "profile_revision": 3,
            "profile_digest": "a" * 64,
            "source_profile_sha256": "b" * 64,
            "source_resume_sha256": "c" * 64,
        },
        "generated_at": "2026-09-06T00:30:00+00:00",
        "job_context": {"job_id": "42", "job_snapshot_sha256": "d" * 64},
        "facts": [],
        "protected_fact_keys": [],
        "unresolved_fact_keys": ["application_defaults.sponsorship_required"],
        "mutation_authority": False,
        "submission_authority": False,
        "projection_digest": "e" * 64,
    }


def _capture(*, complete: bool = False, unresolved: list[str] | None = None) -> dict:
    binding = _projection()["candidate_profile_binding"]
    blockers = []
    unresolved = unresolved or []
    status = "NEEDS_INPUT" if blockers or unresolved else ("READY_TO_APPLY" if complete else "PREPARED")
    return {
        "version": phase9.PREFLIGHT_PACKAGE_VERSION,
        "status": status,
        "application_id": "application-1",
        "job": {
            "id": 42,
            "company_name": "Example Co",
            "title": "People Analyst",
            "job_url": "https://example.test/job/42",
            "apply_url": "https://boards.greenhouse.io/example/jobs/42",
            "location_raw": "New York, NY",
        },
        "job_snapshot_sha256": "d" * 64,
        "application_truth": _projection(),
        "resume": {
            "version_id": "resume-1",
            "job_id": 42,
            "version_number": 2,
            "rendered_resume_sha256": "f" * 64,
            "candidate_truth_binding": binding,
            "job_snapshot_binding": {
                "job_id": 42,
                "job_snapshot_sha256": "d" * 64,
                "generation_input_sha256": "1" * 64,
            },
            "status": "VALIDATED",
        },
        "answer_inventory": {
            "candidate_truth_binding": binding,
            "answers": [
                {
                    "answer_id": "answer-1",
                    "question_family": "candidate_fact",
                    "planning_use": "autofill_ready",
                    "source": "user",
                    "confidence": 1.0,
                }
            ],
            "excluded_answers": [],
        },
        "application_question_state": {
            "complete": complete,
            "unresolved_question_keys": unresolved,
        },
        "opportunity": {
            "evaluation_id": "opportunity-1",
            "job_id": 42,
            "result_sha256": "2" * 64,
            "job_snapshot_sha256": "d" * 64,
            "candidate_truth_binding": binding,
            "status": "PASS",
            "fresh": True,
        },
        "relationship": None,
        "readiness": {
            "version": "phase4-7-integrity-v1",
            "job_id": 42,
            "status": "READY",
            "blockers": blockers,
            "checks": {},
            "candidate_truth_binding": binding,
            "job_snapshot_sha256": "d" * 64,
            "submission_authority": False,
            "automatic_actions_executed": False,
        },
        "submission_authority": False,
        "automatic_actions_executed": False,
    }


def test_capture_state_model_keeps_prepared_distinct_from_ready(monkeypatch) -> None:
    binding = _projection()["candidate_profile_binding"]
    monkeypatch.setattr(phase9, "safe_owned_job_snapshot", lambda _job_id: {
        "job": {
            "id": 42,
            "company_name": "Example Co",
            "title": "People Analyst",
            "job_url": "https://example.test/job/42",
            "apply_url": "https://boards.greenhouse.io/example/jobs/42",
            "location_raw": "New York, NY",
        },
        "job_snapshot_sha256": "d" * 64,
    })
    monkeypatch.setattr(phase9, "current_application_truth_projection", lambda **_kwargs: _projection())
    monkeypatch.setattr(phase9, "_resume_reference", lambda _id: {
        "version_id": "resume-1", "job_id": 42, "version_number": 1,
        "rendered_resume_sha256": "f" * 64, "candidate_truth_binding": binding,
        "job_snapshot_binding": {"job_snapshot_sha256": "d" * 64}, "status": "VALIDATED",
    })
    monkeypatch.setattr(phase9, "_opportunity_reference", lambda _id: {
        "evaluation_id": "opportunity-1", "job_id": 42, "result_sha256": "2" * 64,
        "job_snapshot_sha256": "d" * 64, "candidate_truth_binding": binding,
        "status": "PASS", "fresh": True,
    })
    monkeypatch.setattr(phase9, "_relationship_reference", lambda _id: None)
    monkeypatch.setattr(phase9, "application_preparation_readiness", lambda **_kwargs: {
        "version": "phase4-7-integrity-v1", "status": "READY", "blockers": [],
        "submission_authority": False, "automatic_actions_executed": False,
    })
    monkeypatch.setattr(phase9.answer_brain_v2, "planning_input", lambda: {
        "candidate_truth_binding": binding, "answers": [], "excluded_answers": []
    })

    prepared = phase9._capture(
        job_id=42,
        application_id="application-1",
        resume_version_id="resume-1",
        opportunity_evaluation_id="opportunity-1",
        relationship_strategy_id=None,
        application_questions_complete=False,
        unresolved_question_keys=[],
    )
    ready = phase9._capture(
        job_id=42,
        application_id="application-1",
        resume_version_id="resume-1",
        opportunity_evaluation_id="opportunity-1",
        relationship_strategy_id=None,
        application_questions_complete=True,
        unresolved_question_keys=[],
    )
    needs_input = phase9._capture(
        job_id=42,
        application_id="application-1",
        resume_version_id="resume-1",
        opportunity_evaluation_id="opportunity-1",
        relationship_strategy_id=None,
        application_questions_complete=True,
        unresolved_question_keys=["sponsorship_future"],
    )

    assert prepared["status"] == "PREPARED"
    assert ready["status"] == "READY_TO_APPLY"
    assert needs_input["status"] == "NEEDS_INPUT"
    assert ready["submission_authority"] is False
    assert ready["automatic_actions_executed"] is False


def test_answer_inventory_never_copies_canonical_answer_text() -> None:
    inventory = phase9._answer_inventory(
        {
            "candidate_truth_binding": {"profile_digest": "a" * 64},
            "answers": [
                {
                    "answer_id": "answer-1",
                    "question_key": "why_role",
                    "question_family": "motivation",
                    "canonical_answer": "secret answer body must not cross",
                    "planning_use": "context_only",
                    "source": "user",
                    "confidence": 0.9,
                }
            ],
            "excluded_answers": [
                {
                    "answer_id": "answer-2",
                    "question_key": "old",
                    "question_family": "candidate_fact",
                    "canonical_answer": "stale secret",
                    "reason": "stale_or_unbound_profile_answer",
                }
            ],
        }
    )
    text = str(inventory)
    assert "secret answer body must not cross" not in text
    assert "stale secret" not in text
    assert inventory["answers"][0]["question_key"] == "why_role"


def test_preflight_persistence_is_content_addressed_idempotent_and_non_submitting(
    hunter_db, monkeypatch
) -> None:
    capture = _capture()
    monkeypatch.setattr(phase9, "_capture", lambda **_kwargs: copy.deepcopy(capture))

    first = phase9.prepare_preflight_package(
        job_id=42,
        application_id="application-1",
        idempotency_key="package-key",
        resume_version_id="resume-1",
        opportunity_evaluation_id="opportunity-1",
    )
    second = phase9.prepare_preflight_package(
        job_id=42,
        application_id="application-1",
        idempotency_key="package-key",
        resume_version_id="resume-1",
        opportunity_evaluation_id="opportunity-1",
    )

    assert second["package_id"] == first["package_id"]
    assert first["package_digest"] == sha256_json(phase9.package_digest_payload(capture))
    assert first["status"] == "PREPARED"
    assert first["submission_authority"] is False
    assert first["automatic_actions_executed"] is False
    connection = database.get_connection()
    try:
        assert connection.execute("SELECT COUNT(*) FROM application_preflight_packages").fetchone()[0] == 1
    finally:
        connection.close()


def test_preflight_retry_reuses_fresh_committed_package_before_new_evaluation(
    hunter_db, monkeypatch
) -> None:
    capture = _capture()
    calls = {"count": 0}

    def captured(**_kwargs):
        calls["count"] += 1
        return copy.deepcopy(capture)

    monkeypatch.setattr(phase9, "_capture", captured)
    first = phase9.prepare_preflight_package(
        job_id=42,
        application_id="application-1",
        idempotency_key="package-retry-key",
        resume_version_id="resume-1",
        opportunity_evaluation_id="opportunity-1",
    )
    assert calls["count"] == 2

    replay = phase9.prepare_preflight_package(
        job_id=42,
        application_id="application-1",
        idempotency_key="package-retry-key",
        resume_version_id="resume-1",
        opportunity_evaluation_id="opportunity-new-equivalent",
    )

    assert replay["package_id"] == first["package_id"]
    # One freshness capture using the package's original immutable bindings;
    # no new package candidate is captured.
    assert calls["count"] == 3


def test_observational_truth_timestamp_does_not_trigger_false_drift(hunter_db, monkeypatch) -> None:
    first = _capture()
    second = copy.deepcopy(first)
    second["application_truth"]["generated_at"] = "2026-09-06T00:30:01+00:00"
    # Projection digest stays stable because Phase 8 excludes generated_at.
    values = iter([first, second])
    monkeypatch.setattr(phase9, "_capture", lambda **_kwargs: next(values))

    package = phase9.prepare_preflight_package(
        job_id=42,
        application_id="application-1",
        idempotency_key="package-timestamp-key",
        resume_version_id="resume-1",
        opportunity_evaluation_id="opportunity-1",
    )

    assert package["status"] == "PREPARED"
    assert package["package_digest"] == sha256_json(
        phase9.package_digest_payload(first)
    )


def test_freshness_allows_strict_immutable_inflight_continuation(
    hunter_db, monkeypatch
) -> None:
    capture = _capture(complete=True)
    monkeypatch.setattr(phase9, "_capture", lambda **_kwargs: copy.deepcopy(capture))
    package = phase9.prepare_preflight_package(
        job_id=42,
        application_id="application-1",
        idempotency_key="package-continuation-key",
        resume_version_id="resume-1",
        opportunity_evaluation_id="opportunity-1",
        application_questions_complete=True,
    )
    monkeypatch.setattr(
        phase9,
        "_capture",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Selected resume is not bound to the current job snapshot.")
        ),
    )
    monkeypatch.setattr(
        phase9,
        "_immutable_inflight_continuation",
        lambda _package: {
            "allowed": True,
            "reason": "additive_job_enrichment",
            "current_job_snapshot_sha256": "9" * 64,
        },
    )

    freshness = phase9.preflight_package_freshness(package["package_id"])

    assert freshness["fresh"] is True
    assert freshness["immutable_inflight_continuation"] is True
    assert freshness["stored_package_digest"] == package["package_digest"]
    assert freshness["current_package_digest"] == package["package_digest"]


def test_semantic_truth_extension_allows_only_resourced_or_added_facts() -> None:
    stored = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "candidate_profile_binding": {"profile_revision": 6},
        "protected_fact_keys": ["work_authorization"],
        "unresolved_fact_keys": ["salary"],
        "facts": [
            {
                "key": "contact.email",
                "value_reference": "vault://email",
                "protected": True,
                "source": {"extraction_id": "old"},
            }
        ],
    }
    current = copy.deepcopy(stored)
    current["candidate_profile_binding"] = {"profile_revision": 7}
    current["facts"][0]["source"] = {"extraction_id": "new"}
    current["facts"].append(
        {
            "key": "contact.portfolio",
            "value_reference": "vault://portfolio",
            "protected": False,
            "source": {"extraction_id": "new"},
        }
    )

    assert phase9._semantic_truth_extension(stored, current) is True

    current["facts"].append(
        {
            "key": "application_defaults.age_18_plus",
            "value_reference": "vault://age-18-plus",
            "protected": True,
            "source": {"extraction_id": "new"},
        }
    )
    current["protected_fact_keys"].append("application_defaults.age_18_plus")
    assert phase9._semantic_truth_extension(stored, current) is True

    current["protected_fact_keys"].append("contact.email")
    assert phase9._semantic_truth_extension(stored, current) is False
    current["protected_fact_keys"].remove("contact.email")

    current["candidate_profile_binding"] = {"profile_revision": 5}
    assert phase9._semantic_truth_extension(stored, current) is False

    current["candidate_profile_binding"] = {"profile_revision": 7}
    current["facts"][0]["value_reference"] = "vault://different-email"
    assert phase9._semantic_truth_extension(stored, current) is False


def test_mid_capture_change_fails_closed_before_any_package_write(hunter_db, monkeypatch) -> None:
    first = _capture()
    second = copy.deepcopy(first)
    second["job_snapshot_sha256"] = "9" * 64
    values = iter([first, second])
    monkeypatch.setattr(phase9, "_capture", lambda **_kwargs: next(values))

    with pytest.raises(RuntimeError, match="inputs changed"):
        phase9.prepare_preflight_package(
            job_id=42,
            application_id="application-1",
            idempotency_key="package-key",
            resume_version_id="resume-1",
            opportunity_evaluation_id="opportunity-1",
        )

    connection = database.get_connection()
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        count = (
            connection.execute("SELECT COUNT(*) FROM application_preflight_packages").fetchone()[0]
            if "application_preflight_packages" in tables
            else 0
        )
    finally:
        connection.close()
    assert count == 0
