from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app import career_policy, database
from app import opportunity_intelligence_v4 as opportunity_v4
from app import phase45_truth_binding
from app.tenant_foundation import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    associate_owned_record,
)


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUNSHI_CAREER_POLICY_ENABLED", "1")


def _candidate(*, revision: int = 1, digest_seed: str = "a") -> dict:
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "user_id": DEFAULT_USER_ID,
        "source_extraction_id": "stage-b-opportunity-extraction",
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
                "fact_id": "fact-experience",
                "key": "experience.0.bullets",
                "category": "experience",
                "trust_level": "confirmed",
                "source": "master_resume",
                "value": [
                    "Built workforce dashboards using Excel and Power BI.",
                    "Analyzed people data and HR reporting metrics.",
                ],
                "protected": False,
            },
            {
                "fact_id": "fact-protected",
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


def _job(
    *,
    title: str = "People Analytics Analyst",
    location: str = "New York, NY",
    remote_type: str = "Hybrid",
    qualifications: str = (
        "Advanced Excel required.\n"
        "10+ years of payroll implementation experience required."
    ),
    work_authorization: str | None = None,
) -> int:
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
                f"stage-b-opportunity-{uuid4()}",
                "fixture",
                "Analytics Co",
                title,
                location,
                "New York",
                "NY",
                "US",
                remote_type,
                (
                    "Company marketing text about culture and benefits. "
                    "This paragraph deliberately repeats innovation collaboration future growth."
                ),
                title,
                "Full-time",
                "Build workforce dashboards using Excel and Power BI.",
                qualifications,
                "People analytics preferred.",
                "Power BI preferred.",
                "Excel, Power BI, People Analytics",
                work_authorization,
                95,
            ),
        ).lastrowid
        associate_owned_record(connection, record_domain="job", record_key=str(job_id))
        connection.commit()
        return int(job_id)
    finally:
        connection.close()


def _pass_policy() -> None:
    career_policy.save_preferences(
        {
            "target_roles": ["People Analytics Analyst"],
            "allowed_locations": ["New York"],
            "remote_allowed": True,
            "hybrid_allowed": True,
            "onsite_allowed": True,
        }
    )
    career_policy.save_autopilot_policy(
        {
            "allowed_role_families": ["People Analytics Analyst"],
            "allowed_locations": ["New York"],
            "employment_types": ["Full-time"],
        }
    )


def test_v4_uses_stage_b_requirement_components_not_legacy_raw_token_bag(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    box = [_candidate()]
    _bind(monkeypatch, box)
    _pass_policy()

    result = opportunity_v4.evaluate_job(_job(), persist=False)
    names = {item["input"] for item in result["score_explanation"]}

    assert "must_have_evidence_fit" in names
    assert "core_responsibility_evidence_fit" in names
    assert "career_direction_fit" in names
    assert "skill_fit" not in names
    assert "evidence_backed_experience_fit" not in names
    assert result["stage_b_binding"]["jd_snapshot_id"]
    assert result["stage_b_binding"]["match_snapshot_id"]
    assert result["unsupported_must_have_requirement_ids"]
    assert result["submission_authority"] is False
    assert result["automatic_actions_executed"] is False
    assert "SECRET-PROTECTED-VISA-VALUE" not in json.dumps(result)


def test_v4_protected_eligibility_requirement_remains_explicit_unknown(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    box = [_candidate()]
    _bind(monkeypatch, box)
    _pass_policy()

    result = opportunity_v4.evaluate_job(
        _job(work_authorization="Must be authorized to work in the United States"),
        persist=False,
    )

    assert result["status"] == "NEEDS_INPUT"
    assert any(value.startswith("eligibility_requirement:JDREQ-") for value in result["unknowns"])
    assert result["pursuit_strategy"]["pursuit_state"] == "WATCH"
    assert "SECRET-PROTECTED-VISA-VALUE" not in json.dumps(result)


def test_v4_hard_policy_failure_cannot_be_overridden_by_strong_stage_b_fit(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    box = [_candidate()]
    _bind(monkeypatch, box)
    career_policy.save_preferences(
        {
            "target_roles": ["People Analytics Analyst"],
            "hybrid_allowed": True,
            "remote_allowed": True,
            "onsite_allowed": True,
        }
    )
    career_policy.save_autopilot_policy(
        {
            "allowed_role_families": ["People Analytics Analyst"],
            "allowed_locations": ["New Jersey"],
            "employment_types": ["Full-time"],
        }
    )

    result = opportunity_v4.evaluate_job(
        _job(qualifications="Advanced Excel required."),
        persist=False,
    )

    assert result["opportunity_score"] is not None
    assert "location_not_allowed" in result["hard_failures"]
    assert result["status"] == "FAIL"
    assert result["pursuit_strategy"]["pursuit_state"] == "IGNORE"


def test_v4_persistence_binds_exact_stage_b_state_and_stales_on_candidate_truth_change(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    box = [_candidate()]
    _bind(monkeypatch, box)
    _pass_policy()
    job_id = _job(qualifications="Advanced Excel required.")

    persisted = opportunity_v4.evaluate_job(job_id)
    loaded = opportunity_v4.get_evaluation(persisted["evaluation_id"])

    assert loaded["result_sha256"] == persisted["result_sha256"]
    assert loaded["stage_b_binding"]["match_digest"] == persisted["stage_b_binding"]["match_digest"]
    assert opportunity_v4.evaluation_freshness(persisted["evaluation_id"])["fresh"] is True

    box[0] = _candidate(revision=2, digest_seed="b")
    freshness = opportunity_v4.evaluation_freshness(persisted["evaluation_id"])
    assert freshness["fresh"] is False
    assert any("candidate_truth_binding" in item or "stage_b_binding" in item for item in freshness["changed_bindings"])


def test_v4_migration_is_additive(hunter_db) -> None:
    connection = database.get_connection()
    try:
        before = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        migration = __import__(
            "migrations.040_stage_b_opportunity_intelligence_v4",
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
    assert "jobs" in after
    assert "opportunity_intelligence_v4_evaluations" in after
