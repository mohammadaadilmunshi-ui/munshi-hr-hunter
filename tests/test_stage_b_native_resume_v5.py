from __future__ import annotations

import base64
import os

import pytest

from app import database
from app import native_resume_service_v2 as resume_v2
from app import native_resume_service_v3 as resume_v3
from app import native_resume_service_v5 as resume_v5
from app import resume_profile_details_v31 as profile_details
from app import stage_b_resume_binding_v1 as stage_b_binding
from app.tenant_foundation import associate_owned_record


def _vault_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _confirmed_profile(hunter_db, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _vault_key())
    source = resume_v3.save_confirmed_source(
        content_text=(
            "Example Candidate\n"
            "candidate@example.test | New Jersey\n"
            "SUMMARY\nPeople analytics candidate with Excel and Power BI experience.\n"
            "EXPERIENCE\nExample Co | HR Analyst\n"
            "Built workforce dashboards using Excel and Power BI.\n"
            "Maintained accurate employee documentation.\n"
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
                        "Maintained accurate employee documentation.",
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
        response_id="resp-stage-b-v5-profile",
    )
    resume_v3.confirm_profile_extract(extracted["extraction_id"])
    profile_details.save_candidate_profile_details(
        {
            "visa_or_permit": "SECRET-VISA-VALUE",
            "sponsorship_required": True,
            "work_modes": ["Hybrid"],
        }
    )


def _job() -> int:
    connection = database.get_connection()
    try:
        job_id = connection.execute(
            """INSERT INTO jobs(
                job_fingerprint,source,company_name,title,location_raw,city,state,country,
                remote_type,description_raw,salary_raw,target_track,employment_type,
                responsibilities,qualifications,preferred_qualifications,preferred_skills,
                skills_keywords,work_authorization,hunter_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"stage-b-v5-{os.urandom(8).hex()}",
                "fixture",
                "Example Co",
                "People Analytics Analyst",
                "New York, NY",
                "New York",
                "NY",
                "US",
                "Hybrid",
                "Build workforce dashboards and analyze people data.",
                "$80,000 - $95,000 per year",
                "People Analytics Analyst",
                "Full-time",
                "Build workforce dashboards using Excel and Power BI.",
                "Advanced Excel required. Workday configuration required.",
                "People analytics preferred.",
                "Power BI preferred.",
                "Excel, Power BI, People Analytics",
                None,
                95,
            ),
        ).lastrowid
        associate_owned_record(connection, record_domain="job", record_key=str(job_id))
        connection.commit()
        return int(job_id)
    finally:
        connection.close()


def _evidence_id(prompt_payload: dict, phrase: str) -> str:
    phrase_folded = phrase.casefold()
    candidates = [
        item
        for item in prompt_payload["evidence_bundle"]["items"]
        if phrase_folded in str(item.get("text") or "").casefold()
    ]
    truth_candidates = [
        item for item in candidates if str(item.get("evidence_id") or "").startswith("truth:")
    ]
    selected = (truth_candidates or candidates)[0]
    return str(selected["evidence_id"])


def _writer_document(prompt_payload: dict, *, inject_workday: bool = False) -> dict:
    summary_id = _evidence_id(prompt_payload, "People analytics candidate")
    experience_id = _evidence_id(prompt_payload, "Built workforce dashboards")
    skills_id = _evidence_id(prompt_payload, "Excel")
    bullet = (
        "Configured Workday workflows and built workforce dashboards using Excel and Power BI."
        if inject_workday
        else "Built workforce dashboards using Excel and Power BI."
    )
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
            "evidence_ids": [summary_id],
        },
        "education": [],
        "skills": [
            {
                "label": "Analytics",
                "skills": ["Excel", "Power BI", "People Analytics"],
                "evidence_ids": [skills_id],
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
                        "text": bullet,
                        "evidence_ids": [experience_id],
                    }
                ],
            }
        ],
        "projects": [],
        "certifications": [],
    }


def _fake_writer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    inject_workday: bool = False,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-stage-b-v5")

    def call(**kwargs):
        return (
            _writer_document(
                kwargs["prompt_payload"],
                inject_workday=inject_workday,
            ),
            "resp-stage-b-v5",
            "gpt-test",
        )

    monkeypatch.setattr(resume_v2, "_call_openai_v2", call)


def test_v5_generation_atomically_binds_plan_and_claim_trace(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    job_id = _job()

    result = resume_v5.generate_resume(job_id=job_id, rewrite_mode="medium")

    assert result["candidate_truth_bound"] is True
    assert result["job_snapshot_bound"] is True
    assert result["stage_b_bound"] is True
    assert result["stage_b_binding"]["plan_id"]
    assert len(result["stage_b_binding"]["plan_digest"]) == 64
    assert result["stage_b_binding"]["trace_id"] == result["stage_b_trace"]["trace_id"]
    assert result["stage_b_trace"]["diagnostics"]["visible_resume_changed"] is False
    assert result["stage_b_trace"]["diagnostics"]["jd_linked_claim_count"] >= 1
    assert result["diagnostics"]["stage_b"]["claim_guard"]["status"] == "PASS"
    assert "SECRET-VISA-VALUE" not in str(result)
    assert resume_v5.native_resume_authority_enabled() is False

    connection = database.get_connection()
    try:
        version_count = connection.execute(
            "SELECT COUNT(*) FROM native_resume_versions WHERE version_id=?",
            (result["version_id"],),
        ).fetchone()[0]
        truth_count = connection.execute(
            "SELECT COUNT(*) FROM native_resume_truth_bindings WHERE version_id=?",
            (result["version_id"],),
        ).fetchone()[0]
        job_count = connection.execute(
            "SELECT COUNT(*) FROM native_resume_job_bindings WHERE version_id=?",
            (result["version_id"],),
        ).fetchone()[0]
        stage_b_count = connection.execute(
            "SELECT COUNT(*) FROM native_resume_stage_b_bindings WHERE version_id=?",
            (result["version_id"],),
        ).fetchone()[0]
        trace_count = connection.execute(
            "SELECT COUNT(*) FROM jd_resume_claim_traces WHERE resume_version_id=?",
            (result["version_id"],),
        ).fetchone()[0]
    finally:
        connection.close()

    assert (version_count, truth_count, job_count, stage_b_count, trace_count) == (1, 1, 1, 1, 1)


def test_v5_rejects_unsupported_jd_only_language_before_persistence(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch, inject_workday=True)
    job_id = _job()

    with pytest.raises(ValueError, match="unsupported JD-only language"):
        resume_v5.generate_resume(job_id=job_id)

    connection = database.get_connection()
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM native_resume_versions WHERE job_id=?",
            (job_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 0


def test_v5_rolls_back_resume_truth_job_trace_and_stage_b_binding_together(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    job_id = _job()

    original_save = stage_b_binding.save_binding

    def fail_after_validating(*args, **kwargs):
        # Fail at the last sidecar write, after the resume/truth/job/trace writes
        # have been attempted inside the same transaction.
        raise RuntimeError("synthetic final Stage B binding failure")

    monkeypatch.setattr(stage_b_binding, "save_binding", fail_after_validating)
    try:
        with pytest.raises(RuntimeError, match="synthetic final Stage B binding failure"):
            resume_v5.generate_resume(job_id=job_id)
    finally:
        monkeypatch.setattr(stage_b_binding, "save_binding", original_save)

    connection = database.get_connection()
    try:
        version_count = connection.execute(
            "SELECT COUNT(*) FROM native_resume_versions WHERE job_id=?",
            (job_id,),
        ).fetchone()[0]
        trace_count = connection.execute(
            "SELECT COUNT(*) FROM jd_resume_claim_traces WHERE job_id=?",
            (job_id,),
        ).fetchone()[0]
        stage_b_count = connection.execute(
            "SELECT COUNT(*) FROM native_resume_stage_b_bindings WHERE job_id=?",
            (job_id,),
        ).fetchone()[0]
        orphan_truth = connection.execute(
            """SELECT COUNT(*) FROM native_resume_truth_bindings b
               JOIN native_resume_versions v ON v.version_id=b.version_id
               WHERE v.job_id=?""",
            (job_id,),
        ).fetchone()[0]
        orphan_job = connection.execute(
            "SELECT COUNT(*) FROM native_resume_job_bindings WHERE job_id=?",
            (job_id,),
        ).fetchone()[0]
    finally:
        connection.close()

    assert (version_count, trace_count, stage_b_count, orphan_truth, orphan_job) == (0, 0, 0, 0, 0)


def test_v5_parent_revision_requires_exact_same_stage_b_plan(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    job_id = _job()
    first = resume_v5.generate_resume(job_id=job_id)

    second = resume_v5.generate_resume(
        job_id=job_id,
        parent_version_id=first["version_id"],
        instruction="Tighten wording without adding facts.",
    )

    assert second["parent_version_id"] == first["version_id"]
    assert second["stage_b_binding"]["plan_digest"] == first["stage_b_binding"]["plan_digest"]
    assert second["version_number"] == first["version_number"] + 1


def test_v5_migration_is_additive(hunter_db) -> None:
    connection = database.get_connection()
    try:
        before = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        migration = __import__(
            "migrations.039_stage_b_native_resume_v5",
            fromlist=["apply"],
        )
        migration.apply(connection)
        connection.commit()
        after = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()

    assert before <= after
    assert "native_resume_stage_b_bindings" in after
