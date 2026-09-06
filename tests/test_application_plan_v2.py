from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from app import application_plan_v2 as plans
from app import database
from app.phase67_common import sha256_json


_BINDING = {
    "source_extraction_id": "extract-1",
    "profile_revision": 3,
    "profile_digest": "a" * 64,
    "source_profile_sha256": "b" * 64,
    "source_resume_sha256": "c" * 64,
}


def _package() -> dict:
    return {
        "package_id": "preflight-1",
        "package_digest": "1" * 64,
        "status": "READY_TO_APPLY",
        "application_id": "application-1",
        "job_id": 42,
        "job_snapshot_sha256": "d" * 64,
        "resume_version_id": "resume-v5-1",
        "resume_sha256": "e" * 64,
        "snapshot": {
            "job": {
                "id": 42,
                "company_name": "Example Co",
                "title": "People Analytics Analyst",
                "job_url": "https://example.test/jobs/42",
                "apply_url": "https://boards.greenhouse.io/example/jobs/42",
                "location_raw": "New York, NY",
            },
            "application_truth": {
                "tenant_id": "default",
                "user_id": "local-owner",
                "candidate_profile_binding": copy.deepcopy(_BINDING),
            },
        },
    }


def _resume() -> dict:
    return {
        "version_id": "resume-v5-1",
        "version_number": 4,
        "job_id": 42,
        "status": "VALIDATED",
        "html_sha256": "e" * 64,
        "candidate_truth_bound": True,
        "job_snapshot_bound": True,
        "stage_b_bound": True,
        "candidate_truth_binding": copy.deepcopy(_BINDING),
        "job_snapshot_binding": {
            "job_id": 42,
            "job_snapshot_sha256": "d" * 64,
            "generation_input_sha256": "f" * 64,
        },
        "stage_b_binding": {
            "plan_id": "resume-plan-1",
            "plan_digest": "2" * 64,
        },
    }


def _artifacts() -> dict:
    return {
        "pdf": {
            "artifact_id": "artifact-pdf-1",
            "artifact_kind": "PDF",
            "object_reference": "hunter-native-resume://resume-v5-1/pdf",
            "sha256": "3" * 64,
            "filename": "Example_People_Analytics_v4.pdf",
            "mime_type": "application/pdf",
            "byte_count": 1200,
            "page_count": 1,
        },
        "docx": {
            "artifact_id": "artifact-docx-1",
            "artifact_kind": "DOCX",
            "object_reference": "hunter-native-resume://resume-v5-1/docx",
            "sha256": "4" * 64,
            "filename": "Example_People_Analytics_v4.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "byte_count": 1400,
            "page_count": None,
        },
    }


def _wire_capture_dependencies(hunter_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plans.preflight_v1, "get_preflight_package", lambda _id: _package())
    monkeypatch.setattr(
        plans.preflight_v1,
        "preflight_package_freshness",
        lambda _id: {"fresh": True},
    )
    monkeypatch.setattr(
        plans,
        "safe_owned_job_snapshot",
        lambda _job_id: {
            "job": _package()["snapshot"]["job"],
            "job_snapshot_sha256": "d" * 64,
        },
    )
    monkeypatch.setattr(plans.resume_v5, "get_version", lambda _id: _resume())
    monkeypatch.setattr(plans.native_resume_artifact_v5, "materialize", lambda _id: _artifacts())


def _permissions(**changes: bool) -> dict[str, bool]:
    values = {
        "background_prepare": True,
        "resume_upload": True,
        "normal_answer_autofill": True,
        "protected_fact_execution": False,
        "self_id_execution": False,
        "final_review": False,
        "final_submit": False,
    }
    values.update(changes)
    return values


def test_application_plan_gate_is_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(plans.PLAN_ENV, raising=False)
    assert plans.application_plan_v2_enabled() is False
    monkeypatch.setenv(plans.PLAN_ENV, "true")
    assert plans.application_plan_v2_enabled() is True


def test_normal_confirmed_autofill_answer_becomes_execution_value(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_capture_dependencies(hunter_db, monkeypatch)
    monkeypatch.setattr(
        plans.answer_brain_v2,
        "resolve_answer",
        lambda **_kwargs: {
            "status": "ANSWERED",
            "resolution": "stored_verified",
            "answer": {
                "canonical_answer": "Yes",
                "source": "user",
                "confidence": 1.0,
                "user_confirmed": True,
                "autofill_allowed": True,
            },
        },
    )
    requirements = plans._normalize_requirements(
        [
            {
                "question_key": "candidate.email_confirmed",
                "question_family": "candidate_fact",
                "question": "Is this your current email?",
            }
        ]
    )

    result = plans._capture(
        preflight_package_id="preflight-1",
        requirements=requirements,
        permissions=_permissions(),
        provider_policy=None,
        secure_refs={},
        resume_format="PDF",
    )

    assert result["executable"] is True
    assert result["expected_state"] == "READY_TO_APPLY"
    assert result["answers"][0]["execution_value"] == "Yes"
    assert result["resume"]["artifact_id"] == "artifact-pdf-1"
    assert result["resume"]["pdf_page_count"] == 1
    assert result["submission_authority"] is False


def test_unknown_required_answer_fails_to_needs_input_without_guessing(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_capture_dependencies(hunter_db, monkeypatch)
    monkeypatch.setattr(
        plans.answer_brain_v2,
        "resolve_answer",
        lambda **_kwargs: {"status": "NEEDS_INPUT", "reason": "no_safe_answer"},
    )
    requirements = plans._normalize_requirements(
        [
            {
                "question_key": "job.custom_required",
                "question_family": "unknown",
                "question": "A required employer-specific question",
            }
        ]
    )

    result = plans._capture(
        preflight_package_id="preflight-1",
        requirements=requirements,
        permissions=_permissions(),
        provider_policy=None,
        secure_refs={},
        resume_format="PDF",
    )

    assert result["executable"] is False
    assert result["expected_state"] == "NEEDS_INPUT"
    assert result["answers"][0]["execution_value"] is None
    assert result["unresolved"] == [
        {"question_key": "job.custom_required", "reason": "no_safe_answer"}
    ]


def test_protected_answer_never_serializes_plaintext_and_requires_scoped_resolver(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_capture_dependencies(hunter_db, monkeypatch)
    requirements = plans._normalize_requirements(
        [
            {
                "question_key": "work.authorization.current",
                "question_family": "work_authorization",
                "question": "Are you authorized to work in the United States?",
            }
        ]
    )

    blocked = plans._capture(
        preflight_package_id="preflight-1",
        requirements=requirements,
        permissions=_permissions(),
        provider_policy=None,
        secure_refs={},
        resume_format="PDF",
    )
    assert blocked["expected_state"] == "NEEDS_INPUT"
    assert blocked["answers"][0]["execution_value"] is None
    assert blocked["answers"][0]["secure_resolver_ref"] is None

    ready = plans._capture(
        preflight_package_id="preflight-1",
        requirements=requirements,
        permissions=_permissions(protected_fact_execution=True),
        provider_policy=None,
        secure_refs={
            "work.authorization.current": "hunter-secure://candidate-truth/work-authorization/current"
        },
        resume_format="PDF",
    )
    answer = ready["answers"][0]
    assert ready["executable"] is True
    assert answer["execution_value"] is None
    assert answer["secure_resolver_ref"].startswith("hunter-secure://")
    assert "authorized" not in str(answer.get("display_value") or "").casefold()


def test_credential_requirement_remains_unresolved_even_with_prepare_permissions(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_capture_dependencies(hunter_db, monkeypatch)
    requirements = plans._normalize_requirements(
        [
            {
                "question_key": "ats.password",
                "question_family": "credential_requirement",
                "question": "Password",
            }
        ]
    )
    result = plans._capture(
        preflight_package_id="preflight-1",
        requirements=requirements,
        permissions=_permissions(),
        provider_policy=None,
        secure_refs={},
        resume_format="PDF",
    )
    assert result["expected_state"] == "NEEDS_INPUT"
    assert result["answers"][0]["execution_value"] is None
    assert result["unresolved"][0]["reason"] == (
        "credential_or_post_offer_value_requires_explicit_flow"
    )


def test_provider_and_prepare_permissions_fail_closed(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_capture_dependencies(hunter_db, monkeypatch)
    package = _package()
    package["snapshot"]["job"]["apply_url"] = "https://example.test/custom-ats/42"
    monkeypatch.setattr(plans.preflight_v1, "get_preflight_package", lambda _id: package)
    monkeypatch.setattr(
        plans,
        "safe_owned_job_snapshot",
        lambda _job_id: {
            "job": package["snapshot"]["job"],
            "job_snapshot_sha256": "d" * 64,
        },
    )
    result = plans._capture(
        preflight_package_id="preflight-1",
        requirements=[],
        permissions=_permissions(background_prepare=False, resume_upload=False),
        provider_policy=None,
        secure_refs={},
        resume_format="PDF",
    )
    assert result["expected_state"] == "NEEDS_INPUT"
    assert set(result["global_blockers"]) == {
        "background_prepare_permission_missing",
        "resume_upload_permission_missing",
        "provider_not_permitted_or_supported",
    }


def test_plan_digest_excludes_identity_metadata_but_not_execution_content() -> None:
    base = {
        "plan_id": "plan-a",
        "idempotency_key": "key-a",
        "plan_digest": "x" * 64,
        "created_at": "time-a",
        "application_id": "app-1",
        "answers": [{"question_key": "q", "execution_value": "Yes"}],
    }
    changed_identity = {
        **base,
        "plan_id": "plan-b",
        "idempotency_key": "key-b",
        "created_at": "time-b",
    }
    assert sha256_json(plans.plan_digest_payload(base)) == sha256_json(
        plans.plan_digest_payload(changed_identity)
    )
    changed_content = copy.deepcopy(base)
    changed_content["answers"][0]["execution_value"] = "No"
    assert sha256_json(plans.plan_digest_payload(base)) != sha256_json(
        plans.plan_digest_payload(changed_content)
    )


def test_plan_persistence_accepts_exact_idempotent_retry_and_rejects_key_reuse(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(plans.PLAN_ENV, "true")
    capture = {
        "version": plans.PLAN_VERSION,
        "application_id": "application-1",
        "job": {"id": 42, "job_snapshot_digest": "d" * 64},
        "candidate_truth_binding": {"profile_digest": "a" * 64},
        "resume": {
            "version_id": "resume-v5-1",
            "artifact_id": "artifact-pdf-1",
            "artifact_sha256": "3" * 64,
        },
        "provider_policy": {"provider": "GREENHOUSE"},
        "expected_state": "READY_TO_APPLY",
        "executable": True,
        "answers": [],
        "unresolved": [],
        "permissions": _permissions(),
        "global_blockers": [],
        "submission_authority": False,
        "automatic_actions_executed": False,
    }
    monkeypatch.setattr(plans, "_capture", lambda **_kwargs: copy.deepcopy(capture))
    monkeypatch.setattr(
        plans,
        "current_owner",
        lambda _connection: SimpleNamespace(tenant_id="default", user_id="local-owner"),
    )

    def simple_schema(connection=None):
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
                    snapshot_json TEXT NOT NULL, inputs_json TEXT NOT NULL, plan_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(tenant_id,user_id,idempotency_key), UNIQUE(tenant_id,user_id,plan_digest)
                )"""
            )
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    monkeypatch.setattr(plans, "ensure_schema", simple_schema)

    first = plans.prepare_application_plan(
        preflight_package_id="preflight-1",
        idempotency_key="plan-key",
        permissions=_permissions(),
    )
    second = plans.prepare_application_plan(
        preflight_package_id="preflight-1",
        idempotency_key="plan-key",
        permissions=_permissions(),
    )
    assert first["plan_id"] == second["plan_id"]
    assert first["plan_digest"] == second["plan_digest"]

    changed = copy.deepcopy(capture)
    changed["answers"] = [{"question_key": "q", "execution_value": "different"}]
    monkeypatch.setattr(plans, "_capture", lambda **_kwargs: copy.deepcopy(changed))
    with pytest.raises(ValueError, match="Idempotency key belongs to a different"):
        plans.prepare_application_plan(
            preflight_package_id="preflight-1",
            idempotency_key="plan-key",
            permissions=_permissions(),
        )
