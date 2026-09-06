from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app import database
from app import jd_resume_plan_v1 as planner
from app import phase45_truth_binding
from app.tenant_foundation import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    associate_owned_record,
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
                f"stage-b-plan-{uuid4()}",
                "fixture",
                "Example Co",
                "People Analytics Analyst",
                "New York, NY",
                "New York",
                "NY",
                "US",
                "Hybrid",
                (
                    "Must be authorized to work in the United States. "
                    "We are unable to provide visa sponsorship."
                ),
                "$80,000 - $95,000 per year",
                "People Analytics",
                "Full-time",
                "Build workforce dashboards using Excel and Power BI.",
                (
                    "Advanced Excel required.\n"
                    "10+ years of payroll implementation experience required."
                ),
                "Workday experience preferred.",
                "Power BI preferred.",
                "Excel, Power BI, HRIS",
                "Must be authorized to work in the United States",
                90,
            ),
        ).lastrowid
        associate_owned_record(connection, record_domain="job", record_key=str(job_id))
        connection.commit()
        return int(job_id)
    finally:
        connection.close()


def _candidate(*, revision: int = 1, digest_seed: str = "a") -> dict:
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "user_id": DEFAULT_USER_ID,
        "source_extraction_id": "stage-b-plan-extraction",
        "profile_revision": revision,
        "profile_digest": digest_seed * 64,
        "facts": [
            {
                "fact_id": "fact-skills",
                "key": "skills.analytics.skills",
                "category": "skills",
                "trust_level": "confirmed",
                "source": "master_resume",
                "value": ["Excel", "Power BI", "People Analytics"],
                "protected": False,
            },
            {
                "fact_id": "fact-exp",
                "key": "experience.0.bullets",
                "category": "experience",
                "trust_level": "confirmed",
                "source": "master_resume",
                "value": ["Built workforce dashboards using Excel and Power BI."],
                "protected": False,
            },
            {
                "fact_id": "fact-secret",
                "key": "application_defaults.visa_or_permit",
                "category": "application_defaults",
                "trust_level": "confirmed",
                "source": "candidate",
                "value": "SECRET-PROTECTED-VISA-VALUE",
                "protected": True,
            },
        ],
    }


def _bind(monkeypatch: pytest.MonkeyPatch, box: list[dict]) -> None:
    monkeypatch.setattr(
        phase45_truth_binding,
        "current_candidate_profile_snapshot",
        lambda: box[0],
    )


def test_stage_b_resume_plan_prioritizes_supported_requirements_and_blocks_unsupported(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = [_candidate()]
    _bind(monkeypatch, box)

    plan = planner.plan_for_job(_job(), persist=False)
    refs = {item["requirement_id"]: item for item in plan["requirement_refs"]}

    assert plan["summary_priority_requirement_ids"]
    assert plan["skills_priority_requirement_ids"]
    assert plan["unsupported_must_have_requirement_ids"]
    assert set(plan["unsupported_must_have_requirement_ids"]) <= set(
        plan["do_not_claim_requirement_ids"]
    )
    assert all(
        refs[requirement_id]["match_status"]
        in {"DIRECT", "STRONG_TRANSFERABLE", "PARTIAL"}
        for requirement_id in plan["one_page_retention_order"]
    )
    assert plan["submission_authority"] is False


def test_stage_b_writer_context_contains_supported_employer_text_but_no_protected_candidate_value(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = [_candidate()]
    _bind(monkeypatch, box)

    plan = planner.plan_for_job(_job(), persist=False)
    context = planner.writer_context(plan)
    serialized = json.dumps(context)

    assert context["supported_requirements"]
    assert context["do_not_claim"]
    assert "SECRET-PROTECTED-VISA-VALUE" not in serialized
    assert context["submission_authority"] is False


def test_stage_b_resume_plan_is_idempotent_and_stales_with_candidate_truth(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = [_candidate()]
    _bind(monkeypatch, box)
    job_id = _job()

    first = planner.plan_for_job(job_id)
    second = planner.plan_for_job(job_id)

    assert first["plan_id"] == second["plan_id"]
    assert planner.plan_freshness(first["plan_id"])["fresh"] is True

    box[0] = _candidate(revision=2, digest_seed="b")
    assert planner.plan_freshness(first["plan_id"])["fresh"] is False


def test_stage_b_resume_plan_migration_is_additive(hunter_db) -> None:
    connection = database.get_connection()
    try:
        before = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        migration = __import__(
            "migrations.037_stage_b_resume_tailoring_plan_v1",
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
    assert "jd_resume_tailoring_plans" in after
