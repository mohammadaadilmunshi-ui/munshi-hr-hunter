from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app import database
from app import jd_requirement_match_v1 as matcher
from app import phase45_truth_binding
from app.tenant_foundation import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    associate_owned_record,
)


def _job(
    *,
    qualifications: str = (
        "3+ years of HR operations experience required.\n"
        "Advanced Excel required."
    ),
) -> int:
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
                f"stage-b-match-{uuid4()}",
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
                qualifications,
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


def _candidate_snapshot(*, revision: int = 1, digest_seed: str = "a") -> dict:
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "user_id": DEFAULT_USER_ID,
        "source_extraction_id": "stage-b-extraction",
        "profile_revision": revision,
        "profile_digest": digest_seed * 64,
        "facts": [
            {
                "fact_id": "fact-excel",
                "key": "skills.analytics.skills",
                "category": "skills",
                "trust_level": "confirmed",
                "source": "master_resume",
                "value": ["Excel", "Power BI", "People Analytics"],
                "protected": False,
            },
            {
                "fact_id": "fact-hr-ops",
                "key": "experience.0.bullets",
                "category": "experience",
                "trust_level": "confirmed",
                "source": "master_resume",
                "value": [
                    "Built workforce dashboards using Excel and Power BI.",
                    "Maintained accurate HR documentation and employee records.",
                ],
                "protected": False,
            },
            {
                "fact_id": "fact-protected-visa",
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


def test_stage_b_match_is_bound_to_exact_jd_and_candidate_truth(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = [_candidate_snapshot()]
    _bind(monkeypatch, box)
    job_id = _job()

    result = matcher.match_job(job_id)

    assert result["job_id"] == job_id
    assert result["jd_snapshot_digest"]
    assert result["candidate_truth_binding"]["profile_revision"] == 1
    assert result["candidate_truth_binding"]["profile_digest"] == "a" * 64
    assert result["submission_authority"] is False


def test_stage_b_match_uses_nonprotected_evidence_and_keeps_protected_eligibility_unknown(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = [_candidate_snapshot()]
    _bind(monkeypatch, box)
    result = matcher.match_job(_job(), persist=False)

    excel_matches = [
        item
        for item in result["requirement_matches"]
        if item["type"] in {"SKILL", "TOOL"}
        and any(key.startswith("skills.") for key in item["evidence_keys"])
    ]
    assert excel_matches
    assert any(
        item["match_status"] in {"DIRECT", "STRONG_TRANSFERABLE", "PARTIAL"}
        for item in excel_matches
    )

    protected_unknowns = [
        item
        for item in result["requirement_matches"]
        if item["type"] in {"WORK_AUTHORIZATION", "SPONSORSHIP"}
    ]
    assert protected_unknowns
    assert all(item["match_status"] == "UNKNOWN" for item in protected_unknowns)
    assert "SECRET-PROTECTED-VISA-VALUE" not in json.dumps(result)


def test_stage_b_match_exposes_unsupported_must_haves(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = [_candidate_snapshot()]
    _bind(monkeypatch, box)
    job_id = _job(
        qualifications=(
            "10+ years of payroll implementation experience required.\n"
            "Advanced Excel required."
        )
    )

    result = matcher.match_job(job_id, persist=False)

    assert result["unsupported_must_have_requirement_ids"]
    assert result["evidence_coverage_score"] is not None


def test_stage_b_match_is_idempotent_and_stales_after_candidate_truth_change(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = [_candidate_snapshot()]
    _bind(monkeypatch, box)
    job_id = _job()

    first = matcher.match_job(job_id)
    second = matcher.match_job(job_id)

    assert first["match_snapshot_id"] == second["match_snapshot_id"]
    assert matcher.match_freshness(first["match_snapshot_id"])["fresh"] is True

    box[0] = _candidate_snapshot(revision=2, digest_seed="b")
    assert matcher.match_freshness(first["match_snapshot_id"])["fresh"] is False


def test_stage_b_match_migration_is_additive(hunter_db) -> None:
    connection = database.get_connection()
    try:
        before = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        migration = __import__(
            "migrations.036_stage_b_candidate_job_match_v1",
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
    assert "candidate_job_match_snapshots" in after
