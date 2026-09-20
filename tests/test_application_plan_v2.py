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
    monkeypatch.setattr(
        plans.application_execution_artifact,
        "native",
        lambda **_kwargs: {
            "artifact_id": "artifact-pdf-1",
            "artifact_reference": "hunter-native-resume://resume-v5-1/pdf",
            "artifact_sha256": "3" * 64,
            "filename": "Example_People_Analytics_v4.pdf",
            "mime_type": "application/pdf",
            "page_count": 1,
            "submission_authority": False,
        },
    )


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

    with pytest.raises(ValueError, match="owner-scoped resolver"):
        plans._scoped_secure_refs(
            requirements,
            {"work.authorization.current": "hunter-secure://candidate-truth/work-authorization/current"},
        )


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


def test_provider_requires_a_real_supported_ats_host_and_policy_is_closed(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_capture_dependencies(hunter_db, monkeypatch)
    package = _package()
    package["snapshot"]["job"]["apply_url"] = "https://greenhouse.evil.test/jobs/42"
    monkeypatch.setattr(plans.preflight_v1, "get_preflight_package", lambda _id: package)
    result = plans._capture(
        preflight_package_id="preflight-1", requirements=[], permissions=_permissions(),
        provider_policy=None, secure_refs={}, resume_format="PDF",
    )
    assert result["provider_policy"]["provider"] == "UNSUPPORTED"
    assert "provider_not_permitted_or_supported" in result["global_blockers"]
    with pytest.raises(ValueError, match="Unsupported provider policy fields"):
        plans._normalize_provider_policy({"permitted": True, "host": "evil.test"}, provider="GREENHOUSE")


def test_secure_resolver_is_exactly_scoped_to_its_protected_question() -> None:
    with pytest.raises(ValueError, match="outside its Candidate Truth scope"):
        plans._normalize_secure_refs(
            {"work.authorization.current": "hunter-secure://candidate-truth/other/value"}
        )
    requirements = plans._normalize_requirements([
        {"question_key": "candidate.email", "question_family": "candidate_fact"}
    ])
    with pytest.raises(ValueError, match="matching protected application question"):
        plans._scoped_secure_refs(
            requirements,
            {"candidate.email": "hunter-secure://candidate-truth/candidate/email"},
        )


def test_inferred_protected_sensitivity_cannot_be_downgraded_or_leak_plaintext(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_capture_dependencies(hunter_db, monkeypatch)
    requirement = plans._normalize_requirements(
        [{
            "question_key": "candidate.value",
            "question_family": "candidate_fact",
            "normalized_question": "What is your Social Security number?",
            "sensitivity_class": "NORMAL",
        }]
    )
    assert requirement[0]["sensitivity_class"] == "POST_OFFER"
    result = plans._capture(
        preflight_package_id="preflight-1", requirements=requirement,
        permissions=_permissions(), provider_policy=None, secure_refs={}, resume_format="PDF",
    )
    assert result["answers"][0]["execution_value"] is None


def test_non_normal_question_rejects_plaintext_conditions() -> None:
    with pytest.raises(ValueError, match="plaintext conditions"):
        plans._normalize_requirements([{
            "question_key": "work.authorization.current",
            "question_family": "work_authorization",
            "conditions": {"answer": "yes"},
        }])


def test_capture_rejects_preflight_snapshot_from_another_owner(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_capture_dependencies(hunter_db, monkeypatch)
    package = _package()
    package["snapshot"]["application_truth"]["user_id"] = "another-user"
    monkeypatch.setattr(plans.preflight_v1, "get_preflight_package", lambda _id: package)
    with pytest.raises(PermissionError, match="owner"):
        plans._capture(
            preflight_package_id="preflight-1", requirements=[], permissions=_permissions(),
            provider_policy=None, secure_refs={}, resume_format="PDF",
        )


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


def test_needs_input_reprepare_is_immutable_owner_scoped_and_replay_safe(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(plans.PLAN_ENV, "true")

    def simple_schema(connection=None):
        owns = connection is None
        connection = connection or database.get_connection()
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS application_plans_v2(
                plan_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
                application_id TEXT NOT NULL, job_id INTEGER NOT NULL, preflight_package_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, plan_version TEXT NOT NULL, status TEXT NOT NULL,
                executable INTEGER NOT NULL, provider TEXT NOT NULL, job_snapshot_sha256 TEXT NOT NULL,
                candidate_profile_digest TEXT NOT NULL, resume_version_id TEXT NOT NULL,
                resume_artifact_id TEXT NOT NULL, resume_artifact_sha256 TEXT NOT NULL,
                snapshot_json TEXT NOT NULL, inputs_json TEXT NOT NULL, plan_digest TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tenant_id,user_id,idempotency_key), UNIQUE(tenant_id,user_id,plan_digest))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS application_plan_revisions_v2(
                revision_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
                prior_plan_id TEXT NOT NULL, prior_plan_digest TEXT NOT NULL,
                replacement_plan_id TEXT, replacement_plan_digest TEXT, idempotency_key TEXT NOT NULL,
                resolution_digest TEXT NOT NULL, resolutions_json TEXT NOT NULL,
                UNIQUE(tenant_id,user_id,idempotency_key), UNIQUE(tenant_id,user_id,prior_plan_id),
                UNIQUE(tenant_id,user_id,replacement_plan_id))""")
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    monkeypatch.setattr(plans, "ensure_schema", simple_schema)

    def capture(**kwargs):
        ready = bool(kwargs.get("answer_overrides", {}).get("candidate.phone"))
        return {
            "version": plans.PLAN_VERSION,
            "application_id": "application-1",
            "job": {"id": 42, "job_snapshot_digest": "d" * 64},
            "candidate_truth_binding": {"profile_digest": "a" * 64},
            "resume": {"version_id": "resume-v5-1", "artifact_id": "artifact-pdf-1", "artifact_sha256": "3" * 64},
            "provider_policy": {"provider": "GREENHOUSE"},
            "expected_state": "READY_TO_APPLY" if ready else "NEEDS_INPUT",
            "executable": ready,
            "answers": [{"question_key": "candidate.phone", "execution_value": "555-0100" if ready else None}],
            "unresolved": [] if ready else [{"question_key": "candidate.phone", "reason": "no_safe_answer"}],
            "permissions": _permissions(), "global_blockers": [],
            "submission_authority": False, "automatic_actions_executed": False,
        }

    monkeypatch.setattr(plans, "_capture", capture)
    first = plans.prepare_application_plan(
        preflight_package_id="preflight-1", idempotency_key="needs-input-1",
        answer_requirements=[{"question_key": "candidate.phone", "question_family": "candidate_fact"}],
        permissions=_permissions(),
    )
    original_snapshot = copy.deepcopy(first["snapshot"])
    real_freshness = plans.application_plan_freshness

    def fail_only_replacement_freshness(plan_id: str) -> dict:
        if plan_id == first["plan_id"]:
            return {"fresh": True, "executable": False}
        return {"fresh": False, "executable": False}

    monkeypatch.setattr(plans, "application_plan_freshness", fail_only_replacement_freshness)
    with pytest.raises(RuntimeError, match="stale"):
        plans.reprepare_needs_input_plan(
            prior_plan_id=first["plan_id"], idempotency_key="needs-input-revision-1",
            expected_prior_plan_digest=first["plan_digest"],
            answer_resolutions=[{"question_key": "candidate.phone", "canonical_answer": "555-0100"}],
        )

    connection = database.get_connection()
    try:
        pending = connection.execute(
            """SELECT * FROM application_plan_revisions_v2
               WHERE idempotency_key='needs-input-revision-1'"""
        ).fetchone()
        persisted_replacement = connection.execute(
            """SELECT plan_id FROM application_plans_v2
               WHERE idempotency_key='needs-input-revision-1'"""
        ).fetchone()
    finally:
        connection.close()
    assert pending is not None
    assert pending["replacement_plan_id"] is None
    assert pending["replacement_plan_digest"] is None
    assert pending["prior_plan_digest"] == first["plan_digest"]
    assert persisted_replacement is not None
    persisted_replacement_id = str(persisted_replacement["plan_id"])

    with pytest.raises(ValueError, match="immutable replacement revision"):
        plans.reprepare_needs_input_plan(
            prior_plan_id=first["plan_id"], idempotency_key="needs-input-revision-race",
            expected_prior_plan_digest=first["plan_digest"],
            answer_resolutions=[{"question_key": "candidate.phone", "canonical_answer": "555-0100"}],
        )

    monkeypatch.setattr(plans, "application_plan_freshness", real_freshness)
    revision = plans.reprepare_needs_input_plan(
        prior_plan_id=first["plan_id"], idempotency_key="needs-input-revision-1",
        expected_prior_plan_digest=first["plan_digest"],
        answer_resolutions=[{"question_key": "candidate.phone", "canonical_answer": "555-0100"}],
    )
    replay = plans.reprepare_needs_input_plan(
        prior_plan_id=first["plan_id"], idempotency_key="needs-input-revision-1",
        expected_prior_plan_digest=first["plan_digest"],
        answer_resolutions=[{"question_key": "candidate.phone", "canonical_answer": "555-0100"}],
    )
    assert revision["plan_id"] != first["plan_id"]
    assert revision["plan_id"] == persisted_replacement_id
    assert revision["status"] == "READY_TO_APPLY"
    assert replay["plan_id"] == revision["plan_id"]
    assert plans.get_application_plan(first["plan_id"])["snapshot"] == original_snapshot

    connection = database.get_connection()
    try:
        lineage = connection.execute(
            """SELECT * FROM application_plan_revisions_v2
               WHERE idempotency_key='needs-input-revision-1'"""
        ).fetchone()
    finally:
        connection.close()
    assert lineage["prior_plan_digest"] == first["plan_digest"]
    assert lineage["replacement_plan_id"] == revision["plan_id"]
    assert lineage["replacement_plan_digest"] == revision["plan_digest"]

    with pytest.raises(ValueError, match="Expected Application Plan digest"):
        plans.reprepare_needs_input_plan(
            prior_plan_id=first["plan_id"], expected_prior_plan_digest="0" * 64,
            idempotency_key="needs-input-revision-wrong-digest",
            answer_resolutions=[{"question_key": "candidate.phone", "canonical_answer": "555-0100"}],
        )
    with pytest.raises(ValueError, match="different Application Plan revision"):
        plans.reprepare_needs_input_plan(
            prior_plan_id=first["plan_id"], idempotency_key="needs-input-revision-1",
            expected_prior_plan_digest=first["plan_digest"],
            answer_resolutions=[{"question_key": "candidate.phone", "canonical_answer": "555-0101"}],
        )

    real_current_owner = plans.current_owner
    monkeypatch.setattr(
        plans,
        "current_owner",
        lambda _connection=None: SimpleNamespace(tenant_id="other", user_id="other"),
    )
    with pytest.raises(LookupError, match="not owned"):
        plans.reprepare_needs_input_plan(
            prior_plan_id=first["plan_id"], idempotency_key="needs-input-revision-owner",
            expected_prior_plan_digest=first["plan_digest"],
            answer_resolutions=[{"question_key": "candidate.phone", "canonical_answer": "555-0100"}],
        )
    monkeypatch.setattr(plans, "current_owner", real_current_owner)
    protected = copy.deepcopy(first)
    protected["snapshot"]["unresolved"] = [{"question_key": "candidate.phone", "reason": "protected"}]
    protected["inputs"]["answer_requirements"][0]["sensitivity_class"] = "PROTECTED"
    with pytest.raises(ValueError, match="separate policy"):
        plans._resolution_inputs(protected, [{"question_key": "candidate.phone", "canonical_answer": "x"}])
    monkeypatch.setattr(plans, "application_plan_freshness", lambda _id: {"fresh": False})
    with pytest.raises(RuntimeError, match="stale"):
        plans.reprepare_needs_input_plan(
            prior_plan_id=first["plan_id"], expected_prior_plan_digest=first["plan_digest"],
            idempotency_key="needs-input-revision-stale",
            answer_resolutions=[{"question_key": "candidate.phone", "canonical_answer": "555-0100"}],
        )

def test_explicit_resolution_does_not_grant_execution_without_autofill_permission() -> None:
    requirement = plans._normalize_requirement(
        {
            "question_key": "candidate.phone",
            "question_family": "candidate_fact",
            "question": "What is your phone number?",
            "required": True,
        }
    )
    answer, reason = plans._resolve_requirement(
        requirement,
        permissions=_permissions(normal_answer_autofill=False),
        secure_refs={},
        answer_overrides={
            "candidate.phone": {
                "canonical_answer": "555-0100",
                "provenance": "candidate_explicit_resolution",
            }
        },
    )
    assert answer["display_value"] == "555-0100"
    assert answer["execution_value"] is None
    assert answer["autofill_allowed"] is False
    assert answer["requires_review"] is True
    assert reason == "normal_answer_autofill_permission_missing"


def test_agile_ats_provider_is_exactly_host_bound(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_capture_dependencies(hunter_db, monkeypatch)
    package = _package()
    package["snapshot"]["job"]["apply_url"] = (
        "https://neomax.jobs.agile-ats.com/jobs/details/1119"
    )
    monkeypatch.setattr(
        plans.preflight_v1,
        "get_preflight_package",
        lambda _id: package,
    )
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
        permissions=_permissions(),
        provider_policy=None,
        secure_refs={},
        resume_format="PDF",
    )

    assert result["provider_policy"]["provider"] == "AGILE_ATS"
    assert result["provider_policy"]["permitted"] is True
    assert result["provider_policy"]["allowed_hosts"] == ["agile-ats.com"]
    assert result["expected_state"] == "READY_TO_APPLY"
    assert result["executable"] is True


def test_agile_ats_provider_does_not_match_unrelated_host(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_capture_dependencies(hunter_db, monkeypatch)
    package = _package()
    package["snapshot"]["job"]["apply_url"] = "https://agile-ats.com.evil.test/jobs/1119"
    monkeypatch.setattr(
        plans.preflight_v1,
        "get_preflight_package",
        lambda _id: package,
    )

    result = plans._capture(
        preflight_package_id="preflight-1",
        requirements=[],
        permissions=_permissions(),
        provider_policy=None,
        secure_refs={},
        resume_format="PDF",
    )

    assert result["provider_policy"]["provider"] == "UNSUPPORTED"
    assert "provider_not_permitted_or_supported" in result["global_blockers"]


@pytest.mark.parametrize(
    ("question_key", "question", "semantic_type"),
    [
        ("contact.address_line1", "Address", "UNKNOWN"),
        ("contact.postal_code", "Zip Code", "POSTAL_CODE"),
        ("contact.region", "State / Province", "STATE_PROVINCE"),
    ],
)
def test_runtime_normal_address_questions_survive_plan_validation(
    question_key: str,
    question: str,
    semantic_type: str,
) -> None:
    observed, requirement = plans._runtime_question_requirement(
        {
            "question_key": question_key,
            "control_id": "control-" + question_key.replace(".", "-"),
            "question": question,
            "semantic_type": semantic_type,
            "sensitivity_class": "NORMAL",
        }
    )
    assert observed["question_key"] == question_key
    assert observed["sensitivity_class"] == "NORMAL"
    assert requirement["question_family"] == "location"
    assert requirement["sensitivity_class"] == "NORMAL"
