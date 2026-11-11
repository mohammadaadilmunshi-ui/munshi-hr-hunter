from __future__ import annotations

import base64
import copy
import importlib
import json
import os

import pytest

from app import database
from app import native_resume_service_v2 as v2
from app import native_resume_service_v3 as v3
from app import native_resume_service_v4 as v4
from app import profile_truth_overrides_v1 as overrides
from app import resume_profile_details_v31 as profile_details
from app.answer_brain_v2 import (
    planning_input,
    resolve_answer,
    save_answer,
    save_profile_answer,
)
from app.phase45_truth_binding import (
    current_candidate_profile_snapshot,
    resume_truth_binding,
)


def _vault_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _confirmed_profile(hunter_db, monkeypatch) -> dict:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _vault_key())
    source = v3.save_confirmed_source(
        content_text=(
            "Example Candidate\n"
            "candidate@example.test | New Jersey\n"
            "SUMMARY\nPeople analytics candidate with Excel and Power BI experience.\n"
            "EXPERIENCE\nExample Co | HR Analyst\nBuilt workforce dashboards using Excel and Power BI.\n"
            "SKILLS\nExcel | Power BI"
        ),
        label="Master Resume",
        source_kind="pasted_text",
    )
    extracted = v3._persist_profile(
        source=source,
        profile=v3.CandidateProfileExtract(
            professional_summary="People analytics candidate with Excel and Power BI experience.",
            contact=v3.ContactProfile(
                full_name="Example Candidate",
                location="New Jersey",
                email="candidate@example.test",
            ),
            experience=[
                v3.ExperienceProfile(
                    employer="Example Co",
                    title="HR Analyst",
                    bullets=["Built workforce dashboards using Excel and Power BI."],
                )
            ],
            skills=[v3.SkillCategory(category="Analytics", skills=["Excel", "Power BI"])],
        ),
        model="test-model",
        response_id="resp-phase45",
    )
    confirmed = v3.confirm_profile_extract(extracted["extraction_id"])
    profile_details.save_candidate_profile_details(
        {
            "visa_or_permit": "SECRET-VISA-VALUE",
            "sponsorship_required": True,
            "work_modes": ["Hybrid"],
        }
    )
    return confirmed


def _job() -> int:
    connection = database.get_connection()
    try:
        job_id = connection.execute(
            """INSERT INTO jobs(
                job_fingerprint,source,company_name,title,location_raw,description_raw,
                responsibilities,qualifications,preferred_skills,skills_keywords,hunter_score
            ) VALUES (
                'phase45-job','fixture','Analytics Co','People Analytics Analyst','New York, NY',
                'Build workforce dashboards and HR reporting using Excel and Power BI.',
                'Build dashboards and analyze workforce data.',
                'Excel, Power BI, HR analytics.',
                'People analytics preferred.',
                'Excel,Power BI,People Analytics',95
            )"""
        ).lastrowid
        connection.commit()
        return int(job_id)
    finally:
        connection.close()


def _resume_document(evidence_id: str) -> dict:
    return {
        "schema_version": "native-resume-v1",
        "template_version": "ats-single-column-v1",
        "candidate_name": "Example Candidate",
        "contact": {
            "location": "New Jersey",
            "email": "candidate@example.test",
            "phone": "",
            "linkedin": "",
            "github": "",
            "portfolio": "",
        },
        "summary": {
            "text": "People analytics candidate with Excel and Power BI experience.",
            "evidence_ids": [evidence_id],
        },
        "education": [],
        "skills": [
            {
                "label": "Analytics",
                "skills": ["Excel", "Power BI"],
                "evidence_ids": [evidence_id],
            }
        ],
        "experience": [
            {
                "organization": "Example Co",
                "title": "HR Analyst",
                "dates": "",
                "location": "",
                "bullets": [
                    {
                        "text": "Built workforce dashboards using Excel and Power BI.",
                        "evidence_ids": [evidence_id],
                    }
                ],
            }
        ],
        "projects": [],
        "certifications": [],
    }


def test_phase4_generation_is_atomically_bound_and_protected_truth_never_enters_prompt(
    hunter_db, monkeypatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-phase45-resume-key-value")
    job_id = _job()
    snapshot = current_candidate_profile_snapshot()
    bundle = v4._truth_bound_evidence_bundle(snapshot)
    evidence_id = next(
        item["evidence_id"]
        for item in bundle["items"]
        if "People analytics candidate" in item["text"]
    )
    payloads: list[dict] = []

    def fake_call(*, prompt_payload, config, rewrite_mode):
        payloads.append(copy.deepcopy(prompt_payload))
        return _resume_document(evidence_id), "resp-v4", "gpt-test"

    monkeypatch.setattr(v2, "_call_openai_v2", fake_call)
    version = v4.generate_resume(job_id=job_id)

    assert version["candidate_truth_bound"] is True
    assert version["candidate_truth_binding"]["source_extraction_id"] == snapshot["source_extraction_id"]
    assert version["candidate_truth_binding"]["profile_revision"] == snapshot["profile_revision"]
    assert version["candidate_truth_binding"]["profile_digest"] == snapshot["profile_digest"]
    assert version["diagnostics"]["candidate_truth_binding"]["profile_digest"] == snapshot["profile_digest"]
    assert resume_truth_binding(version["version_id"])["profile_digest"] == snapshot["profile_digest"]

    serialized = json.dumps(payloads)
    assert "candidate_truth_profile" in serialized
    assert "SECRET-VISA-VALUE" not in serialized
    assert "application_defaults.visa_or_permit" not in serialized
    assert v4.native_resume_authority_enabled() is False


def test_phase4_refuses_revision_after_candidate_truth_changes(hunter_db, monkeypatch) -> None:
    extracted = _confirmed_profile(hunter_db, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-phase45-resume-key-value")
    job_id = _job()
    snapshot = current_candidate_profile_snapshot()
    bundle = v4._truth_bound_evidence_bundle(snapshot)
    evidence_id = next(item["evidence_id"] for item in bundle["items"] if item["kind"] == "confirmed_resume_source")

    monkeypatch.setattr(
        v2,
        "_call_openai_v2",
        lambda **kwargs: (_resume_document(evidence_id), "resp-v4", "gpt-test"),
    )
    first = v4.generate_resume(job_id=job_id)

    overrides.save_profile_override(
        extracted,
        "professional_summary",
        "Candidate-confirmed updated analytics summary.",
    )
    with pytest.raises(ValueError, match="Candidate Truth Profile changed"):
        v4.generate_resume(job_id=job_id, parent_version_id=first["version_id"])


def test_phase5_question_keys_prevent_same_family_memory_collision(hunter_db, monkeypatch) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    monkeypatch.setenv("MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED", "1")

    save_answer(
        question_family="location",
        question_key="location.current_residence",
        canonical_answer="New Jersey",
        source="user",
        user_confirmed=True,
        confidence=1,
        autofill_allowed=True,
    )
    save_answer(
        question_family="location",
        question_key="location.preferred_work_location",
        canonical_answer="New York",
        source="user",
        user_confirmed=True,
        confidence=1,
        autofill_allowed=True,
    )

    first = resolve_answer(
        question_family="location",
        question_key="location.current_residence",
    )
    second = resolve_answer(
        question_family="location",
        question_key="location.preferred_work_location",
    )
    assert first["answer"]["canonical_answer"] == "New Jersey"
    assert second["answer"]["canonical_answer"] == "New York"
    assert first["question_key"] != second["question_key"]


def test_phase5_profile_memory_becomes_stale_when_truth_revision_changes(hunter_db, monkeypatch) -> None:
    extracted = _confirmed_profile(hunter_db, monkeypatch)
    monkeypatch.setenv("MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED", "1")

    save_profile_answer(
        question_family="candidate_fact",
        question_key="candidate.professional_summary",
        profile_fact_key="profile.professional_summary",
        autofill_allowed=True,
    )
    current = resolve_answer(
        question_family="candidate_fact",
        question_key="candidate.professional_summary",
        profile_fact_key="profile.professional_summary",
    )
    assert current["status"] == "ANSWERED"
    assert current["answer"]["candidate_truth_binding"]["profile_digest"]

    overrides.save_profile_override(
        extracted,
        "professional_summary",
        "New candidate-confirmed professional summary.",
    )
    stale = resolve_answer(
        question_family="candidate_fact",
        question_key="candidate.professional_summary",
        profile_fact_key="profile.professional_summary",
    )
    assert stale == {
        "status": "NEEDS_INPUT",
        "reason": "stale_profile_answer",
        "question_key": "candidate.professional_summary",
    }


def test_phase5_protected_profile_fact_never_enters_normal_answer_or_planning_projection(
    hunter_db, monkeypatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    monkeypatch.setenv("MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED", "1")

    resolved = resolve_answer(
        question_family="work_authorization",
        question_key="work_authorization.visa_or_permit",
        profile_fact_key="application_defaults.visa_or_permit",
    )
    assert resolved["status"] == "NEEDS_INPUT"
    assert resolved["reason"] == "protected_profile_fact_requires_explicit_policy"

    with pytest.raises(ValueError, match="Protected Candidate Truth"):
        save_profile_answer(
            question_family="work_authorization",
            question_key="work_authorization.visa_or_permit",
            profile_fact_key="application_defaults.visa_or_permit",
            autofill_allowed=True,
        )

    serialized = json.dumps(planning_input())
    assert "SECRET-VISA-VALUE" not in serialized
    assert "application_defaults.visa_or_permit" not in serialized


def test_migration_031_is_additive_and_preserves_original_phase4_phase5_tables(hunter_db) -> None:
    connection = database.get_connection()
    try:
        importlib.import_module("migrations.031_phase45_truth_bindings").apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert {
        "native_resume_sources",
        "native_resume_versions",
        "application_answer_vault",
        "sensitive_self_identification_vault",
        "native_resume_truth_bindings",
        "application_answer_truth_bindings",
    } <= tables
