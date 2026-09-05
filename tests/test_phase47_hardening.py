from __future__ import annotations

import base64
import copy
import importlib
import json
import os

import pytest

from app import answer_brain_v2 as answers_v2
from app import career_policy, database
from app import native_resume_service_v2 as resume_v2
from app import native_resume_service_v3 as resume_v3
from app import native_resume_service_v4 as resume_v4
from app import opportunity_intelligence_v3 as opportunity_v3
from app import phase45_truth_binding
from app import phase47_integrity
from app import profile_truth_overrides_v1 as overrides
from app import relationship_intelligence_v3 as relationship_v3
from app import resume_profile_details_v31 as profile_details
from app.tenant_foundation import associate_owned_record


def _vault_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _confirmed_profile(hunter_db, monkeypatch: pytest.MonkeyPatch) -> dict:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _vault_key())
    source = resume_v3.save_confirmed_source(
        content_text=(
            "Example Candidate\n"
            "candidate@example.test | New Jersey\n"
            "SUMMARY\nPeople analytics candidate with Excel and Power BI experience.\n"
            "EXPERIENCE\nExample Co | HR Analyst\n"
            "Built workforce dashboards using Excel and Power BI.\n"
            "Analyzed people data and HR reporting metrics.\n"
            "SKILLS\nExcel | Power BI | People Analytics"
        ),
        label="Master Resume",
        source_kind="pasted_text",
    )
    extracted = resume_v3._persist_profile(
        source=source,
        profile=resume_v3.CandidateProfileExtract(
            professional_summary="People analytics candidate with Excel and Power BI experience.",
            contact=resume_v3.ContactProfile(
                full_name="Example Candidate",
                location="New Jersey",
                email="candidate@example.test",
            ),
            experience=[
                resume_v3.ExperienceProfile(
                    employer="Example Co",
                    title="HR Analyst",
                    bullets=[
                        "Built workforce dashboards using Excel and Power BI.",
                        "Analyzed people data and HR reporting metrics.",
                    ],
                )
            ],
            skills=[
                resume_v3.SkillCategory(
                    category="Analytics",
                    skills=["Excel", "Power BI", "People Analytics"],
                )
            ],
        ),
        model="test-model",
        response_id="resp-phase47",
    )
    confirmed = resume_v3.confirm_profile_extract(extracted["extraction_id"])
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
                job_fingerprint,source,company_name,title,location_raw,city,state,country,
                remote_type,description_raw,target_track,employment_type,responsibilities,
                qualifications,preferred_qualifications,preferred_skills,skills_keywords,
                work_authorization,hunter_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"phase47-{os.urandom(8).hex()}",
                "fixture",
                "Analytics Co",
                "People Analytics Analyst",
                "New York, NY",
                "New York",
                "NY",
                "US",
                "Hybrid",
                "Build workforce dashboards and analyze people data using Excel and Power BI.",
                "People Analytics Analyst",
                "Full-time",
                "Build dashboards and analyze workforce data.",
                "Excel, Power BI, HR analytics.",
                "People analytics preferred.",
                "Workforce reporting and stakeholder support.",
                "Excel,Power BI,People Analytics",
                None,
                95,
            ),
        ).lastrowid
        associate_owned_record(connection, record_domain="job", record_key=str(job_id))
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


def _fake_resume_writer(monkeypatch: pytest.MonkeyPatch, snapshot: dict) -> None:
    bundle = resume_v4._truth_bound_evidence_bundle(snapshot)
    evidence_id = next(
        item["evidence_id"]
        for item in bundle["items"]
        if "People analytics candidate" in item["text"]
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-phase47")
    monkeypatch.setattr(
        resume_v2,
        "_call_openai_v2",
        lambda **kwargs: (_resume_document(evidence_id), "resp-phase47-v4", "gpt-test"),
    )


def test_phase4_resume_is_bound_to_exact_job_snapshot_and_parent_refuses_changed_job(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    snapshot = phase45_truth_binding.current_candidate_profile_snapshot()
    _fake_resume_writer(monkeypatch, snapshot)
    job_id = _job()

    first = resume_v4.generate_resume(job_id=job_id)
    assert first["candidate_truth_bound"] is True
    assert first["job_snapshot_bound"] is True
    assert len(first["job_snapshot_binding"]["job_snapshot_sha256"]) == 64
    assert len(first["job_snapshot_binding"]["generation_input_sha256"]) == 64
    assert first["diagnostics"]["job_snapshot_sha256"] == first["job_snapshot_binding"]["job_snapshot_sha256"]

    connection = database.get_connection()
    try:
        connection.execute(
            "UPDATE jobs SET description_raw=? WHERE id=?",
            ("Changed job description after resume generation.", job_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="stored job changed"):
        resume_v4.generate_resume(job_id=job_id, parent_version_id=first["version_id"])


def test_phase4_refuses_persistence_when_candidate_truth_changes_during_generation(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    initial = phase45_truth_binding.current_candidate_profile_snapshot()
    changed = copy.deepcopy(initial)
    changed["profile_revision"] = int(initial["profile_revision"]) + 1
    changed["profile_digest"] = "b" * 64
    _fake_resume_writer(monkeypatch, initial)
    sequence = iter([initial, changed])
    monkeypatch.setattr(
        phase45_truth_binding,
        "current_candidate_profile_snapshot",
        lambda: next(sequence),
    )
    job_id = _job()

    with pytest.raises(RuntimeError, match="changed during resume generation"):
        resume_v4.generate_resume(job_id=job_id)

    connection = database.get_connection()
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM native_resume_versions WHERE job_id=?",
            (job_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 0


def test_phase5_profile_answer_and_truth_binding_are_atomic(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    monkeypatch.setenv("MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED", "1")

    def fail_binding(**kwargs):
        raise RuntimeError("synthetic binding failure")

    monkeypatch.setattr(phase45_truth_binding, "save_answer_truth_binding", fail_binding)
    with pytest.raises(RuntimeError, match="synthetic binding failure"):
        answers_v2.save_profile_answer(
            question_family="candidate_fact",
            question_key="candidate.professional_summary",
            profile_fact_key="profile.professional_summary",
            autofill_allowed=True,
        )

    connection = database.get_connection()
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM application_answer_vault WHERE source='profile_evidence'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 0


def test_phase5_planning_input_never_contains_stale_profile_answer_content(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    extracted = _confirmed_profile(hunter_db, monkeypatch)
    monkeypatch.setenv("MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED", "1")
    old_value = "People analytics candidate with Excel and Power BI experience."
    answers_v2.save_profile_answer(
        question_family="candidate_fact",
        question_key="candidate.professional_summary",
        profile_fact_key="profile.professional_summary",
        autofill_allowed=True,
    )
    overrides.save_profile_override(
        extracted,
        "professional_summary",
        "Candidate-confirmed revised professional summary.",
    )

    planning = answers_v2.planning_input()
    serialized = json.dumps(planning)
    assert old_value not in serialized
    assert planning["answers"] == []
    assert planning["excluded_answers"][0]["reason"] == "stale_or_unbound_profile_answer"


def test_phase6_refuses_persistence_if_binding_inputs_change_mid_evaluation(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUNSHI_CAREER_POLICY_ENABLED", "1")
    career_policy.save_preferences({"target_roles": ["People Analytics Analyst"]})
    career_policy.save_autopilot_policy({})
    first = {
        "tenant_id": "local-singleton",
        "user_id": "local-user",
        "source_extraction_id": "phase6-snapshot",
        "profile_revision": 1,
        "profile_digest": "a" * 64,
        "facts": [
            {"key": "skills.analytics.skills", "value": ["Excel", "Power BI"], "protected": False},
            {"key": "experience.0.bullets", "value": ["Built workforce dashboards using Excel."], "protected": False},
        ],
    }
    second = copy.deepcopy(first)
    second["profile_revision"] = 2
    second["profile_digest"] = "b" * 64
    sequence = iter([first, second])
    monkeypatch.setattr(
        phase45_truth_binding,
        "current_candidate_profile_snapshot",
        lambda: next(sequence),
    )
    job_id = _job()

    with pytest.raises(RuntimeError, match="Opportunity inputs changed during evaluation"):
        opportunity_v3.evaluate_job(job_id)

    connection = database.get_connection()
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM opportunity_intelligence_evaluations WHERE job_id=?",
            (job_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 0


def test_phase7_incomplete_relationship_evidence_cannot_upgrade_apply() -> None:
    top = {
        "relationship_score": 95.0,
        "score_confidence": 0.70,
        "unknowns": ["relationship_evidence_strength"],
        "recommended_action": "review",
    }
    opportunity = {
        "fresh": True,
        "pursuit_state": "APPLY",
        "hard_failures": [],
        "unknowns": [],
    }
    combined = relationship_v3._combined_strategy_hardened([top], opportunity)
    assert combined["combined_pursuit_state"] == "APPLY"
    assert combined["networking_action"] == "review"


def test_phase47_integrity_requires_one_current_truth_and_job_snapshot(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    monkeypatch.setenv("MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED", "1")
    monkeypatch.setenv("MUNSHI_CAREER_POLICY_ENABLED", "1")
    monkeypatch.setenv("MUNSHI_RELATIONSHIP_INTELLIGENCE_ENABLED", "1")
    career_policy.save_preferences({"target_roles": ["People Analytics Analyst"]})
    career_policy.save_autopilot_policy({})
    snapshot = phase45_truth_binding.current_candidate_profile_snapshot()
    _fake_resume_writer(monkeypatch, snapshot)
    job_id = _job()

    resume = resume_v4.generate_resume(job_id=job_id)
    opportunity = opportunity_v3.evaluate_job(job_id)
    ready = phase47_integrity.application_preparation_readiness(
        job_id=job_id,
        resume_version_id=resume["version_id"],
        opportunity_evaluation_id=opportunity["evaluation_id"],
    )
    assert ready["status"] == "READY"
    assert ready["submission_authority"] is False
    assert ready["automatic_actions_executed"] is False

    connection = database.get_connection()
    try:
        connection.execute(
            "UPDATE jobs SET description_raw=? WHERE id=?",
            ("A materially changed description after all prior snapshots.", job_id),
        )
        connection.commit()
    finally:
        connection.close()

    stale = phase47_integrity.application_preparation_readiness(
        job_id=job_id,
        resume_version_id=resume["version_id"],
        opportunity_evaluation_id=opportunity["evaluation_id"],
    )
    assert stale["status"] == "HOLD"
    assert "resume_job_snapshot_stale" in stale["blockers"]
    assert "opportunity_evaluation_stale" in stale["blockers"]


def test_migration_033_is_additive(hunter_db) -> None:
    job_id = _job()
    connection = database.get_connection()
    try:
        importlib.import_module("migrations.033_phase47_integrity_bindings").apply(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        job = connection.execute("SELECT id,title FROM jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        connection.close()
    assert "native_resume_job_bindings" in tables
    assert dict(job) == {"id": job_id, "title": "People Analytics Analyst"}
