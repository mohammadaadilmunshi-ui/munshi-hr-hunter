from __future__ import annotations

import copy

import pytest

from app import application_plan_v2 as plans
from app import application_review_v2 as reviews

B = {
    "source_extraction_id": "extract-cover",
    "profile_revision": 1,
    "profile_digest": "a" * 64,
}


def package():
    return {
        "package_id": "preflight-cover-plan",
        "package_digest": "1" * 64,
        "status": "READY_TO_APPLY",
        "application_id": "application-cover-plan",
        "job_id": 42,
        "job_snapshot_sha256": "2" * 64,
        "resume_version_id": "resume-cover-plan",
        "resume_sha256": "3" * 64,
        "snapshot": {
            "job": {
                "id": 42,
                "company_name": "Example Co",
                "title": "People Analyst",
                "job_url": "https://boards.greenhouse.io/example/jobs/42",
                "apply_url": "https://boards.greenhouse.io/example/jobs/42",
            },
            "application_truth": {
                "tenant_id": "default",
                "user_id": "local-owner",
                "candidate_profile_binding": copy.deepcopy(B),
            },
        },
    }


def resume():
    return {
        "version_id": "resume-cover-plan",
        "version_number": 1,
        "job_id": 42,
        "status": "VALIDATED",
        "html_sha256": "3" * 64,
        "candidate_truth_bound": True,
        "job_snapshot_bound": True,
        "stage_b_bound": True,
        "candidate_truth_binding": copy.deepcopy(B),
        "job_snapshot_binding": {"job_snapshot_sha256": "2" * 64},
        "stage_b_binding": {"plan_id": "resume-plan-cover", "plan_digest": "4" * 64},
    }


def arts():
    return {
        "pdf": {
            "artifact_id": "resume-artifact-cover",
            "artifact_kind": "PDF",
            "object_reference": "hunter-native-resume://cover/pdf",
            "sha256": "5" * 64,
            "filename": "resume.pdf",
            "mime_type": "application/pdf",
            "byte_count": 100,
            "page_count": 1,
        },
        "docx": {
            "artifact_id": "resume-docx-cover",
            "artifact_kind": "DOCX",
            "object_reference": "hunter-native-resume://cover/docx",
            "sha256": "6" * 64,
            "filename": "resume.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "byte_count": 100,
            "page_count": None,
        },
    }


def cover():
    return {
        "version": "munshi-cover-letter-artifact-v1",
        "cover_letter_id": "cover-letter-1",
        "artifact_id": "cover-letter-1",
        "artifact_reference": "hunter-cover-letter://cover-letter-1/pdf",
        "artifact_sha256": "7" * 64,
        "content_sha256": "8" * 64,
        "filename": "cover-letter.pdf",
        "mime_type": "application/pdf",
        "byte_count": 200,
        "page_count": 1,
        "application_id": "application-cover-plan",
        "preflight_package_id": "preflight-cover-plan",
        "preflight_package_digest": "1" * 64,
        "job_id": 42,
        "job_snapshot_sha256": "2" * 64,
        "candidate_profile_digest": "a" * 64,
        "resume_version_id": "resume-cover-plan",
        "resume_artifact_sha256": "5" * 64,
        "submission_authority": False,
    }


def wire(hunter_db, monkeypatch):
    monkeypatch.setattr(
        plans.preflight_v1, "get_preflight_package", lambda _id: package()
    )
    monkeypatch.setattr(
        plans.preflight_v1, "preflight_package_freshness", lambda _id: {"fresh": True}
    )
    monkeypatch.setattr(
        plans,
        "safe_owned_job_snapshot",
        lambda _id: {
            "job_snapshot_sha256": "2" * 64,
            "job": package()["snapshot"]["job"],
        },
    )
    monkeypatch.setattr(plans.resume_v5, "get_version", lambda _id: resume())
    monkeypatch.setattr(
        plans.native_resume_artifact_v5, "materialize", lambda _id: arts()
    )
    monkeypatch.setattr(
        plans.application_execution_artifact,
        "native",
        lambda **_k: {
            "artifact_id": "resume-artifact-cover",
            "artifact_reference": "hunter-native-resume://cover/pdf",
            "artifact_sha256": "5" * 64,
            "filename": "resume.pdf",
            "mime_type": "application/pdf",
            "page_count": 1,
            "submission_authority": False,
        },
    )
    monkeypatch.setattr(
        plans.cover_letter_artifact_v1, "get_artifact", lambda _id: cover()
    )
    monkeypatch.setattr(
        plans.cover_letter_artifact_v1,
        "freshness",
        lambda _id: {"fresh": True, "submission_authority": False},
    )
    monkeypatch.setattr(plans.cover_letter_artifact_v1, "artifact_bytes", lambda _id: b"cover")


def permissions():
    return {
        "background_prepare": True,
        "resume_upload": True,
        "cover_letter_upload": True,
        "normal_answer_autofill": True,
        "protected_fact_execution": False,
        "self_id_execution": False,
        "final_review": False,
        "final_submit": False,
    }


def test_plan_exact_cover_letter(hunter_db, monkeypatch):
    wire(hunter_db, monkeypatch)
    r = plans._capture(
        preflight_package_id="preflight-cover-plan",
        requirements=[],
        permissions=permissions(),
        provider_policy=None,
        secure_refs={},
        resume_format="PDF",
        cover_letter_id="cover-letter-1",
    )
    assert r["executable"] is True
    assert r["cover_letter"]["artifact_sha256"] == "7" * 64
    assert r["cover_letter"]["resume_artifact_sha256"] == "5" * 64
    assert r["cover_letter"]["submission_authority"] is False


def test_resume_only_capture_has_no_cover_letter_key(hunter_db, monkeypatch):
    wire(hunter_db, monkeypatch)
    r = plans._capture(
        preflight_package_id="preflight-cover-plan",
        requirements=[],
        permissions=permissions(),
        provider_policy=None,
        secure_refs={},
        resume_format="PDF",
    )
    assert "cover_letter" not in r


def test_review_exact_cover_digest_and_legacy_shape():
    base = {"resume": {"artifact_sha256": "5" * 64}, "answers": []}
    rec = {
        "application_id": "application-cover-plan",
        "provider": "GREENHOUSE",
        "plan_digest": "9" * 64,
        "snapshot": base,
    }
    pkg = {
        "application_id": "application-cover-plan",
        "provider": "GREENHOUSE",
        "browser_form_digest": "a" * 64,
        "resume_sha256": "5" * 64,
        "required_fields": 0,
        "completed_required_fields": 0,
        "unresolved_count": 0,
        "validation_errors": [],
        "destination_url": "https://boards.greenhouse.io/example/jobs/42",
        "checkpoint_id": "checkpoint-cover",
        "review_fields": [],
    }
    n = reviews._normalized_prepared_package(plan_record=rec, prepared_package=pkg)
    assert "cover_letter_sha256" not in n
    rec2 = {**rec, "snapshot": {**base, "cover_letter": {"artifact_sha256": "7" * 64}}}
    pkg2 = {**pkg, "cover_letter_sha256": "7" * 64}
    n2 = reviews._normalized_prepared_package(plan_record=rec2, prepared_package=pkg2)
    assert n2["cover_letter_sha256"] == "7" * 64
    bad = {**pkg2, "cover_letter_sha256": "b" * 64}
    with pytest.raises(ValueError, match="cover letter"):
        reviews._normalized_prepared_package(plan_record=rec2, prepared_package=bad)
