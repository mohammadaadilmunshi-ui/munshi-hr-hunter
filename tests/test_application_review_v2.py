from __future__ import annotations

import copy
import sqlite3
from types import SimpleNamespace

import pytest

from app import application_review_v2 as reviews
from app import database
from app.phase67_common import sha256_json


def _plan() -> dict:
    snapshot = {
        "version": "munshi-application-plan-v2",
        "application_id": "application-1",
        "job": {
            "id": 42,
            "company": "Example Co",
            "title": "People Analytics Analyst",
            "job_url": "https://example.test/jobs/42",
            "apply_url": "https://boards.greenhouse.io/example/jobs/42",
            "job_snapshot_digest": "d" * 64,
        },
        "resume": {
            "engine": "NATIVE_V5",
            "version_id": "resume-v5-1",
            "artifact_id": "artifact-pdf-1",
            "artifact_sha256": "3" * 64,
            "filename": "Example_People_Analytics.pdf",
            "source_bindings": {"job": {"job_snapshot_sha256": "d" * 64}},
        },
        "answers": [
            {
                "question_key": "candidate.portfolio",
                "question_family": "candidate_fact",
                "execution_value": "https://example.test/portfolio",
                "sensitivity_class": "NORMAL",
                "source": "user",
            },
            {
                "question_key": "work.authorization.current",
                "question_family": "work_authorization",
                "execution_value": None,
                "secure_resolver_ref": "hunter-secure://candidate-truth/work/authorization/current",
                "sensitivity_class": "PROTECTED",
                "source": "candidate_truth",
            },
        ],
        "provider_policy": {"provider": "GREENHOUSE", "permitted": True},
        "expected_state": "READY_TO_APPLY",
        "executable": True,
        "submission_authority": False,
        "automatic_actions_executed": False,
    }
    return {
        "plan_id": "application-plan-1",
        "tenant_id": "default",
        "user_id": "local-owner",
        "application_id": "application-1",
        "provider": "GREENHOUSE",
        "plan_digest": "a" * 64,
        "status": "READY_TO_APPLY",
        "executable": True,
        "snapshot": snapshot,
        "submission_authority": False,
    }


def _prepared(**changes) -> dict:
    value = {
        "application_id": "application-1",
        "provider": "GREENHOUSE",
        "destination_url": "https://boards.greenhouse.io/example/jobs/42",
        "browser_form_digest": "b" * 64,
        "resume_sha256": "3" * 64,
        "plan_digest": "a" * 64,
        "required_fields": 2,
        "completed_required_fields": 2,
        "unresolved_count": 0,
        "validation_errors": [],
        "checkpoint_id": "checkpoint-1",
        "review_fields": [
            {
                "question_key": "candidate.portfolio",
                "question_family": "candidate_fact",
                "display_value": "https://example.test/portfolio",
                "sensitivity_class": "NORMAL",
                "source": "user",
            },
            {
                "question_key": "work.authorization.current",
                "question_family": "work_authorization",
                "display_value": "YES-PLAINTEXT-MUST-NOT-PERSIST",
                "sensitivity_class": "PROTECTED",
                "source": "candidate_truth",
            },
        ],
    }
    value.update(changes)
    return value


@pytest.fixture
def review_authority(hunter_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(reviews.REVIEW_ENV, "true")
    plan_state = {"value": _plan(), "fresh": True}

    monkeypatch.setattr(
        reviews.application_plan_v2,
        "get_application_plan",
        lambda _plan_id: copy.deepcopy(plan_state["value"]),
    )
    monkeypatch.setattr(
        reviews.application_plan_v2,
        "application_plan_freshness",
        lambda _plan_id: {"fresh": plan_state["fresh"]},
    )
    monkeypatch.setattr(
        reviews,
        "current_owner",
        lambda _connection: SimpleNamespace(tenant_id="default", user_id="local-owner"),
    )

    def simple_schema(connection: sqlite3.Connection | None = None) -> None:
        owns = connection is None
        connection = connection or database.get_connection()
        try:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS application_reviews_v2 (
                    review_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    application_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    review_version TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    prepared_package_digest TEXT NOT NULL,
                    browser_form_digest TEXT NOT NULL,
                    resume_artifact_sha256 TEXT NOT NULL,
                    review_digest TEXT NOT NULL,
                    review_json TEXT NOT NULL,
                    invalidated_at TEXT,
                    invalidation_reason TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(tenant_id,user_id,idempotency_key),
                    UNIQUE(tenant_id,user_id,review_digest)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS application_review_approvals_v2 (
                    approval_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    approval_version TEXT NOT NULL,
                    review_digest TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    prepared_package_digest TEXT NOT NULL,
                    browser_form_digest TEXT NOT NULL,
                    approval_digest TEXT NOT NULL,
                    approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    consumed_at TEXT,
                    consumption_key TEXT,
                    UNIQUE(tenant_id,user_id,review_id),
                    UNIQUE(tenant_id,user_id,idempotency_key),
                    UNIQUE(tenant_id,user_id,approval_digest)
                )"""
            )
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    monkeypatch.setattr(reviews, "ensure_schema", simple_schema)
    return plan_state


def test_review_gate_is_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(reviews.REVIEW_ENV, raising=False)
    assert reviews.application_review_v2_enabled() is False
    monkeypatch.setenv(reviews.REVIEW_ENV, "true")
    assert reviews.application_review_v2_enabled() is True


def test_create_review_is_digest_bound_idempotent_and_masks_sensitive_values(
    review_authority,
) -> None:
    first = reviews.create_review(
        plan_id="application-plan-1",
        prepared_package=_prepared(),
        idempotency_key="review-key-1",
    )
    second = reviews.create_review(
        plan_id="application-plan-1",
        prepared_package=_prepared(),
        idempotency_key="review-key-1",
    )

    assert first["review_id"] == second["review_id"]
    assert len(first["review_digest"]) == 64
    assert len(first["prepared_package_digest"]) == 64
    assert first["browser_form_digest"] == "b" * 64
    assert first["approved"] is False
    assert first["submission_authority"] is False

    review_json = first["review"]
    assert review_json["submission_readiness"] == "READY_FOR_REVIEW"
    assert review_json["submission_authority"] is False
    assert review_json["answers"][0]["display_value"] == "https://example.test/portfolio"
    assert review_json["answers"][1]["display_value"] == "[protected or sensitive value]"
    serialized = str(review_json)
    assert "YES-PLAINTEXT-MUST-NOT-PERSIST" not in serialized
    assert "hunter-secure://" not in serialized


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"resume_sha256": "4" * 64}, "resume"),
        ({"provider": "LEVER"}, "provider"),
        ({"unresolved_count": 1}, "unresolved"),
        ({"validation_errors": ["required field missing"]}, "validation"),
        ({"completed_required_fields": 1}, "incomplete"),
        ({"plan_digest": "f" * 64}, "plan digest"),
    ],
)
def test_create_review_fails_closed_on_preparation_conflict(
    review_authority,
    changes,
    message,
) -> None:
    with pytest.raises(ValueError, match=f"(?i){message}"):
        reviews.create_review(
            plan_id="application-plan-1",
            prepared_package=_prepared(**changes),
            idempotency_key="review-key-conflict",
        )


def test_approval_binds_exact_displayed_digests_and_replays_only_same_key(
    review_authority,
) -> None:
    review = reviews.create_review(
        plan_id="application-plan-1",
        prepared_package=_prepared(),
        idempotency_key="review-key-1",
    )
    first = reviews.approve_review(
        review_id=review["review_id"],
        idempotency_key="approval-key-1",
        expected_review_digest=review["review_digest"],
        expected_prepared_package_digest=review["prepared_package_digest"],
        expected_browser_form_digest=review["browser_form_digest"],
    )
    second = reviews.approve_review(
        review_id=review["review_id"],
        idempotency_key="approval-key-1",
        expected_review_digest=review["review_digest"],
        expected_prepared_package_digest=review["prepared_package_digest"],
        expected_browser_form_digest=review["browser_form_digest"],
    )

    assert first["approved"] is True
    assert first["replayed"] is False
    assert first["submission_authority"] is False
    assert second["approval_id"] == first["approval_id"]
    assert second["replayed"] is True

    with pytest.raises(ValueError, match="(?i)exact displayed"):
        reviews.approve_review(
            review_id=review["review_id"],
            idempotency_key="approval-key-wrong",
            expected_review_digest="e" * 64,
            expected_prepared_package_digest=review["prepared_package_digest"],
            expected_browser_form_digest=review["browser_form_digest"],
        )


def test_new_material_review_invalidates_prior_approval(
    review_authority,
) -> None:
    first = reviews.create_review(
        plan_id="application-plan-1",
        prepared_package=_prepared(),
        idempotency_key="review-key-1",
    )
    approval = reviews.approve_review(
        review_id=first["review_id"],
        idempotency_key="approval-key-1",
        expected_review_digest=first["review_digest"],
        expected_prepared_package_digest=first["prepared_package_digest"],
        expected_browser_form_digest=first["browser_form_digest"],
    )

    second = reviews.create_review(
        plan_id="application-plan-1",
        prepared_package=_prepared(
            browser_form_digest="c" * 64,
            checkpoint_id="checkpoint-2",
        ),
        idempotency_key="review-key-2",
    )
    assert second["review_id"] != first["review_id"]

    stale = reviews.get_review(first["review_id"])
    assert stale["invalidated_at"] is not None
    assert stale["invalidation_reason"] == "superseded_by_material_review_change"

    with pytest.raises(ValueError, match="(?i)invalidated"):
        reviews.consume_approval(
            approval_id=approval["approval_id"],
            consumption_key="submit-command-1",
            expected_review_digest=first["review_digest"],
            expected_prepared_package_digest=first["prepared_package_digest"],
            expected_browser_form_digest=first["browser_form_digest"],
        )


def test_revalidation_invalidates_when_plan_becomes_stale(
    review_authority,
) -> None:
    review = reviews.create_review(
        plan_id="application-plan-1",
        prepared_package=_prepared(),
        idempotency_key="review-key-1",
    )
    review_authority["fresh"] = False
    result = reviews.revalidate_review(review["review_id"])
    assert result == {
        "review_id": review["review_id"],
        "fresh": False,
        "reason": "application_plan_stale",
    }
    stored = reviews.get_review(review["review_id"])
    assert stored["invalidated_at"] is not None


def test_revalidation_with_changed_form_digest_invalidates_review(
    review_authority,
) -> None:
    review = reviews.create_review(
        plan_id="application-plan-1",
        prepared_package=_prepared(),
        idempotency_key="review-key-1",
    )
    result = reviews.revalidate_review(
        review["review_id"],
        prepared_package=_prepared(browser_form_digest="c" * 64),
    )
    assert result["fresh"] is False
    assert result["reason"] == "browser_form_changed"


def test_approval_consumption_is_single_use_and_exactly_replayable(
    review_authority,
) -> None:
    review = reviews.create_review(
        plan_id="application-plan-1",
        prepared_package=_prepared(),
        idempotency_key="review-key-1",
    )
    approval = reviews.approve_review(
        review_id=review["review_id"],
        idempotency_key="approval-key-1",
        expected_review_digest=review["review_digest"],
        expected_prepared_package_digest=review["prepared_package_digest"],
        expected_browser_form_digest=review["browser_form_digest"],
    )

    first = reviews.consume_approval(
        approval_id=approval["approval_id"],
        consumption_key="submit-command-1",
        expected_review_digest=review["review_digest"],
        expected_prepared_package_digest=review["prepared_package_digest"],
        expected_browser_form_digest=review["browser_form_digest"],
    )
    second = reviews.consume_approval(
        approval_id=approval["approval_id"],
        consumption_key="submit-command-1",
        expected_review_digest=review["review_digest"],
        expected_prepared_package_digest=review["prepared_package_digest"],
        expected_browser_form_digest=review["browser_form_digest"],
    )
    assert first["consumed"] is True
    assert first["replayed"] is False
    assert first["submission_authority"] is False
    assert second["replayed"] is True

    with pytest.raises(ValueError, match="(?i)another command"):
        reviews.consume_approval(
            approval_id=approval["approval_id"],
            consumption_key="submit-command-2",
            expected_review_digest=review["review_digest"],
            expected_prepared_package_digest=review["prepared_package_digest"],
            expected_browser_form_digest=review["browser_form_digest"],
        )


def test_prepared_package_digest_changes_when_material_review_changes(
    review_authority,
) -> None:
    plan = _plan()
    base = reviews._normalized_prepared_package(
        plan_record=plan,
        prepared_package=_prepared(),
    )
    changed = reviews._normalized_prepared_package(
        plan_record=plan,
        prepared_package=_prepared(checkpoint_id="checkpoint-2"),
    )
    assert sha256_json(base) != sha256_json(changed)
