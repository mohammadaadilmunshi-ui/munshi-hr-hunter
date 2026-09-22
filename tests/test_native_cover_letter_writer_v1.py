from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import native_cover_letter_writer_v1 as writer


PROFILE = "a" * 64
JOB = "b" * 64
PLAN = "c" * 64
EVIDENCE = "d" * 64
ARTIFACT = "e" * 64


def context():
    return {
        "package": {
            "package_id": "preflight-cl-1",
            "package_digest": "1" * 64,
            "application_id": "application-cl-1",
            "resume_version_id": "resume-v5-cl-1",
        },
        "job": {
            "id": 42,
            "company_name": "Example Co",
            "title": "People Analyst",
        },
        "job_id": 42,
        "job_snapshot_sha256": JOB,
        "candidate_snapshot": {"snapshot": True},
        "candidate_binding": {"profile_digest": PROFILE},
        "resume_version_id": "resume-v5-cl-1",
        "jd_plan": {
            "plan_id": "jd-plan-cl-1",
            "plan_digest": PLAN,
            "job_snapshot_sha256": JOB,
            "candidate_truth_binding": {"profile_digest": PROFILE},
            "requirement_refs": [
                {
                    "requirement_id": "req-supported",
                    "priority": "CORE_RESPONSIBILITY",
                    "type": "RESPONSIBILITY",
                    "exact_text": "Support recruiting operations",
                    "match_status": "DIRECT",
                    "evidence_ids": ["ev-1"],
                },
                {
                    "requirement_id": "req-forbidden",
                    "priority": "MUST_HAVE",
                    "type": "EXPERIENCE",
                    "exact_text": "Five years of compensation experience",
                    "match_status": "NO_EVIDENCE",
                    "evidence_ids": [],
                },
            ],
            "do_not_claim_requirement_ids": ["req-forbidden"],
        },
        "evidence_bundle": {
            "evidence_digest": EVIDENCE,
            "items": [
                {
                    "evidence_id": "ev-1",
                    "kind": "experience",
                    "label": "Recruiting operations",
                    "text": "Managed recruiting operations and supported 15 hires per month.",
                }
            ],
        },
        "evidence_map": {
            "ev-1": {
                "evidence_id": "ev-1",
                "kind": "experience",
                "label": "Recruiting operations",
                "text": "Managed recruiting operations and supported 15 hires per month.",
            }
        },
        "requirement_map": {
            "req-supported": {
                "requirement_id": "req-supported",
                "exact_text": "Support recruiting operations",
                "match_status": "DIRECT",
            },
            "req-forbidden": {
                "requirement_id": "req-forbidden",
                "exact_text": "Five years of compensation experience",
                "match_status": "NO_EVIDENCE",
            },
        },
        "supported_requirement_ids": {"req-supported"},
        "forbidden_requirement_ids": {"req-forbidden"},
    }


def draft(text="I have managed recruiting operations and supported 15 hires per month."):
    return {
        "paragraphs": [
            {
                "text": text,
                "evidence_ids": ["ev-1"],
                "requirement_ids": ["req-supported"],
            },
            {
                "text": (
                    "I would bring that recruiting operations experience to the "
                    "People Analyst role while continuing to learn from the team."
                ),
                "evidence_ids": ["ev-1"],
                "requirement_ids": ["req-supported"],
            },
        ]
    }


@pytest.fixture()
def wired(hunter_db, monkeypatch):
    monkeypatch.setattr(writer, "_current_context", lambda _id: context())
    monkeypatch.setattr(writer, "_assert_context_still_current", lambda _ctx: None)
    monkeypatch.setattr(
        writer.resume_v2,
        "_resolve_writer_config",
        lambda: {
            "api_key": "test-only",
            "key_source": "test",
            "model": "gpt-test",
            "reasoning_effort": "medium",
            "max_output_tokens": 4000,
            "max_calls_per_generation": 2,
        },
    )
    monkeypatch.setattr(
        writer.cover_letter_artifact_v1,
        "create",
        lambda **kwargs: {
            "cover_letter_id": "cover-letter-cl-1",
            "artifact_id": "cover-letter-cl-1",
            "artifact_reference": "hunter-cover-letter://cover-letter-cl-1/pdf",
            "artifact_sha256": ARTIFACT,
            "filename": "cover-letter.pdf",
            "mime_type": "application/pdf",
            "page_count": 1,
            "job_snapshot_sha256": JOB,
            "candidate_profile_digest": PROFILE,
            "resume_version_id": "resume-v5-cl-1",
            "submission_authority": False,
        },
    )
    monkeypatch.setattr(
        writer.cover_letter_artifact_v1,
        "get_artifact",
        lambda _id: {
            "cover_letter_id": "cover-letter-cl-1",
            "artifact_id": "cover-letter-cl-1",
            "artifact_reference": "hunter-cover-letter://cover-letter-cl-1/pdf",
            "artifact_sha256": ARTIFACT,
            "filename": "cover-letter.pdf",
            "mime_type": "application/pdf",
            "page_count": 1,
            "submission_authority": False,
        },
    )
    monkeypatch.setattr(
        writer.cover_letter_artifact_v1,
        "freshness",
        lambda _id: {"fresh": True, "submission_authority": False},
    )


def test_truth_bound_writer_creates_immutable_one_page_artifact(wired, monkeypatch):
    calls = []
    monkeypatch.setattr(
        writer,
        "_call_writer",
        lambda **kwargs: (
            calls.append(kwargs) or draft(),
            "response-1",
            "gpt-test",
        ),
    )

    result = writer.generate(preflight_package_id="preflight-cl-1")

    assert result["cover_letter_id"] == "cover-letter-cl-1"
    assert result["artifact_sha256"] == ARTIFACT
    assert result["page_count"] == 1
    assert result["candidate_profile_digest"] == PROFILE
    assert result["job_snapshot_sha256"] == JOB
    assert result["submission_authority"] is False
    assert len(calls) == 1


def test_logical_replay_does_not_call_model_again(wired, monkeypatch):
    calls = {"count": 0}

    def model(**kwargs):
        calls["count"] += 1
        return draft(), "response-1", "gpt-test"

    monkeypatch.setattr(writer, "_call_writer", model)
    first = writer.generate(preflight_package_id="preflight-cl-1")
    second = writer.generate(preflight_package_id="preflight-cl-1")

    assert first["generation_id"] == second["generation_id"]
    assert first["cover_letter_id"] == second["cover_letter_id"]
    assert calls["count"] == 1


def test_unknown_evidence_id_fails_closed():
    value = draft()
    value["paragraphs"][0]["evidence_ids"] = ["invented-evidence"]
    with pytest.raises(ValueError, match="unknown Candidate evidence"):
        writer._validate_draft(value, context())


def test_forbidden_requirement_fails_closed():
    value = draft()
    value["paragraphs"][0]["requirement_ids"] = ["req-forbidden"]
    with pytest.raises(ValueError, match="unsupported JD requirements"):
        writer._validate_draft(value, context())


def test_unsupported_numeric_candidate_claim_fails_closed():
    value = draft(
        "I have managed recruiting operations for 99 years and supported 15 hires per month."
    )
    with pytest.raises(ValueError, match="unsupported quantified"):
        writer._validate_draft(value, context())


def test_generation_repairs_forbidden_jd_language_once(wired, monkeypatch):
    unsafe = draft(
        "I have managed recruiting operations and have Five years of compensation experience."
    )
    calls = []

    def model(**kwargs):
        calls.append(kwargs["prompt_payload"])
        return (unsafe if len(calls) == 1 else draft()), f"response-{len(calls)}", "gpt-test"

    monkeypatch.setattr(writer, "_call_writer", model)
    result = writer.generate(preflight_package_id="preflight-cl-1")

    assert result["cover_letter_id"] == "cover-letter-cl-1"
    assert result["model_calls"] == 2
    assert len(calls) == 2
    assert "failed the authoritative truth check" in calls[1]["repair_instruction"]


def test_generation_persists_nothing_when_truth_repair_still_fails(
    wired, monkeypatch
):
    unsafe = draft(
        "I have managed recruiting operations and have Five years of compensation experience."
    )
    calls = {"model": 0, "artifact": 0}

    def model(**_kwargs):
        calls["model"] += 1
        return unsafe, f"response-{calls['model']}", "gpt-test"

    def artifact(**_kwargs):
        calls["artifact"] += 1
        raise AssertionError("unsafe cover letter must not be persisted")

    monkeypatch.setattr(writer, "_call_writer", model)
    monkeypatch.setattr(writer.cover_letter_artifact_v1, "create", artifact)

    with pytest.raises(ValueError, match="forbidden unsupported requirement"):
        writer.generate(preflight_package_id="preflight-cl-1")

    assert calls == {"model": 2, "artifact": 0}


def test_context_drift_after_model_fails_before_artifact(wired, monkeypatch):
    monkeypatch.setattr(
        writer,
        "_call_writer",
        lambda **kwargs: (draft(), "response-1", "gpt-test"),
    )
    monkeypatch.setattr(
        writer,
        "_assert_context_still_current",
        lambda _ctx: (_ for _ in ()).throw(RuntimeError("Candidate Truth changed")),
    )
    with pytest.raises(RuntimeError, match="Candidate Truth changed"):
        writer.generate(preflight_package_id="preflight-cl-1")


def test_invalid_artifact_authority_binding_fails_closed(wired, monkeypatch):
    monkeypatch.setattr(
        writer,
        "_call_writer",
        lambda **kwargs: (draft(), "response-1", "gpt-test"),
    )
    monkeypatch.setattr(
        writer.cover_letter_artifact_v1,
        "create",
        lambda **kwargs: {
            "cover_letter_id": "bad",
            "artifact_sha256": ARTIFACT,
            "page_count": 2,
            "job_snapshot_sha256": JOB,
            "candidate_profile_digest": PROFILE,
            "resume_version_id": "resume-v5-cl-1",
            "submission_authority": False,
        },
    )
    with pytest.raises(RuntimeError, match="authority binding"):
        writer.generate(preflight_package_id="preflight-cl-1")


def test_migration_051_is_additive():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "051_native_cover_letter_writer_v1.py"
    )
    assert migration.is_file()
    assert "native_cover_letter_writer_v1.ensure_schema" in migration.read_text(
        encoding="utf-8"
    )
