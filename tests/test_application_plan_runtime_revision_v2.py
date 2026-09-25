from __future__ import annotations

import copy

import pytest
from app import application_plan_v2 as plans
from app import database


def _permissions() -> dict[str, bool]:
    return {
        "background_prepare": True,
        "resume_upload": True,
        "normal_answer_autofill": True,
        "protected_fact_execution": False,
        "self_id_execution": False,
        "final_review": False,
        "final_submit": False,
    }


def _simple_schema(connection=None) -> None:
    owns = connection is None
    connection = connection or database.get_connection()
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS application_plans_v2(
                plan_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
                application_id TEXT NOT NULL, job_id INTEGER NOT NULL,
                preflight_package_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                plan_version TEXT NOT NULL, status TEXT NOT NULL, executable INTEGER NOT NULL,
                provider TEXT NOT NULL, job_snapshot_sha256 TEXT NOT NULL,
                candidate_profile_digest TEXT NOT NULL, resume_version_id TEXT NOT NULL,
                resume_artifact_id TEXT NOT NULL, resume_artifact_sha256 TEXT NOT NULL,
                snapshot_json TEXT NOT NULL, inputs_json TEXT NOT NULL,
                plan_digest TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tenant_id,user_id,idempotency_key),
                UNIQUE(tenant_id,user_id,plan_digest))"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS application_plan_revisions_v2(
                revision_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
                prior_plan_id TEXT NOT NULL, prior_plan_digest TEXT NOT NULL,
                replacement_plan_id TEXT, replacement_plan_digest TEXT,
                idempotency_key TEXT NOT NULL, resolution_digest TEXT NOT NULL,
                resolutions_json TEXT NOT NULL,
                UNIQUE(tenant_id,user_id,idempotency_key),
                UNIQUE(tenant_id,user_id,prior_plan_id),
                UNIQUE(tenant_id,user_id,replacement_plan_id))"""
        )
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _capture(**kwargs):
    requirements = list(kwargs.get("requirements") or [])
    overrides = dict(kwargs.get("answer_overrides") or {})
    answers = []
    unresolved = []
    for requirement in requirements:
        key = str(requirement["question_key"])
        override = overrides.get(key)
        answers.append(
            {
                "question_key": key,
                "normalized_question": requirement["normalized_question"],
                "sensitivity_class": requirement["sensitivity_class"],
                "execution_value": None if override is None else override["canonical_answer"],
                "autofill_allowed": override is not None,
            }
        )
        if override is None and bool(requirement.get("required", True)):
            unresolved.append({"question_key": key, "reason": "no_safe_answer"})
    executable = not unresolved
    return {
        "version": plans.PLAN_VERSION,
        "application_id": "application-1",
        "job": {
            "id": 42,
            "company": "Fixture",
            "title": "Analyst",
            "job_url": "https://boards.greenhouse.io/example/jobs/42",
            "apply_url": "https://boards.greenhouse.io/example/jobs/42",
            "job_snapshot_digest": "d" * 64,
        },
        "candidate_truth_binding": {"profile_digest": "a" * 64},
        "resume": {
            "version_id": "resume-v5-1",
            "version_number": 1,
            "artifact_id": "artifact-pdf-1",
            "artifact_reference": "hunter-native-resume://resume-v5-1/pdf",
            "artifact_sha256": "3" * 64,
            "filename": "resume.pdf",
            "mime_type": "application/pdf",
        },
        "answers": answers,
        "unresolved": unresolved,
        "permissions": _permissions(),
        "provider_policy": {"provider": "GREENHOUSE", "permitted": True},
        "expected_state": "READY_TO_APPLY" if executable else "NEEDS_INPUT",
        "executable": executable,
        "global_blockers": [],
        "submission_authority": False,
        "automatic_actions_executed": False,
    }


def test_runtime_browser_resolution_creates_exact_replay_safe_supersession(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(plans.PLAN_ENV, "true")
    monkeypatch.setenv(plans.RUNTIME_RESOLUTION_ENV, "true")
    monkeypatch.setattr(plans, "ensure_schema", _simple_schema)
    monkeypatch.setattr(plans, "_capture", _capture)
    prior = plans.prepare_application_plan(
        preflight_package_id="preflight-1",
        idempotency_key="runtime-prior",
        permissions=_permissions(),
    )
    original_snapshot = copy.deepcopy(prior["snapshot"])
    context = {
        "session_id": "apply-session-1",
        "checkpoint_id": "checkpoint-1",
        "browser_form_digest": "f" * 64,
        "unresolved_questions": [
            {
                "question_key": "first_name",
                "control_id": "first_name",
                "question": "Full name",
                "semantic_type": "text",
                "sensitivity_class": "NORMAL",
            }
        ],
    }
    resolutions = [{"question_key": "first_name", "canonical_answer": "Aadil"}]
    replacement = plans.reprepare_runtime_needs_input_plan(
        prior_plan_id=prior["plan_id"],
        expected_prior_plan_digest=prior["plan_digest"],
        idempotency_key="runtime-revision-1",
        runtime_context=context,
        answer_resolutions=resolutions,
    )
    replay = plans.reprepare_runtime_needs_input_plan(
        prior_plan_id=prior["plan_id"],
        expected_prior_plan_digest=prior["plan_digest"],
        idempotency_key="runtime-revision-1",
        runtime_context=context,
        answer_resolutions=resolutions,
    )
    assert replacement["plan_id"] != prior["plan_id"]
    assert replacement["status"] == "READY_TO_APPLY"
    assert replacement["executable"] is True
    assert replay["plan_id"] == replacement["plan_id"]
    assert plans.get_application_plan(prior["plan_id"])["snapshot"] == original_snapshot
    lineage = plans.runtime_plan_supersession(replacement["plan_id"])
    assert lineage is not None
    assert lineage["prior_plan_id"] == prior["plan_id"]
    assert lineage["prior_plan_digest"] == prior["plan_digest"]
    assert lineage["replacement_plan_id"] == replacement["plan_id"]
    assert lineage["replacement_plan_digest"] == replacement["plan_digest"]
    assert lineage["session_id"] == "apply-session-1"
    assert lineage["checkpoint_id"] == "checkpoint-1"
    assert lineage["browser_form_digest"] == "f" * 64
    assert plans.runtime_plan_replacement(prior["plan_id"]) == lineage
    assert replacement["snapshot"]["answers"][0]["execution_value"] == "Aadil"
    assert plans.runtime_plan_is_superseded(prior["plan_id"]) is True
    with pytest.raises(RuntimeError, match="superseded"):
        plans.executable_plan(prior["plan_id"])
    with pytest.raises(ValueError, match="different Application Plan revision"):
        plans.reprepare_runtime_needs_input_plan(
            prior_plan_id=prior["plan_id"],
            expected_prior_plan_digest=prior["plan_digest"],
            idempotency_key="runtime-revision-1",
            runtime_context=context,
            answer_resolutions=[
                {"question_key": "first_name", "canonical_answer": "Different"}
            ],
        )


def test_runtime_browser_resolution_cannot_downgrade_protected_question(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(plans.PLAN_ENV, "true")
    monkeypatch.setenv(plans.RUNTIME_RESOLUTION_ENV, "true")
    monkeypatch.setattr(plans, "ensure_schema", _simple_schema)
    monkeypatch.setattr(plans, "_capture", _capture)
    prior = plans.prepare_application_plan(
        preflight_package_id="preflight-1",
        idempotency_key="runtime-protected-prior",
        permissions=_permissions(),
    )
    with pytest.raises(ValueError, match="separate policy"):
        plans.reprepare_runtime_needs_input_plan(
            prior_plan_id=prior["plan_id"],
            expected_prior_plan_digest=prior["plan_digest"],
            idempotency_key="runtime-protected-revision",
            runtime_context={
                "session_id": "apply-session-protected",
                "checkpoint_id": "checkpoint-protected",
                "browser_form_digest": "e" * 64,
                "unresolved_questions": [
                    {
                        "question_key": "work_authorization",
                        "control_id": "work_authorization",
                        "question": "Are you authorized to work in the United States?",
                        "semantic_type": "select",
                        "sensitivity_class": "NORMAL",
                    }
                ],
            },
            answer_resolutions=[
                {"question_key": "work_authorization", "canonical_answer": "Yes"}
            ],
        )


def test_runtime_resolution_rejects_unclassified_questions(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(plans.PLAN_ENV, "true")
    monkeypatch.setenv(plans.RUNTIME_RESOLUTION_ENV, "true")
    monkeypatch.setattr(plans, "ensure_schema", _simple_schema)
    monkeypatch.setattr(plans, "_capture", _capture)
    prior = plans.prepare_application_plan(
        preflight_package_id="preflight-1",
        idempotency_key="runtime-unknown-prior",
        permissions=_permissions(),
    )
    with pytest.raises(ValueError, match="unclassified"):
        plans.reprepare_runtime_needs_input_plan(
            prior_plan_id=prior["plan_id"],
            expected_prior_plan_digest=prior["plan_digest"],
            idempotency_key="runtime-unknown-revision",
            runtime_context={
                "session_id": "apply-session-unknown",
                "checkpoint_id": "checkpoint-unknown",
                "browser_form_digest": "d" * 64,
                "unresolved_questions": [{
                    "question_key": "criminal_history",
                    "control_id": "criminal_history",
                    "question": "Have you ever been convicted of a crime?",
                    "semantic_type": "radio",
                    "sensitivity_class": "NORMAL",
                }],
            },
            answer_resolutions=[
                {"question_key": "criminal_history", "canonical_answer": "No"}
            ],
        )
